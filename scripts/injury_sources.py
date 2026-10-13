"""What do injury sources say about how long a player is out? Prints the
fields DailyFaceoff's line chart and ESPN's injury feed carry for injured
players (one team as the example), so we know whether an expected return can
be read rather than inferred from return curves. Field names and a few rows
only: the Check workflow's log is public.

    python -m scripts.injury_sources [TEAM]     # default NYR
"""
from __future__ import annotations

import json
import sys

from clients import dfo_lines
from clients.cache import get

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
ESPN_NAMES = {"NYR": "Rangers"}  # how ESPN names the example team
HINTS = ("injur", "news", "return", "status", "detail", "comment", "date", "type", "description")


def _hinted(d: dict) -> dict:
    return {k: v for k, v in d.items() if any(h in k.lower() for h in HINTS)}


def dfo(team: str) -> None:
    slug = next(t["slug"] for t in dfo_lines.teams() if t["code"] == team)
    props = dfo_lines._page_props(f"/teams/{slug}/line-combinations")
    players = props["combinations"]["players"]
    print(f"DFO {team}: page keys {sorted(props)}")
    print(f"  player keys {sorted(players[0])}")
    hurt = [p for p in players if p.get("injuryStatus")]
    print(f"  {len(hurt)} injured listed")
    for p in hurt[:4]:
        print(f"  {p['name']}: {json.dumps(_hinted(p), default=str)[:400]}")


def espn(team: str) -> None:
    data = get(ESPN_INJURIES).json()
    teams = data.get("injuries", [])
    print(f"\nESPN injuries: top keys {sorted(data)}, {len(teams)} teams")
    name = ESPN_NAMES.get(team, team)
    t = next((t for t in teams if name.lower() in t.get("displayName", "").lower()), teams[0] if teams else None)
    if not t:
        return
    rows = t.get("injuries", [])
    print(f"  {t.get('displayName')}: {len(rows)} injured; entry keys {sorted(rows[0]) if rows else []}")
    for r in rows[:4]:
        who = r.get("athlete", {}).get("displayName")
        print(f"  {who}: {json.dumps(_hinted(r), default=str)[:400]}")


def main() -> None:
    team = sys.argv[1] if len(sys.argv) > 1 else "NYR"
    for source in (dfo, espn):
        try:
            source(team)
        except Exception as e:  # one source failing shouldn't hide the other
            print(f"{source.__name__}: failed ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
