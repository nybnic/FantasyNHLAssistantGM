"""Seed state/roster.json from a list of your players (one-time setup).

    python -m scripts.seed_roster my_team.txt

One player per line: "Name", or "Name | positions | slot" to override:
    Nathan MacKinnon
    Leon Draisaitl | C,LW
    Elias Pettersson | D
    Seth Jarvis | RW | IR+
Positions default to Yahoo's eligibility from data/yahoo/skaters.csv and
goalies.csv (your local Yahoo export) when present, else the NHL position.
Slots default to unknown: the first briefing then lists a full lineup, and
tapping Done records it. Put injured players' IR/IR+ slot in the file.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from clients import nhl_client, nhl_stats
from clients.dfo_projections import normalize_name
from config.league import BENCH_SLOTS, IR_SLOT_STATUSES, STARTERS
from league import roster as roster_mod
from model.context import current_season_id

NHL_TO_YAHOO = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
VALID_SLOTS = {*STARTERS, roster_mod.BENCH, *IR_SLOT_STATUSES}
MAX_PLAYERS = sum(STARTERS.values()) + BENCH_SLOTS + len(IR_SLOT_STATUSES)


def yahoo_eligibility() -> dict[str, list[str]]:
    """Normalized name -> Yahoo positions, from the local projections export."""
    try:
        from draft.yahoo_projections import load_yahoo_projections

        return {normalize_name(p.name): sorted(p.elig) for p in load_yahoo_projections()}
    except (ImportError, FileNotFoundError, ValueError):
        return {}


def main(path: str) -> None:
    last_season = current_season_id(dt.date.today()) - 10_001
    registry = nhl_client.current_rosters() + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": g.position}
        for g in nhl_stats.skater_games(last_season)
    ] + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": "G"}
        for g in nhl_stats.goalie_games(last_season)
    ]
    by_name: dict[str, dict[int, dict]] = {}
    for p in registry:
        by_name.setdefault(normalize_name(p["name"]), {}).setdefault(p["id"], p)
    eligibility = yahoo_eligibility()

    players, problems = [], []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        fields = [f.strip() for f in raw.split("|")]
        name, positions, slot = fields[0], fields[1] if len(fields) > 1 else "", fields[2] if len(fields) > 2 else ""
        candidates = list(by_name.get(normalize_name(name), {}).values())
        wanted = [pos.strip() for pos in positions.split(",") if pos.strip()]
        if len(candidates) > 1 and wanted:
            candidates = [c for c in candidates if NHL_TO_YAHOO.get(c["position"]) in wanted
                          or (c["position"] in "LRC" and set(wanted) & {"C", "LW", "RW"})]
        if len(candidates) != 1:
            found = ", ".join(f"{c['name']} {c['team']} {c['position']}" for c in candidates) or "no match"
            problems.append(f"{name}: {found} - add positions to disambiguate or fix the spelling")
            continue
        c = candidates[0]
        if slot and slot not in VALID_SLOTS:
            problems.append(f"{name}: unknown slot {slot!r} (use one of {sorted(VALID_SLOTS)})")
            continue
        players.append(roster_mod.RosterPlayer(
            id=c["id"],
            name=c["name"],
            team=c["team"],
            positions=wanted or eligibility.get(normalize_name(c["name"])) or [NHL_TO_YAHOO[c["position"]]],
            slot=slot or None,
        ))

    if len(players) > MAX_PLAYERS:
        problems.append(f"{len(players)} players, but a roster holds at most {MAX_PLAYERS}")
    if problems:
        raise SystemExit("Fix these lines and rerun:\n  " + "\n  ".join(problems))
    roster_mod.save(players)
    print(f"Saved {len(players)} players to {roster_mod.ROSTER_FILE}:\n{roster_mod.describe(players)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
