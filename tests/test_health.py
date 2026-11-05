import datetime as dt
import os
import time

from bot import common, daily
from notify import telegram
from clients import cache, health
from config.settings import load_settings
from state import gm_state

NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)


def _outbox(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(a[2]) or 1)
    return common.Outbox(load_settings()), sent


def test_a_broken_dailyfaceoff_fetch_shows_in_telegram_once_a_day(monkeypatch, tmp_path):
    outbox, sent = _outbox(monkeypatch)
    state = gm_state.load(tmp_path / "s.json")
    health.clear()

    def team_lines(team):
        raise ConnectionError("DFO down")
    for team in ("BOS", "TOR"):
        assert common._safe(team_lines, team, default={}) == {}
    common._safe(lambda: 1 / 0)  # not data: a chart, say
    daily.alert_health(state, outbox, NOW)
    daily.alert_health(state, outbox, NOW)  # same day: once
    assert sent == ["Data check:\nDailyFaceoff line charts: failed (ConnectionError) (2x). "
                    "Meanwhile injuries and scratches unknown, so those players count as healthy."]
    health.clear()


def test_an_old_cached_copy_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    path = tmp_path / "schedule" / "2026-10-01.json"
    path.parent.mkdir()
    path.write_text("[]", encoding="utf-8")
    then = time.time() - 5 * cache.HOUR
    os.utime(path, (then, then))
    health.clear()

    def down():
        raise ConnectionError
    cache.cached_json("schedule/2026-10-01", 3 * cache.HOUR, down)
    assert health.problems() == {"NHL schedule": ["down, using a copy 5 h old"]}
    health.clear()


def _stale(monkeypatch, tmp_path, name, hours_old, max_age=cache.HOUR / 2):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    path = tmp_path / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    then = time.time() - hours_old * cache.HOUR
    os.utime(path, (then, then))
    health.clear()

    def down():
        raise ConnectionError
    cache.cached_json(name, max_age, down)
    problems = health.problems()
    health.clear()
    return problems


def test_one_missed_refresh_is_not_reported(monkeypatch, tmp_path):
    """2026-11-04: DFO's starters 403'd once; the copy used was 31 min old (of 30),
    and the data check said "1 h old"."""
    assert _stale(monkeypatch, tmp_path, "dfo_starters/2026-11-04", 31 / 60) == {}


def test_a_copy_an_hour_overdue_is_reported(monkeypatch, tmp_path):
    starters = "dfo_starters/2026-11-04"
    assert _stale(monkeypatch, tmp_path, starters, 2) == {"DailyFaceoff starting goalies": ["down, using a copy 2 h old"]}
    # In the goalie news hours it's read fresh every run (a 60 s max_age): in minutes then.
    assert _stale(monkeypatch, tmp_path, starters, 70 / 60, max_age=60) == {
        "DailyFaceoff starting goalies": ["down, using a copy 70 min old"]}


def test_stale_line_charts_are_reported(monkeypatch, tmp_path):
    """The cache name is dfo_lines_v2/ since 2026-10-13; health knew only dfo_lines/."""
    assert health.source_of("dfo_lines_v2/BOS") == "DailyFaceoff line charts"
