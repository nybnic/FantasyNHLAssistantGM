"""The other 15 teams' rosters, as far as the Assistant GM knows them.

Seeded from the draft results (scripts/seed_league.py), then refreshed by
pasting a team's Yahoo page into Telegram (/opp). Everyone on a current NHL
roster who isn't on a fantasy roster here, on yours, or in `taken` (/taken)
counts as a free agent.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from league.roster import RosterPlayer

LEAGUE_FILE = Path("state/league.json")


def load(path: Path = LEAGUE_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("teams", {})
    data.setdefault("taken", [])
    return data


def save(data: dict, path: Path = LEAGUE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def players(data: dict, team: str) -> list[RosterPlayer]:
    return [RosterPlayer(**p) for p in data["teams"].get(team, {}).get("players", [])]


def updated(data: dict, team: str) -> str | None:
    return data["teams"].get(team, {}).get("updated")


def set_team(data: dict, team: str, roster: list[RosterPlayer], date: dt.date) -> None:
    """Replace a team's roster. Its players are no longer merely "taken"."""
    data["teams"][team] = {"updated": date.isoformat(), "players": [asdict(p) for p in roster]}
    ids = {p.id for p in roster}
    data["taken"] = [pid for pid in data["taken"] if pid not in ids]
    for other, entry in data["teams"].items():
        if other != team:
            entry["players"] = [p for p in entry["players"] if p["id"] not in ids]


def mark_taken(data: dict, player_ids: list[int]) -> None:
    data["taken"] = sorted(set(data["taken"]) | set(player_ids))


def rostered_ids(data: dict) -> set[int]:
    ids = set(data["taken"])
    for entry in data["teams"].values():
        ids.update(p["id"] for p in entry["players"])
    return ids
