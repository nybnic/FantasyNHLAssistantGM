"""The plan: composed coherently (tests/test_matchup.py has the keeper rules)
and kept once sent unless it must change (engine/plan.decide)."""
import datetime as dt
from types import SimpleNamespace

from bot import common, weekly
from config.settings import load_settings
from engine import matchup, plan
from engine.addprice import AddPrice
from league.roster import RosterPlayer
from notify import telegram
from state import gm_state

PRICE = AddPrice(0.10, 0.009, 1.23)
SAMUELSSON = RosterPlayer(6, "Mattias Samuelsson", "BUF", ["D"], "D")
LINDELL = RosterPlayer(7, "Esa Lindell", "DAL", ["D"], "D")
FRIDAY = dt.date(2026, 10, 9)


def _move(add_id, name, value_now, drop=SAMUELSSON, long_term=0.0, week_gain=3.0):
    return matchup.Move(RosterPlayer(add_id, name, "NYR", ["LW"]), drop, week_gain, long_term, 0.0, 2, 0.20,
                        0.20 + value_now, later_weight=0.009)


def _stored(m, when=FRIDAY):
    return {"key": plan.key(m), "add": {"id": m.add.id, "name": m.add.name},
            "drop": {"id": m.drop.id, "name": m.drop.name} if m.drop else None, "when": when.isoformat()}


def test_the_plan_seen_holds_through_a_near_tie():
    kelly, kantserov = _move(1, "Parker Kelly", 0.20), _move(2, "Roman Kantserov", 0.21)
    new = [plan.Planned(kantserov, FRIDAY, "now")]
    verdict = plan.decide([_stored(kelly)], new, {plan.key(kelly): kelly, plan.key(kantserov): kantserov}, {}, PRICE)
    assert verdict.keep  # +1 win-pt: under CHANGE_MARGIN


def test_a_clearly_better_plan_replaces_it_and_says_by_how_much():
    kelly, star = _move(1, "Parker Kelly", 0.20), _move(2, "Star", 0.25)
    verdict = plan.decide([_stored(kelly)], [plan.Planned(star, FRIDAY, "now")],
                          {plan.key(kelly): kelly, plan.key(star): star}, {}, PRICE)
    assert not verdict.keep and verdict.reason == "the new plan is better by 5 win-pts"


def test_a_move_that_can_no_longer_be_made_changes_the_plan_with_its_reason():
    kelly, other = _move(1, "Parker Kelly", 0.20), _move(2, "Other", 0.15)
    current = {plan.key(kelly): kelly, plan.key(other): other}
    new = [plan.Planned(other, FRIDAY, "now")]
    taken = plan.decide([_stored(kelly)], new, current, {plan.key(kelly): "someone took him"}, PRICE)
    assert not taken.keep and taken.reason == "Parker Kelly for Mattias Samuelsson: someone took him"
    hurt = _move(1, "Parker Kelly", 0.02)  # injured: no longer worth an add
    worse = plan.decide([_stored(kelly)], new, {plan.key(hurt): hurt, plan.key(other): other}, {}, PRICE)
    assert not worse.keep and "is no longer worth an add (worth 2.0 win-pts, under the 10.0" in worse.reason
    gone = plan.decide([_stored(kelly)], new, {plan.key(other): other}, {}, PRICE)
    assert not gone.keep and "no longer comes up as a move" in gone.reason


def test_the_same_moves_are_the_same_plan_and_no_plan_seen_means_nothing_to_keep():
    kelly = _move(1, "Parker Kelly", 0.20)
    same = plan.decide([_stored(kelly)], [plan.Planned(kelly, FRIDAY, "now")], {plan.key(kelly): kelly}, {}, PRICE)
    assert same.keep
    assert plan.decide(None, [plan.Planned(kelly, FRIDAY, "now")], {}, {}, PRICE) == plan.Verdict(False, "")


def _flow(monkeypatch, tmp_path):
    """run_plan over fake plan searches: what gets sent, run after run."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent, marked = [], []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(a[2]) or len(sent))
    monkeypatch.setattr(telegram, "mark_handled", lambda token, chat, mid, label: marked.append((mid, label)))
    monkeypatch.setattr(weekly, "save_board", lambda *a, **k: None)
    monkeypatch.setattr(weekly, "current_opponent", lambda state, week: "Retrot Chicken Wings")
    state = gm_state.load(tmp_path / "state.json")
    players = [SAMUELSSON, LINDELL, RosterPlayer(8, "Core", "BOS", ["C"], "C")]
    me = matchup.TeamWeek("me", 137.0, 223.0, 357.0, 17, 2, 1.6, 0.96)
    them = matchup.TeamWeek("them", 163.0, 250.0, 325.0, 17, 2, 2.0, 1.0)
    searches = []

    def plan_moves(state, players, league, date, week, opponent, build_context, extra_ids=(), exclude=()):
        ranked, composed, pool = searches.pop(0)
        wk = SimpleNamespace(me=me, them=them, pool=pool, price=PRICE, max_moves=1, season_used=3, ahead=[],
                             yahoo_projected=None, live_check=None, days=[dt.date(2026, 10, 5), dt.date(2026, 10, 11)])
        return weekly.PlanMoves(wk, None, ranked, composed, [], players, 0)

    monkeypatch.setattr(weekly, "plan_moves", plan_moves)
    return state, players, sent, marked, searches


def test_a_card_goes_out_once_the_plan_holds_through_near_ties_and_a_change_says_why(monkeypatch, tmp_path):
    state, players, sent, marked, searches = _flow(monkeypatch, tmp_path)
    outbox = common.Outbox(load_settings())
    kelly, kantserov = _move(1, "Parker Kelly", 0.20), _move(2, "Roman Kantserov", 0.21)
    pool = [kelly.add, kantserov.add]
    friday = dt.datetime(2026, 10, 9, 6, tzinfo=dt.timezone.utc)
    # 1. A screenshot, no plan yet: the score and the new plan, then Kelly's card.
    searches.append(([kelly, kantserov], [plan.Planned(kelly, FRIDAY, "spare")], pool))
    weekly.run_plan(state, players, {}, friday, outbox, None, "check")
    assert sent[0].split("\n")[:2] == ["137-163, expect 223-250: win 15%.", "New plan:"]
    assert sent[1].startswith("Make it Fri 9 Oct (today), before that evening's games.\nAdd Parker Kelly (NYR, LW")
    # 2. Another: Kantserov now ranks 1 win-pt higher. The plan holds; no new card.
    searches.append(([kantserov, kelly], [plan.Planned(kantserov, FRIDAY, "now")], pool))
    weekly.run_plan(state, players, {}, friday, outbox, None, "check")
    assert len(sent) == 3 and sent[-1].endswith("Plan unchanged: Parker Kelly for Mattias Samuelsson today (Fri 9 Oct).")
    # 3. The evening: nothing changed, nothing said.
    searches.append(([kantserov, kelly], [plan.Planned(kantserov, FRIDAY, "now")], pool))
    weekly.run_plan(state, players, {}, friday, outbox, None, "quiet")
    assert len(sent) == 3
    # 4. Someone takes Kelly: one change message with the reason, Kelly's card relabelled, a new card.
    searches.append(([kantserov], [plan.Planned(kantserov, FRIDAY, "now")], [kantserov.add]))
    weekly.run_plan(state, players, {}, friday, outbox, None, "quiet")
    assert sent[3].startswith("Plan changed (Parker Kelly for Mattias Samuelsson: someone took him). "
                              "Was: Parker Kelly for Mattias Samuelsson. Now:")
    assert sent[4].split("\n")[1].startswith("Add Roman Kantserov") and len(sent) == 5
    assert marked == [(2, "Replaced: Roman Kantserov for Mattias Samuelsson")]
    assert [m["add"]["name"] for m in state["plan"]["moves"]] == ["Roman Kantserov"]
    assert state["plan"]["moves"][0]["rec_id"] in state["pending"] and len(state["pending"]) == 2  # both scored later


def test_a_move_made_leaves_the_plan_without_a_change_message(monkeypatch, tmp_path):
    state, players, sent, marked, searches = _flow(monkeypatch, tmp_path)
    outbox = common.Outbox(load_settings())
    kelly, other = _move(1, "Parker Kelly", 0.20), _move(2, "Other", 0.12, drop=LINDELL)
    friday = dt.datetime(2026, 10, 9, 6, tzinfo=dt.timezone.utc)
    searches.append(([kelly], [plan.Planned(kelly, FRIDAY, "spare")], [kelly.add, other.add]))
    weekly.run_plan(state, players, {}, friday, outbox, None, "quiet")
    players[:] = [LINDELL, RosterPlayer(1, "Parker Kelly", "NYR", ["LW"], "BN"), players[2]]  # Done in Yahoo
    searches.append(([other], [plan.Planned(other, FRIDAY, "now")], [other.add]))
    weekly.run_plan(state, players, {}, friday, outbox, None, "quiet")
    # A new plan is announced each time there was none open, then its card; no "changed", nothing relabelled.
    assert [m.split("\n")[0 if m.startswith("New") else 1].split(" (")[0] for m in sent] == [
        "New plan:", "Add Parker Kelly", "New plan:", "Add Other"]
    assert marked == []


def test_the_first_plan_relabels_the_earlier_systems_cards_this_week(monkeypatch, tmp_path):
    state, players, sent, marked, searches = _flow(monkeypatch, tmp_path)
    state["pending"]["add-2026-10-09-0609-0"] = {"type": "add", "date": "2026-10-09", "message_id": 292,
                                                 "add": {"id": 2}, "drop": 6}
    state["pending"]["add-2026-09-30-0900-0"] = {"type": "add", "date": "2026-09-30", "message_id": 100,
                                                 "add": {"id": 3}, "drop": 6}  # last week's: left alone
    state["pending"]["add-2026-10-05-0241-0"] = {"type": "add", "date": "2026-10-05", "message_id": 245,
                                                 "add": {"id": 8}, "drop": None}  # made (Core is mine): left alone
    kelly = _move(1, "Parker Kelly", 0.20)
    searches.append(([kelly], [plan.Planned(kelly, FRIDAY, "spare")], [kelly.add]))
    weekly.run_plan(state, players, {}, dt.datetime(2026, 10, 9, 17, tzinfo=dt.timezone.utc),
                    common.Outbox(load_settings()), None, "quiet")
    assert marked == [(292, "Replaced: Parker Kelly for Mattias Samuelsson")]
