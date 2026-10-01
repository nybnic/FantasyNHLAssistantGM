"""Disk cache and a retrying GET shared by the hockey-data clients."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import requests

from clients import health

CACHE_DIR = Path("data/cache/gm")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
HOUR = 3600
DAY = 24 * HOUR
# When a refetch fails, a copy up to this old beats failing the run (a lost
# weekly plan) or a silent fallback (DFO's injuries gone: everyone healthy).
# Judgment call: schedules and rosters barely change in two days.
STALE_LIMIT = 2 * DAY
logger = logging.getLogger(__name__)


def cached_json(name: str, max_age: float | None, fetch: Callable[[], Any]) -> Any:
    """Cached JSON for `name`, refetched when older than `max_age` seconds.
    `max_age=None` never expires (completed seasons don't change). If the
    refetch fails, a copy younger than STALE_LIMIT is used instead."""
    path = CACHE_DIR / f"{name}.json"
    age = time.time() - path.stat().st_mtime if path.exists() else None
    if age is not None and (max_age is None or age < max_age):
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = fetch()
    except Exception:
        if age is None or age >= STALE_LIMIT:
            raise
        logger.warning("Refetching %s failed; using the cached copy from %.1f h ago", name, age / HOUR,
                       exc_info=True)
        health.report(name, f"down, using a copy {age / HOUR:.0f} h old")
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")
