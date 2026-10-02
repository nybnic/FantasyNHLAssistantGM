"""Does the add price win more weeks than the old points rule? A season sim.

Each simulated regular season has 23 weeks and the real budget (29 adds
before the playoff reserve, at most 2 a week). Every week draws an expected
margin (spread tau) and a result (spread sigma); an add's points this week
move the margin, its later points are spread over the next weeks it would
be held. The candidate adds are this week's real ones (scaled to a full
week), or with --logged every week the bot has logged (state["add_pools"]).
All policies face the same draws.

    python -m scripts.sim_add_policy                # 2000 seasons
    python -m scripts.sim_add_policy --seasons 5000

Policies: "price" (engine/addprice.py), "points" (the rule it replaced: 3 pts
at an even pace, scaled by the pace, plus a 2-win-pt or 2-week-payoff gate,
nothing counted in a decided week), "never".
"""
from __future__ import annotations

import argparse
import datetime as dt
import math

import numpy as np

from bot import common, weekly
from engine import addprice, matchup
from league import roster as roster_mod, teams, weeks
from model import context
from state import gm_state

WEEKS = 23
BUDGET = 29
HOLD_WEEKS = 3  # later points arrive over this many weeks (a streamer's hold)
OLD_BASE, OLD_MIN_WIN, OLD_CONCEDE = 3.0, 0.02, 0.10


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def current_pool() -> dict:
    date = dt.datetime.now(common.NHL_TIME).date()
    week = weeks.week_of(date)
    state, players, league = gm_state.load(), roster_mod.load(), teams.load()
    wk = weekly.week_inputs(date, week, players, league, state, context.build, common.current_opponent(state, week))
    ranked = matchup.candidate_moves(players, wk.them, weekly.add_candidates(wk, weekly.next_week(week, players, wk)),
                                     wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future, wk.weeks_after,
                                     weekly.waiver_days(wk.pool, league, date),  # what the week offers, adds or not
                                     wk.so_far, wk.hold_days, wk.later_weight)
    remaining = sum(d >= date for d in wk.days)
    return addprice.pool_entry(ranked, remaining, math.sqrt(wk.me.variance + wk.them.variance), wk.tau)


def simulate(pools: list[dict], seasons: int, seed: int) -> tuple[dict[str, tuple[float, float, float]], tuple]:
    rng = np.random.default_rng(seed)
    sigma, tau = pools[0]["sigma"], pools[0]["tau"]
    weight = addprice.later_weight(sigma, tau)
    paces = np.linspace(0.25, 2.0, 15)
    lam_at = {round(p, 3): addprice.solve(pools, p, weight) for p in paces}

    def price_lam(pace: float) -> float:
        p = min(paces, key=lambda x: abs(x - pace))
        return lam_at[round(p, 3)]

    results = {"price": [], "points": [], "never": []}
    adds_used = {k: [] for k in results}
    for _ in range(seasons):
        margins = rng.normal(0, tau, WEEKS)
        noise = rng.normal(0, sigma, WEEKS)
        week_pools = [pools[i] for i in rng.integers(0, len(pools), WEEKS)]
        for policy in results:
            left, bank, wins, used = BUDGET, np.zeros(WEEKS + HOLD_WEEKS), 0, 0
            for w in range(WEEKS):
                margin = margins[w] + bank[w]
                pace = left / (WEEKS - w)
                options = [list(m) for m in week_pools[w]["moves"]]
                for _slot in range(2):
                    if left <= 0 or not options or policy == "never":
                        break
                    p_now = _phi(margin / sigma)
                    gains = [(_phi((margin + g) / sigma) - p_now, g, later, k) for k, (g, later, *_) in enumerate(options)]
                    if policy == "price":
                        dp, g, later, k = max(gains, key=lambda x: x[0] + weight * x[2])
                        ok = dp + weight * later >= price_lam(pace)
                    else:
                        even = BUDGET / WEEKS
                        thr = OLD_BASE / min(max(pace / even, 0.5), 2.0)
                        counts = OLD_CONCEDE <= p_now <= 1 - OLD_CONCEDE
                        dp, g, later, k = max(gains, key=lambda x: (x[1] if counts else 0) + x[2])
                        ok = (g if counts else 0) + later >= thr and (dp >= OLD_MIN_WIN or later >= 2 * thr)
                    if not ok:
                        break
                    taken = options.pop(k)
                    options = [o for o in options if o[2] != taken[2] and (o[3] is None or o[3] != taken[3])]
                    margin += g
                    bank[w + 1:w + 1 + HOLD_WEEKS] += later / HOLD_WEEKS
                    left, used = left - 1, used + 1
                wins += margin + noise[w] > 0
            results[policy].append(wins)
            adds_used[policy].append(used)
    diff = np.array(results["price"]) - np.array(results["points"])  # same seasons: a paired difference
    return ({k: (float(np.mean(v)), float(np.std(v) / math.sqrt(len(v))), float(np.mean(adds_used[k])))
             for k, v in results.items()}, (float(diff.mean()), float(diff.std() / math.sqrt(len(diff)))))


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, default=2000)
    parser.add_argument("--logged", action="store_true", help="use every logged week's candidates, not this week's")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    pools = list(gm_state.load()["add_pools"].values()) if args.logged else [current_pool()]
    print(f"{len(pools)} week(s) of candidates, sigma {pools[0]['sigma']:.1f}, tau {pools[0]['tau']:.1f}; "
          f"{args.seasons} seasons of {WEEKS} weeks, {BUDGET} adds")
    table, (diff, diff_se) = simulate(pools, args.seasons, args.seed)
    for policy, (wins, se, used) in table.items():
        print(f"  {policy:7} {wins:5.2f} wins a season (+/- {se:.2f}), {used:4.1f} adds used")
    print(f"  price - points: {diff:+.3f} wins a season (+/- {diff_se:.3f}, paired)")


if __name__ == "__main__":
    main_()
