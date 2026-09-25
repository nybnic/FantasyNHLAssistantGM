"""Rule-agnostic roster checks: injury/IR slot mismatches and empty active
slots. Neither needs the league's scoring categories, so both are safe to
run before the draft rules are finalized.
"""
from __future__ import annotations

from typing import Any

from engine.models import Recommendation

# Statuses Yahoo uses that make a player IR-slot eligible. Confirmed against
# yahoo_fantasy_api's own roster() docstring examples ('' and 'DTD'); the
# rest ('O', 'IR', 'IR-LT') are the commonly documented Yahoo NHL statuses -
# double check the exact set against your real roster on the first dry run.
IR_ELIGIBLE_STATUSES = {"IR", "IR-LT", "IR-NR", "O"}


def _is_active_slot(slot: str) -> bool:
    return slot != "BN" and slot != "NA" and not slot.startswith("IR")


def check_ir_eligible(
    roster: list[dict[str, Any]], roster_slots: dict[str, dict]
) -> list[Recommendation]:
    ir_slot_names = [slot for slot in roster_slots if slot.startswith("IR")]
    if not ir_slot_names:
        return []

    ir_capacity = sum(roster_slots[s].get("count", 0) for s in ir_slot_names)
    ir_used = sum(1 for p in roster if p.get("selected_position") in ir_slot_names)

    recs = []
    for player in roster:
        status = player.get("status", "")
        if status in IR_ELIGIBLE_STATUSES and player.get("selected_position") not in ir_slot_names:
            if ir_used < ir_capacity:
                recs.append(
                    Recommendation(
                        key=f"ir_stash:{player['player_id']}",
                        category="injury",
                        message=(
                            f"{player['name']} is {status} and occupying an active/bench "
                            f"spot - you have an open IR slot, move him there to free up "
                            f"a roster spot."
                        ),
                    )
                )
    return recs


def check_empty_active_slots(
    roster: list[dict[str, Any]], roster_slots: dict[str, dict]
) -> list[Recommendation]:
    active_slot_names = {slot for slot in roster_slots if _is_active_slot(slot)}

    filled_counts: dict[str, int] = {}
    for player in roster:
        pos = player.get("selected_position")
        if pos in active_slot_names:
            filled_counts[pos] = filled_counts.get(pos, 0) + 1

    bench_players = [p["name"] for p in roster if p.get("selected_position") == "BN"]

    recs = []
    for slot in active_slot_names:
        info = roster_slots[slot]
        if filled_counts.get(slot, 0) < info.get("count", 0):
            note = f" Bench players: {', '.join(bench_players)}." if bench_players else ""
            recs.append(
                Recommendation(
                    key=f"empty_slot:{slot}",
                    category="lineup",
                    message=f"Your {slot} slot is empty.{note}",
                )
            )
    return recs
