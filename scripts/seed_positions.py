"""Seed state/positions.json with Yahoo position eligibility from Nico's local
Yahoo export (data/yahoo/skaters.csv, gitignored).

Only positions are copied (player id -> ["C", "LW"]): no projections or other
Yahoo data. Nico approved this one exception to "Yahoo exports stay local"
(2026-10-01). Positions the bot already learned from screenshots are kept:
they're newer than the export.

    python -m scripts.seed_positions
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from clients import nhl_client
from clients.names import normalize_name
from league import parse, positions

EXPORT = Path("data/yahoo/skaters.csv")
# "Nathan MacKinnonPlayer NoteCOL - C": name, then Yahoo's team and positions.
_CELL = re.compile(r"^(.*?)(?:Player Note|No new player Notes).*?([A-Z]{2,3}) - ((?:C|LW|RW|D)(?:,(?:C|LW|RW|D))*)$")


def main() -> None:
    by_name: dict[str, list[dict]] = {}
    for p in nhl_client.current_rosters():
        by_name.setdefault(normalize_name(p["name"]), []).append(p)
    seen, unmatched, ambiguous = {}, [], []
    with EXPORT.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m = _CELL.match(row["Forwards/Defensemen"])
            if not m:
                continue
            name, team, pos = m.group(1), m.group(2), m.group(3).split(",")
            matches = by_name.get(normalize_name(name), [])
            if len(matches) > 1:  # two players by that name: Yahoo's team tells them apart
                matches = [p for p in matches if p["team"] == parse.YAHOO_TO_NHL_TEAM.get(team, team)]
            if len(matches) > 1:  # same team too (the two Elias Petterssons): D or forward
                matches = [p for p in matches if (p["position"] == "D") == (pos == ["D"])]
            if len(matches) == 1:
                seen[matches[0]["id"]] = pos
            elif matches:
                ambiguous.append(name)
            else:
                unmatched.append(name)
    known = positions.load()
    added = {pid: pos for pid, pos in seen.items() if pid not in known}  # screenshots are newer: keep them
    positions.learn(known, added)
    positions.save(known)
    print(f"{len(seen)} skaters matched, {len(added)} added ({len(seen) - len(added)} already learned); "
          f"{len(unmatched)} not on a current NHL roster, {len(ambiguous)} ambiguous: {', '.join(ambiguous) or '-'}")


if __name__ == "__main__":
    main()
