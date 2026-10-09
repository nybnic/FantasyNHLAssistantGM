import datetime as dt
import json
from types import SimpleNamespace

from bot import board, weekly
from engine import matchup
from engine.addprice import AddPrice
from league.roster import RosterPlayer
from state import gm_state

NOW = dt.datetime(2026, 10, 9, 6, 9, tzinfo=dt.timezone.utc)
DAYS = [dt.date(2026, 10, 5), dt.date(2026, 10, 11)]
SAMUELSSON = RosterPlayer(1, "Mattias Samuelsson", "BUF", ["D"])


def _move(add_id, name, week_gain, long_term, win_after, drop=SAMUELSSON):
    return matchup.Move(RosterPlayer(add_id, name, "NYR", ["LW"]), drop, week_gain, long_term, 0.0, 2, 0.16,
                        win_after, later_weight=0.0088)


def _plan(ranked, moves, max_moves=1, held=False):
    me = matchup.TeamWeek("Nico's Groovy Team", 136.8, 223.0, 357.2, 17, 2, 1.6, 0.96)
    them = matchup.TeamWeek("Retrot Chicken Wings", 162.8, 249.5, 324.7, 17, 2, 2.0, 1.0)
    wk = SimpleNamespace(me=me, them=them, days=DAYS, season_used=3, max_moves=max_moves, live=True,
                         yahoo_projected=[229.96, 257.38], price=AddPrice(0.097, 0.0088, 1.23), later_weight=0.0088,
                         tau=21.4)
    return weekly.PlanMoves(wk, None, ranked, moves, [], [], 0, held)


def test_the_board_marks_each_move_now_waits_passes_or_fails():
    tolvanen = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21)  # 5 + 8 = 13 win-pts: clears 9.7
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)  # a keeper: does nothing this week
    kantserov = _move(4, "Roman Kantserov", 1.5, 24.3, 0.17)  # clears the price, not taken
    arvidsson = _move(5, "Viktor Arvidsson", 5.5, 0.5, 0.21)  # 5 + 0.4: under the price
    b = board.build(_plan([kelly, kantserov, tolvanen, arvidsson], [tolvanen]), 2, "Retrot Chicken Wings", NOW,
                    NOW.date(), kelly)
    by_name = {r["add"]["name"]: r for r in b["moves"]}
    assert [by_name[n]["status"] for n in ("Eeli Tolvanen", "Parker Kelly", "Roman Kantserov", "Viktor Arvidsson")] \
        == ["now", "waits", "passes", "fails"]
    assert b["plan"] == {"now": ["2:1"], "ir": [], "waits": "3:1", "held": False}
    assert by_name["Viktor Arvidsson"]["why"].startswith("worth 5.4 win-pts, under the 9.7")
    assert by_name["Eeli Tolvanen"]["value"] == round(tolvanen.value, 4) and by_name["Eeli Tolvanen"]["why"] == ""
    assert b["header"]["win"] == round(matchup.win_prob(_plan([], []).wk.me, _plan([], []).wk.them), 4)
    assert b["header"]["adds_left"] == {"season": 33, "week": 1} and b["header"]["price"]["lam"] == 0.097
    assert b["header"]["me"]["sd"] == 18.9 and b["opponent"] == "Retrot Chicken Wings"
    json.dumps(b)  # all of it JSON


def test_a_plans_second_add_is_on_the_board_though_judged_after_the_first():
    first, second = _move(2, "First", 5.0, 9.0, 0.21), _move(6, "Second", 4.0, 2.0, 0.25)
    b = board.build(_plan([first], [first, second], max_moves=2), 2, "R", NOW, NOW.date(), None)
    assert b["plan"]["now"] == ["2:1", "6:1"] and {r["key"] for r in b["moves"]} == {"2:1", "6:1"}


def test_with_no_adds_left_nothing_passes_and_there_is_no_price():
    move = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21)
    b = board.build(_plan([move], [], max_moves=0), 2, "R", NOW, NOW.date(), None)
    assert b["header"]["price"] is None and b["moves"][0]["status"] == "fails"
    assert b["moves"][0]["why"] == "no adds left"


def test_the_plan_saves_its_board_and_explain_week_reads_the_same_table(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "BOARD_FILE", tmp_path / "board.json")
    tolvanen = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21)
    path = weekly.save_board(_plan([tolvanen], [tolvanen]), 2, "R", NOW, NOW.date(), None, dry_run=False)
    saved = board.load(path)
    assert path == tmp_path / "board.json" and saved["plan"]["now"] == ["2:1"]
    lines = board.table(saved)
    assert "Eeli Tolvanen" in lines[1] and "now" in lines[1] and "16%->21%" in lines[1]
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


def test_a_keeper_that_does_nothing_this_week_is_made_monday_else_today():
    monday, friday = dt.date(2026, 10, 12), dt.date(2026, 10, 9)
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)
    kelly.next_weeks = 11.1  # nothing this week, plenty in the next two
    assert board.when(kelly, friday, monday) == monday
    assert board.when(_move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21), friday, monday) == friday
    claim = _move(2, "Claimed", 4.9, 9.2, 0.21)
    claim.plays_from = dt.date(2026, 10, 10)
    assert board.when(claim, friday, monday) == dt.date(2026, 10, 10)
