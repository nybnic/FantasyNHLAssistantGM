"""Your Yahoo roster as the Assistant GM believes it to be.

There's no Yahoo API access, so this is human-in-the-loop: seeded once with
scripts/seed_roster.py, then kept in sync by your Done taps on Telegram, and
replaced whenever you paste your Yahoo team page (/myteam).
`slot` is the Yahoo slot a player sits in (C, LW, RW, D, G, BN, IR, IR+),
or None until you first confirm a lineup.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.league import BENCH_SLOTS, IR_SLOT_STATUSES, STARTERS

ROSTER_FILE = Path("state/roster.json")
IR_SLOTS = tuple(IR_SLOT_STATUSES)
BENCH = "BN"
SLOT_CAPACITY = {**STARTERS, BENCH: BENCH_SLOTS, **{s: 1 for s in IR_SLOTS}}
MAX_PLAYERS = sum(SLOT_CAPACITY.values())
MIN_PASTED = 10  # judgment call: fewer means a partial copy, not a roster


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


def replace(players: list[RosterPlayer], pasted: list[RosterPlayer], tagged: set[int]) -> list[str]:
    """Make `players` the pasted roster and say what changed. Slots come from
    the paste, or stay as they were for a player it gives none; if they then
    overfill a slot, the active ones are cleared so tonight's briefing lists a
    full lineup. Positions come from Yahoo's tag, else stay as they were."""
    old = {p.id: p for p in players}
    new = {p.id: p for p in pasted}
    for p in pasted:
        prev = old.get(p.id)
        if prev and p.id not in tagged:
            p.positions = prev.positions
        if prev and p.slot is None:
            p.slot = prev.slot
    counts: dict[str | None, int] = {}
    for p in pasted:
        counts[p.slot] = counts.get(p.slot, 0) + 1
    overfilled = [s for s, n in counts.items() if s and n > SLOT_CAPACITY.get(s, 0)]
    if overfilled:
        for p in active(pasted):
            p.slot = None
    lines = []
    if added := [p.name for p in pasted if p.id not in old]:
        lines.append("Added: " + ", ".join(added))
    if dropped := [p.name for p in players if p.id not in new]:
        lines.append("Dropped: " + ", ".join(dropped))
    if moved := [f"{p.name} {old[p.id].slot or '?'}->{p.slot or '?'}" for p in pasted
                 if p.id in old and old[p.id].slot != p.slot]:
        lines.append("Slots: " + ", ".join(moved))
    if overfilled:
        lines.append(f"Too many players in {', '.join(overfilled)}, so I don't know your lineup: "
                     "tonight's briefing lists a full one.")
    elif unknown := [p.name for p in active(pasted) if p.slot is None]:
        lines.append(f"No slot for {', '.join(unknown)}: tonight's briefing lists a full lineup.")
    players[:] = pasted
    return lines


def describe(players: list[RosterPlayer]) -> str:
    order = ["C", "LW", "RW", "D", "G", BENCH, *IR_SLOTS, None]
    lines = []
    for p in sorted(players, key=lambda p: (order.index(p.slot) if p.slot in order else len(order), p.name)):
        lines.append(f"{p.slot or '?':<3} {p.name} ({p.team}, {'/'.join(p.positions)})")
    return "\n".join(lines)
