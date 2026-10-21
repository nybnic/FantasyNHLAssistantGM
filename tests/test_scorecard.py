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
    assert scorecard.line(scored) == ("Suggestions not made (14 days after each, raw points): 1, "
                                      "would have been +14 vs their drops.")
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
    assert text == ("Against keeping the drafted roster (doing nothing): +5 pts this week; season +2 pts, "
                    f"{v['wins'] - 0.02:+.2f} wins.")


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



# Each move against the team just before it.
def _day(n):
    return (dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat()


NAMES = {str(i): {"name": f"P{i}", "team": "BOS", "positions": ["G" if i >= 90 else "C"]}
         for i in (1, 2, 3, 4, 5, 6, 7, 8, 90, 91)}


def test_moves_are_read_off_the_daily_roster_with_ir_stints_and_cuts_told_apart():
    my_days = {_day(0): [1, 2, 3, 90], _day(1): [1, 2, 3, 90],
               _day(2): [1, 2, 4, 5, 6],  # 3 out for 4 (a Done tap's pair), 90 cut for 6 back from IR, 5 into the open spot
               _day(3): [1, 4, 5, 6], _day(4): [1, 4, 5, 6, 91],  # 2 to IR (back on day 6); 91 into an open spot
               _day(5): [1, 4, 5, 91, 2], _day(6): [1, 4, 7, 91, 2],  # 2 back from IR cuts 6; 5 out for 7
               }
    adds = [{"id": i, "date": _day(d)} for i, d in ((4, 2), (5, 2), (91, 4), (7, 6))]
    moves = scorecard.made_moves(adds, my_days, NAMES, on_ir=set(), known={4: 3})
    got = [(m.add, m.drop, m.day.isoformat(), m.until and m.until.isoformat(), m.cut, m.cut_from and m.cut_from.isoformat())
           for m in moves]
    assert got == [(4, 3, _day(2), None, None, None),
                   (5, None, _day(2), _day(5), 90, _day(2)),  # into the open spot, so 90's cut is his
                   (91, None, _day(4), None, 6, _day(5)),  # the latest open-spot add holds 6's cut
                   (7, 5, _day(6), None, None, None)]  # the day's only drop, nobody back from IR
    assert moves[0].key == "4:3" and moves[1].key == "5:0"


def test_a_move_is_scored_with_its_swap_undone_and_checked_against_the_forecast():
    from league.roster import RosterPlayer
    p = {i: RosterPlayer(i, f"P{i}", "BOS", ["C"]) for i in (1, 5, 9)}
    days = [D + dt.timedelta(days=k) for k in range(7)]
    history = {d: [p[1], p[9]] for d in days}  # 9 added on day 0 for 5
    m = scorecard.MadeMove(9, 5, D, None)
    first = {"expected": [200.0, 200.0], "so_far": [0.0, 0.0], "sd": [30.0, 30.0]}
    later = [D + dt.timedelta(days=7 + k) for k in range(7)]
    by_week = scorecard.move_weeks(m, history | {d: [p[1], p[9]] for d in later}, {5: p[5]}, _WeekCtx(),
                                   {40: first}, [(40, days), (41, later)])
    assert by_week[0]["pts"] == 8.0 - 2.75 and by_week[1] == {"week": 41, "pts": 0.0, "wins": None}
    forecast = {"date": "2026-09-30", "value": 0.2, "now_wins": 0.05, "ahead_wins": [0.02, 0.01], "week_pts": 4.0,
                "ahead_week_pts": [1.0, 1.0], "later_wins": 0.12, "price": 0.14}
    c = scorecard.move_check(m, by_week, forecast, {"9": {"name": "Add"}, "5": {"name": "Drop"}})
    assert c["pts"] == 5.25 and c["forecast"]["pts"] == 6.0 and c["forecast"]["wins"] == 0.08
    assert scorecard.check_line(c) == (
        f"Add for Drop (Thu 1 Oct), its first 3 weeks: +5 pts, {c['wins']:+.2f} wins; the plan forecast +6 pts, "
        "+0.08 (all in +0.20 with later points; an add cost 0.14).")
    assert scorecard.checks_line([c]) == (f"Moves checked so far: 1, +5 pts, {c['wins']:+.2f} wins in their first 3 "
                                          f"weeks; the 1 with a forecast {c['wins']:+.2f} wins against +0.08 forecast.")
    c.pop("forecast")
    assert scorecard.check_line(c).endswith("(no forecast saved: from before the Board, or not the plan's move).")


def test_a_move_is_checked_once_its_first_three_weeks_are_over_and_forecasts_are_logged(tmp_path):
    from bot import weekly
    from league import weeks
    from state import gm_state
    state = gm_state.load(tmp_path / "s.json")
    start = weeks.week_of(D)
    days = [d for w in range(start, start + 3) for d in weeks.days(w)]
    before = days[0] - dt.timedelta(days=1)
    state["my_players"] = {"1": {"name": "Keeper", "team": "BOS", "positions": ["C"]},
                           "5": {"name": "Drafted", "team": "BOS", "positions": ["LW"]},
                           "9": {"name": "Add", "team": "BOS", "positions": ["LW"]}}
    state["my_days"] = {before.isoformat(): [1, 5]} | {d.isoformat(): [1, 9] for d in days}
    state["adds"] = [{"id": 9, "name": "Add", "date": days[0].isoformat(), "source": "done"}]
    b = {"date": before.isoformat(), "at": "x", "header": {"price": {"lam": 0.14}},
         "plan": {"moves": [{"key": "9:5"}]},
         "moves": [{"key": "9:5", "value": 0.2, "now_wins": 0.05, "ahead_wins": [0.02, 0.01], "week_pts": 4.0,
                    "ahead_week_pts": [1.0, 1.0], "later_wins": 0.12}, {"key": "8:5", "value": 0.3}]}
    weekly.log_forecasts(state, b)
    assert list(state["move_forecasts"]) == ["9:5"] and state["move_forecasts"]["9:5"]["price"] == 0.14
    ctx = _WeekCtx()
    ctx.today = days[-1]  # the third week's last day isn't over
    assert weekly.check_moves(state, [], start + 2, ctx) == []
    ctx.today = days[-1] + dt.timedelta(days=1)
    assert weekly.check_moves(state, [], start + 1, ctx) == []  # only two weeks reported so far
    new = weekly.check_moves(state, [], start + 2, ctx)
    assert [c["key"] for c in new] == ["9:5"] and new[0]["drop"] == "Drafted" and new[0]["pts"] == 5.25
    assert new[0]["forecast"]["wins"] == 0.08
    assert weekly.check_moves(state, [], start + 2, ctx) == []  # once
