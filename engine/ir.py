"""The IR and IR+ slots: who can go there, and who is coming back.

Moving an injured player out of the active roster frees his spot, so the
next add needs no drop. Yahoo decides eligibility by its own injury tag,
which the bot can't see; DailyFaceoff's status stands in for it (a DFO "ir"
is likely Yahoo's IR, "out" its O, which only IR+ takes), so the advice
says to check Yahoo's tag. A player in an IR slot who is back in DFO's
lineup needs an active spot again: Yahoo won't allow any move until he has one.
"""
from __future__ import annotations

from dataclasses import dataclass

from clients.dfo_lines import LineInfo
from clients.names import normalize_name
from config.league import IR_SLOT_STATUSES
from league.roster import IR_SLOTS, RosterPlayer, SLOT_CAPACITY, active


@dataclass
class IrMove:
    player: RosterPlayer
    slot: str  # "IR" or "IR+"
    status: str  # Yahoo's likely tag: "IR" or "O"


def _info(p: RosterPlayer, lines: dict[str, dict[str, LineInfo]]) -> LineInfo | None:
    return lines.get(p.team, {}).get(normalize_name(p.name))


def likely_status(info: LineInfo | None) -> str | None:
    """Yahoo's likely injury tag from DFO's status: "IR", "O", or None (no
    reason to move him; day-to-day players stay active, they often play next game)."""
    if info is None:
        return None
    if info.injury == "ir" or (info.injury is None and "ir" in info.groups):  # DFO's status first
        return "IR"
    return "O" if info.injury == "out" else None


def free_slots(players: list[RosterPlayer]) -> list[str]:
    """The empty IR and IR+ slots, IR first."""
    return [s for s in IR_SLOTS for _ in range(SLOT_CAPACITY[s] - sum(p.slot == s for p in players))]


def slot_for(status: str | None, free: list[str]) -> str | None:
    """The free slot a player with Yahoo's likely tag `status` can go to: IR
    first, so IR+ stays free for an "O"."""
    return next((s for s in free if status in IR_SLOT_STATUSES[s]), None) if status else None


def moves(players: list[RosterPlayer], lines: dict[str, dict[str, LineInfo]]) -> list[IrMove]:
    """Injured active players who fit an empty IR slot, one per slot. IR goes
    to the IR slot first, so IR+ stays free for an "O"."""
    free = free_slots(players)
    injured = [(p, likely_status(_info(p, lines))) for p in active(players)]
    out = []
    for p, status in sorted(((p, s) for p, s in injured if s), key=lambda ps: ps[1] != "IR"):
        slot = slot_for(status, free)
        if slot:
            free.remove(slot)
            out.append(IrMove(p, slot, status))
    return out


def returning(players: list[RosterPlayer], lines: dict[str, dict[str, LineInfo]]) -> list[RosterPlayer]:
    """Players in an IR slot whom DFO lists in his team's lineup, healthy."""
    back = []
    for p in players:
        info = _info(p, lines)
        if p.slot in IR_SLOTS and info and not info.injury and "ir" not in info.groups and info.groups:
            back.append(p)
    return back


def after(players: list[RosterPlayer], ir_moves: list[IrMove]) -> list[RosterPlayer]:
    """The roster with the IR moves made (copies; the real roster is unchanged)."""
    slot = {m.player.id: m.slot for m in ir_moves}
    return [RosterPlayer(p.id, p.name, p.team, p.positions, slot.get(p.id, p.slot)) for p in players]


def text(ir_moves: list[IrMove], back: list[RosterPlayer], weakest: RosterPlayer | None = None) -> str:
    """The IR lines, or "" when there's nothing to do."""
    lines = []
    for m in ir_moves:
        tag = "IR" if m.status == "IR" else "out (Yahoo's O)"
        lines.append(f"IR: move {m.player.name} to {m.slot} (DailyFaceoff lists him {tag}; check Yahoo's tag). "
                     "It frees a roster spot, so an add needs no drop.")
    for p in back:
        lines.append(f"IR: {p.name} is back in {p.team}'s lineup. He needs an active spot before Yahoo allows "
                     "any other move" + (f": the weakest to drop is {weakest.name}." if weakest else "."))
    return "\n".join(lines)
