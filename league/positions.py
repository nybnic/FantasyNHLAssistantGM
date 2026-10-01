"""Yahoo position eligibility, learned from what Yahoo shows us.

NHL data gives one position per player; Yahoo often allows more (McBain C,LW;
Tolvanen LW,RW) and sometimes others entirely (Guentzel: NHL C, Yahoo
LW,RW). Of 560 skaters in Nico's preseason Yahoo export, 37% differed
(2026-10-01), which mis-slots free agents in every add and streamer check.
So every Yahoo screenshot or paste the bot reads (team pages, matchups,
transactions) records the positions it shows, and free agents take them
from here, falling back to the NHL position for players Yahoo hasn't shown.
"""
from __future__ import annotations

import json
from pathlib import Path

POSITIONS_FILE = Path("state/positions.json")
_VALID = {"C", "LW", "RW", "D", "G"}


def load(path: Path | None = None) -> dict[int, list[str]]:
    path = path or POSITIONS_FILE
    if not path.exists():
        return {}
    return {int(pid): pos for pid, pos in json.loads(path.read_text(encoding="utf-8")).items()}


def learn(known: dict[int, list[str]], seen: dict[int, list[str]]) -> int:
    """Record what Yahoo showed; returns how many entries changed."""
    changed = 0
    for pid, pos in seen.items():
        pos = [p for p in pos if p in _VALID]
        if pos and known.get(pid) != pos:
            known[pid] = pos
            changed += 1
    return changed


def save(known: dict[int, list[str]], path: Path | None = None) -> None:
    path = path or POSITIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({str(k): v for k, v in sorted(known.items())}, indent=0), encoding="utf-8")


def eligible(known: dict[int, list[str]], player_id: int, nhl_position: str) -> list[str]:
    """Yahoo's positions when known, else the NHL one."""
    return known.get(player_id) or [nhl_position]
