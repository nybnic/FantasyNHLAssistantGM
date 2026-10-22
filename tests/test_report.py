import datetime as dt

import pytest

from clients.nhl_client import ScheduledGame
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


class _BreakdownContext:
    """Two skaters with real stat lines, a goalie with a start's line."""
    today = THU
    team_starts: dict = {}
    goalie_games: dict = {}
    last_results: dict = {}
    replacement_xfp = {"C": 1.5, "D": 1.2}
    lines = {1: {"g": 0.4, "a": 0.6, "ppa": 0.2, "sog": 3.0, "hit": 1.0, "blk": 0.5, "fow": 8.0, "pm": 0.1},
             2: {"a": 0.3, "sog": 1.5, "hit": 2.0, "blk": 2.0}}

    def skater(self, pid, position, before=None):
        from model.projections import SkaterProjection
        return SkaterProjection(pid, "", position, "WPG", 5, 19.5, 2.5, dict(self.lines[pid]))

    def goalie_start(self, pid, team, opp, home, before=None):
        stats = {"gs": 1.0, "w": 0.5, "ga": 2.6, "sv": 25.0, "so": 0.06}
        return {**stats, "xfp": 1 + 4 * 0.5 - 2.6 + 0.35 * 25 + 5 * 0.06}

    def prior_start_share(self, pid):
        return 0.6

    def durability(self, pid):
        return 0.8

    def games_missed(self, pid, team):
        return 0


def test_ice_time_is_his_average_a_game_this_season_in_minutes():
    from clients.nhl_stats import SkaterGame
    ctx = _BreakdownContext()
    games = [(THU - dt.timedelta(days=3), 1200, 120), (THU - dt.timedelta(days=1), 1500, 60),
             (THU, 9999, 999)]  # Thursday's is tonight's: not played yet
    ctx.skater_games = {1: [SkaterGame(1, "", "C", "WPG", "CHI", d, 0, toi=t, pp_toi=pp) for d, t, pp in games]}
    view = report.player_view(RosterPlayer(1, "Mark Scheifele", "WPG", ["C"]), ctx, {}, {}, {}, "me")
    assert view["toi"] == 22.5 and view["pp_toi"] == 1.5  # seconds a game, as minutes
    ctx.skater_games = {}
    view = report.player_view(RosterPlayer(1, "Mark Scheifele", "WPG", ["C"]), ctx, {}, {}, {}, "me")
    assert view["toi"] == pytest.approx(19.5 / 60, abs=0.01)  # no games yet: the projection's


def test_a_players_points_split_by_stat_add_up_to_his_projection():
    ctx = _BreakdownContext()
    view = report.player_view(RosterPlayer(1, "Mark Scheifele", "WPG", ["C"]), ctx, {}, {}, {}, "me")
    assert sum(view["per_game"].values()) == pytest.approx(view["xfp"], abs=0.01)
    assert view["per_game"]["Faceoffs"] == pytest.approx(0.8) and view["per_game"]["PP, SH, GWG"] == pytest.approx(0.1)
    assert view["stats"]["Faceoff wins"] == 8.0 and view["stats"]["PP points"] == pytest.approx(0.2)  # counts, not points
    # Missed games cost only his edge over a streamer: 20% of games x (xFP - 1.5) / xFP.
    assert view["durability"] == 0.8 and view["kept"] == pytest.approx(1 - 0.2 * (view["xfp"] - 1.5) / view["xfp"],
                                                                       abs=0.001)
    sched = {FRI: [ScheduledGame(1, dt.datetime(2026, 10, 2, 23, tzinfo=dt.timezone.utc), "WPG", "CHI")]}
    goalie = report.player_view(RosterPlayer(3, "Connor Hellebuyck", "WPG", ["G"]), ctx, {}, {}, sched, "me")
    assert goalie["starts"][0]["opp"] == "CHI" and goalie["xfp"] == pytest.approx(9.45)
    assert goalie["per_game"]["Goals against"] == pytest.approx(-2.6)
    assert goalie["stats"]["Goals against"] == pytest.approx(2.6) and goalie["stats"]["Saves"] == 25.0
    assert goalie["next"] == {"date": "2026-10-02", "opp": "CHI"}
    # No start left this week: his line is his next start's, next week.
    idle = report.player_view(RosterPlayer(3, "Connor Hellebuyck", "WPG", ["G"]), ctx, {}, {}, {}, "me", sched)
    assert idle["starts"] == [] and idle["xfp"] == pytest.approx(9.45) and idle["next"]["opp"] == "CHI"


def test_a_players_season_so_far_is_set_against_what_was_projected_before_each_game():
    from clients.nhl_stats import GoalieGame, SkaterGame
    ctx = _BreakdownContext()
    asked = []
    real = ctx.skater
    ctx.skater = lambda pid, pos, before=None: asked.append(before) or real(pid, pos)
    ctx.skater_games = {1: [SkaterGame(1, "", "C", "WPG", "CHI", THU - dt.timedelta(days=2), 0,
                                       stats={"g": 1, "sog": 4, "hit": 2}),
                            SkaterGame(1, "", "C", "WPG", "DAL", THU - dt.timedelta(days=1), 0, stats={"a": 1, "blk": 1}),
                            SkaterGame(1, "", "C", "WPG", "MIN", THU, 0, stats={"g": 5})]}  # tonight's: not yet
    s = report.player_view(RosterPlayer(1, "Mark Scheifele", "WPG", ["C"]), ctx, {}, {}, {}, "me")["season"]
    assert asked[:2] == [THU - dt.timedelta(days=2), THU - dt.timedelta(days=1)]  # each game's projection from before it
    xfp = ctx.skater(1, "C").xfp
    # 1 goal (4) + 4 shots (2) + 2 hits (1.2), then 1 assist (2.75) + 1 block (0.8)
    assert s["gp"] == 2 and s["pts"] == pytest.approx(10.75, abs=0.06) and s["proj"] == pytest.approx(2 * xfp, abs=0.05)
    assert s["pts_projected"] == s["pts"]
    assert s["stats"]["Goals"] == [1.0, 0.8] and s["stats"]["Shots"] == [4.0, 6.0]  # [actual, projected]
    assert [g["opp"] for g in s["last"]] == ["DAL", "CHI"] and s["last"][0]["pts"] == pytest.approx(3.55, abs=0.06)
    ctx.goalie_games = {3: [GoalieGame(3, "", "WPG", "CHI", THU - dt.timedelta(days=3), 0, True, True,
                                       stats={"gs": 1, "w": 1, "sv": 30, "ga": 2}),
                            GoalieGame(3, "", "WPG", "DAL", THU - dt.timedelta(days=1), 0, False, False,
                                       stats={"sv": 10, "ga": 1})]}
    g = report.player_view(RosterPlayer(3, "Connor Hellebuyck", "WPG", ["G"]), ctx, {}, {}, {}, "me")["season"]
    assert g["gp"] == 2 and g["starts"] == 1 and g["relief"] == 1
    assert g["proj"] == pytest.approx(9.45, abs=0.06) and g["stats"]["Saves"] == [30.0, 25.0]  # the start only
    assert g["last"][0] == {"date": "2026-09-30", "opp": "@DAL", "pts": g["pts"] - g["pts_projected"], "proj": None}
    assert report.player_view(RosterPlayer(2, "Neal Pionk", "WPG", ["D"]), ctx, {}, {}, {}, "me")["season"] is None


def test_team_games_lists_each_teams_opponents_by_day_from_today():
    game = lambda d, home, away: ScheduledGame(1, dt.datetime(d.year, d.month, d.day, 23, tzinfo=dt.timezone.utc),
                                               home, away)
    nxt = FRI + dt.timedelta(days=4)
    view = report.team_games([(1, {THU - dt.timedelta(days=1): [game(THU, "WPG", "CHI")], THU: [game(THU, "WPG", "CHI")],
                                   FRI: [game(FRI, "DAL", "WPG")]}),
                              (2, {nxt: [game(nxt, "CHI", "DAL")]})], THU)
    assert view["days"] == [{"date": "2026-10-01", "week": 1}, {"date": "2026-10-02", "week": 1},
                            {"date": "2026-10-06", "week": 2}]
    assert view["teams"]["WPG"] == ["CHI", "@DAL", None] and view["teams"]["CHI"] == ["@WPG", None, "DAL"]


def test_a_teams_breakdown_reconciles_to_its_projection():
    roster = [RosterPlayer(1, "Mark Scheifele", "WPG", ["C"], "C"), RosterPlayer(2, "Neal Pionk", "WPG", ["D"], "D"),
              RosterPlayer(3, "Connor Hellebuyck", "WPG", ["G"], "G"), RosterPlayer(4, "Hurt Guy", "WPG", ["D"], "IR+")]
    week = matchup.TeamWeek("me", 40.0, 40.0 + 8.0 + 3.0 + 9.0 - 2.0, 300.0, 4, 1, 1.0, 0.8,
                            by_player={1: (8.0, 2, 0.0), 2: (3.0, 2, 1.5), 3: (9.0, 1.0, 0.0)}, goalie_lost=2.0)
    view = report.team_view(week, roster, _BreakdownContext())
    assert [r["id"] for r in view["players"]] == [3, 1, 2, 4] and view["players"][3]["slot"] == "IR+"
    assert sum(view["by_stat"].values()) == pytest.approx(18.0, abs=0.05) and view["rest"] == 18.0
    assert view["by_stat"]["Goalies"] == 7.0 and view["by_stat"]["Blocks"] == pytest.approx(8 * 0.4 / 6.7 + 3 * 1.6 / 4.375, abs=0.01)


def test_streaming_view_has_each_nights_games_and_open_slots_and_each_teams_opponents():
    game = lambda d, home, away: ScheduledGame(1, dt.datetime(d.year, d.month, d.day, 23, tzinfo=dt.timezone.utc),
                                               home, away)
    nxt = FRI + dt.timedelta(days=4)
    mine = _team(0, {THU: 5}, {THU: {1: ("C", 1.0), 2: ("D", 1.0), 3: ("BN", 1.0)}})
    view = report.streaming_view([(1, "Bahelin Boys", {THU - dt.timedelta(days=1): [game(THU, "WPG", "CHI")],
                                                       THU: [game(THU, "WPG", "CHI"), game(THU, "DAL", "BOS")],
                                                       FRI: [game(FRI, "DAL", "WPG")]}, mine),
                                  (2, "Them", {nxt: [game(nxt, "CHI", "DAL")]}, None)], THU)
    assert [(d["date"], d["games"]) for d in view["days"]] == [("2026-10-01", 2), ("2026-10-02", 1), ("2026-10-06", 1)]
    assert view["days"][0]["open"] == {"C": 1, "LW": 2, "RW": 2, "D": 3, "G": 2}  # the bench takes no slot
    assert view["days"][1]["open"]["D"] == 4  # nobody of mine plays: all open
    assert view["days"][2]["open"] is None  # no projection of my week
    teams = {t["team"]: t["cells"] for t in view["teams"]}
    assert teams["WPG"] == ["CHI", "@DAL", None] and teams["BOS"] == ["@DAL", None, None]
    assert [w["week"] for w in view["weeks"]] == [1, 2]


def test_the_result_carries_the_scoreboard_before_the_suggestions_line():
    plan = {"at": "2026-11-09T10:00+00:00", "expected": [150.0, 140.0], "sd": [25.0, 25.0], "win": 0.61}
    view = report.result_view(7, "Gwp", [dt.date(2026, 11, 9)], [160.0], [155.0], [True, True], plan, [], 0,
                              report.budget_view([], 7), True, "Suggestions so far: ...", {"added": 12.0},
                              "Your moves since the draft: +12 pts this week.")
    lines = report.result_text(view).split("\n")
    assert lines[-2:] == ["Your moves since the draft: +12 pts this week.", "Suggestions so far: ..."]
    assert view["value"] == {"added": 12.0}


def test_with_the_plan_a_players_nights_follow_the_plans_lineups_and_end_at_his_drop():
    knight, larsson = RosterPlayer(3, "Spencer Knight", "CHI", ["G"], "G"), RosterPlayer(5, "Adam Larsson", "SEA", ["D"], "D")
    mine = _team(0, {THU: 5.0, FRI: 5.0}, {THU: {3: ("G", 0.6), 5: ("D", 1.0)}, FRI: {5: ("D", 1.0)}})
    with_plan = _team(0, {THU: 9.0, FRI: 5.0}, {THU: {3: ("G", 0.6), 5: ("BN", 1.0), 1: ("D", 1.0)}, FRI: {1: ("D", 1.0)}})
    nurse = matchup.Move(RosterPlayer(1, "Darnell Nurse", "SJS", ["D"]), larsson, 2.0, 0.0, 0.0, 2, 0.46, 0.5)
    view = report.schedule_view([knight, larsson], [(1, "Bahelin Boys", mine, mine)], [], [],
                                [{"move": nurse, "this_week": with_plan, "next_week": None, "when": FRI}], [with_plan])
    rows = {r["name"]: r for r in view["rows"]}
    assert rows["A. Larsson"]["cells"] == ["start", "start"] and rows["A. Larsson"]["plan_cells"] == ["bench", None]
    assert rows["S. Knight"]["plan_cells"] == ["start", None] and rows["S. Knight"]["id"] == 3
    assert "plan_cells" not in report.schedule_view([knight], [(1, "B", mine, mine)])["rows"][0]  # no plan, as is only
