import datetime as dt

import pytest

from engine import matchup, report
from league.roster import RosterPlayer
from notify import charts

THU, FRI = dt.date(2026, 10, 1), dt.date(2026, 10, 2)


def _team(so_far, by_day, lineups=None, expected=None):
    return matchup.TeamWeek("t", so_far, expected or so_far + sum(by_day.values()), 400.0, 10, 1, 2, 1.0,
                            by_day=by_day, lineups=lineups or {})


def _move(pid, name, win_after, drop=None, games=3):
    return matchup.Move(RosterPlayer(pid, name, "NYR", ["C"]), drop, 2.0, 0.0, 0.0, games, 0.46, win_after)


def test_week_view_keeps_each_players_best_add_plus_the_recommended_and_runs_the_totals():
    murashov = RosterPlayer(5, "Sergei Murashov", "PIT", ["G"])
    best = _move(1, "Joonas Korpisalo", 0.55, murashov)
    worse = _move(1, "Joonas Korpisalo", 0.50)
    recommended = _move(2, "Arturs Silovs", 0.46, murashov, games=1)
    view = report.week_view(1, _team(13.4, {THU: 40.0, FRI: 20.0}), _team(51.5, {THU: 20.0, FRI: 30.0}),
                            [worse, best] + [_move(10 + i, f"F A{i}", 0.47) for i in range(6)], [recommended], top=2)
    assert [a["label"] for a in view["adds"]] == ["Korpisalo for Murashov", "A0 (open spot)", "Silovs for Murashov"]
    assert [a["recommended"] for a in view["adds"]] == [False, False, True]
    assert view["race"]["labels"] == ["So far", "Thu", "Fri"]
    assert view["race"]["me"] == pytest.approx([13.4, 53.4, 73.4])
    assert view["race"]["them"] == pytest.approx([51.5, 71.5, 101.5])


def test_schedule_view_marks_starts_benched_games_and_open_slots():
    roster = [RosterPlayer(1, "Mark Scheifele", "WPG", ["C"], "C"), RosterPlayer(2, "Neal Pionk", "WPG", ["D"], "D"),
              RosterPlayer(3, "Spencer Knight", "CHI", ["G"], "G"), RosterPlayer(4, "Hurt Guy", "BUF", ["D"], "IR+")]
    mine = _team(0, {THU: 5, FRI: 3}, {THU: {1: ("C", 1.0), 2: ("BN", 1.0), 3: ("G", 0.75)}, FRI: {2: ("D", 1.0)}})
    theirs = _team(0, {THU: 5, FRI: 3}, {THU: {9: ("C", 1.0)}})
    view = report.schedule_view(roster, [(1, "Bahelin Boys", mine, theirs)])
    assert [r["name"] for r in view["rows"]] == ["M. Scheifele", "N. Pionk", "S. Knight"]  # no IR
    assert [r["cells"] for r in view["rows"]] == [["start", None], ["bench", "start"], ["start", None]]
    assert view["rows"][2]["probs"] == [0.75, None] and view["rows"][0]["probs"] == [None, None]
    assert view["open"][0] == {"C": 1, "LW": 2, "RW": 2, "D": 4, "G": 1}
    assert view["my_games"] == [2, 1] and view["their_games"] == [1, 0]


def test_budget_paces_the_regular_season_then_the_playoff_reserve():
    decisions = [{"type": "add", "decision": "done", "date": "2026-10-01"},
                 {"type": "add", "decision": "skip", "date": "2026-10-01"},
                 {"type": "add", "decision": "done", "date": "2026-10-06"}]
    budget = report.budget_view(decisions, week=2)
    assert budget["used"] == [1, 2]
    assert budget["pace"][22] == pytest.approx(30) and budget["pace"][-1] == pytest.approx(36)


def test_add_view_marks_weeks_past_the_rule_horizon_as_less_certain():
    view = report.add_view(_move(2, "Arturs Silovs", 0.46), 1, [(2, 7.2), (3, 2.7), (4, 7.8)], {})
    assert [(w["week"], w["confident"]) for w in view["weeks"]] == [(1, True), (2, True), (3, True), (4, False)]


def test_every_chart_draws_a_png():
    roster = [RosterPlayer(3, "Spencer Knight", "CHI", ["G"], "G")]
    mine = _team(13.4, {THU: 5.0}, {THU: {3: ("BN", 0.4)}})
    week = report.week_view(1, mine, _team(51.5, {THU: 5.0}), [_move(1, "Joonas Korpisalo", 0.55)], [])
    schedule = report.schedule_view(roster, [(1, "Bahelin Boys", mine, mine)])
    add = report.add_view(_move(2, "Arturs Silovs", 0.46), 1, [(2, -1.5)], report.budget_view([], 1))
    for png in (charts.week_chart(week), charts.schedule_chart(schedule), charts.add_chart(add)):
        assert png.startswith(b"\x89PNG")
