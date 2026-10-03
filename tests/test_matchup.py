import datetime as dt
from dataclasses import dataclass

import pytest

from clients.nhl_client import ScheduledGame
from engine import availability, matchup
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
    last_results: dict = {}
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
                               max_moves=1, price=_price(0.001), available_from={9: TUE})
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)  # Tuesday's game only
    assert moves[0].plays_from == TUE
    assert "on waivers: claim him, he plays from Tue 10 Nov" in matchup.move_text(moves[0])


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


def test_midweek_ahead_with_no_adds_left_doesnt_point_to_adds():
    text = matchup.midweek_text(_team(160), _team(150), None, None, recommended=False)
    assert text.endswith("No need to chase.") and "adds below" not in text


def test_midweek_behind_with_no_adds_left_doesnt_say_chase():
    text = matchup.midweek_text(_team(150), _team(155), None, None, recommended=False)
    assert "so chase" not in text and text.endswith("with no adds left to chase with.")


def test_midweek_even_week_is_called_even_not_ahead():
    # 173 - 173 read "ahead (50%), protect the lead: you're 0 expected points up".
    assert matchup.stance(0.505) == "even" and matchup.stance(0.495) == "even"
    text = matchup.midweek_text(_team(173.2), _team(173), None, None, recommended=False)
    assert text == "Rest of the week: dead even (50%), every point counts."
    chase = matchup.Move(RosterPlayer(9, "Streamer", "NYR", ["C"]), None, week_gain=4.0, long_term=0.0,
                         next_weeks=0.0, games=3, win_before=0.50, win_after=0.56)
    assert "Biggest swing: add Streamer" in matchup.midweek_text(_team(173), _team(173), chase, _price(0.08),
                                                                recommended=False)


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


def test_a_streamer_who_joins_next_week_gets_nothing_this_week():
    # This week's adds are spent: he plays from Monday, so only next week's game counts.
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "NYR", "BOS")]}
    next_mon = MON + dt.timedelta(days=7)
    next_schedule = {next_mon: [_game(next_mon, "NYR", "PHI")]}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    pool = [RosterPlayer(9, "Wing", "NYR", ["LW"])]
    joins = {9: next_mon}
    ranked = matchup.candidate_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future={},
                                     weeks_after=0, available_from=joins)
    assert ranked[0].week_gain == 0 and ranked[0].win_after == ranked[0].win_before
    [found] = matchup.streamers(roster, ranked, FakeContext(), schedule, next_schedule, {}, {},
                                available_from=joins)
    assert not any(9 in day for day in found["this_week"].lineups.values())
    assert 9 in found["next_week"].lineups[next_mon] and found["next_gain"] > 0


def test_a_deferred_adds_drop_keeps_playing_until_he_joins():
    # A claim from Tuesday: the drop plays Monday, the add from Tuesday, never both.
    star, wing = RosterPlayer(1, "Star", "NYR", ["LW"], "LW"), RosterPlayer(9, "Wing", "NYR", ["LW"])
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "NYR", "BOS")]}
    trial, joins, leaves = matchup._deferred([star], wing, star, TUE)
    week = matchup.project("me", trial, FakeContext(), schedule, {}, {}, joins=joins, so_far=(0, 0, 0), leaves=leaves)
    assert set(week.lineups[MON]) == {1} and set(week.lineups[TUE]) == {9}
    assert matchup._deferred([star], wing, star, None)[1:] == (None, None)


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
    assert matchup.streaming_spots(mine + goalies, free, ctx, {}) == {1, 2, 3}  # two goalies: both core
    third = RosterPlayer(22, "G3", "BOS", ["G"])
    assert matchup.streaming_spots(mine + goalies + [third], free, ctx, {}) == {1, 2, 3, 21}  # the weakest of three
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


def test_banked_points_come_from_each_days_roster():
    ctx = FakeContext()
    ctx.today = WED
    ctx.skater_games = {5: [_Log(MON, {"g": 1})], 9: [_Log(MON, {"g": 3}), _Log(TUE, {"a": 1})]}
    now = [RosterPlayer(9, "Added Tue", "NYR", ["C"], "C")]  # and 5 was dropped Tuesday
    history = {MON: [RosterPlayer(5, "Dropped", "BOS", ["C"], "C")], TUE: now}
    skaters, _, _ = matchup._so_far(now, ctx, [MON, TUE, WED], history)
    assert skaters == pytest.approx(4.0 + 2.75)  # his Monday goal, then the new player's Tuesday assist


def _goalie(pid, team):
    return RosterPlayer(pid, f"G{pid}", team, ["G"], "G")


def test_the_long_run_checks_the_goalie_minimum_week_by_week():
    ctx = FakeContext()
    ctx.prior_start_share = lambda pid: 1.0  # certain starters, to keep the arithmetic plain
    goalies = [_goalie(20, "BOS"), _goalie(21, "TOR")]
    days = {MON + dt.timedelta(days=d): [_game(MON + dt.timedelta(days=d), "BOS", "TOR")] for d in (7, 8)}  # wk A
    days[MON + dt.timedelta(days=14)] = [_game(MON + dt.timedelta(days=14), "BOS", "NJD")]  # week B: one start
    week = matchup.project("me", goalies, ctx, days, {}, {}, long_run=True)
    # Week A: 4 starts, its 4 x 9 points count (with each goalie's odds of being there); week B: 1 start, zeroed.
    assert week.goalie_min_prob == 0.0  # the riskiest week
    one_block = matchup._at_least([1.0] * 5, 0, 3)  # what treating the span as one week would have said
    assert one_block == 1.0 and week.expected < 4 * 9.0


def test_a_third_goalie_is_insurance_in_the_long_run_only():
    ctx = FakeContext()
    ctx.prior_start_share = lambda pid: 1.0
    two = [_goalie(20, "BOS"), _goalie(21, "TOR")]
    three = two + [_goalie(22, "NJD")]
    later = {MON + dt.timedelta(days=d): [_game(MON + dt.timedelta(days=d), "BOS", "TOR"),
                                          _game(MON + dt.timedelta(days=d), "NJD", "PHI")] for d in (14, 16)}
    this_week = {MON: later[MON + dt.timedelta(days=14)], WED: later[MON + dt.timedelta(days=16)]}
    # This week everyone is there: the third goalie sits behind the two starters, worth nothing.
    assert matchup.project("me", three, ctx, this_week, {}, {}).expected == \
        pytest.approx(matchup.project("me", two, ctx, this_week, {}, {}).expected)
    # Weeks on, either starter may be lost for the week (GOALIE_KEEP): then the third plays.
    gain = matchup.project("me", three, ctx, later, {}, {}, long_run=True).expected \
        - matchup.project("me", two, ctx, later, {}, {}, long_run=True).expected
    assert gain > 1.0


def test_a_lost_last_start_moves_the_teams_next_game_only():
    ctx = FakeContext()
    ctx.prior_start_share = lambda pid: 0.6
    ctx.team_starts = {"BOS": [(MON - dt.timedelta(days=2), 20)]}
    ctx.last_results = {"BOS": "L bad"}
    goalie = _goalie(20, "BOS")
    schedule = {d: [_game(d, "BOS", "TOR")] for d in (MON, WED)}
    assert matchup._first_games(schedule, MON) == {"BOS": MON, "TOR": MON}
    assert matchup._first_games(schedule, MON - dt.timedelta(days=7)) == {}  # a later week holds no next game
    share = (4 * 0.6 + 1) / 5

    def start_prob(date, next_game):
        return matchup._player_day(goalie, ctx, date, schedule[date][0], False, {}, {}, next_game=next_game)[2]
    assert start_prob(MON, True) == pytest.approx(availability.with_odds(share, 0.32))
    assert start_prob(WED, False) == pytest.approx(share)


def _full_roster(ir_taken: tuple[str, ...] = ()) -> list[RosterPlayer]:
    """14 active (no open spot): 10 skaters who fill every skater slot on BOS
    nights, 2 goalies, and 2 who play the other nights, 113 my weakest; plus
    whoever fills the IR slots."""
    positions = ["C", "C", "LW", "LW", "RW", "RW", "D", "D", "D", "D", "G", "G", "LW", "RW"]
    roster = [RosterPlayer(100 + i, f"Player {i}", "NYR" if i >= 12 else "BOS", [pos], "BN")
              for i, pos in enumerate(positions)]
    return roster + [RosterPlayer(200 + i, f"Hurt {i}", "BOS", ["C"], slot) for i, slot in enumerate(ir_taken)]


def _stash_setup(injury="ir"):
    from clients.dfo_lines import LineInfo
    ctx = FakeContext()
    ctx.xfp = {11: 4.0, 113: 0.5}  # the injured star, my weakest
    future = {MON + dt.timedelta(days=d): [_game(MON + dt.timedelta(days=d), *(("BOS", "PIT") if d % 2 else ("NYR", "SEA")))]
              for d in range(7, 49)}
    schedule = {MON: [_game(MON, "BOS", "PIT")]}
    lines = {"PIT": {"star": LineInfo(groups={"f1"}, injury=injury)}, "BOS": {}}
    star = RosterPlayer(11, "Star", "PIT", ["C"])
    opponent = matchup.TeamWeek("them", 0, 15.0, 30.0, 12, 3, 0, 1.0)
    return ctx, future, schedule, lines, star, opponent


def test_an_injured_free_agent_is_stashed_in_an_empty_ir_slot_and_the_drop_waits():
    ctx, future, schedule, lines, star, opponent = _stash_setup()
    moves = matchup.candidate_moves(_full_roster(), opponent, [star], ctx, schedule, lines, {}, future, 10,
                                    later_weight=0.01)
    stash = next(m for m in moves if m.ir_slot)
    assert stash.ir_slot == "IR" and stash.drop is None and stash.later_drop.id == 113
    assert stash.long_term > 0 and abs(stash.week_gain) < 0.5  # out this week, worth it later
    # The drop is charged only once he's likely back: the netted gain sits below
    # an extra player's, above an immediate swap's.
    swap = next(m for m in moves if not m.ir_slot and m.drop and m.drop.id == 113)
    trial = _full_roster() + [RosterPlayer(11, "Star", "PIT", ["C"], "BN")]
    extra = (matchup.project("me", trial, ctx, future, lines, {}, True).expected
             - matchup.project("me", _full_roster(), ctx, future, lines, {}, True).expected)
    assert swap.long_term < stash.long_term < matchup.LONG_RUN_DISCOUNT * extra / 6 * 10
    assert "Stash Star (PIT, C, injured): add him straight into your empty IR slot, no drop now" in \
        matchup.move_text(stash)
    assert "drop Player 13 then" in matchup.move_text(stash)


def test_no_stash_without_a_free_slot_that_takes_his_injury():
    ctx, future, schedule, lines, star, opponent = _stash_setup()
    full = matchup.candidate_moves(_full_roster(("IR", "IR+")), opponent, [star], ctx, schedule, lines, {}, future, 10)
    assert not any(m.ir_slot for m in full)
    ctx, future, schedule, lines, star, opponent = _stash_setup(injury="out")  # Yahoo's O: IR+ only
    only_ir = matchup.candidate_moves(_full_roster(("IR+",)), opponent, [star], ctx, schedule, lines, {}, future, 10)
    assert not any(m.ir_slot for m in only_ir)
    plus = matchup.candidate_moves(_full_roster(("IR",)), opponent, [star], ctx, schedule, lines, {}, future, 10)
    assert [m.ir_slot for m in plus if m.ir_slot] == ["IR+"]


def test_a_stash_fills_its_ir_slot_so_the_next_add_cant_use_it():
    ctx, future, schedule, lines, star, opponent = _stash_setup()
    other = RosterPlayer(12, "Also Hurt", "PIT", ["C"])
    lines["PIT"]["also hurt"] = lines["PIT"]["star"]
    ctx.xfp[12] = 3.5
    moves = matchup.best_moves(_full_roster(("IR+",)), opponent, [star, other], ctx, schedule, lines, {}, future, 10,
                               max_moves=2, price=_price(0.0, 0.01), candidates=[star, other])
    assert [m.ir_slot for m in moves].count("IR") <= 1


def test_injured_free_agents_are_shortlisted_as_stashes():
    ctx, future, schedule, lines, star, opponent = _stash_setup()
    pool = [star] + [RosterPlayer(300 + i, f"Healthy {i}", "BOS", ["C"]) for i in range(30)]
    assert matchup.stash_pool(pool, ctx, lines) == [star]
    assert star in matchup.shortlist(pool, ctx, schedule, lines, {})
