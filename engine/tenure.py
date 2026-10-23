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

# Fit by `fit` on the replay's audit (2026-10-23: Check `replay-audit`, 16 leagues, the policy
# "pace 0.08 k2" with the live valuation, 7,474 moves). Index k - 1: P(still standing k weeks after).
SURVIVAL: dict[str, tuple[float, ...]] = {
    "skater, lowest": (0.796, 0.603, 0.423, 0.34, 0.272, 0.208, 0.148, 0.126, 0.115, 0.101, 0.083, 0.074, 0.06,
                       0.049, 0.049, 0.041, 0.041, 0.041, 0.041, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "skater, 2nd lowest": (0.885, 0.77, 0.656, 0.552, 0.47, 0.379, 0.341, 0.296, 0.266, 0.234, 0.194, 0.165, 0.152,
                           0.136, 0.12, 0.116, 0.106, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1),
    "skater, 3rd lowest": (0.913, 0.826, 0.733, 0.662, 0.59, 0.518, 0.455, 0.413, 0.359, 0.316, 0.281, 0.255, 0.234,
                           0.217, 0.183, 0.176, 0.165, 0.161, 0.15, 0.138, 0.117, 0.117, 0.117, 0.117, 0.117, 0.117),
    "skater, 4th-6th": (0.977, 0.939, 0.855, 0.791, 0.745, 0.698, 0.655, 0.622, 0.583, 0.532, 0.486, 0.446, 0.427,
                        0.393, 0.373, 0.356, 0.341, 0.329, 0.313, 0.295, 0.284, 0.205, 0.13, 0.13, 0.13, 0.13),
    "skater, 7th+": (0.989, 0.98, 0.944, 0.924, 0.885, 0.86, 0.821, 0.792, 0.755, 0.717, 0.687, 0.659, 0.646,
                     0.601, 0.578, 0.57, 0.565, 0.556, 0.556, 0.55, 0.517, 0.415, 0.282, 0.282, 0.282, 0.282),
    "goalie, the weakest of 3+": (0.733, 0.524, 0.362, 0.277, 0.217, 0.185, 0.16, 0.147, 0.141, 0.131, 0.119, 0.111,
                                  0.107, 0.107, 0.104, 0.102, 0.096, 0.089, 0.072, 0.052, 0.041, 0.041, 0.041, 0.041,
                                  0.041, 0.041),
    "goalie": (0.89, 0.657, 0.506, 0.376, 0.307, 0.27, 0.25, 0.224, 0.218, 0.198, 0.188, 0.163, 0.154, 0.147, 0.14,
               0.122, 0.121, 0.107, 0.101, 0.094, 0.073, 0.063, 0.06, 0.06, 0.06, 0.06),
}


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
