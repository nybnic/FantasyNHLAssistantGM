"""DailyFaceoff team line combinations: lines, PP units, goalie depth, injuries.

Each /teams/{slug}/line-combinations page embeds __NEXT_DATA__ JSON
(verified Sep 2026). `combinations.players` lists every player once per
group he's in: f1..f4, d1..d4, g (slot g1/g2), pp1/pp2, pk1/pk2, ir. Each
has name, injuryStatus ("out", "dtd", "ir" or None) and gameTimeDecision.
No NHL player ids: callers match by name within a team.

`teams()` is also how the other DailyFaceoff readers map team names to NHL
codes. DFO's `shortName` is the NHL code except for the eight in
DFO_TO_NHL. Matching on names instead is fragile: the NHL still calls Utah
"Utah Hockey Club" while DFO says "Utah Mammoth".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from clients.cache import DAY, HOUR, cached_json, get
from clients.names import normalize_name

BASE_URL = "https://www.dailyfaceoff.com"
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# DFO short names that differ from NHL codes (all 32 teams checked Sep 2026).
DFO_TO_NHL = {"LA": "LAK", "MON": "MTL", "NAS": "NSH", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "VEG": "VGK", "WAS": "WSH"}


@dataclass
class LineInfo:
    groups: set[str] = field(default_factory=set)  # e.g. {"f1", "pp1"}, {"g"}, {"ir"}
    goalie_depth: int | None = None  # 1 for g1, 2 for g2
    injury: str | None = None  # "out", "dtd", "ir"
    game_time_decision: bool = False


def _page_props(path: str) -> dict:
    text = get(f"{BASE_URL}{path}").text
    return json.loads(_NEXT_DATA_RE.search(text).group(1))["props"]["pageProps"]


def teams() -> list[dict]:
    """DFO's 32 teams: code (NHL), name ("Utah Mammoth"), mascot, slug."""

    def fetch() -> list[dict]:
        return [
            {
                "code": DFO_TO_NHL.get(t["shortName"], t["shortName"]),
                "name": t["name"],
                "mascot": t["mascot"],
                "slug": t["slug"],
            }
            for t in _page_props("/teams/colorado-avalanche/line-combinations")["sortedTeams"]
        ]

    return cached_json("dfo_teams", 30 * DAY, fetch)


def team_lines(team: str) -> dict[str, LineInfo]:
    """Normalized player name -> LineInfo for one NHL team code."""
    slug = next((t["slug"] for t in teams() if t["code"] == team), None)
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
