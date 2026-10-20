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


def _goalie_move(add_id, name, team, value, drop=SAMUELSSON):
    m = _move(add_id, name, value, drop=drop)
    return matchup.Move(RosterPlayer(add_id, name, team, ["G"]), drop, m.week_gain, m.long_term, 0.0, 2,
                        m.win_before, m.win_after, later_weight=m.later_weight)


def test_a_goalie_add_worth_it_only_if_he_starts_tonight_waits_for_the_confirmation():
    # 2026-10-20: Kuemper for Pinto in the plan at 70% to start tonight; Woll for Pinto next best.
    kuemper, woll = _goalie_move(30, "Darcy Kuemper", "LAK", 0.44), _goalie_move(40, "Joseph Woll", "PHI", 0.37)
    teammate = _goalie_move(31, "Anton Forsberg", "LAK", 0.40)  # his partner is no alternative
    skater = _move(5, "Viktor Arvidsson", 0.11, drop=LINDELL)
    tonight = {FRIDAY: [SimpleNamespace(home="WSH", away="LAK")]}
    plan_ = [plan.Planned(kuemper, FRIDAY, "now"), plan.Planned(skater, FRIDAY, "now")]
    ranked = [kuemper, teammate, woll, skater]
    partner = lambda team, gid: "Anton Forsberg"

    def run(if_not, starters=None, when=FRIDAY, ranked=ranked):
        return plan.starter_conditions([plan.Planned(p.move, when, p.why) for p in plan_], ranked,
                                       lambda m, name: if_not, partner, starters or {}, FRIDAY, tonight, PRICE)
    out = run(0.30)  # if Forsberg starts, Kuemper's 0.30 is under Woll's 0.37
    c = out[0].condition
    assert (c.team, c.goalie, c.other, c.alt, c.if_not) == ("LAK", "Darcy Kuemper", "Anton Forsberg", woll, 0.30)
    assert out[1].condition is None  # a skater: nothing to wait for
    assert run(0.38)[0].condition is None  # the move either way
    assert run(0.30, {"LAK": {"goalie_name": "Darcy Kuemper", "confirmed": True}})[0].condition is None  # known
    assert run(0.30, when=FRIDAY + dt.timedelta(days=1))[0].condition is None  # not tonight's question
    # No alternative for the drop: only if he starts, else no add (under the price).
    alone = run(0.05, ranked=[kuemper])[0].condition
    assert alone.alt is None and alone.if_not == 0.05
    assert run(0.12, ranked=[kuemper])[0].condition is None  # still worth an add if he sits


def test_a_conditional_card_says_when_and_a_confirmation_the_other_way_changes_the_plan(monkeypatch, tmp_path):
    from bot import messages
    state, players, sent, marked, searches = _flow(monkeypatch, tmp_path)
    players.append(RosterPlayer(9, "Shane Pinto", "OTT", ["C"], "BN"))
    pinto = players[-1]
    kuemper, woll = _goalie_move(30, "Darcy Kuemper", "LAK", 0.44, pinto), _goalie_move(40, "Joseph Woll", "PHI", 0.37, pinto)
    cond = plan.StarterCondition("LAK", "Darcy Kuemper", "Anton Forsberg", woll, 0.30)
    conditional = plan.Planned(kuemper, FRIDAY, "now", "", cond)
    assert messages.make_it(conditional, FRIDAY) == (
        "Make it Fri 9 Oct (today), once LAK confirms Darcy Kuemper in net tonight (DailyFaceoff, usually after "
        "the morning skate). If Anton Forsberg starts: Joseph Woll for Shane Pinto instead.")
    assert messages.plan_lines([conditional], FRIDAY, 2)[0].endswith(
        "; only once LAK confirms Darcy Kuemper starts tonight; if Anton Forsberg starts, Joseph Woll for Shane Pinto instead).")
    starters = {}
    real = weekly.plan_moves

    def plan_moves(*a, **k):
        p = real(*a, **k)
        p.wk.starters = dict(starters)
        return p
    monkeypatch.setattr(weekly, "plan_moves", plan_moves)
    outbox = common.Outbox(load_settings())
    morning = dt.datetime(2026, 10, 9, 6, tzinfo=dt.timezone.utc)
    pool = [kuemper.add, woll.add]
    searches.append(([kuemper, woll], [conditional], pool))
    weekly.run_plan(state, players, {}, morning, outbox, None, "quiet")
    assert sent[1].startswith("Make it Fri 9 Oct (today), once LAK confirms Darcy Kuemper in net tonight")
    assert state["plan"]["moves"][0]["if_starts"] == {"team": "LAK", "goalie": "Darcy Kuemper",
                                                      "other": "Anton Forsberg"}
    # The afternoon: Forsberg confirmed. Woll is worth less than Kuemper was, but Kuemper was only if he started.
    starters["LAK"] = {"goalie_name": "Anton Forsberg", "confirmed": True}
    kuemper_now = _goalie_move(30, "Darcy Kuemper", "LAK", 0.36, pinto)
    searches.append(([woll, kuemper_now], [plan.Planned(woll, FRIDAY, "now")], pool))
    weekly.run_plan(state, players, {}, morning + dt.timedelta(hours=9), outbox, None, "quiet")
    assert sent[2].startswith("Plan changed (Darcy Kuemper for Shane Pinto: Anton Forsberg starts for LAK tonight). "
                              "Was: Darcy Kuemper for Shane Pinto. Now:\nFri 9 Oct (today): Joseph Woll for Shane Pinto")
    assert [m["add"]["name"] for m in state["plan"]["moves"]] == ["Joseph Woll"]


def test_a_confirmation_his_way_says_go_once(monkeypatch, tmp_path):
    state, players, sent, marked, searches = _flow(monkeypatch, tmp_path)
    kuemper, woll = _goalie_move(30, "Darcy Kuemper", "LAK", 0.44), _goalie_move(40, "Joseph Woll", "PHI", 0.37)
    cond = plan.StarterCondition("LAK", "Darcy Kuemper", "Anton Forsberg", woll, 0.30)
    starters = {}
    real = weekly.plan_moves

    def plan_moves(*a, **k):
        p = real(*a, **k)
        p.wk.starters = dict(starters)
        return p
    monkeypatch.setattr(weekly, "plan_moves", plan_moves)
    outbox = common.Outbox(load_settings())
    morning = dt.datetime(2026, 10, 9, 6, tzinfo=dt.timezone.utc)
    pool = [kuemper.add, woll.add]
    searches.append(([kuemper, woll], [plan.Planned(kuemper, FRIDAY, "now", "", cond)], pool))
    weekly.run_plan(state, players, {}, morning, outbox, None, "quiet")
    starters["LAK"] = {"goalie_name": "Darcy Kuemper", "confirmed": True}
    for hours in (8, 9):  # confirmed: the plan holds, now unconditional; said once
        searches.append(([kuemper, woll], [plan.Planned(kuemper, FRIDAY, "now")], pool))
        weekly.run_plan(state, players, {}, morning + dt.timedelta(hours=hours), outbox, None, "quiet")
    assert sent[2:] == ["Darcy Kuemper is confirmed in LAK's net tonight: make Darcy Kuemper for Mattias Samuelsson now."]
    assert "if_starts" not in state["plan"]["moves"][0]


def test_a_move_the_new_plan_keeps_is_judged_at_the_plans_value():
    # Arvidsson is worth 9.1 on today's roster but 12 once the plan's goalie add is made.
    goalie, other_goalie = _goalie_move(30, "Darcy Kuemper", "LAK", 0.44), _goalie_move(31, "Leevi Merilainen", "VAN", 0.47)
    arvidsson_today, arvidsson_after = _move(5, "Viktor Arvidsson", 0.091, drop=LINDELL), _move(5, "Viktor Arvidsson", 0.12, drop=LINDELL)
    new = [plan.Planned(other_goalie, FRIDAY, "now"), plan.Planned(arvidsson_after, FRIDAY, "now")]
    current = {plan.key(goalie): goalie, plan.key(other_goalie): other_goalie, plan.key(arvidsson_today): arvidsson_today}
    verdict = plan.decide([_stored(goalie), _stored(arvidsson_today)], new, current, {}, PRICE)
    assert verdict == plan.Verdict(False, "the new plan is better by 3 win-pts")  # not "no longer worth an add"
