"""Value over replacement (VORP) and tiers for the draft board.

Replacement level at a position = what you can still get there once the
league has filled its starting slots plus its share of bench spots, i.e.
roughly the best player at that position left on waivers. Ranking by
points *above* that level is what makes a 600-pt goalie comparable to a
600-pt center.

Multi-position players are valued at whichever eligible position gives
them the most value - a C/RW who takes few faceoffs counts as a winger.
"""
from __future__ import annotations

from collections import deque
from typing import Sequence

from draft.projections import Projection
from draft.scoring import BENCH_SLOTS, LEAGUE_TEAMS, STARTERS

# How the league's 16 x 2 bench spots are expected to be used. Daily
# lineups + only 2 G slots make a third goalie the most common bench stash.
BENCH_SHARE = {"C": 5, "LW": 4, "RW": 4, "D": 6, "G": 13}
assert sum(BENCH_SHARE.values()) == LEAGUE_TEAMS * BENCH_SLOTS

REPLACEMENT_WINDOW = 5  # average this many players at the cut-off


def replacement_rank(pos: str) -> int:
    return LEAGUE_TEAMS * STARTERS[pos] + BENCH_SHARE[pos]


def league_rosters(projections: Sequence[Projection], candidates: int = 500) -> dict[int, str]:
    """The points-maximizing way to fill every team's slots league-wide.

    Min-cost flow: source -> player (1 unit, cost -fpts) -> eligible position
    -> sink (capacity = replacement_rank(pos)). Solved by successive shortest
    paths (queue-based Bellman-Ford); only the top `candidates` players by
    points can matter, which keeps it fast.
    Returns {player_id: position} for every rostered player.
    """
    players = sorted(projections, key=lambda p: p.fpts, reverse=True)[:candidates]
    positions = sorted({pos for p in players for pos in (p.elig or {p.pos})} & set(STARTERS))
    n_p = len(players)
    src, sink = n_p + len(positions), n_p + len(positions) + 1
    pos_node = {pos: n_p + i for i, pos in enumerate(positions)}

    graph: list[list[int]] = [[] for _ in range(sink + 1)]
    to, cap, cost = [], [], []

    def add_edge(u: int, v: int, c: int, w: float) -> None:
        for a, b, cc, ww in ((u, v, c, w), (v, u, 0, -w)):
            graph[a].append(len(to))
            to.append(b)
            cap.append(cc)
            cost.append(ww)

    for i, p in enumerate(players):
        add_edge(src, i, 1, -p.fpts)
        for pos in p.elig or {p.pos}:
            if pos in pos_node:
                add_edge(i, pos_node[pos], 1, 0.0)
    for pos, node in pos_node.items():
        add_edge(node, sink, replacement_rank(pos), 0.0)

    while True:
        dist = [float("inf")] * (sink + 1)
        prev_edge = [-1] * (sink + 1)
        in_queue = [False] * (sink + 1)
        dist[src] = 0.0
        queue = deque([src])
        while queue:
            u = queue.popleft()
            in_queue[u] = False
            for e in graph[u]:
                if cap[e] > 0 and dist[u] + cost[e] < dist[to[e]] - 1e-9:
                    dist[to[e]] = dist[u] + cost[e]
                    prev_edge[to[e]] = e
                    if not in_queue[to[e]]:
                        in_queue[to[e]] = True
                        queue.append(to[e])
        if dist[sink] >= 0:  # no augmenting path adds points any more
            break
        v = sink
        while v != src:
            e = prev_edge[v]
            cap[e] -= 1
            cap[e ^ 1] += 1
            v = to[e ^ 1]

    assigned = {}
    for i, p in enumerate(players):
        for e in graph[i]:
            if to[e] in pos_node.values() and cap[e] == 0 and cost[e] == 0.0 and e % 2 == 0:
                assigned[p.player_id] = positions[to[e] - n_p]
    return assigned


def waiver_pool(projections: Sequence[Projection]) -> dict[str, list[Projection]]:
    """The best REPLACEMENT_WINDOW players at each position who don't make
    the optimal league-wide rosters - what is really left on waivers."""
    rostered = league_rosters(projections)
    return {
        pos: sorted(
            (p for p in projections if p.player_id not in rostered and pos in (p.elig or {p.pos})),
            key=lambda p: p.fpts, reverse=True,
        )[:REPLACEMENT_WINDOW]
        for pos in STARTERS
    }


def value_positions(projections: Sequence[Projection]) -> tuple[dict[str, float], dict[int, str]]:
    """Replacement level per position and the position each player is valued at.

    Replacement level = average points of the waiver pool at that position.
    Each player is valued at the eligible position where he beats
    replacement by the most.
    """
    levels = {}
    for pos, pool in waiver_pool(projections).items():
        levels[pos] = sum(p.fpts for p in pool) / len(pool) if pool else 0.0
    value_pos = {
        p.player_id: max((p.elig or {p.pos}) & set(levels), key=lambda pos: p.fpts - levels[pos])
        for p in projections
    }
    return levels, value_pos


def assign_tiers(
    values: Sequence[float], min_gap: float = 6.0, gap_factor: float = 2.2, max_width: float = 25.0
) -> list[int]:
    """Tier numbers for a descending list of values: a new tier starts where
    the drop to the next player is much larger than typical nearby drops,
    or once a tier spans `max_width` points (~0.3 pts/game over a season -
    beyond that players are no longer interchangeable)."""
    tiers = [1] * len(values)
    tier = 1
    tier_top = values[0] if values else 0.0
    for i in range(1, len(values)):
        gap = values[i - 1] - values[i]
        lo, hi = max(1, i - 6), min(len(values), i + 6)
        local = sorted(values[j - 1] - values[j] for j in range(lo, hi))
        typical = local[len(local) // 2] if local else 0.0
        if gap >= max(min_gap, gap_factor * typical) or tier_top - values[i] > max_width:
            tier += 1
            tier_top = values[i]
        tiers[i] = tier
    return tiers
