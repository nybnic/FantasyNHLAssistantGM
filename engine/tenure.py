"""How long an add stays: P(the swap still stands k weeks after it), by where the
add sits on my roster (Nico, 2026-10-23: "not an arbitrary value deciding whether
value should count only short term or the full season"; the bot's own policy).

Replaces, once it ships, the keep share and the streaming hold (matchup.keep_share,
hold_weeks): a move's gain on each later day counts times the odds the add is
still mine then and the size a gap that far ahead realizes at (check_gaps), not
the 6-week rate x the weeks left x LONG_RUN_DISCOUNT. Fit on the replay
(scripts/replay.py --audit): the tenures of the moves its policy made, by bin,
Kaplan-Meier with the season's end as censoring. Not used live yet (engine freeze:
it ships on a replay gain, then two weeks in shadow).
"""
from __future__ import annotations

HORIZON = 26  # weeks the curves cover; beyond, the last value
# Where an add sits after the move. A skater: how many of my skaters are closer
# to waiver level at their position than he is (matchup.waiver_gap): 0 = he'd be
# the next one cut. A goalie: the weakest of three or more (my streaming goalie), or not.
SKATER_BINS = ("skater, lowest", "skater, 2nd lowest", "skater, 3rd lowest", "skater, 4th-6th", "skater, 7th+")
GOALIE_BINS = ("goalie, the weakest of 3+", "goalie")
# A gap projected k weeks ahead realizes at this share (scripts/check_gaps.py, fringe
# skaters, 2024-25 / 2025-26 averaged; model/CLAUDE.md): weeks 1-2 as matchup.GAP_REALIZES.
REALIZES = ((2, None), (6, 0.84), (12, 0.76), (HORIZON, 0.74))

# Fit by `fit` on the replay's audit (see the module docstring); empty until the first fit.
SURVIVAL: dict[str, tuple[float, ...]] = {}


def skater_bin(lower: int) -> str:
    """The bin of a skater add with `lower` of my skaters below him by waiver gap."""
    return SKATER_BINS[lower if lower < 3 else 3 if lower < 6 else 4]


def survival(bin_: str, k: int, table: dict[str, tuple[float, ...]] | None = None) -> float:
    """P(an add in `bin_` still stands k weeks after his first: k = 0 is his first week (1)."""
    curve = (table if table is not None else SURVIVAL).get(bin_)
    if k <= 0 or not curve:
        return 1.0
    return curve[min(k, len(curve)) - 1]


def realizes(k: int) -> float:
    """The share of a gap projected k weeks ahead that shows up (k >= 3; the weeks
    ahead use matchup.GAP_REALIZES)."""
    for last, share in REALIZES:
        if k <= last and share is not None:
            return share
    return REALIZES[-1][1]


def fit(records: list[tuple[str, int, int]], horizon: int = HORIZON) -> dict[str, tuple[float, ...]]:
    """Kaplan-Meier survival by bin from (bin, weeks held, weeks left in the season
    from the move's week): held counts the move's own week, so an add gone by the
    next Monday held 1; held == weeks left is the season ending (censored). Curve
    index k - 1: P(still standing k weeks after the first)."""
    by_bin: dict[str, list[tuple[int, int]]] = {}
    for b, held, left in records:
        by_bin.setdefault(b, []).append((held, left))
    out = {}
    for b, rs in by_bin.items():
        s, curve = 1.0, []
        for k in range(1, horizon + 1):
            at_risk = [h for h, left in rs if h >= k and left > k]  # seen in week k, and week k+1 exists
            ended = sum(1 for h in at_risk if h == k)
            if at_risk:
                s *= 1 - ended / len(at_risk)
            curve.append(round(s, 3))
        out[b] = tuple(curve)
    return out
