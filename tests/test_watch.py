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
    assert [s.kind for s in sig] == ["goalie"]
    assert sig[0].reason == "Igor Shesterkin out for the season"
    assert sig[0].numbers["after"] - sig[0].numbers["before"] >= watch.SHARE_JUMP


def test_dfos_no1_is_flagged_when_the_model_has_him_low(monkeypatch):
    healthy = {"NYR": {"igor shesterkin": LineInfo(groups={"g"}, goalie_depth=2),
                       "dylan garand": LineInfo(groups={"g"}, goalie_depth=1)}}
    assert watch.goalie_signals(Ctx(), [GARAND], healthy, TODAY) == []  # No. 1 all along (a 1A/1B pair)
    assert watch.goalie_signals(Ctx(), [GARAND], healthy, TODAY, {"3": ["g", "g1"]}) == []
    moved = watch.goalie_signals(Ctx(), [GARAND], healthy, TODAY, {"3": ["g", "g2"]})
    assert [(s.kind, s.reason) for s in moved] == [("no1", "DFO has moved him up to the No. 1")]
    assert watch.promotions([GARAND], healthy, {})[1] == {"3": ["g", "g1"]}  # his depth, remembered
    monkeypatch.setattr(availability, "ABSENT_TO_TEAMMATES", 0.0)  # nothing redistributed: no jump
    out = {"NYR": {**LINES["NYR"], "dylan garand": LineInfo(groups={"g"}, goalie_depth=2)}}
    assert watch.goalie_signals(Ctx(), [GARAND], out, TODAY) == []


def test_a_promotion_is_flagged_against_the_groups_seen_last_time():
    first, seen = watch.promotions([SKATER], LINES, {})
    assert first == [] and seen == {"10": ["f2", "pp1"]}  # first sighting: no alert
    same, _ = watch.promotions([SKATER], LINES, seen)
    assert same == []
    moved, _ = watch.promotions([SKATER], LINES, {"10": ["f3", "pp2"]})
    assert [s.reason for s in moved] == ["moved up to the second line and the first power play"]


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
    assert len(sent) == 1 and sent[0].startswith("Watch: Dylan Garand (NYR G), a free agent")
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
