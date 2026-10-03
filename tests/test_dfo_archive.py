import datetime as dt
from dataclasses import dataclass

from bot import dfo_archive
from bot.common import NHL_TIME

DAY = dt.date(2026, 10, 6)


@dataclass
class _Game:
    start: dt.datetime


def _at(hour, minute=0):
    return dt.datetime.combine(DAY, dt.time(hour, minute), NHL_TIME)


def test_due_after_morning_skates_or_before_an_early_first_puck():
    evening = [_Game(_at(19))]
    assert not dfo_archive.due(_at(12, 30), evening, None)
    assert dfo_archive.due(_at(13), evening, None)
    assert not dfo_archive.due(_at(14), evening, DAY.isoformat())  # once a day
    assert dfo_archive.due(_at(12, 30), [_Game(_at(12, 30))], None)  # a matinee: 30 min before
    assert not dfo_archive.due(_at(15), [], None)  # no games


NHL = [{"id": 1, "name": "Anton Lundell", "team": "FLA", "position": "C"},
       {"id": 2, "name": "Sam Reinhart", "team": "FLA", "position": "C"}]
CHART = [{"name": "Anton Lundell", "group": "f1", "slot": "c1", "injury": None, "gtd": False},
         {"name": "Anton Lundell", "group": "pp1", "slot": "pp1c", "injury": None, "gtd": False},
         {"name": "Aleksander Barkov", "group": "ir", "slot": "ir1", "injury": "ir", "gtd": False}]


def test_archives_every_chart_row_with_nhl_ids_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(dfo_archive.nhl_client, "games_on", lambda d: [_Game(_at(19))])
    monkeypatch.setattr(dfo_archive.nhl_client, "current_rosters", lambda: NHL * 8 + [
        {"id": 100 + i, "name": "X", "team": f"T{i:02d}", "position": "C"} for i in range(16)])
    monkeypatch.setattr(dfo_archive.dfo_lines, "rows", lambda team: CHART)
    (tmp_path / ".git").mkdir()
    state = {"dfo_archived": None}
    dfo_archive.archive_step(state, _at(13), dry_run=False, root=tmp_path)
    lines = (tmp_path / "dfo_lines" / "2026" / "2026-10-06.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(dfo_archive.COLUMNS)
    assert "2026-10-06,FLA,Anton Lundell,1,pp1,pp1c,,0" in lines
    assert "2026-10-06,FLA,Aleksander Barkov,,ir,ir1,ir,0" in lines  # not on the NHL roster: no id
    assert state["dfo_archived"] == "2026-10-06"


def test_without_the_private_checkout_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.setattr(dfo_archive.nhl_client, "games_on", lambda d: (_ for _ in ()).throw(AssertionError))
    state = {"dfo_archived": None}
    dfo_archive.archive_step(state, _at(13), dry_run=False, root=tmp_path)
    assert state["dfo_archived"] is None and not any(tmp_path.iterdir())


def test_a_mostly_failed_fetch_isnt_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(dfo_archive.nhl_client, "games_on", lambda d: [_Game(_at(19))])
    monkeypatch.setattr(dfo_archive.nhl_client, "current_rosters", lambda: NHL)
    monkeypatch.setattr(dfo_archive.dfo_lines, "rows", lambda team: CHART)
    (tmp_path / ".git").mkdir()
    state = {"dfo_archived": None}
    dfo_archive.archive_step(state, _at(13), dry_run=False, root=tmp_path)
    assert state["dfo_archived"] is None and not (tmp_path / "dfo_lines").exists()


def test_an_archive_the_bot_cant_push_to_is_reported_in_the_data_check(tmp_path, monkeypatch):
    from clients import health
    (tmp_path / ".git").mkdir()
    health.clear()
    monkeypatch.setenv("ARCHIVE_WRITABLE", "true")
    assert dfo_archive.writable(tmp_path) and not health.problems()
    monkeypatch.setenv("ARCHIVE_WRITABLE", "false")
    assert not dfo_archive.writable(tmp_path)
    assert "deploy key needs write access" in health.problems()["Data archive"][0]
    health.clear()
