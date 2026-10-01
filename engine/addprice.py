"""What an add is worth and what it costs, both in win probability.

H2H only counts weekly wins, so an add's value is the win probability it
buys: this week's change in P(win), plus its later points converted at what
a point is worth in a typical future week (later_weight). A point matters most
when a matchup is close; across the league's matchups (expected margins with
spread tau, a week's result with spread sigma) the average worth of one point
is 1 / sqrt(2 pi (sigma^2 + tau^2)) wins.

An add costs what it could buy later: its price `lam` is set so that spending
whenever a move beats it (at most MAX_ADDS_PER_WEEK a week) uses adds at the
pace the budget allows. `solve` finds it by simulating weeks: margins drawn
around zero with spread tau, and the add candidates the bot has actually seen
(this week's, and earlier weeks' from its log), each pool a typical week's.
So a close week with good streamers clears the price twice, a lopsided one
not at all. Until 4-6 weeks are logged the pools are few: treat lam as provisional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from config.league import MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK, PLAYOFF_WEEKS, REGULAR_SEASON_WEEKS

PLAYOFF_RESERVE = 6  # adds kept for playoff weeks (decision log)
DEFAULT_TAU = 20.0  # spread of matchup margins until league rosters give one (a judgment call)
SIMS_PER_POOL = 3000
POOL_SIZE = 25  # candidate adds kept per week for the simulation
_erf = np.frompyfunc(math.erf, 1, 1)


@dataclass
class AddPrice:
    lam: float  # win probability an add must buy
    later_weight: float  # win probability per point gained in a later week
    pace: float  # adds a week the budget allows


def later_weight(sigma: float, tau: float) -> float:
    return 1.0 / math.sqrt(2 * math.pi * (sigma ** 2 + tau ** 2))


def pace(adds_left: int, week: int) -> float | None:
    """Adds a week the budget allows from here; None when the regular season's
    share is spent (the rest are for the playoffs)."""
    if week > REGULAR_SEASON_WEEKS:
        return adds_left / max(PLAYOFF_WEEKS[-1] - week + 1, 1)
    spare = adds_left - PLAYOFF_RESERVE
    if spare <= 0:
        return None
    return spare / (REGULAR_SEASON_WEEKS - week + 1)


def even_pace() -> float:
    return (MAX_ADDS_PER_SEASON - PLAYOFF_RESERVE) / REGULAR_SEASON_WEEKS


def _phi(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _erf(x / math.sqrt(2)).astype(float))


def _picks(pool: dict, weight: float, sims: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """The values of the best move and the best second move (no shared add or
    drop) in `sims` simulated weeks of one pool:
    {"moves": [[full-week points, later points, add id, drop id], ...], "sigma", "tau"}."""
    moves = np.array([m[:2] for m in pool["moves"]], dtype=float)
    ids = [(m[2], m[3]) for m in pool["moves"]]
    g, later = moves[:, 0], moves[:, 1] * weight
    sigma, n = pool["sigma"], len(ids)
    margin = rng.normal(0.0, pool["tau"], size=(sims, 1))
    base = _phi(margin / sigma)
    first = _phi((margin + g) / sigma) - base + later
    j1 = first.argmax(axis=1)
    v1 = first[np.arange(sims), j1]
    clash = np.array([[a1 == a2 or (d1 is not None and d1 == d2) for (a2, d2) in ids] for (a1, d1) in ids])
    after = margin + g[j1][:, None]
    second = _phi((after + g) / sigma) - _phi(after / sigma) + later
    second[clash[j1]] = -np.inf
    v2 = second.max(axis=1) if n > 1 else np.full(sims, -np.inf)
    return v1, v2


def solve(pools: list[dict], target_pace: float, weight: float, seed: int = 1) -> float:
    """The price at which the expected adds a week match `target_pace`."""
    pools = [p for p in pools if p["moves"]]
    if not pools or target_pace >= MAX_ADDS_PER_WEEK:
        return 0.0
    rng = np.random.default_rng(seed)
    picks = [_picks(p, weight, SIMS_PER_POOL, rng) for p in pools]
    v1 = np.concatenate([p[0] for p in picks])
    v2 = np.minimum(np.concatenate([p[1] for p in picks]), v1)  # a second add only after the first

    def spent(lam: float) -> float:
        return float(np.mean((v1 >= lam).astype(int) + (v2 >= lam).astype(int)))

    if spent(0.0) <= target_pace:
        return 0.0  # even every helpful move fits the budget
    lo, hi = 0.0, max(float(v1.max()), 1e-6)
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if spent(mid) > target_pace else (lo, mid)
    return hi


def pool_entry(moves: list, remaining_days: int, sigma_now: float, tau: float) -> dict:
    """This week's candidate adds as a typical week's pool: this week's gain
    scaled to a full week, later points as they are, sigma scaled likewise."""
    scale = 7 / max(remaining_days, 1)
    best: dict[int, object] = {}
    for m in moves:
        if m.add.id not in best or m.week_gain + m.long_term > best[m.add.id].week_gain + best[m.add.id].long_term:
            best[m.add.id] = m
    top = sorted(best.values(), key=lambda m: m.week_gain * scale + m.long_term, reverse=True)[:POOL_SIZE]
    return {"moves": [[round(m.week_gain * scale, 2), round(m.long_term, 2), m.add.id, m.drop.id if m.drop else None]
                      for m in top],
            "sigma": round(sigma_now * math.sqrt(scale), 2), "tau": round(tau, 2)}
