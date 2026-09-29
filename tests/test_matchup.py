import datetime as dt
from dataclasses import dataclass

import pytest

from clients.nhl_client import ScheduledGame
from engine import matchup
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


def test_add_threshold_paces_adds_and_keeps_a_playoff_reserve():
    even = matchup.add_threshold(36, 1)
    assert matchup.add_threshold(20, 1) > even > matchup.add_threshold(36, 20)
    assert matchup.add_threshold(6, 10) is None
    assert matchup.add_threshold(2, 24) == matchup.BASE_ADD_SCORE / 2


def test_best_move_fills_an_open_spot_with_the_free_agent_who_plays():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C"), RosterPlayer(2, "Depth", "NJD", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "TOR")], TUE: [_game(TUE, "NYR", "PHI")]}
    pool = [RosterPlayer(9, "Streamer", "NYR", ["C"]), RosterPlayer(10, "Idle", "SEA", ["C"])]
    later = {}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=0,
                               max_moves=2, threshold=1.0)
    assert [(m.add.id, m.drop) for m in moves] == [(9, None)]
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)
    assert moves[0].win_after > moves[0].win_before


def test_adds_used_counts_done_adds_by_week():
    decisions = [
        {"type": "add", "decision": "done", "date": "2026-11-09"},
        {"type": "add", "decision": "skip", "date": "2026-11-10"},
        {"type": "add", "decision": "done", "date": "2026-11-02"},
        {"type": "lineup", "decision": "done", "date": "2026-11-10"},
    ]
    assert matchup.adds_used(decisions, [MON, TUE]) == (2, 1)
    assert matchup.max_moves(2, 1) == 1 and matchup.max_moves(36, 0) == 0


def test_an_empty_starting_slot_is_filled_before_adding_bench_depth():
    # The D slots are empty; a lesser defenseman beats a better forward who'd sit.
    roster = [RosterPlayer(1, "C1", "BOS", ["C"], "C"), RosterPlayer(11, "C2", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "BOS", "NYR")]}
    pool = [RosterPlayer(9, "Forward", "NYR", ["C"]), RosterPlayer(2, "Defenseman", "NYR", ["D"])]
    later = {WED: [_game(WED, "BOS", "NYR")]}  # everyone plays: the forward would sit
    opponent = matchup.TeamWeek("them", 0, 8.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=10,
                               max_moves=1, threshold=1.0)
    assert [m.add.id for m in moves] == [2]


def test_never_drops_below_three_goalies_for_a_skater():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")] + [
        RosterPlayer(20 + i, f"G{i}", "SEA", ["G"], "BN") for i in range(3)]
    roster += [RosterPlayer(30 + i, f"F{i}", "BOS", ["C"], "BN") for i in range(10)]
    schedule = {MON: [_game(MON, "NYR", "PHI")]}
    pool = [RosterPlayer(9, "Streamer", "NYR", ["C"])]
    later = {}
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future=later, weeks_after=0,
                               max_moves=1, threshold=0.5)
    assert moves and not moves[0].drop.is_goalie


def test_a_waiver_claim_only_counts_from_the_day_it_clears():
    roster = [RosterPlayer(1, "Star", "BOS", ["C"], "C")]
    schedule = {MON: [_game(MON, "NYR", "PHI")], TUE: [_game(TUE, "NYR", "BOS")]}
    pool = [RosterPlayer(9, "Claim", "NYR", ["C"])]
    opponent = matchup.TeamWeek("them", 0, 5.0, 10.0, 2, 3, 0, 1.0)
    moves = matchup.best_moves(roster, opponent, pool, FakeContext(), schedule, {}, {}, future={}, weeks_after=0,
                               max_moves=1, threshold=0.5, available_from=TUE)
    assert moves[0].week_gain == pytest.approx(0.97 * 3.0)  # Tuesday's game only
