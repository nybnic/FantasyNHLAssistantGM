"""Your Yahoo roster as the Assistant GM believes it to be.

There's no Yahoo API access, so this is human-in-the-loop: seeded once with
scripts/seed_roster.py, then kept in sync by your Done taps on Telegram.
`slot` is the Yahoo slot a player sits in (C, LW, RW, D, G, BN, IR, IR+),
or None until you first confirm a lineup.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.league import IR_SLOT_STATUSES

ROSTER_FILE = Path("state/roster.json")
IR_SLOTS = tuple(IR_SLOT_STATUSES)
BENCH = "BN"


@dataclass
class RosterPlayer:
    id: int
    name: str
    team: str
    positions: list[str] = field(default_factory=list)  # Yahoo eligibility, e.g. ["C", "LW"]
    slot: str | None = None

    @property
    def is_goalie(self) -> bool:
        return "G" in self.positions


def load(path: Path = ROSTER_FILE) -> list[RosterPlayer]:
    if not path.exists():
        return []
    return [RosterPlayer(**p) for p in json.loads(path.read_text(encoding="utf-8"))["players"]]


def save(players: list[RosterPlayer], path: Path = ROSTER_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"players": [asdict(p) for p in players]}, indent=2), encoding="utf-8")


def active(players: list[RosterPlayer]) -> list[RosterPlayer]:
    return [p for p in players if p.slot not in IR_SLOTS]


def lineup_known(players: list[RosterPlayer]) -> bool:
    return all(p.slot for p in active(players))


def apply_lineup(players: list[RosterPlayer], assignment: dict[int, str]) -> None:
    for p in players:
        if p.id in assignment:
            p.slot = assignment[p.id]


def describe(players: list[RosterPlayer]) -> str:
    order = ["C", "LW", "RW", "D", "G", BENCH, *IR_SLOTS, None]
    lines = []
    for p in sorted(players, key=lambda p: (order.index(p.slot) if p.slot in order else len(order), p.name)):
        lines.append(f"{p.slot or '?':<3} {p.name} ({p.team}, {'/'.join(p.positions)})")
    return "\n".join(lines)
