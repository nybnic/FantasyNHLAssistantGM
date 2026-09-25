"""Best-effort scraper for DailyFaceoff's public starting-goalies page.

DailyFaceoff has no official API and no affiliation with the NHL, but it's
the only free source that publishes likely/confirmed starters ahead of the
official NHL lineup, which is often posted close to game time. The page is
a Next.js app that server-renders its data into a `__NEXT_DATA__` JSON
script tag - parsing that is far more stable than scraping rendered
HTML/CSS, since it doesn't depend on class names or layout.

IMPORTANT: the exact shape of each entry in that JSON was NOT observed with
live in-season data during development (checked in the 2026 preseason lull,
when the feed was empty - `data: []`). `_parse_entry` below is a best-effort
guess at common Next.js/DailyFaceoff field naming; verify it against real
data on the first dry run once games are actually being confirmed, and
adjust the key names it tries if needed. It fails soft (logs and returns
nothing) rather than raising, so a schema drift degrades this one signal
instead of crashing the whole pipeline.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

import requests

URL = "https://www.dailyfaceoff.com/starting-goalies/{date}"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
logger = logging.getLogger(__name__)


class GoalieScrapeError(RuntimeError):
    pass


def _fetch_raw(date: dt.date) -> list:
    resp = requests.get(
        URL.format(date=date.isoformat()),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    match = _NEXT_DATA_RE.search(resp.text)
    if not match:
        raise GoalieScrapeError("__NEXT_DATA__ block not found on DailyFaceoff page")
    payload = json.loads(match.group(1))
    return payload["props"]["pageProps"]["data"]


def _parse_entry(entry: dict) -> list[dict]:
    """Extract {team, goalie_name, confirmed} dicts from one raw game entry."""
    results = []
    for side in ("awayTeam", "homeTeam", "away_team", "home_team"):
        team = entry.get(side)
        if not isinstance(team, dict):
            continue
        goalie = (
            team.get("goalie")
            or team.get("confirmedGoalie")
            or team.get("startingGoalie")
        )
        abbrev = (
            team.get("abbreviation")
            or team.get("abbrev")
            or team.get("teamAbbreviation")
        )
        if not (goalie and abbrev):
            continue
        name = goalie.get("name") if isinstance(goalie, dict) else goalie
        confirmed = bool(goalie.get("isConfirmed")) if isinstance(goalie, dict) else False
        if name:
            results.append({"team": abbrev, "goalie_name": name, "confirmed": confirmed})
    return results


def get_starters(date: dt.date) -> dict[str, dict]:
    """Return {team_triCode: {"goalie_name": ..., "confirmed": bool}} for `date`.

    Returns an empty dict (logged, not raised) if DailyFaceoff can't be
    reached or parsed, so callers should treat this signal as optional.
    """
    try:
        raw_entries = _fetch_raw(date)
    except Exception:
        logger.warning("DailyFaceoff starting-goalie fetch failed for %s", date, exc_info=True)
        return {}

    starters: dict[str, dict] = {}
    for entry in raw_entries:
        for parsed in _parse_entry(entry):
            starters[parsed["team"]] = {
                "goalie_name": parsed["goalie_name"],
                "confirmed": parsed["confirmed"],
            }

    if raw_entries and not starters:
        logger.warning(
            "DailyFaceoff returned %d entries for %s but none parsed - its "
            "JSON schema may have changed; check _parse_entry in "
            "clients/goalie_client.py",
            len(raw_entries), date,
        )
    return starters
