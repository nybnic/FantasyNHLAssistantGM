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


def test_the_cards_are_readable_on_a_phone():
    """The Telegram cards (site/index.html?card, ?card=move) on the latest plan run's data, and
    Monday's result (?card=result) on a week of its own: no
    text under 13px (Telegram shows them at ~80%), not too tall, and the plan's moves named.
    Skipped without a browser; CHROMIUM_EXECUTABLE points Playwright at one it didn't install."""
    import os
    from pathlib import Path
    import pytest
    from notify import snapshot
    sync_api = pytest.importorskip("playwright.sync_api")
    data = json.loads(Path("site/data.json").read_text(encoding="utf-8"))
    moves = {r["key"]: r for r in data["moves"]}
    from engine import report
    days = [dt.date(2026, 10, 12) + dt.timedelta(i) for i in range(7)]
    result = report.result_view(3, "Löllöt Höntsääjät", days, [9.6, 81.1, 26.7, 74.9, 8.2, 88.1, 0.0],
                                [42.8, 55.4, 3.5, 68.6, 25.4, 48.9, 20.0], [True, False],
                                {"at": "2026-10-12T07:21+00:00", "expected": [203.6, 200.0], "sd": [32, 31], "win": 0.53},
                                [{"name": "Gabriel Landeskog", "date": "2026-10-13"}], 1, report.budget_view([], 3))
    shown = []
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"))
        except Exception as e:
            pytest.skip(f"no browser: {str(e).splitlines()[0]}")
        try:
            pages = [("plan", None, data)] + [("move", p["key"], data) for p in data["plan"] if p["key"] in moves][:1]
            for kind, key, page_data in pages + [("result", None, result)]:
                tab = browser.new_page(viewport={"width": snapshot.WIDTH, "height": 800}, locale="en-GB")
                tab.add_init_script(f"window.__CARD__ = {json.dumps(kind)}; window.__CARD_KEY__ = {json.dumps(key)}; "
                                    f"window.__DATA__ = {json.dumps(page_data)};")
                tab.goto(snapshot.PAGE.resolve().as_uri())
                tab.wait_for_selector("body[data-ready='1']", timeout=20_000)
                smallest = tab.evaluate("""() => Math.min(...[...document.querySelectorAll('.nc *')]
                    .filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
                    .map(e => parseFloat(getComputedStyle(e).fontSize)))""")
                shown.append((kind, key, tab.locator("main").bounding_box(), smallest, tab.locator(".nc").inner_text()))
        finally:
            browser.close()
    for kind, key, box, smallest, text in shown:
        assert box["width"] == snapshot.WIDTH and box["height"] <= (1.75 if kind == "plan" else 2.4) * box["width"]
        assert smallest >= 13 and "couldn't be drawn" not in text
        if kind == "result":
            assert "WIN" in text and "Höntsääjät" in text and "They missed the goalie minimum" in text
            continue
        for p in data["plan"] if kind == "plan" else [moves[key]]:
            r = moves.get(p["key"])
            assert r["add"]["name"].split(" ", 1)[1] in text if r else p["swap"] in text


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


def _why_board():
    """A Board with a planned add, a Monday keeper, a move under the price, the
    weeks ahead and the breakdowns /why reads."""
    tolvanen = _move(2, "Eeli Tolvanen", 4.9, 9.2, 0.21, drop=RosterPlayer(7, "Esa Lindell", "DAL", ["D"]))
    tolvanen.ahead_wins, tolvanen.ahead_week_pts = (0.03, 0.02), (3.0, 2.0)
    kelly = _move(3, "Parker Kelly", 0.0, 37.3, 0.16)
    arvidsson = _move(5, "Viktor Arvidsson", 5.5, 0.5, 0.21)
    p = _plan([kelly, tolvanen, arvidsson], [(tolvanen, NOW.date(), "now"), (kelly, MONDAY, "monday")])
    p.wk.ahead = [matchup.WeekAhead(3, frozenset(), -2.0, 40.0, "Löllöt Höntsääjät"),
                  matchup.WeekAhead(4, frozenset(), 5.0, 40.0, "Jättiläisentie Giants")]
    details = {"players": {"7": {"id": 7, "name": "Esa Lindell", "team": "DAL", "positions": "D", "owner": "me",
                                 "goalie": False, "xfp": 4.1}},
               "teams": {"me": {"players": [{"id": 7, "pts": 8.2, "games": 2.0, "bench_pts": 4.0, "slot": "D"}]}}}
    return board.build(p, 2, "Retrot Chicken Wings", NOW, NOW.date(), details=details)


def test_why_gives_a_planned_moves_value_part_by_part_against_what_an_add_needs():
    text = messages.why_text(_why_board(), "tolvanen")
    lines = text.split("\n")
    assert lines[0] == "Eeli Tolvanen for Esa Lindell: in the plan, Fri 9 Oct (today)."
    assert lines[1].startswith("Worth +0.") and lines[1].endswith("expected wins; an add needs 0.10.")
    assert lines[2] == "This week +0.05: +4.9 pts, win 16% -> 21%."
    assert lines[3] == "Weeks 3-4 +0.05: +3.0 pts vs Löllöt Höntsääjät, +2.0 pts vs Jättiläisentie Giants."
    assert lines[4].startswith("Later +0.08: +9.2 pts")
    assert lines[-1] == "From the plan of Fri 9 Oct 09:09. The dashboard has every move."


def test_why_my_player_shows_his_week_and_the_best_move_dropping_him():
    text = messages.why_text(_why_board(), "Esa Lindell")
    assert text.startswith("Esa Lindell (DAL D): 4.1 pts a game, 8 pts in 2 lineup games left this week "
                           "(4 more on your bench).\nDropping him: 1 moves weighed, the best:\nEeli Tolvanen for")


def test_why_a_keeper_and_a_move_under_the_price_say_when_and_why_not():
    b = _why_board()
    assert "in the plan, Mon 12 Oct (it does nothing this week, so it takes next week's add)" \
        in messages.why_text(b, "Kelly")
    assert messages.why_text(b, "Viktor Arvidsson").split("\n")[0] == \
        "Viktor Arvidsson for Mattias Samuelsson: not worth an add."
    assert messages.why_text(b, "Arvidsson for Samuelsson").startswith("Viktor Arvidsson for Mattias Samuelsson")
    assert messages.why_text(b, "Kelly for Lindell").startswith("Parker Kelly for Lindell wasn't weighed.")


def test_why_asks_which_one_or_says_who_the_board_holds():
    b = _why_board()
    b["moves"][0]["add"]["name"] = "Parker Lindell"
    assert messages.why_text(b, "Lindell") == "Which one? Esa Lindell, Parker Lindell."
    assert messages.why_text(b, "Nobody").startswith("Nobody isn't on the latest plan's Board.")
    assert messages.why_text(None, "Kelly") == "No plan yet: send /week first."


def test_a_planned_move_on_the_board_is_the_one_the_plan_judged():
    first = _move(2, "First", 5.0, 9.0, 0.21)
    second_today = _move(6, "Second", 4.0, 2.0, 0.25, drop=RosterPlayer(8, "X", "DAL", ["D"]))
    second_planned = _move(6, "Second", 4.0, 12.0, 0.25, drop=RosterPlayer(8, "X", "DAL", ["D"]))
    b = board.build(_plan([first, second_today], [(first, NOW.date(), "now"), (second_planned, MONDAY, "monday")],
                          max_moves=2), 2, "R", NOW, NOW.date())
    row = next(r for r in b["moves"] if r["key"] == "6:8")
    assert row["later_pts"] == 12.0 and row["status"] == "plan"


def test_the_race_is_each_finished_days_points_adding_up_to_the_banked_score(monkeypatch):
    days = [dt.date(2026, 10, 19) + dt.timedelta(i) for i in range(7)]
    by_day = {"me": {days[0]: 9.0, days[1]: 46.0, days[2]: 5.0}, "them": {days[0]: 23.0, days[1]: 57.0, days[2]: 13.0}}
    monkeypatch.setattr(matchup, "_so_far", lambda roster, ctx, ds, history: (by_day[roster][ds[0]], 0.0, 0))
    race = weekly.race_days(None, days, days[3], ("me", {}, 61.0), ("them", {}, 93.0))  # Thursday: Mon-Wed are done
    assert race["days"] == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert race["me"] == [9.2, 46.8, 5.1] and round(sum(race["me"])) == 61  # box scores scaled to the banked 61
    assert sum(race["them"]) == 93.0
    # Yahoo's score from Tuesday's and Wednesday's screenshots (the days before each) anchors Monday and Tuesday.
    live = [{"through": "2026-10-20", "yahoo": [9.25, 23.25]}, {"through": "2026-10-21", "yahoo": [55.0, 80.0]},
            {"through": "2026-10-28", "yahoo": [1.0, 1.0]}]  # another week's: ignored
    race = weekly.race_days(None, days, days[3], ("me", {}, 60.75), ("them", {}, 93.5), checkpoints=live)
    assert race["me"] == [9.2, 45.8, 5.8] and race["them"] == [23.2, 56.8, 13.5]
def test_every_dashboard_tab_draws_on_a_phone():
    """Each tab (site/index.html#today ... #watch) on the latest plan run's data: no card that
    couldn't be drawn, no script error, nothing wider than the phone, and a move's link opens its sheet."""
    import os
    from pathlib import Path
    import pytest
    from notify import snapshot
    sync_api = pytest.importorskip("playwright.sync_api")
    data = json.loads(Path("site/data.json").read_text(encoding="utf-8"))
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"))
        except Exception as e:
            pytest.skip(f"no browser: {str(e).splitlines()[0]}")
        try:
            for tab in ["today", "matchup", "moves", "schedule", "watch"] + [f"move={p['key']}" for p in data["plan"][:1] if p["key"]]:
                page = browser.new_page(viewport={"width": 390, "height": 844}, locale="en-GB")
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(f"window.__DATA__ = {json.dumps(data)};")
                page.goto(snapshot.PAGE.resolve().as_uri() + f"#{tab}")
                page.wait_for_selector("body[data-ready='1']", timeout=20_000)
                assert not errors and "couldn't be drawn" not in page.locator("main").inner_text(), (tab, errors)
                assert page.evaluate("document.documentElement.scrollWidth") <= 390, tab
                assert page.locator(".tabbar button.on").count() == 1
                if tab.startswith("move="):  # its sheet opens with the move's card: the add and the drop face to face
                    add = next(r for r in data["moves"] if r["key"] == tab[5:])["add"]["name"]
                    assert page.locator(".sheet .in-sheet .nc-who b").first.inner_text() == add
                if tab == "today":  # a phone has no hover: a tap on a mark pins its number, a tap elsewhere hides it
                    page.locator("section.card svg rect[rx]").first.click()
                    assert page.locator("#tip").is_visible() and page.locator("#tip b").inner_text()
                    page.mouse.click(5, 5)
                    assert not page.locator("#tip").is_visible()
        finally:
            browser.close()
