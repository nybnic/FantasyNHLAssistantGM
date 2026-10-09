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
    adds = [{"id": 1, "date": "2026-10-01"}, {"id": 2, "date": "2026-10-06"}]
    budget = report.budget_view(adds, week=2)
    assert budget["used"] == [1, 2]
    assert budget["pace"][22] == pytest.approx(30) and budget["pace"][-1] == pytest.approx(36)


def test_a_goalie_streamer_shows_his_expected_starts_not_just_games():
    silovs = RosterPlayer(2, "Arturs Silovs", "PIT", ["G"])
    move = matchup.Move(silovs, None, 0.0, 0.0, 0.0, 3, 0.5, 0.5)
    mine = _team(0, {THU: 0.0, FRI: 0.0}, {THU: {}, FRI: {}})
    week = _team(0, {THU: 3.0, FRI: 1.0}, {THU: {2: ("G", 0.65)}, FRI: {2: ("BN", 0.46)}})
    stream = {"position": "G", "move": move, "next_gain": 2.0, "this_week": week, "next_week": None}
    row = report.schedule_view([], [(1, "Bahelin Boys", mine, mine)], [stream])["streamers"][0]
    assert (row["slot_games"], row["slot_starts"]) == (1, 0.65)  # the benched game isn't a start


def test_every_chart_draws_a_png():
    roster = [RosterPlayer(3, "Spencer Knight", "CHI", ["G"], "G")]
    mine = _team(13.4, {THU: 5.0}, {THU: {3: ("BN", 0.4)}})
    stream = {"position": "C", "move": _move(1, "Joonas Korpisalo", 0.55), "next_gain": 2.0,
              "this_week": _team(0, {THU: 1.0}, {THU: {1: ("C", 1.0)}}), "next_week": None}
    schedule = report.schedule_view(roster, [(1, "Bahelin Boys", mine, mine)], [stream])
    assert schedule["streamers"][0]["cells"] == ["start"] and schedule["streamers"][0]["slot_games"] == 1
    assert charts.schedule_chart(schedule).startswith(b"\x89PNG")


def _result(mine, theirs, goalie_min=(True, True), finished=True):
    plan = {"at": "2026-11-09T10:00+00:00", "expected": [150.0, 140.0], "sd": [25.0, 25.0], "win": 0.61}
    days = [dt.date(2026, 11, 9) + dt.timedelta(days=i) for i in range(len(mine))]
    adds = [{"id": 5, "name": "Esa Lindell", "date": "2026-11-10"}, {"id": None, "name": None, "date": "2026-11-11"}]
    return report.result_view(7, "Gwp", days, mine, theirs, list(goalie_min), plan, adds, 1,
                              report.budget_view(adds, 7), finished)


def test_the_weeks_result_against_its_first_plan():
    view = _result([20.0, 30.0, 110.0], [40.0, 40.0, 75.0])
    assert view["final"] == [160.0, 155.0] and view["vs_plan"] == [10.0, 15.0] and view["z"] == [0.4, 0.6]
    text = report.result_text(view)
    assert text.startswith("Week 7 vs Gwp: you won 160 - 155")
    assert "The plan (Mon 09 Nov) said 150 - 140, 61% to win. Against it you scored +10, they +15." in text
    assert "Adds: 2 made (Esa Lindell), 1 suggestion skipped. 34 left; 2 used vs 9.1 at an even pace." in text


def test_a_result_says_who_missed_the_goalie_minimum_and_a_week_in_progress_is_so_far():
    assert "You missed the goalie minimum" in report.result_text(_result([10.0], [20.0], (False, True)))
    text = report.result_text(_result([10.0], [20.0], finished=False))
    assert text.startswith("Week 7 vs Gwp: so far 10 - 20") and "The plan" not in text


def test_the_result_chart_draws():
    assert charts.result_chart(_result([20.0, 30.0], [40.0, 40.0])).startswith(b"\x89PNG")


def test_the_plans_adds_get_a_grid_row_and_the_slots_show_what_the_plan_changes():
    knight = RosterPlayer(3, "Spencer Knight", "CHI", ["G"], "G")
    mine = _team(0, {THU: 5.0, FRI: 5.0}, {THU: {3: ("G", 0.6)}, FRI: {}})
    with_plan = _team(0, {THU: 9.0, FRI: 5.0}, {THU: {3: ("G", 0.6), 1: ("C", 1.0)}, FRI: {1: ("BN", 1.0)}})
    kelly = _move(1, "Parker Kelly", 0.5)
    stream_kelly = {"position": "C", "move": kelly, "next_gain": 1.0, "this_week": with_plan, "next_week": None}
    view = report.schedule_view([knight], [(1, "Bahelin Boys", mine, mine)], [stream_kelly], [],
                                [{"move": kelly, "this_week": with_plan, "next_week": None, "when": THU}], [with_plan])
    assert [(r["name"], r["plan"], r["cells"], r["when"]) for r in view["plan"]] == [
        ("P. Kelly", True, ["start", "bench"], "2026-10-01")]
    assert view["streamers"] == []  # the plan's add isn't listed twice
    assert view["open"][0]["C"] == 2 and view["with_plan"]["open"][0]["C"] == 1
    assert view["with_plan"]["my_games"] == [2, 0] and view["with_plan"]["benched"] == [0, 1]
    assert view["slots"]["D"] == 4 and view["benched"] == [0, 0]
