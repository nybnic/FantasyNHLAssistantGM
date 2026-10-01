import datetime as dt
from dataclasses import dataclass

import pytest

from clients.nhl_client import ScheduledGame
from engine import matchup
from engine.addprice import AddPrice
from league.roster import RosterPlayer

UTC = dt.timezone.utc
MON, TUE = dt.date(2026, 11, 9), dt.date(2026, 11, 10)


WED = dt.date(2026, 11, 11)


def _game(date, home, away):
    return ScheduledGame(hash((date, home)), dt.datetime.combine(date, dt.time(23, 0), UTC), home, away)


@dataclass
class _Proj:
    xfp: float


class FakeContext:
    today = MON
    team_starts: dict = {}
    skater_games: dict = {}
    goalie_games: dict = {}
    xfp = {1: 4.0, 2: 2.0, 9: 3.0, 11: 4.0}

    def skater(self, pid, position):
        return _Proj(self.xfp.get(pid, 1.0))

    def goalie_start(self, pid, team, opp, home):
        return {"xfp": 9.0}

    def prior_start_share(self, pid):
        return 0.9

    def durability(self, pid):
        return 1.0

    def games_missed(self, pid, team):
        return 0


def test_at_least_counts_goalie_games_already_played():
    assert matchup._at_least([0.5, 0.5], 0, 3) == 0
    assert matchup._at_least([0.5, 0.5], 1, 3) == pytest.approx(0.25)
    assert matchup._at_least([], 3, 3) == 1


def test_win_probability_is_even_for_equal_teams_and_rises_with_the_lead():
    team = matchup.TeamWeek("a", 0, 150, 400, 30, 0, 4, 1.0)
    ahead = matchup.TeamWeek("b", 0, 180, 400, 30, 0, 4, 1.0)
    assert matchup.win_prob(team, team) == pytest.approx(0.5)
    assert 0.8 < matchup.win_prob(ahead, team) < 0.95


def test_project_uses_each_days_best_lineup_and_the_goalie_minimum():
    roster = [RosterPlayer(1, "Skater", "BOS", ["C"], "C"), RosterPlayer(8, "Goalie", "TOR", ["G"], "G")]
    schedule = {MON: [_game(MON, "BOS", "TOR")], TUE: [_game(TUE, "TOR", "NJD")]}
    week = matchup.project("me", roster, FakeContext(), schedule, {}, {})
    assert week.player_games == 3
    # One skater game at 0.97 x 4; the goalie can't reach 3 games, so his points don't count.
    assert week.goalie_min_prob == 0
    assert week.expected == pytest.approx(0.97 * 4.0)


def _price(lam, later_weight=0.0):
    return AddPrice(lam=lam, later_weight=later_weight, pace=1.3)


def test_best_move_fills_an_open_spot_with_the_free_agent_who_plays():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "NJD", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "TOR")], TUE: [_game(TUE, "NYR", "PHI")]}
    pool = [RosterPlayer(9, "Streamer", "NYR", ["C"]), RosterPlayer(10, "Idle", "SEA", ["C"])]
    later = {}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=0,
                               max_moves=2, price=_price(0.01))
    assert [(m.add.id, m.drop) for m in moves] == [(9, None)]
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)
    assert moves[0].win_after > moves[0].win_before


def test_adds_used_counts_the_ledger_by_week():
    adds = [{"id": 1, "date": "2026-11-09"}, {"id": 2, "date": "2026-11-02"}]
    assert matchup.adds_used(adds, [MON, TUE]) == (2, 1)
    assert matchup.max_moves(2, 1) == 1 and matchup.max_moves(36, 0) == 0


def test_an_empty_starting_slot_is_filled_before_adding_bench_depth():
    # The D slots are empty; a lesser defenseman beats a better forward who'd sit.
    roster = [RosterPlayer(1, "C1", "BOS", ["C"], "C"), RosterPlayer(11, "C2", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "NYR")]}
    pool = [RosterPlayer(9, "Forward", "NYR", ["C"]), RosterPlayer(2, "Defenseman", "NYR", ["D"])]
    later = {WED: [_game(WED, "BOS", "NYR")]}  # everyone plays: the forward would sit
    opponent = matchup.TeamWeek("them", 0, 8.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=10,
                               max_moves=1, price=_price(0.01))
    assert [m.add.id for m in moves] == [2]


def test_never_drops_below_three_goalies_for_a_skater():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")] + [
        RosterPlayer(20 + i, f"G{i}", "SEA", ["G"], "BN") for i in range(3)]
    roster += [RosterPlayer(30 + i, f"F{i}", "BOS", ["C"], "BN") for i in range(10)]
    schedule = {MON: [_game(MON, "NYR", "PHI")]}
    pool = [RosterPlayer(9, "Streamer", "NYR", ["C"])]
    later = {}
    opponent = matchup.TeamWeek("them", 0, 1.0, 10.0, 2, 3, 0, 1.0)  # a close week, so a streamer is worth it
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=0,
                               max_moves=1, price=_price(0.001))
    assert moves and not moves[0].drop.is_goalie


def test_a_waiver_claim_only_counts_from_the_day_it_clears():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "NYR", "BOS")]}
    pool = [RosterPlayer(9, "Claim", "NYR", ["C"])]
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future={}, weeks_after=0,
                               max_moves=1, price=_price(0.001), available_from=TUE)
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)  # Tuesday's game only


def test_a_move_is_worth_an_add_when_its_win_probability_now_and_later_beats_the_price():
    fa = RosterPlayer(9, "FA", "NYR", ["C"])
    close = matchup.Move(fa, None, 5.0, 0.0, 0.0, 3, 0.50, 0.56)  # +6 win-pts this week
    lopsided = matchup.Move(fa, None, 5.0, 0.0, 0.0, 3, 0.05, 0.06)  # the same points in a lost week: +1
    keeper = matchup.Move(fa, None, 0.0, 10.0, 8.0, 3, 0.05, 0.05, later_weight=0.008)  # +8 later
    price = _price(0.04)
    assert matchup.rejection(close, price) is None
    assert matchup.rejection(lopsided, price) == "worth 1.0 win-pts, under the 4.0 an add costs"
    assert matchup.rejection(keeper, price) is None


def test_a_hopeless_week_gets_no_streamer_and_says_so():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "NJD", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "TOR")], TUE: [_game(TUE, "NYR", "PHI")]}
    pool = [RosterPlayer(9, "Streamer", "NYR", ["C"])]
    opponent = matchup.TeamWeek("them", 0, 60.0, 10.0, 20, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future={}, weeks_after=0,
                               max_moves=2, price=_price(0.02))
    assert moves == []
    me = matchup.project("me", roster, FakeContext(), schedule, {}, {})
    assert "looks lost" in matchup.text(1, [MON, TUE], me, opponent, None, 0, 0, MON)


@dataclass
class _Log:
    date: dt.date
    stats: dict
    started: bool = True


def test_an_added_players_earlier_games_dont_count_for_you():
    # The free agent had a big Monday; on Tuesday his points can't come with him.
    ctx = FakeContext()
    ctx.today = TUE
    ctx.skater_games = {9: [_Log(MON, {"g": 3})]}
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "NYR", "BOS")]}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.candidate_moves(roster, opponent, [RosterPlayer(9, "Hot", "NYR", ["C"])], ctx, schedule,
                                    {}, {}, future={}, weeks_after=0)
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)  # Tuesday's game only


def _team(expected):
    return matchup.TeamWeek("t", 10.0, expected, 400.0, 20, 2, 2, 1.0)


def test_midweek_ahead_protects_the_lead():
    text = matchup.midweek_text(_team(160), _team(150), None, 3.0, recommended=False)
    assert "protect the lead" in text and "10 expected points up" in text


def test_midweek_close_behind_chases_and_names_the_biggest_swing():
    chase = matchup.Move(RosterPlayer(9, "Streamer", "NYR", ["C"]), RosterPlayer(2, "Depth", "NJD", ["C"]),
                         week_gain=2.0, long_term=-3.0, next_weeks=-1.0, games=3, win_before=0.42, win_after=0.47)
    text = matchup.midweek_text(_team(150), _team(155), chase, _price(0.08), recommended=False)
    assert "so chase: you trail by 5" in text
    assert "add Streamer (3 games left) for Depth, win 42% -> 47%" in text
    assert ("Not a recommended add (dropping Depth costs about 3 pts later, more than this week's +5 win-pts "
            "make up for)") in text
    assert "your call" in text
    assert "That's the add below" in matchup.midweek_text(_team(150), _team(155), chase, _price(0.08),
                                                          recommended=True)


def test_midweek_hopeless_leaves_it_to_the_lost_week_line():
    assert matchup.stance(0.04) == "lost" and matchup.stance(0.3) == "chase" and matchup.stance(0.7) == "protect"
    assert matchup.midweek_text(_team(100), _team(160), None, 3.0, recommended=False) is None
    assert "looks lost" in matchup.text(1, [MON, TUE], _team(100), _team(160), None, 0, 0, MON)


def test_a_streamer_is_shortlisted_for_games_on_nights_his_slot_is_open():
    # Neither D plays Monday in the biggest numbers, but only the one on the open night fits.
    pool = [RosterPlayer(40 + i, f"Busy {i}", "BOS", ["D"]) for i in range(10)] + [RosterPlayer(60, "Fit", "SEA", ["D"])]
    schedule = {MON: [_game(MON, "BOS", "TOR")], TUE: [_game(TUE, "SEA", "NJD")]}
    open_days = {MON: {"C"}, TUE: {"D"}}
    picked = matchup.shortlist(pool, FakeContext(), schedule, {}, {}, open_days=open_days)
    assert 60 in [p.id for p in picked]
    assert 60 not in [p.id for p in matchup.shortlist(pool, FakeContext(), schedule, {}, {})]


def test_the_best_streamer_per_position_counts_only_points_that_reach_the_lineup():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "NJD", ["D"], "D")]
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "SEA", "BOS")]}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    pool = [RosterPlayer(9, "Wing", "NYR", ["LW"]), RosterPlayer(11, "Dman", "SEA", ["D"])]
    ranked = matchup.candidate_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future={}, weeks_after=0)
    found = matchup.streamers(roster, ranked, FakeContext(), schedule, {}, {}, {})
    assert {s["position"]: s["move"].add.id for s in found} == {"LW": 9, "D": 11}
    assert all(s["move"].drop is None for s in found)  # open roster spots first


def test_drops_are_tried_per_group_so_a_weak_forward_is_considered():
    ctx = FakeContext()
    ctx.xfp = {1: 2.0, 2: 1.0, 3: 1.1, 4: 1.2, 5: 1.3, 6: 3.0}
    mine = [RosterPlayer(1, "Weak F", "BOS", ["C"], "C"), RosterPlayer(6, "Good F", "BOS", ["C"], "C")] + [
        RosterPlayer(i, f"D{i}", "BOS", ["D"], "D") for i in (2, 3, 4, 5)]
    # The two weakest D, then both forwards: per group, so the forwards are tried at all.
    assert [p.id for p in matchup.drop_candidates(mine, ctx, {})] == [2, 3, 1, 6]


def test_streaming_spots_are_the_skaters_closest_to_waiver_level_at_their_position():
    ctx = FakeContext()
    ctx.xfp = {1: 5.0, 2: 2.0, 3: 2.6, 9: 4.6, 11: 1.9}  # 9: best free C, 11: best free D
    mine = [RosterPlayer(1, "Good C", "BOS", ["C"]), RosterPlayer(2, "Low D", "BOS", ["D"]),
            RosterPlayer(3, "Mid D", "BOS", ["D"])]
    free = [RosterPlayer(9, "FA C", "NYR", ["C"]), RosterPlayer(11, "FA D", "NYR", ["D"])]
    # The C scores most per game but sits only 0.4 above his waiver level; Mid D sits 0.7 above.
    assert matchup.streaming_spots(mine, free, ctx, {}) == {1, 2, 3}
    goalies = [RosterPlayer(20, "G1", "BOS", ["G"]), RosterPlayer(21, "G2", "BOS", ["G"])]
    ctx.prior_start_share = lambda pid: 0.3 if pid == 21 else 0.6
    assert matchup.streaming_spots(mine + goalies, free, ctx, {}) == {1, 2, 3, 21}  # plus the weakest goalie
    matchup_spots = matchup.STREAMING_SPOTS
    try:
        matchup.STREAMING_SPOTS = 2
        assert matchup.streaming_spots(mine, free, ctx, {}) == {1, 2}
    finally:
        matchup.STREAMING_SPOTS = matchup_spots


def test_dropping_a_streaming_spot_costs_only_the_next_two_weeks(monkeypatch):
    # The free agent plays the next 2 weeks, then his team is idle for 4: a
    # streamer. Dropped for a streaming spot, the idle weeks don't count.
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "SEA", ["C"], "C")]
    days = [WED + dt.timedelta(days=i) for i in range(42)]
    future = {d: [_game(d, "NYR" if i < 14 else "BOS", "SEA")] for i, d in enumerate(days)}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    fa = [RosterPlayer(9, "Streamer", "NYR", ["C"])]
    monkeypatch.setattr(matchup, "STREAMING_SPOTS", 1)
    ctx = FakeContext()
    ctx.xfp = {1: 4.0, 2: 2.0, 9: 3.0}
    move = next(m for m in matchup.candidate_moves(roster, opponent, fa, ctx, {MON: []}, {}, {}, future, weeks_after=20)
                if m.drop and m.drop.id == 2)
    assert move.next_weeks > 0 and move.long_term == pytest.approx(move.next_weeks)
    monkeypatch.setattr(matchup, "STREAMING_SPOTS", 0)  # Depth is core now: the season view
    core = next(m for m in matchup.candidate_moves(roster, opponent, fa, ctx, {MON: []}, {}, {}, future, weeks_after=20)
                if m.drop and m.drop.id == 2)
    assert core.long_term < 0


def test_this_weeks_last_add_goes_to_a_move_that_pays_now_and_the_keeper_waits():
    murashov, schenn = RosterPlayer(5, "Sergei Murashov", "PIT", ["G"]), RosterPlayer(6, "Brayden Schenn", "NYI", ["C"])
    keeper = matchup.Move(RosterPlayer(1, "Arturs Silovs", "PIT", ["G"]), murashov, week_gain=0.0, long_term=48.0,
                          next_weeks=9.0, games=1, win_before=0.48, win_after=0.48, later_weight=0.003)
    streamer = matchup.Move(RosterPlayer(2, "Jack McBain", "UTA", ["C", "LW"]), schenn, week_gain=10.0,
                            long_term=-4.0, next_weeks=-4.6, games=3, win_before=0.48, win_after=0.59,
                            later_weight=0.003)
    ranked = [keeper, streamer]  # by value: the keeper first (14.4 win-pts against 9.8)
    price = _price(0.05, 0.003)
    moves = matchup.best_moves([], None, [], FakeContext(), {}, {}, {}, {}, 20, max_moves=1, price=price,
                               so_far=(0.0, 0.0, 0), candidates=[], ranked=ranked)
    assert moves == [streamer]
    assert matchup.can_wait(ranked, moves, price) is keeper
    assert matchup.why_not(keeper, price, moves) == "worth an add, but this week's go to Jack McBain"
    # With nothing else passing, the keeper takes the add now: no reason to wait.
    assert matchup.best_moves([], None, [], FakeContext(), {}, {}, {}, {}, 20, max_moves=1, price=price,
                              so_far=(0.0, 0.0, 0), candidates=[], ranked=[keeper]) == [keeper]


def test_a_failing_move_is_explained_by_its_long_run_cost_only_when_that_is_why():
    schenn = RosterPlayer(6, "Brayden Schenn", "NYI", ["C"])
    costly = matchup.Move(RosterPlayer(2, "Jack McBain", "UTA", ["C"]), schenn, week_gain=2.0, long_term=-24.0,
                          next_weeks=-4.0, games=3, win_before=0.48, win_after=0.52, later_weight=0.003)
    assert matchup.why_not(costly, _price(0.03)) == (
        "dropping Brayden Schenn costs about 24 pts later, more than this week's +4 win-pts make up for")


def test_the_add_card_names_a_keeper_or_a_streamer_by_the_long_run_that_ranked_it():
    schenn = RosterPlayer(6, "Brayden Schenn", "NYI", ["C"])
    keeper = matchup.Move(RosterPlayer(2, "Vasily Podkolzin", "EDM", ["LW", "RW"]), schenn, week_gain=5.9,
                          long_term=42.6, next_weeks=-2.4, games=2, win_before=0.48, win_after=0.54)
    assert "a keeper: ahead of Brayden Schenn" in matchup.move_text(keeper) and "streamer" not in matchup.move_text(keeper)
    streamer = matchup.Move(RosterPlayer(3, "Jack McBain", "UTA", ["C"]), schenn, week_gain=10.0,
                            long_term=-4.0, next_weeks=-4.0, games=3, win_before=0.48, win_after=0.59)
    assert "a streamer" in matchup.move_text(streamer)


def test_a_streamer_is_held_as_long_as_the_add_pace_takes_to_cycle_the_spots():
    # 35 adds left in week 1: 29 for 23 regular weeks, ~1.26 a week, over 4 spots (3 skaters, a goalie).
    assert matchup.hold_weeks(35, 1) == pytest.approx(4 / (29 / 23))
    assert matchup.hold_weeks(13, 20) == pytest.approx(4 / 1.75)  # spending is due: shorter holds
    assert matchup.hold_weeks(7, 20) == pytest.approx(4 / 0.5)  # nearly out: floor of 0.5 adds a week


def test_a_streaming_spot_swap_is_credited_only_while_he_would_be_held(monkeypatch):
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "SEA", ["C"], "C")]
    days = [WED + dt.timedelta(days=i) for i in range(42)]
    future = {d: [_game(d, "NYR", "SEA")] for d in days}  # the streamer plays every day
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    monkeypatch.setattr(matchup, "STREAMING_SPOTS", 1)
    ctx = FakeContext()
    ctx.xfp = {1: 4.0, 2: 2.0, 9: 3.0}

    def credit(hold_days):
        moves = matchup.candidate_moves(roster, opponent, [RosterPlayer(9, "Streamer", "NYR", ["C"])], ctx,
                                        {MON: []}, {}, {}, future, weeks_after=20, hold_days=hold_days)
        return next(m for m in moves if m.drop and m.drop.id == 2).long_term

    assert credit(7) == pytest.approx(credit(14) / 2) and credit(7) > 0


def test_a_player_out_tonight_counts_later_in_the_week():
    from clients.dfo_lines import LineInfo
    from engine import availability
    roster = [RosterPlayer(1, "Skater", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "TOR")], WED: [_game(WED, "BOS", "NJD")]}
    lines = {"BOS": {"skater": LineInfo(groups={"f1"}, injury="out")}}
    week = matchup.project("me", roster, FakeContext(), schedule, lines, {})
    assert week.expected == pytest.approx(availability.return_curve(1)[0] * 4.0)  # out Monday, 32% Wednesday


def test_a_player_out_tonight_is_not_free_to_drop_in_the_long_run():
    from clients.dfo_lines import LineInfo
    roster = [RosterPlayer(1, "Hurt Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "NJD", ["C"], "C")]
    future = {MON + dt.timedelta(days=d): [_game(MON + dt.timedelta(days=d), "BOS", "NJD")] for d in range(7, 49, 2)}
    lines = {"BOS": {"hurt star": LineInfo(groups={"f1"}, injury="out")}}
    with_him = matchup.project("me", roster, FakeContext(), future, lines, {}, long_run=True)
    without = matchup.project("me", roster[1:], FakeContext(), future, lines, {}, long_run=True)
    # Out tonight used to mean zero for all six weeks, so dropping him cost nothing later.
    assert with_him.expected - without.expected > 0.5 * 4.0 * len(future)
