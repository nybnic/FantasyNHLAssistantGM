"""bot/watch.py: the breakout watch flags free agents whose role just changed, once."""
import datetime as dt
from types import SimpleNamespace

import pytest

from bot import messages, watch
from clients.dfo_lines import LineInfo
from clients.injury_news import SEASON
from engine import availability
from league.roster import RosterPlayer

TODAY = dt.date(2026, 10, 13)
NOON = dt.datetime(2026, 10, 13, 10, 0, tzinfo=dt.timezone.utc)  # 13:00 in Helsinki
NIGHT = dt.datetime(2026, 10, 13, 22, 0, tzinfo=dt.timezone.utc)  # 01:00: quiet hours

GARAND = RosterPlayer(3, "Dylan Garand", "NYR", ["G"])
SKATER = RosterPlayer(10, "Will Cuylle", "NYR", ["LW"])


class Ctx:
    """The model context as the watch asks it: Shesterkin started 8 of NYR's
    last 10, Garand 2; Cuylle's ice time jumped in his last 3 games."""

    def __init__(self, toi=(14 * 60,) * 10 + (18 * 60,) * 3):
        self.today = TODAY
        self.team_starts = {"NYR": [(TODAY - dt.timedelta(days=2 * i + 1), 1 if i % 5 else 3) for i in range(10)]}
        self.skater_games = {10: [SimpleNamespace(date=TODAY - dt.timedelta(days=2 * (len(toi) - i)), toi=t)
                                  for i, t in enumerate(toi)]}

    def team_goalies(self, team):
        return [(1, "Igor Shesterkin"), (3, "Dylan Garand")]

    def goalie_missed(self, pid, team):
        return 2 if pid == 1 else 0

    def prior_start_share(self, pid):
        return {1: 0.7, 3: 0.25}.get(pid)


LINES = {"NYR": {"igor shesterkin": LineInfo(groups={"ir"}, injury="out", out_until=SEASON),
                 "dylan garand": LineInfo(groups={"g"}, goalie_depth=1),
                 "will cuylle": LineInfo(groups={"f2", "pp1"})}}


def test_a_backup_whose_starter_is_out_for_the_season_is_flagged():
    sig = watch.goalie_signals(Ctx(), [GARAND], LINES, TODAY)
    assert [(s.kind, s.reason) for s in sig] == [("goalie", "his starter Igor Shesterkin out for the season")]
    assert sig[0].numbers["projected"] == 0.25 and sig[0].numbers["starter_out"]
    assert sig[0].numbers["expected"] - 0.25 >= watch.SHARE_OVER_PROJECTED


def test_a_starters_injury_triggers_at_once_whatever_the_shares(monkeypatch):
    # Out for now with no timeline, and nothing redistributed: the shares alone wouldn't flag him.
    monkeypatch.setattr(availability, "ABSENT_TO_TEAMMATES", 0.0)
    out_now = {"NYR": {"igor shesterkin": LineInfo(groups={"ir"}, injury="out"),
                       "dylan garand": LineInfo(groups={"g"}, goalie_depth=1)}}
    sig = watch.goalie_signals(Ctx(), [GARAND], out_now, TODAY)
    assert [s.reason for s in sig] == ["his starter Igor Shesterkin out (out)"]
    assert sig[0].numbers["expected"] - sig[0].numbers["projected"] < watch.SHARE_OVER_PROJECTED


def test_a_backups_one_start_is_no_watch_but_a_run_of_starts_is():
    healthy = {"NYR": {"igor shesterkin": LineInfo(groups={"g"}, goalie_depth=2),
                       "dylan garand": LineInfo(groups={"g"}, goalie_depth=1)}}  # DFO: Garand starts tonight
    assert watch.goalie_signals(Ctx(), [GARAND], healthy, TODAY) == []  # 2 of the last 10: as projected
    ctx = Ctx()
    ctx.team_starts["NYR"] = [(TODAY - dt.timedelta(days=2 * i + 1), 3 if i < 6 else 1) for i in range(10)]
    sig = watch.goalie_signals(ctx, [GARAND], healthy, TODAY)
    assert [s.reason for s in sig] == ["he has started 6 of his team's last 10"]
    assert sig[0].numbers["expected"] == pytest.approx((4 * 0.25 + 6) / 14)


def test_a_promotion_is_flagged_from_its_first_game():
    first, seen = watch.promotions([SKATER], LINES, {}, TODAY)
    assert first == [] and seen == {"10": {"groups": ["f2", "pp1"], "since": {"f2": None, "pp1": None}}}
    assert watch.promotions([SKATER], LINES, seen, TODAY + dt.timedelta(days=5))[0] == []  # there all along
    moved, seen = watch.promotions([SKATER], LINES, {"10": {"groups": ["f3", "pp2"], "since": {}}}, TODAY)
    assert [(s.reason, s.numbers["days"]) for s in moved] == [
        ("moved up to the second line and the first power play", 0)]
    assert seen["10"]["since"] == {"f2": "2026-10-13", "pp1": "2026-10-13"}
    back = {"NYR": {"will cuylle": LineInfo(groups={"f3", "pp2"})}}
    gone, seen = watch.promotions([SKATER], back, seen, TODAY + dt.timedelta(days=1))
    assert gone == [] and seen["10"]["since"] == {}  # sent back down: a new move up flags again
    assert watch.promotions([SKATER], LINES, {"10": ["f3"]}, TODAY)[0]  # an old record (a list of groups)
    text = messages.watch_text(moved, {})
    assert "DFO moved up to the second line and the first power play.\n" in text


def test_an_ice_time_jump_is_flagged_only_with_enough_games():
    assert [s.kind for s in watch.ice_time_signals(Ctx(), [SKATER])] == ["ice time"]
    assert watch.ice_time_signals(Ctx(toi=(14 * 60,) * 4 + (18 * 60,) * 3), [SKATER]) == []
    assert watch.ice_time_signals(Ctx(toi=(14 * 60,) * 10 + (15 * 60,) * 3), [SKATER]) == []


def _step(monkeypatch, now, state, values=None):
    sent = []
    monkeypatch.setattr(watch, "free_agents", lambda players, league: [GARAND])
    monkeypatch.setattr(watch.nhl_client, "current_teams", lambda: ["NYR"])
    monkeypatch.setattr(watch.dfo_lines, "team_lines", lambda team: LINES[team])
    monkeypatch.setattr(watch, "current_opponent", lambda state, week: "Them")
    outbox = SimpleNamespace(send=lambda text, buttons=None: sent.append(text))
    found = watch.watch_step(state, [SKATER], {}, now, outbox, lambda d: Ctx(),
                             value_moves=lambda *a: values or {})
    return found, sent


def test_an_alert_is_sent_once_and_waits_out_quiet_hours(monkeypatch):
    state = {}
    found, sent = _step(monkeypatch, NIGHT, state)
    assert [s.kind for s in found] == ["goalie"] and sent == []  # quiet hours: still due
    found, sent = _step(monkeypatch, NOON, state, {3: (0.14, 0.093, True)})
    assert len(sent) == 1 and sent[0].startswith("Watch: Dylan Garand (NYR G), a free agent\nHis starter Igor Shesterkin out "
                                                  "for the season. Expected to start 56% of the games ahead, "
                                                  "against 25% projected before the season.")
    assert "+14 win-pts against the 9.3 an add costs: in the plan now." in sent[0]
    assert state["watch"]["alerted"] == {"3:goalie": "2026-10-13"} and state["watch_score"] == []
    _, sent = _step(monkeypatch, NOON + dt.timedelta(hours=1), state)
    assert sent == []  # once


def test_the_watch_text_says_what_the_plan_makes_of_him():
    sig = watch.Signal(SKATER, "ice time", "more ice time", {"recent": 18 * 60 + 40, "usual": 14 * 60 + 10})
    text = messages.watch_text([sig], {10: (0.04, 0.093, False)})
    assert "Ice time 18:40 a game in his last 3, up from 14:10." in text
    assert text.endswith("+4 win-pts against the 9.3 an add costs: not yet worth one.")
    assert messages.watch_text([sig], {}).endswith("passes the plan's checks this week (waivers, or he'd cost points now).")
    assert messages.watch_text([sig], {10: (-0.06, 0.092, False)}).endswith(
        "-6 win-pts against the 9.2 an add costs: not yet worth one.")


def _skater_step(monkeypatch, state, values, signals):
    """The watch with given skater signals, valued by `values` (id -> (value, price, in the plan))."""
    sent = []
    monkeypatch.setattr(watch, "free_agents", lambda players, league: [SKATER])
    monkeypatch.setattr(watch.nhl_client, "current_teams", lambda: ["NYR"])
    monkeypatch.setattr(watch.dfo_lines, "team_lines", lambda team: LINES[team])
    monkeypatch.setattr(watch, "current_opponent", lambda state, week: "Them")
    monkeypatch.setattr(watch, "goalie_signals", lambda *a: [])
    monkeypatch.setattr(watch, "promotions", lambda *a: ([s for s in signals if s.kind == "promoted"], {}))
    monkeypatch.setattr(watch, "ice_time_signals", lambda *a: [s for s in signals if s.kind == "ice time"])
    scored = []
    outbox = SimpleNamespace(send=lambda text, buttons=None: sent.append(text))
    found = watch.watch_step(state, [GARAND], {}, NOON, outbox, lambda d: Ctx(),
                             value_moves=lambda st, *a: scored.append(list(st["watch_score"])) or values)
    return found, sent, scored


PROMOTED = watch.Signal(SKATER, "promoted", "moved up to the second line", {"groups": ["f2"], "days": 0})
MORE_ICE = watch.Signal(SKATER, "ice time", "more ice time", {"recent": 18 * 60, "usual": 14 * 60})


def test_a_promotion_under_half_the_add_price_is_logged_not_sent(monkeypatch):
    state = {"watch": {"groups": {}, "alerted": {}}}
    found, sent, scored = _skater_step(monkeypatch, state, {10: (0.04, 0.093, False)}, [PROMOTED])
    assert found == [] and sent == [] and scored == [[10]]
    assert state["watch"]["alerted"] == {"10:promoted": "2026-10-13"}  # not valued again
    [row] = state["watch"]["log"]
    assert (row["sent"], row["kinds"], row["value"]) == (False, ["promoted"], 0.04)
    assert row["why"] == "DFO moved up to the second line." and row["verdict"].endswith("not yet worth one.")
    assert watch.watch_view(state, TODAY) == [row]
    assert watch.watch_view(state, TODAY + dt.timedelta(days=watch.LOG_DAYS)) == []


def test_a_promotion_worth_half_the_add_price_or_in_the_plan_is_sent(monkeypatch):
    for value in [(0.05, 0.093, False), (0.01, 0.093, True)]:
        state = {"watch": {"groups": {}, "alerted": {}}}
        found, sent, _ = _skater_step(monkeypatch, state, {10: value}, [PROMOTED])
        assert found == [PROMOTED] and len(sent) == 1 and state["watch"]["log"][0]["sent"]
    state = {"watch": {"groups": {}, "alerted": {}}}
    assert _skater_step(monkeypatch, state, {}, [PROMOTED])[1] == []  # no move with him passes: logged


def test_an_ice_time_jump_is_sent_whatever_its_value_with_his_promotion_in_one_alert(monkeypatch):
    state = {"watch": {"groups": {}, "alerted": {}}}
    found, sent, _ = _skater_step(monkeypatch, state, {10: (-0.02, 0.093, False)}, [PROMOTED, MORE_ICE])
    assert found == [PROMOTED, MORE_ICE] and len(sent) == 1
    assert sent[0].count("Watch: Will Cuylle") == 1
    assert "DFO moved up to the second line.\nIce time 18:00 a game" in sent[0]
    assert [r["kinds"] for r in state["watch"]["log"]] == [["promoted", "ice time"]]


def test_a_player_alerted_for_one_kind_waits_out_the_realert_days_for_another():
    alerted = {"10:promoted": "2026-10-10"}
    assert watch.due([MORE_ICE], alerted, TODAY) == []
    assert watch.due([MORE_ICE], alerted, dt.date(2026, 10, 24)) == [MORE_ICE]
