"""Daily lineup optimizer: which active players start, and in which slots.

Exact search (dynamic programming over remaining slot capacity) - with 14
active players and 5 slot types that's a few hundred states. Tie-breaks,
far below any real point difference:
1. keep a player in his current slot (no pointless shuffles),
2. then start the better player on season value (sensible choice among
   players who all have no game tonight).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from config.league import STARTERS
from league.roster import BENCH

KEEP_SLOT_BONUS = 1e-3
SEASON_VALUE_WEIGHT = 1e-5


@dataclass(frozen=True)
class Candidate:
    player_id: int
    positions: tuple[str, ...]
    value: float  # expected points tonight (0 if no game)
    season_value: float = 0.0  # tie-breaker only
    current_slot: str | None = None


def optimize(candidates: list[Candidate], slots: dict[str, int] = STARTERS) -> dict[int, str]:
    """player id -> slot (a starting slot or BN)."""
    slot_types = tuple(slots)

    @lru_cache(maxsize=None)
    def best(i: int, capacity: tuple[int, ...]) -> tuple[float, tuple[str, ...]]:
        if i == len(candidates):
            return 0.0, ()
        c = candidates[i]
        score, rest = best(i + 1, capacity)
        options = [(score + (KEEP_SLOT_BONUS if c.current_slot == BENCH else 0.0), (BENCH, *rest))]
        for k, slot in enumerate(slot_types):
            if capacity[k] and slot in c.positions:
                reduced = capacity[:k] + (capacity[k] - 1,) + capacity[k + 1:]
                score, rest = best(i + 1, reduced)
                bonus = (KEEP_SLOT_BONUS if c.current_slot == slot else 0.0) + SEASON_VALUE_WEIGHT * c.season_value
                options.append((score + c.value + bonus, (slot, *rest)))
        return max(options, key=lambda o: o[0])

    _, assignment = best(0, tuple(slots[s] for s in slot_types))
    return {c.player_id: slot for c, slot in zip(candidates, assignment)}
