"""DailyFaceoff's starting goalies: who's in net tonight, before the NHL says.

No official free source names starters ahead of game time, so this reads
dailyfaceoff.com/starting-goalies/{date}. The page embeds __NEXT_DATA__ JSON;
props.pageProps.data has one flat entry per game (verified on archived
2025-26 dates): homeTeamName / awayTeamName (full names), homeGoalieName,
homeNewsStrengthName ("Confirmed", or None once no news was attached), and
the same for away. Labels other than "Confirmed" only appear during the day;
"Likely" ones count as likely starters, anything else is ignored so the
start-share model decides.

Fails soft: any fetch/parse problem returns {} and logs, so this one signal
can't break a run.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

from clients import dfo_lines, health
from clients.cache import HOUR, cached_json, get
from clients.names import normalize_name

URL = "https://www.dailyfaceoff.com/starting-goalies/{date}"
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
logger = logging.getLogger(__name__)


def _team_codes() -> dict[str, str]:
    """Normalized DFO team name -> NHL code ('utah mammoth' -> 'UTA')."""
    return {normalize_name(t["name"]): t["code"] for t in dfo_lines.teams()}


def parse_entries(entries: list[dict], team_codes: dict[str, str]) -> dict[str, dict]:
    starters = {}
    for entry in entries:
        for side in ("home", "away"):
            name = entry.get(f"{side}GoalieName")
            label = (entry.get(f"{side}NewsStrengthName") or "").lower()
            code = team_codes.get(normalize_name(entry.get(f"{side}TeamName") or ""))
            if not (name and code) or not (label == "confirmed" or "likely" in label):
                continue
            starters[code] = {"goalie_name": name, "confirmed": label == "confirmed"}
    return starters


def get_starters(date: dt.date) -> dict[str, dict]:
    """{team code: {"goalie_name", "confirmed"}} for `date`; {} on any failure."""
    try:
        def fetch() -> list[dict]:
            match = _NEXT_DATA_RE.search(get(URL.format(date=date.isoformat())).text)
            return json.loads(match.group(1))["props"]["pageProps"]["data"]

        entries = cached_json(f"dfo_starters/{date.isoformat()}", HOUR / 2, fetch)
        starters = parse_entries(entries, _team_codes())
    except Exception:
        logger.warning("DailyFaceoff starting goalies unavailable for %s", date, exc_info=True)
        health.report("get_starters", "failed")
        return {}
    if entries and not starters and any(e.get("homeNewsStrengthName") for e in entries):
        logger.warning("DailyFaceoff listed %d games for %s but no starter parsed - check goalie_client", len(entries), date)
        health.report("get_starters", "page read, but no starter in it (has its format changed?)")
    return starters
