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

    last_results: dict = {}

    def goalie_start(self, pid, team, opponent, home):
        return {"xfp": 6.0 if home else 5.0}

    def games_missed(self, pid, team):
        return 0


def _schedule():
    from clients.nhl_client import ScheduledGame
    sat, sun = dt.date(2026, 10, 3), dt.date(2026, 10, 4)
    at = dt.datetime(2026, 10, 3, 23, 0, tzinfo=dt.timezone.utc)
    return {sat: [ScheduledGame(1, at, "CHI", "SJS")],
            sun: [ScheduledGame(2, at + dt.timedelta(days=1), "NSH", "CHI")]}


NHL = [{"id": 1, "name": "Mine", "team": "SJS", "position": "C"},
       {"id": 2, "name": "Theirs", "team": "NSH", "position": "D"},
       {"id": 3, "name": "Free", "team": "PIT", "position": "L"},
       {"id": 30, "name": "Knight", "team": "CHI", "position": "G"}]


def test_each_week_logs_every_nhl_player_once_with_his_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(xfp_log.nhl_client, "current_rosters", lambda: NHL)
    monkeypatch.setattr(xfp_log, "week_inputs", lambda date, week: (_schedule(), {}, {}))
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
    assert f",{round((4 * 0.6 + 1) / 5, 3)},0.9," in lines[4]  # goalie: start share, save %
    assert state["xfp_logged"] == 1


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(xfp_log.nhl_client, "current_rosters", lambda: NHL)
    monkeypatch.setattr(xfp_log, "week_inputs", lambda date, week: (_schedule(), {}, {}))
    state = {"xfp_logged": None}
    xfp_log.log_step(state, [], {"teams": {}}, NOW, lambda d: _Ctx(), dry_run=True, path=tmp_path / "x.csv")
    assert not (tmp_path / "x.csv").exists() and state["xfp_logged"] is None


def test_each_player_logs_his_expected_week_and_a_goalie_his_points_per_start(tmp_path, monkeypatch):
    import csv
    monkeypatch.setattr(xfp_log.nhl_client, "current_rosters", lambda: NHL)
    monkeypatch.setattr(xfp_log, "week_inputs", lambda date, week: (_schedule(), {}, {}))
    path = tmp_path / "xfp_log.csv"
    path.write_text("week,date,id\n0,2026-09-29,7\n", encoding="utf-8")  # an older header
    xfp_log.log_step({"xfp_logged": None}, [], {"teams": {}}, NOW, lambda d: _Ctx(), dry_run=False, path=path)
    rows = {r["name"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}
    assert list(rows) == ["", "Mine", "Theirs", "Free", "Knight"]  # the old row kept, under the new header
    assert (rows["Mine"]["week_games"], rows["Free"]["week_games"]) == ("1", "0")
    assert 0 < float(rows["Mine"]["week_xfp"]) <= 2.5  # one game, at his odds of playing
    knight = rows["Knight"]
    assert knight["week_games"] == "2" and knight["xfp_start"] == "5.5"  # home 6, away 5
    assert 0 < float(knight["week_xfp"]) < 11.0  # each game at his odds of starting
