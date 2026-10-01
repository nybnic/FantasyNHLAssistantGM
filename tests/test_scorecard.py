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
