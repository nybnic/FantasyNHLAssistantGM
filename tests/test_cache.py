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
