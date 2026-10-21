import datetime as dt
from dataclasses import dataclass

from engine import scorecard

D = dt.date(2026, 10, 1)


@dataclass
class _Log:
    date: dt.date
    stats: dict


class _Ctx:
    skater_games = {9: [_Log(D, {"g": 2}), _Log(D + dt.timedelta(days=20), {"g": 5})],  # the 2nd: past the window
                    5: [_Log(D + dt.timedelta(days=3), {"a": 1})]}
    goalie_games = {7: [_Log(D, {"gs": 1, "w": 1, "ga": 2, "sv": 30})]}


def _decision(decision, add, drop, date=D):
    return {"rec_id": "r", "type": "add", "decision": decision, "date": date.isoformat(), "add": add,
            "add_name": f"P{add}", "drop": drop, "drop_name": f"P{drop}" if drop else None}


def test_each_suggestion_is_scored_against_its_drop_once_its_window_closes():
    decisions = [_decision("skip", 9, 5), _decision("done", 9, 5), _decision("skip", 7, None),
                 _decision("done", 5, None, D + dt.timedelta(days=10))]  # still inside its window
    scored = scorecard.score(decisions, _Ctx(), D + dt.timedelta(days=15))
    assert [(s.decision, s.add, s.gain) for s in scored] == [("done", "P9", 8.0 - 2.75), ("skip", "P7", 13.5)]
    assert scorecard.line(scored) == ("Suggestions so far (14 days after each, raw points): "
                                      "1 made, +5 pts vs their drops; 1 not made, would have been +14.")
    assert scorecard.line([]) is None


def test_an_untapped_suggestion_is_logged_when_it_expires(tmp_path):
    from state import gm_state
    state = gm_state.load(tmp_path / "s.json")
    state["pending"]["add-x"] = {"type": "add", "date": "2026-10-01", "drop": 5, "drop_name": "P5",
                                 "add": {"id": 9, "name": "P9"}, "message_id": 1}
    gm_state.save(state, dt.date(2026, 10, 20), tmp_path / "s.json")
    assert state["pending"] == {} and state["decisions"][-1]["decision"] == "none"
    assert state["decisions"][-1]["add"] == 9 and state["decisions"][-1]["drop_name"] == "P5"


# The scoreboard: my rosters against the drafted one.
class _Game:
    def __init__(self, date, stats, started=True):
        self.date, self.stats, self.started = date, stats, started


class _WeekCtx:
    """Box scores for one week: the add (9) scores 5 goals' worth on day 1, the
    drafted player he replaced (5) one assist; goalies start three times."""
    today = D + dt.timedelta(days=7)

    def __init__(self, goalie_starts=3):
        self.skater_games = {9: [_Game(D, {"g": 2})], 5: [_Game(D, {"a": 1})], 1: [_Game(D, {"sog": 4})]}
        self.goalie_games = {7: [_Game(D + dt.timedelta(days=k), {"gs": 1, "sv": 20}) for k in range(goalie_starts)]}

    def skater(self, pid, position):
        return type("X", (), {"xfp": 3.0})()


def _rosters():
    from league.roster import RosterPlayer
    common = [RosterPlayer(1, "Keeper", "BOS", ["C"]), RosterPlayer(7, "Goalie", "BOS", ["G"])]
    return common + [RosterPlayer(9, "Add", "BOS", ["LW"])], common + [RosterPlayer(5, "Drafted", "BOS", ["LW"])]


def test_the_scoreboard_plays_my_rosters_against_the_drafted_one():
    mine, drafted = _rosters()
    days = [D + dt.timedelta(days=k) for k in range(7)]
    first = {"expected": [200.0, 200.0], "so_far": [0.0, 0.0], "sd": [30.0, 30.0], "drafted": 196.0}
    # Two goals (8) vs an assist (2.75); both keep the keeper's 4 shots (2) and three goalie starts (24).
    v = scorecard.week_value({d: mine for d in days}, drafted, 33.5, _WeekCtx(), days, first)
    assert (v["actual"], v["drafted"], v["added"], v["margin"], v["forecast"]) == (34.0, 28.75, 5.25, 0.5, 4.0)
    from engine import matchup
    assert v["wins"] == round(0.5 - matchup._phi(-5.25 / (2 * 30.0 ** 2) ** 0.5), 4)
    assert scorecard.outcome(v) == "they turned a loss into a win"
    text = scorecard.value_line(v, [v, {"added": -3.0, "margin": -10.0, "wins": -0.02}])
    assert text.split("\n") == [
        f"Your moves since the draft: +5 pts this week (34 vs the drafted roster's 29), {v['wins']:+.2f} wins at "
        "this week's odds; they turned a loss into a win. Forecast at the week's first plan: +4.",
        f"Season, 2 weeks: +2 pts, {v['wins'] - 0.02:+.2f} wins, 1 result decided by them."]


def test_a_missed_goalie_minimum_zeroes_goalie_points_on_either_roster():
    mine, drafted = _rosters()
    days = [D + dt.timedelta(days=k) for k in range(7)]
    v = scorecard.week_value({d: mine for d in days}, drafted, 0.0, _WeekCtx(goalie_starts=2), days)
    assert v["actual"] == 10.0 and v["drafted"] == 4.75 and "wins" not in v
    assert scorecard.outcome(v) == "you'd have won without them too"
    assert scorecard.value_line(None, []) is None


def test_the_weekly_result_scores_each_finished_week_once_and_keeps_it(tmp_path):
    from bot import weekly
    from state import gm_state
    from league import weeks
    state = gm_state.load(tmp_path / "s.json")
    mine, drafted = _rosters()
    week = weeks.week_of(D)
    days = weeks.days(week)
    state["draft_roster"] = [{"id": p.id, "name": p.name, "team": p.team, "positions": p.positions} for p in drafted]
    state["my_players"] = {str(p.id): {"name": p.name, "team": p.team, "positions": p.positions} for p in mine}
    state["my_days"] = {d.isoformat(): [p.id for p in mine] for d in days}
    state["results"] = {str(week): {"final": {"score": [30.0, 20.0]}}}
    ctx = _WeekCtx()
    ctx.today = days[-1] + dt.timedelta(days=1)
    values = weekly.season_values(state, mine, week, ctx)
    assert values == [state["results"][str(week)]["value"]] and values[0]["added"] == 5.25
    state["my_days"].pop(days[0].isoformat())  # computed once: kept though the record is gone
    assert weekly.season_values(state, mine, week, ctx) == values
