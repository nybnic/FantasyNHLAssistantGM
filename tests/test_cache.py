import os
import time

import pytest

from clients import cache


def _cached(tmp_path, monkeypatch, hours_old: float):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    path = tmp_path / "schedule.json"
    path.write_text('["old"]', encoding="utf-8")
    then = time.time() - hours_old * cache.HOUR
    os.utime(path, (then, then))


def _down():
    raise ConnectionError("NHL API down")


def test_a_failed_refetch_falls_back_to_a_recent_copy(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch, hours_old=5)
    assert cache.cached_json("schedule", 3 * cache.HOUR, _down) == ["old"]
    assert cache.cached_json("schedule", 3 * cache.HOUR, lambda: ["new"]) == ["new"]  # and refreshes when it can


def test_a_failed_refetch_with_only_an_old_copy_still_fails(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch, hours_old=49)
    with pytest.raises(ConnectionError):
        cache.cached_json("schedule", 3 * cache.HOUR, _down)


def test_a_failed_first_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    with pytest.raises(ConnectionError):
        cache.cached_json("schedule", 3 * cache.HOUR, _down)


def test_the_latest_stats_window_is_refetched_hourly(monkeypatch):
    """A plan run after the last puck fetches a night whose games may not all
    be in: that fetch mustn't stand for 6 hours (bot.common.nhl_date)."""
    import datetime as dt
    from clients import nhl_stats
    ages = []
    monkeypatch.setattr(nhl_stats, "cached_json", lambda name, max_age, fetch: ages.append(max_age) or [])
    today = dt.date(2026, 10, 15)
    for end in (today, today - dt.timedelta(days=1), today - dt.timedelta(days=2), today - dt.timedelta(days=5)):
        nhl_stats._report_rows("skater/summary", 20262027, end - dt.timedelta(days=19), end, today)
    assert ages == [cache.HOUR, cache.HOUR, 6 * cache.HOUR, None]


def _answers(monkeypatch, *statuses):
    calls = []

    def fake_get(*a, **k):
        resp = cache.requests.Response()
        resp.status_code = statuses[len(calls)]
        calls.append(resp.status_code)
        return resp
    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    return calls


def test_a_server_error_is_tried_again(monkeypatch):
    calls = _answers(monkeypatch, 503, 200)
    assert cache.get("https://example.com").status_code == 200
    assert calls == [503, 200]


def test_a_refusal_is_not_tried_again(monkeypatch):
    """A 403 (DFO's bot check, 2026-11-04) won't pass in 6 s."""
    calls = _answers(monkeypatch, 403, 200)
    with pytest.raises(cache.requests.HTTPError):
        cache.get("https://example.com")
    assert calls == [403]
