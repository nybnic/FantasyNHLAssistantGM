import datetime as dt
import json
from types import SimpleNamespace

from bot import board, messages, weekly
from engine import matchup
from engine.addprice import AddPrice
from engine.plan import Planned
from league.roster import RosterPlayer
from state import gm_state

NOW = dt.datetime(2026, 10, 9, 6, 9, tzinfo=dt.timezone.utc)
DAYS = [dt.date(2026, 10, 5), dt.date(2026, 10, 11)]
SAMUELSSON = RosterPlayer(1, "Mattias Samuelsson", "BUF", ["D"])


def _move(add_id, name, week_gain, long_term, win_after, drop=SAMUELSSON):
    return matchup.Move(RosterPlayer(add_id, name, "NYR", ["LW"]), drop, week_gain, long_term, 0.0, 2, 0.16,
                        win_after, later_weight=0.0088)


MONDAY = dt.date(2026, 10, 12)


def _plan(ranked, planned, max_moves=1, held=False, price=AddPrice(0.097, 0.0088, 1.23)):
    """`planned`: (move, when, why) as engine/plan.compose gives them."""
    me = matchup.TeamWeek("Nico's Groovy Team", 136.8, 223.0, 357.2, 17, 2, 1.6, 0.96)
    them = matchup.TeamWeek("Retrot Chicken Wings", 162.8, 249.5, 324.7, 17, 2, 2.0, 1.0)
    wk = SimpleNamespace(me=me, them=them, days=DAYS, season_used=3, max_moves=max_moves, live=True,
                         yahoo_projected=[229.96, 257.38], price=price, later_weight=0.0088, tau=21.4)
    return weekly.PlanMoves(wk, None, ranked, [Planned(m, when, why) for m, when, why in planned], [], [], 0, held)


def test_the_board_marks_each_move_in_the_plan_passing_or_failing():
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)  # a keeper: does nothing this week
    tolvanen = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21, drop=RosterPlayer(7, "Esa Lindell", "DAL", ["D"]))
    kantserov = _move(4, "Roman Kantserov", 1.5, 24.3, 0.17)  # clears the price, clashes with Kelly
    arvidsson = _move(5, "Viktor Arvidsson", 5.5, 0.5, 0.21)  # 5 + 0.4: under the price
    p = _plan([kelly, kantserov, tolvanen, arvidsson], [(tolvanen, NOW.date(), "now"), (kelly, MONDAY, "monday")])
    b = board.build(p, 2, "Retrot Chicken Wings", NOW, NOW.date())
    by_name = {r["add"]["name"]: r for r in b["moves"]}
    assert [by_name[n]["status"] for n in ("Eeli Tolvanen", "Parker Kelly", "Roman Kantserov", "Viktor Arvidsson")] \
        == ["plan", "plan", "passes", "fails"]
    assert b["plan"]["moves"] == [{"key": "2:7", "when": "2026-10-09", "why": "now"},
                                  {"key": "3:1", "when": "2026-10-12", "why": "monday"}]
    assert by_name["Parker Kelly"]["when"] == "2026-10-12" and by_name["Roman Kantserov"]["when"] is None
    assert by_name["Viktor Arvidsson"]["why"].startswith("worth 5.4 win-pts, under the 9.7")
    assert by_name["Eeli Tolvanen"]["value"] == round(tolvanen.value, 4)
    assert b["header"]["win"] == round(matchup.win_prob(p.wk.me, p.wk.them), 4)
    assert b["header"]["adds_left"] == {"season": 33, "week": 1} and b["header"]["price"]["lam"] == 0.097
    assert b["header"]["me"]["sd"] == 18.9 and b["opponent"] == "Retrot Chicken Wings"
    json.dumps(b)  # all of it JSON


def test_a_plans_second_add_is_on_the_board_though_judged_after_the_first():
    first, second = _move(2, "First", 5.0, 9.0, 0.21), _move(6, "Second", 4.0, 2.0, 0.25)
    b = board.build(_plan([first], [(first, NOW.date(), "now"), (second, NOW.date(), "now")], max_moves=2), 2, "R",
                    NOW, NOW.date())
    assert [q["key"] for q in b["plan"]["moves"]] == ["2:1", "6:1"] and {r["key"] for r in b["moves"]} == {"2:1", "6:1"}


def test_with_the_budget_spent_nothing_passes():
    move = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21)
    b = board.build(_plan([move], [], max_moves=0, price=None), 2, "R", NOW, NOW.date())
    assert b["header"]["price"] is None and b["moves"][0]["status"] == "fails"


def test_the_plan_saves_its_board_and_explain_week_reads_the_same_table(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "BOARD_FILE", tmp_path / "board.json")
    tolvanen = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21)
    built = weekly.save_board(_plan([tolvanen], [(tolvanen, NOW.date(), "now")]), 2, "R", NOW, NOW.date(), None,
                              dry_run=False, changed="someone took him")
    saved = board.load(tmp_path / "board.json")
    assert saved == json.loads(json.dumps(built)) and saved["plan"]["changed"] == "someone took him"
    lines = board.table(saved)
    assert "Eeli Tolvanen" in lines[1] and "plan" in lines[1] and "16%->21%" in lines[1] and "Fri 09" in lines[1]
    assert board.table(saved, position="D") == lines[:1]  # header only: Tolvanen plays LW


def test_the_ledger_is_its_own_file_and_loads_back_as_one_state(tmp_path):
    path = tmp_path / "gm_state.json"
    state = gm_state.load(path)
    state["adds"].append({"id": 7, "name": "Shane Pinto", "date": "2026-10-07", "source": "roster"})
    state["telegram_offset"] = 42
    gm_state.save(state, dt.date(2026, 10, 9), path)
    run, ledger = (json.loads(p.read_text(encoding="utf-8")) for p in (path, tmp_path / "ledger.json"))
    assert set(ledger) == set(gm_state.LEDGER_KEYS) and not set(run) & set(gm_state.LEDGER_KEYS)
    assert ledger["adds"][0]["name"] == "Shane Pinto" and run["telegram_offset"] == 42
    again = gm_state.load(path)
    assert again["adds"] == state["adds"] and again["telegram_offset"] == 42


def test_a_state_file_from_before_the_split_wins_over_an_older_ledger(tmp_path):
    path = tmp_path / "gm_state.json"
    (tmp_path / "ledger.json").write_text(json.dumps({"adds": [], "seen_mine": {"1": "2026-10-01"}}), encoding="utf-8")
    path.write_text(json.dumps({"adds": [{"id": 7, "name": "Pinto", "date": "2026-10-07", "source": "roster"}]}),
                    encoding="utf-8")
    state = gm_state.load(path)
    assert [a["name"] for a in state["adds"]] == ["Pinto"] and state["seen_mine"] == {"1": "2026-10-01"}


def test_a_move_that_clashes_with_the_plan_says_so():
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)
    kantserov = _move(4, "Roman Kantserov", 1.5, 24.3, 0.17)  # the same drop, Samuelsson
    b = board.build(_plan([kelly, kantserov], [(kelly, NOW.date(), "spare")]), 2, "R", NOW, NOW.date())
    row = next(r for r in b["moves"] if r["add"]["name"] == "Roman Kantserov")
    assert row["status"] == "passes" and row["why"] == "worth an add, but the plan's Parker Kelly uses the same drop"


def test_the_dashboard_data_is_the_board_plus_the_plan_in_the_messages_words():
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)
    olivier = _move(5, "Mathieu Olivier", 5.8, -11.0, 0.22, drop=RosterPlayer(7, "Esa Lindell", "DAL", ["D"]))
    p = _plan([kelly, olivier], [(kelly, NOW.date(), "spare")])
    p.wk.ahead = [matchup.WeekAhead(3, frozenset(), -15.9, 46.1, "Lallat")]
    b = board.build(p, 2, "Retrot Chicken Wings", NOW, NOW.date())
    data = weekly.dashboard_data(b, p, {"schedule": {"days": []}, "budget": {}}, NOW.date(), "")
    assert data["plan"] == [{"day": "Fri 9 Oct (today)", "today": True, "swap": "Parker Kelly for Mattias Samuelsson",
                             "detail": messages._detail(p.plan[0]), "key": "3:1"}]
    assert data["this_week_line"].startswith("For this week alone, the best is Mathieu Olivier for Esa Lindell")
    assert [w["week"] for w in data["weeks"]] == [2, 3] and data["weeks"][1]["opponent"] == "Lallat"
    assert data["weeks"][0]["margin"] == round(matchup.margin(p.wk.me, p.wk.them), 1)
    assert [r["key"] for r in data["moves"]] == ["3:1", "5:7"] and data["schedule"] == {"days": []}
    assert data["header"] is b["header"]
    json.dumps(data)


def test_the_card_image_falls_back_when_no_browser_can_render_it(monkeypatch):
    import builtins
    from notify import snapshot
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name))
                        if name.startswith("playwright") else real(name, *a, **k))
    assert snapshot.card_png({"week": 2}) is None


def test_the_board_carries_the_breakdowns_and_saves_one_player_a_line(tmp_path):
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)
    kelly.ahead_week_pts, kelly.ahead_pts, kelly.held = (1.5, -0.5), 1.0, True
    p = _plan([kelly], [(kelly, NOW.date(), "spare")])
    p.wk.ahead, p.wk.weeks_after, p.wk.hold_days = [], 23, 18
    details = {"teams": {"me": {"players": [{"id": 1, "pts": 5.0}], "by_stat": {"Goals": 2.0}}},
               "players": {1: {"id": 1, "name": "Mattias Samuelsson", "per_game": {"Blocks": 1.6}}}}
    b = board.build(p, 2, "Retrot Chicken Wings", NOW, NOW.date(), details=details)
    h = b["header"]
    assert h["margin"] == round(matchup.margin(p.wk.me, p.wk.them), 2)
    assert h["sd"] == round((p.wk.me.variance + p.wk.them.variance) ** 0.5, 2)
    assert h["long_run"] == {"weeks": 23, "discount": matchup.LONG_RUN_DISCOUNT, "hold_days": 18}
    assert b["moves"][0]["ahead_week_pts"] == [1.5, -0.5] and b["moves"][0]["held"] is True
    path = board.save(b, tmp_path / "board.json")
    text = path.read_text(encoding="utf-8")
    assert '  "1": {"id":1,"name":"Mattias Samuelsson","per_game":{"Blocks":1.6}}' in text
    loaded = board.load(path)
    assert loaded["players"]["1"]["per_game"] == {"Blocks": 1.6} and loaded["teams"] == details["teams"]
    data = weekly.dashboard_data(b, p, {"schedule": {"days": []}, "budget": {}}, NOW.date(), "")
    assert data["teams"] is b["teams"] and data["players"] is b["players"]
