import datetime as dt
from dataclasses import dataclass

from bot import xfp_log
from league.roster import RosterPlayer

NOW = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.timezone.utc)  # week 1


@dataclass
class _Proj:
    xfp: float = 2.5
    toi: float = 1080.0
    pp_toi: float = 120.0
    games: int = 3


class _League:
    save_pct = 0.9


class _Ctx:
    today = NOW.date()
    team_starts = {"CHI": [(dt.date(2026, 10, 1), 30)]}
    goalie_games: dict = {}
    goalie_history: dict = {}
    league = _League()

    def skater(self, pid, group):
        return _Proj()

    def prior_start_share(self, pid):
        return 0.6

    def durability(self, pid):
        return 0.95


NHL = [{"id": 1, "name": "Mine", "team": "SJS", "position": "C"},
       {"id": 2, "name": "Theirs", "team": "NSH", "position": "D"},
       {"id": 3, "name": "Free", "team": "PIT", "position": "L"},
       {"id": 30, "name": "Knight", "team": "CHI", "position": "G"}]


def test_each_week_logs_every_nhl_player_once_with_his_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(xfp_log.nhl_client, "current_rosters", lambda: NHL)
    path = tmp_path / "xfp_log.csv"
    state = {"xfp_logged": None}
    mine = [RosterPlayer(1, "Mine", "SJS", ["C"])]
    league = {"teams": {"Retrot Chicken Wings": {"players": [{"id": 2}]}}}
    xfp_log.log_step(state, mine, league, NOW, lambda d: _Ctx(), dry_run=False, path=path)
    xfp_log.log_step(state, mine, league, NOW + dt.timedelta(hours=1), lambda d: _Ctx(), dry_run=False, path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(xfp_log.COLUMNS) and len(lines) == 1 + len(NHL)  # once per week
    assert lines[1].startswith("1,2026-10-03,1,Mine,SJS,C,me,2.5,18.0,2.0,3,0.95")
    assert ",Retrot Chicken Wings," in lines[2] and ",FA," in lines[3]
    assert lines[4].endswith(f",{round((4 * 0.6 + 1) / 5, 3)},0.9")  # goalie: start share, save %
    assert state["xfp_logged"] == 1


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(xfp_log.nhl_client, "current_rosters", lambda: NHL)
    state = {"xfp_logged": None}
    xfp_log.log_step(state, [], {"teams": {}}, NOW, lambda d: _Ctx(), dry_run=True, path=tmp_path / "x.csv")
    assert not (tmp_path / "x.csv").exists() and state["xfp_logged"] is None
