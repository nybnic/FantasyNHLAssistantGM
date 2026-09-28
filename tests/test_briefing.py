import datetime as dt
from dataclasses import dataclass

import pytest

from clients.dfo_lines import LineInfo
from clients.nhl_client import ScheduledGame
from engine import briefing
from league.roster import RosterPlayer

UTC = dt.timezone.utc
DATE = dt.date(2026, 11, 10)
EVENING_GAME = ScheduledGame(1, dt.datetime(2026, 11, 11, 0, 0, tzinfo=UTC), home="TOR", away="BOS")  # 19:00 ET
MATINEE = ScheduledGame(2, dt.datetime(2026, 11, 10, 17, 0, tzinfo=UTC), home="NYR", away="PHI")  # 12:00 ET


def test_briefing_at_21_local_unless_a_game_starts_earlier():
    assert briefing.briefing_due(DATE, EVENING_GAME.start) == dt.datetime(2026, 11, 10, 21, 0, tzinfo=briefing.LOCAL)
    # Matinee at 19:00 Helsinki -> briefing an hour before.
    assert briefing.briefing_due(DATE, MATINEE.start) == dt.datetime(2026, 11, 10, 18, 0, tzinfo=briefing.LOCAL)


def test_quiet_hours():
    assert briefing.quiet(dt.datetime(2026, 11, 10, 23, 30, tzinfo=briefing.LOCAL))
    assert not briefing.quiet(dt.datetime(2026, 11, 10, 21, 0, tzinfo=briefing.LOCAL))


@dataclass
class _Proj:
    xfp: float


class FakeContext:
    team_starts: dict = {}

    def skater(self, pid, position):
        return _Proj({1: 6.0, 2: 3.0, 3: 5.0}.get(pid, 2.0))

    def goalie_start(self, pid, team, opp, home):
        return {"xfp": 9.0}

    def prior_start_share(self, pid):
        return 0.8


def _roster(slots):
    return [
        RosterPlayer(1, "Top Center", "BOS", ["C"], slots[0]),
        RosterPlayer(2, "Depth Center", "NJD", ["C"], slots[1]),
        RosterPlayer(3, "Bench Center", "TOR", ["C"], slots[2]),
        RosterPlayer(4, "Goalie", "TOR", ["G"], slots[3]),
    ]


def _plan(roster, now, lines=None):
    return briefing.plan(roster, FakeContext(), DATE, [EVENING_GAME], lines or {}, {}, now)


def test_plan_starts_bench_player_with_a_game_over_one_without():
    now = dt.datetime(2026, 11, 10, 21, 0, tzinfo=briefing.LOCAL)
    result = _plan(_roster(["C", "C", "BN", "G"]), now)
    # NJD doesn't play: its center goes to the bench for the TOR one.
    assert result.optimal[2] == "BN" and result.optimal[3] == "C"
    assert result.gain == pytest.approx(0.97 * 5.0)
    text = briefing.text(result)
    assert "Start C   Bench Center @" not in text  # home team TOR
    assert "Start C   Bench Center vs BOS" in text
    assert "Bench C   Depth Center no game" in text


def test_injured_player_is_benched_when_someone_can_replace_him():
    now = dt.datetime(2026, 11, 10, 21, 0, tzinfo=briefing.LOCAL)
    roster = _roster(["C", "C", "BN", "G"])
    roster[1].team = "TOR"  # Depth Center plays tonight too
    lines = {
        "BOS": {"top center": LineInfo(groups={"ir"}, injury="out")},
        "TOR": {"bench center": LineInfo(groups={"f2"}), "depth center": LineInfo(groups={"f3"})},
    }
    result = _plan(roster, now, lines)
    assert result.optimal == {1: "BN", 2: "C", 3: "C", 4: "G"}
    assert "Bench C   Top Center @ TOR, IR  0.0" in briefing.text(result)


def test_unknown_lineup_lists_everything():
    now = dt.datetime(2026, 11, 10, 21, 0, tzinfo=briefing.LOCAL)
    result = _plan(_roster([None] * 4), now)
    assert result.current is None
    assert briefing.text(result).startswith("Set this lineup for tonight")


def test_players_in_started_games_are_locked():
    now = EVENING_GAME.start + dt.timedelta(minutes=5)
    # Bench Center (TOR) already playing on the bench: stays there.
    result = _plan(_roster(["C", "C", "BN", "G"]), now)
    assert result.optimal[3] == "BN"
