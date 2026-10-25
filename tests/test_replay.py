"""The replay harness's own arithmetic (scripts/replay.py): a swap's gain by
day against a full lineup, and the week's real score with the goalie minimum."""
import datetime as dt
import math
from collections import defaultdict

import pytest

from scripts import replay

MON = dt.date(2025, 11, 3)


def _season(xfp, position, team_days, points=None, g_points=None, goalies=()):
    s = object.__new__(replay.Season)
    s.position, s.goalie_ids = position, set(goalies)
    s.team_days = defaultdict(set, team_days)
    s.points, s.g_points = points or {}, g_points or {}
    s._proj = {MON: (xfp, {g: 9.0 for g in goalies})}
    s._team, s._missed = {}, {}
    s.team = lambda pid, m: {1: "A", 2: "A", 3: "B", 9: "C", 10: "C", 11: "C"}[pid]
    s.missed = lambda pid, m: 0
    s.share = lambda pid, m: 0.5
    return s


def test_a_swap_gains_only_what_it_changes_in_the_best_lineup():
    # Two C slots: 1 (5.0) and 2 (3.0) start; 9 (4.0) would take 2's slot.
    tue, wed = MON + dt.timedelta(days=1), MON + dt.timedelta(days=2)
    s = _season({1: 5.0, 2: 3.0, 3: 2.0, 9: 4.0}, {1: "C", 2: "C", 3: "C", 9: "C"},
                {"A": {MON, tue}, "B": {MON}, "C": {tue, wed}})
    week = replay.Week(s, [1, 2, 3], MON, [MON, tue, wed])
    play = replay.availability.HEALTHY_PLAY
    gain = week.gain(9, 3, [MON, tue, wed])
    # Monday: dropping 3 (the third C, benched) costs nothing. Tuesday: 9 replaces
    # 2 in the lineup. Wednesday: 9 alone.
    assert MON not in gain and gain[tue] == pytest.approx((4.0 - 3.0) * play)
    assert gain[wed] == pytest.approx(4.0 * play)
    assert week.gain(9, 2, [tue])[tue] == pytest.approx((4.0 - 3.0) * play)  # same slot, same gain


def test_the_real_score_counts_who_played_and_zeroes_goalies_below_the_minimum():
    days = [MON + dt.timedelta(days=i) for i in range(7)]
    points = {(1, days[0]): 6.0, (2, days[0]): 2.0, (3, days[0]): 4.0}  # 3 Cs played Monday: 2 slots
    g_points = {(10, days[1]): 9.0, (11, days[2]): 7.0}
    s = _season({1: 5.0, 2: 3.0, 3: 4.0}, {1: "C", 2: "C", 3: "C"}, {}, points, g_points, goalies=(10, 11))
    assert replay.actual(s, [1, 2, 3, 10, 11], MON) == 10.0  # 6 + 4; two goalie starts: zeroed
    s.g_points[(10, days[3])] = 5.0
    assert replay.actual(s, [1, 2, 3, 10, 11], MON) == 10.0 + 21.0


def test_a_planned_exit_drops_a_bad_week_ahead_only_when_it_pays():
    # Week +1 gains 6 points, week +2 loses 5, then 2 more held days gain 1.
    future = [MON + dt.timedelta(days=7 + i) for i in range(42)]
    gain = {future[0]: 6.0, future[7]: -5.0, future[14]: 1.0}
    ahead = [replay.matchup.WeekAhead(k, frozenset(future[7 * (k - 1):7 * k]), None, 40.0) for k in (1, 2)]
    w, g1, g2 = 0.01, replay.matchup.GAP_REALIZES[1], replay.matchup.GAP_REALIZES[2]
    full = w * (6.0 * g1 - 5.0 * g2 + 1.0)

    def exit_(rule, price=0.09, keep=0.0):
        return replay.best_exit(gain, future, ahead, 20, w, 16, keep, rule, price)

    assert exit_(None) == (pytest.approx(full), None)
    assert exit_("free") == (pytest.approx(w * 6.0 * g1), 1)  # out after week +1
    # Charged the unused 9 of 16 held days' share of an add: dearer than week +2's loss.
    assert exit_("charged") == (pytest.approx(full), None)
    assert exit_("charged", price=0.01)[1] == 1
    assert exit_("free", keep=1.0)[1] is None  # a keeper is never planned out


def test_a_keeper_added_onto_a_streaming_spot_counts_the_season_only_under_keeper_add():
    from league.roster import RosterPlayer
    ctx = replay.Ctx({1: 5.0, 2: 3.1, 3: 3.4, 9: 3.0})  # 1 a keeper free agent, 2 my streaming spot, 3 a fringe keeper
    keeper, spot, fringe = (RosterPlayer(pid, str(pid), "A", ["C"]) for pid in (1, 2, 3))
    levels, line = {"C": 3.0}, 0.1  # waiver level; the streaming spots' best gap
    assert replay.keep_of(1.0, keeper, spot, {2}, line, levels, ctx) == 0.0  # the live rule: a spot's drop holds
    assert replay.keep_of("add", keeper, spot, {2}, line, levels, ctx) == 1.0  # he stays: the season
    # A streamer added for a fringe keeper: the add's own gap, so still near the hold either way.
    streamer = RosterPlayer(9, "9", "A", ["C"])
    assert replay.keep_of("add", streamer, fringe, set(), line, levels, ctx) == 0.0
    assert replay.keep_of(1.0, streamer, fringe, set(), line, levels, ctx) == 0.0
    assert replay.keep_of("add", fringe, spot, {2}, line, levels, ctx) == pytest.approx(0.3)


def test_goalies_count_by_the_week_with_the_minimum_and_a_goalie_add_gains_it():
    assert replay.at_least([0.5] * 4, 3) == pytest.approx(5 / 16)
    assert replay.at_least([1.0, 1.0, 1.0], 3) == pytest.approx(1.0) and replay.at_least([0.9, 0.9], 3) == 0.0
    days = [MON + dt.timedelta(days=i) for i in range(7)]
    # My two goalies (10, 11) share team C, which plays twice: 4 starts at 0.5, only 2 can happen.
    # Free agent 12's team (D) plays three other nights. Skater 1 (team A) plays Monday.
    s = _season({1: 4.0}, {1: "C"}, {"A": {days[0]}, "C": {days[0], days[2]}, "D": {days[1], days[3], days[5]}},
                goalies=(10, 11, 12))
    s.team = lambda pid, m: {1: "A", 10: "C", 11: "C", 12: "D"}[pid]
    week = replay.Week(s, [1, 10, 11], MON, days)
    plain = 4 * 0.5 * 9.0  # each goalie's start x share x points a start
    assert week.total(days)[0] == pytest.approx(4.0 * replay.availability.HEALTHY_PLAY
                                                + plain * replay.at_least([0.5] * 4, 3))
    # 12 for the skater: three more starts at 0.5, and the minimum is likelier.
    gain = week.gain(12, 1, days)
    after = (plain + 3 * 0.5 * 9.0) * replay.at_least([0.5] * 7, 3)
    assert sum(gain.values()) == pytest.approx(after - plain * replay.at_least([0.5] * 4, 3)
                                               - 4.0 * replay.availability.HEALTHY_PLAY)
    # 12 for goalie 11: team C's nights keep one goalie (2 starts), and 12 adds his 3.
    swap = week.gain(12, 11, days)
    assert sum(swap.values()) == pytest.approx((2 * 0.5 * 9.0 + 3 * 0.5 * 9.0) * replay.at_least([0.5] * 5, 3)
                                               - plain * replay.at_least([0.5] * 4, 3))


def test_the_pacing_price_is_the_bar_at_an_even_pace_lower_when_adds_were_saved():
    n = 25
    even = replay.REGULAR_ADDS / n
    assert replay.monday_price(("pace", 0.08, 1.0), 0, 0, n, [], None) == pytest.approx(0.08)  # on pace
    # Week 11 with 6 used: 24 left for 15 weeks (1.6 a week, more than even): a lower bar.
    assert replay.monday_price(("pace", 0.08, 1.0), 6, 10, n, [], None) == pytest.approx(0.08 * even / 1.6)
    assert replay.monday_price(("pace", 0.08, 2.0), 6, 10, n, [], None) == pytest.approx(0.08 * (even / 1.6) ** 2)
    # 20 used by week 11: 10 for 15 weeks, a higher bar; none left: no adds.
    assert replay.monday_price(("pace", 0.08, 1.0), 20, 10, n, [], None) > 0.08
    assert replay.monday_price(("pace", 0.08, 1.0), 30, 10, n, [], None) == math.inf


def test_the_live_price_logs_each_mondays_pool_once_and_solves_over_them():
    pool = {"moves": [[8.0, 20.0, 1, 7], [6.0, 10.0, 2, 8], [1.0, 2.0, 3, 9]], "sigma": 40.0, "tau": 18.5}
    picks = []
    first = replay.monday_price(("live",), 0, 0, 25, picks, {"pool": pool, "weight": 0.008})
    assert len(picks) == 1 and first > 0
    # The same price as addprice.solve on that pool at that pace (its own random draws aside).
    assert first == pytest.approx(replay.addprice.solve([pool], 30 / 25, 0.008), rel=0.15)
    for _ in range(replay.POOL_WEEKS + 3):
        replay.monday_price(("live",), 0, 0, 25, picks, {"pool": pool, "weight": 0.008})
    assert len(picks) == replay.POOL_WEEKS  # the newest weeks only


def test_the_audit_credits_a_move_while_it_stands_and_reports_by_keep_share():
    # Weeks from MON: 9 replaced 3 in week 0 and stayed two weeks, then left (week 2's roster has 2).
    mondays = [MON + dt.timedelta(days=7 * k) for k in range(3)]
    points = {(9, mondays[0]): 6.0, (3, mondays[0]): 2.0, (9, mondays[1]): 1.0, (3, mondays[1]): 4.0,
              (1, mondays[0]): 5.0}
    s = _season({1: 5.0, 2: 3.0, 3: 2.0, 9: 4.0}, {1: "C", 2: "C", 3: "C", 9: "C"}, {}, points)
    s._proj.update({m: s._proj[MON] for m in mondays})
    rosters = [[1, 9], [1, 9], [1, 2]]
    scores = [replay.actual(s, r, m) for r, m in zip(rosters, mondays)]
    parts = {"value": 0.15, "week_pts": 3.0, "ahead_pts": [1.0, 1.0], "later_pts": 2.0, "rate": 1.0,
             "weeks_later": 10, "keep": 0.0, "later_weight": 0.01, "kind": "same position"}
    rec, = replay.realized(s, [(0, 9, 3, parts)], rosters, mondays, scores, [8.0, 4.5, 0.0])
    assert rec["held"] == 2 and rec["real"] == [4.0, -3.0]  # 6 for 2, then 1 for 4; gone in week 2
    # Week 0: 11 vs 7 against 8, a win the swap made; week 1: 1 vs 4 against 4.5, lost either way.
    assert rec["real_wins"] == 1 and rec["weeks_left"] == 3
    report = replay.audit_report("test", [rec, {**rec, "keep": 1.0, "held": 1, "real": [5.0]}])
    assert "2 moves" in report and "keep 0 (streamer)" in report and "keep 1 (keeper)" in report


def test_a_swaps_kind_says_what_it_does_to_the_rosters_shape():
    s = _season({}, {1: "C", 2: "D", 3: "LW", 4: "C"}, {}, goalies=(10,))
    assert replay.swap_kind(s, 1, 2) == "forward for a D" and replay.swap_kind(s, 2, 1) == "D for a forward"
    assert replay.swap_kind(s, 1, 4) == "same position" and replay.swap_kind(s, 3, 1) == "forward, other position"
    assert replay.swap_kind(s, 10, 1) == "goalie"


def test_the_audit_records_a_moves_parts_as_valued_and_its_six_week_rate():
    from league.roster import RosterPlayer
    s = _season({}, {1: "C", 3: "D"}, {"A": set(), "B": set()})
    s.team = lambda pid, m: {1: "A", 3: "B"}[pid]
    future = [MON + dt.timedelta(days=7 + i) for i in range(42)]
    s.team_days["A"] = set(future[::2])  # 21 games in the 6 weeks
    s.team_days["B"] = set(future[::3])  # 14
    gain = {future[0]: 6.0, future[7]: 3.0, future[20]: 3.0}  # week +1, week +2, week +3
    ahead = [replay.matchup.WeekAhead(k, frozenset(future[7 * (k - 1):7 * k]), None, 40.0) for k in (1, 2)]
    add, drop = RosterPlayer(1, "1", "A", ["C"]), RosterPlayer(3, "3", "B", ["D"])
    rec = replay.predicted_parts(s, MON, add, drop, 0.0, gain, 2.0, 0.02, future, ahead, 20, 0.01, 21, 0.2)
    g1, g2 = replay.matchup.GAP_REALIZES[1], replay.matchup.GAP_REALIZES[2]
    assert rec["ahead_pts"] == pytest.approx([6.0 * g1, 3.0 * g2])
    assert rec["later_pts"] == pytest.approx(3.0)  # a streamer: the held week +3 only
    assert rec["rate"] == pytest.approx(12.0 / 6) and rec["weeks_later"] == 18
    assert rec["kind"] == "forward for a D" and rec["edge"] == pytest.approx((21 - 14) / 6)


def test_tenure_counts_each_later_day_by_the_odds_the_add_still_stands_and_how_gaps_realize():
    future = [MON + dt.timedelta(days=7 + i) for i in range(35)]
    gain = {future[0]: 10.0, future[7]: 10.0, future[14]: 10.0, future[28]: 10.0}  # weeks +1, +2, +3, +5
    ahead = [replay.matchup.WeekAhead(k, frozenset(future[7 * (k - 1):7 * k]), None, 40.0) for k in (1, 2)]
    table = {"b": (0.8, 0.5, 0.4, 0.3, 0.2)}
    wins, pts, later = replay.tenure_after(gain, MON, future, ahead, 0.01, "b", table)
    g1, g2 = replay.matchup.GAP_REALIZES[1], replay.matchup.GAP_REALIZES[2]
    assert pts == pytest.approx((10 * 0.8 * g1, 10 * 0.5 * g2))
    assert wins == pytest.approx((0.01 * 10 * 0.8 * g1, 0.01 * 10 * 0.5 * g2))  # no margin: at later_weight
    assert later == pytest.approx(10 * 0.4 * 0.84 + 10 * 0.2 * 0.84)


def test_the_seasons_last_week_has_no_rate_and_no_later_points():
    from league.roster import RosterPlayer
    s = _season({}, {1: "C", 3: "D"}, {"A": set(), "B": set()})
    s.team = lambda pid, m: {1: "A", 3: "B"}[pid]
    add, drop = RosterPlayer(1, "1", "A", ["C"]), RosterPlayer(3, "3", "B", ["D"])
    rec = replay.predicted_parts(s, MON, add, drop, 1.0, {}, 2.0, 0.02, [], [], 0, 0.01, 0, 0.1)
    assert rec["rate"] == 0.0 and rec["edge"] == 0.0 and rec["later_pts"] == 0.0


def test_the_audit_reports_a_tenure_variant_without_keep_shares():
    rec = {"value": 0.15, "week_pts": 3.0, "ahead_pts": [1.0, 1.0], "later_pts": 2.0, "rate": 1.0, "weeks_later": 10,
           "keep": None, "later_weight": 0.01, "kind": "same position", "bin": "skater, lowest", "week": 0,
           "weeks_left": 5, "held": 2, "real": [4.0, -3.0], "real_wins": 1}
    report = replay.audit_report("tenure", [rec, {**rec, "bin": "goalie", "held": 5, "real": [1.0] * 5}])
    assert "By keep share" not in report and "skater, lowest" in report and "goalie" in report


def test_a_two_week_stream_can_count_the_weeks_ahead_in_full():
    future = [MON + dt.timedelta(days=7 + i) for i in range(35)]
    ahead = [replay.matchup.WeekAhead(k, frozenset(future[7 * (k - 1):7 * k]), None, 40.0) for k in (1, 2)]
    table = {"b": (0.8, 0.5, 0.4, 0.3, 0.2)}
    g1, g2 = replay.matchup.GAP_REALIZES[1], replay.matchup.GAP_REALIZES[2]
    stream = {future[0]: 10.0, future[7]: 10.0, future[21]: 1.0}  # big in the weeks ahead, little after
    keeper = {future[0]: 1.0, future[7]: 1.0, future[21]: 10.0}  # the other way round
    assert replay.tenure_after(stream, MON, future, ahead, 0.01, "b", table)[1] == pytest.approx((8 * g1, 5 * g2))
    assert replay.tenure_after(stream, MON, future, ahead, 0.01, "b", table, "full")[1] == pytest.approx((10 * g1, 10 * g2))
    assert replay.tenure_after(stream, MON, future, ahead, 0.01, "b", table, "stream")[1] == pytest.approx((10 * g1, 10 * g2))
    assert replay.tenure_after(keeper, MON, future, ahead, 0.01, "b", table, "stream")[1] == pytest.approx((0.8 * g1, 0.5 * g2))
    # Later days are tenure's in every mode.
    assert replay.tenure_after(stream, MON, future, ahead, 0.01, "b", table, "full")[2] == pytest.approx(1.0 * 0.3 * 0.84)


def test_a_midweek_move_scores_each_night_with_that_nights_roster():
    days = [MON + dt.timedelta(days=i) for i in range(7)]
    points = {(1, days[0]): 6.0, (2, days[0]): 2.0, (9, days[4]): 5.0, (2, days[4]): 3.0}
    s = _season({1: 5.0, 2: 3.0, 9: 4.0}, {1: "C", 2: "C", 9: "C"}, {}, points)
    # 2 for 9 on Thursday: Monday counts 1 and 2; Friday 9 instead of 2.
    assert replay.actual(s, [1, 2], MON, (days[3], [1, 9])) == 6.0 + 2.0 + 5.0
    assert replay.actual(s, [1, 2], MON) == 6.0 + 2.0 + 3.0
    assert replay.actual(s, [1, 2], MON, days=3, minimum=False) == 8.0  # banked by Thursday
    # The audit: 9 added Thursday counts from Friday, with the swap undone that night only.
    mondays = [MON, MON + dt.timedelta(days=7)]
    week = ([1, 2], (days[3], [1, 9]))
    scores = [replay.actual(s, [1, 2], MON, (days[3], [1, 9])), 0.0]
    rec, = replay.realized(s, [(0, 9, 2, {})], [week, [1, 2]], mondays, scores, [0.0, 0.0])
    assert rec["real"] == [5.0 - 3.0] and rec["held"] == 1  # gone the next week


def test_goalie_weeks_stay_fantasy_weeks_when_projected_from_midweek():
    days = [MON + dt.timedelta(days=i) for i in range(14)]
    s = _season({}, {}, {"C": set(days)}, goalies=(10, 11))
    s.team = lambda pid, m: "C"
    thursday = days[3]
    s._proj[thursday] = s._proj[MON]
    week = replay.Week(s, [10, 11], thursday, days[3:], MON)
    weeks = week.goalie_weeks([10, 11], days[3:])
    assert sorted(weeks) == [0, 1] and len(weeks[0][0]) == 4 and len(weeks[1][0]) == 7


def test_moves_on_several_days_score_each_night_with_that_nights_roster():
    days = [MON + dt.timedelta(days=i) for i in range(7)]
    points = {(2, days[0]): 2.0, (2, days[4]): 3.0, (9, days[4]): 5.0, (8, days[6]): 7.0, (9, days[6]): 1.0}
    s = _season({1: 5.0, 2: 3.0, 9: 4.0, 8: 4.5}, {1: "C", 2: "C", 9: "C", 8: "C"}, {}, points)
    # 2 for 9 on Thursday, then 9 for 8 on Sunday: Monday 2, Friday 9, Sunday 8.
    changes = [(days[3], [1, 9]), (days[6], [1, 8])]
    assert replay.actual(s, [1, 2], MON, changes) == 2.0 + 5.0 + 7.0
    assert replay.actual(s, [1, 2], MON, changes, days=6, minimum=False) == 2.0 + 5.0  # banked by Sunday
    assert replay.changes_of(None) == [] and replay.changes_of(changes[0]) == [changes[0]]
    # The audit: 9 stood from Thursday to Saturday; undone, 2 plays Friday instead.
    mondays = [MON, MON + dt.timedelta(days=7)]
    scores = [replay.actual(s, [1, 2], MON, changes), 0.0]
    rec = replay.realized(s, [(0, 9, 2, {})], [([1, 2], changes), [1, 8]], mondays, scores, [0.0, 0.0])[0]
    assert rec["real"] == [5.0 - 3.0] and rec["held"] == 1


def test_shrink_variants_read_the_margin_by_the_days_left():
    assert replay.REALIZES_BY_DAYS_LEFT[7] == replay.matchup.MARGIN_REALIZES
    assert all(0.0 < replay.REALIZES_BY_DAYS_LEFT[7 - d] <= 1.0 for d in replay.DECISION_DAYS["tenure daily shrink"])
    assert replay.DECISION_DAYS["tenure daily"] == replay.DECISION_DAYS["tenure daily shrink"] == (1, 2, 3, 4, 5, 6)
    assert replay.SHRINK <= set(replay.VARIANTS) and replay.MIDWEEK <= replay.WHOLE_SCHEDULE
