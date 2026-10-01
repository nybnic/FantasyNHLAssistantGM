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


def test_decision_map_shows_this_week_against_the_next_two_and_leads_with_the_call():
    murashov, lindell = RosterPlayer(5, "Sergei Murashov", "PIT", ["G"]), RosterPlayer(6, "Esa Lindell", "DAL", ["D"])
    streamer = _move(1, "Joonas Korpisalo", 0.55, murashov)
    streamer.next_weeks, streamer.long_term = -9.0, -56.0
    weaker = _move(1, "Joonas Korpisalo", 0.50, lindell)
    weaker.long_term = -80.0
    keeper = _move(2, "Arturs Silovs", 0.46, murashov, games=1)
    keeper.next_weeks = 9.0
    view = report.decision_view(1, [streamer, weaker, keeper], [keeper], threshold=3.1, top=2)
    assert {(pt["label"], pt["recommended"]) for pt in view["points"]} == {
        ("Korpisalo for Murashov", False), ("Silovs for Murashov", True)}  # one point per player: the rule's drop
    korpisalo = next(pt for pt in view["points"] if pt["label"].startswith("Korpisalo"))
    assert korpisalo["x"] == pytest.approx(9.0) and korpisalo["y"] == -9.0
    assert view["headline"] == "Recommended: Silovs for Murashov"
    assert view["detail"].startswith("Biggest lift this week: Korpisalo for Murashov, +9 pts of win odds, but -9")
    assert view["x_bar"] == pytest.approx(2.0) and view["y_bar"] == pytest.approx(6.2)


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
    decision = report.decision_view(1, [_move(1, "Joonas Korpisalo", 0.55)], [], threshold=3.0)
    stream = {"position": "C", "move": _move(1, "Joonas Korpisalo", 0.55), "next_gain": 2.0,
              "this_week": _team(0, {THU: 1.0}, {THU: {1: ("C", 1.0)}}), "next_week": None}
    schedule = report.schedule_view(roster, [(1, "Bahelin Boys", mine, mine)], [stream])
    assert schedule["streamers"][0]["cells"] == ["start"] and schedule["streamers"][0]["slot_games"] == 1
    add = report.add_view(_move(2, "Arturs Silovs", 0.46), 1, [(2, -1.5)], report.budget_view([], 1))
    for png in (charts.decision_chart(decision), charts.schedule_chart(schedule), charts.add_chart(add)):
        assert png.startswith(b"\x89PNG")
