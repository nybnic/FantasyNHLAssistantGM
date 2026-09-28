"""DailyFaceoff team line combinations: lines, PP units, goalie depth, injuries.

Each /teams/{slug}/line-combinations page embeds __NEXT_DATA__ JSON
(verified Sep 2026). `combinations.players` lists every player once per
group he's in: f1..f4, d1..d4, g (slot g1/g2), pp1/pp2, pk1/pk2, ir. Each
has name, injuryStatus ("out", "dtd", "ir" or None) and gameTimeDecision.
No NHL player ids: callers match by name within a team.

Team `shortName` isn't always the NHL code (Montreal is "MON"), so teams are
mapped by nickname ("Canadiens") against NHL full names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from clients import nhl_client
from clients.cache import DAY, HOUR, cached_json, get
from clients.dfo_projections import normalize_name

BASE_URL = "https://www.dailyfaceoff.com"
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


@dataclass
class LineInfo:
    groups: set[str] = field(default_factory=set)  # e.g. {"f1", "pp1"}, {"g"}, {"ir"}
    goalie_depth: int | None = None  # 1 for g1, 2 for g2
    injury: str | None = None  # "out", "dtd", "ir"
    game_time_decision: bool = False


def _page_props(path: str) -> dict:
    text = get(f"{BASE_URL}{path}").text
    return json.loads(_NEXT_DATA_RE.search(text).group(1))["props"]["pageProps"]


def _team_slugs() -> dict[str, str]:
    """NHL code -> DFO slug."""

    def fetch() -> dict[str, str]:
        teams = _page_props("/teams/colorado-avalanche/line-combinations")["sortedTeams"]
        full_names = nhl_client.team_full_names()
        slugs = {}
        for code, full in full_names.items():
            for t in teams:
                if full.lower().endswith(t["mascot"].lower()):
                    slugs[code] = t["slug"]
        return slugs

    return cached_json("dfo_team_slugs", 30 * DAY, fetch)


def team_lines(team: str) -> dict[str, LineInfo]:
    """Normalized player name -> LineInfo for one NHL team code."""
    slug = _team_slugs().get(team)
    if not slug:
        return {}

    def fetch() -> list[dict]:
        combos = _page_props(f"/teams/{slug}/line-combinations")["combinations"]
        return [
            {
                "name": p["name"],
                "group": p["groupIdentifier"],
                "slot": p["positionIdentifier"],
                "injury": p.get("injuryStatus"),
                "gtd": bool(p.get("gameTimeDecision")),
            }
            for p in combos["players"]
        ]

    out: dict[str, LineInfo] = {}
    for row in cached_json(f"dfo_lines/{team}", 3 * HOUR, fetch):
        info = out.setdefault(normalize_name(row["name"]), LineInfo())
        info.groups.add(row["group"])
        if row["group"] == "g" and row["slot"] in ("g1", "g2"):
            info.goalie_depth = int(row["slot"][1])
        info.injury = info.injury or row["injury"]
        info.game_time_decision = info.game_time_decision or row["gtd"]
    return out
