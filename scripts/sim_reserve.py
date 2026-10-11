"""The playoff reserve: keep 6 adds for the playoffs always, fewer, or release
them when the playoffs look out of reach? A whole-league season sim.

Nico (2026-10-11): without the playoffs there's no title, so after a bad
start the 6 adds kept for weeks 24-26 might do more now. Here the reserve is
played several ways, all else the same (scripts/sim_leverage.py's league:
16 teams with strengths spread like the league's, weekly scores with the
matchup spread, top 8 by wins then points for, reseeded playoff rounds, my
add candidates each week a logged week's, the add price solved for the
budget's pace):
- "reserve R": R adds kept for the playoffs from week 1 (R = 6 is the rule);
- "release P from W": 6 kept, but from week W on, the first week my
  P(playoffs) (by my strength, the week and my wins so far, from seasons
  played without adds) is under P, all 6 join the regular-season budget.
The other 15 managers add too: each team gains, week by week, what my adds
gain under the rule (calibrated in a first pass), so a team of average
strength is average. In every policy the adds left at the regular season's end go to the playoffs
too (Yahoo's 36 is for the whole season), at most 2 a week, each a logged
week's best this-week gain. Paired: every policy sees the same seasons.

    python -m scripts.sim_reserve                    # 4000 seasons, my strength drawn
    python -m scripts.sim_reserve --seasons 10000 --strength -1

Result (2026-10-11, weeks 1-2's candidates, 10000 paired seasons each; vs
the rule, 6 kept: playoffs / title 53.9% / 10.1% drawn, 13.5% / 1.0% weak
(-1 sd), 90.9% / 15.5% strong (+1 sd)):
- a smaller reserve for good: reserve 4 / 2 / 0 playoffs +2 / +4 / +6 pts,
  title -3.6 / -6.4 / -8.1 pts (drawn); weak -0.5 / -0.8 / -0.9 of its 1.0;
  strong -6.7 / -11.0 / -13.3.
- release when P(playoffs) is low: under 15% from week 13 playoffs +1.8,
  title -0.2 (drawn), weak +3.8 / -0.4, strong +0.1 / -0.0; under 30% or
  from week 4, more playoffs and more title lost (drawn +4.1 / -1.2 at <30%
  from week 4).
- this week's P(win) scaled by its playoff leverage (sim_leverage's policy,
  in this league): playoffs -2 to -3 pts, title +0.2 (+/- 0.2) drawn, -0.3
  weak, +1.2 strong.
Every playoff week is win or go home, and 2 adds a week there are ~10 pts:
spent earlier they buy fewer title chances than they cost. So the 6 stay
reserved; a late release (under 15% from week 13) trades a little title
for playoff appearances, if those count for something.
Caveats: two logged weeks of candidates; an add's later points last 3 weeks;
the others add like me on average.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from config.league import MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK, PLAYOFF_TEAMS, PLAYOFF_WEEKS
from engine import addprice
from scripts import sim_leverage
from scripts.sim_leverage import HOLD_WEEKS, MEAN, STRENGTH_BUCKETS, WEEKS, League, _bucket, _phi, _ranks
from state import gm_state


def playoff_table(league: League, others_wins: np.ndarray, pf: np.ndarray) -> np.ndarray:
    """table[bucket, week, my wins before it] = P(playoffs), from these seasons played without adds."""
    s = league.scores.shape[0]
    rows = np.arange(s)
    wins = others_wins.copy()
    before = np.zeros((s, WEEKS), dtype=int)
    for week in range(WEEKS):
        before[:, week] = wins[:, 0]
        opp = league.my_opp[:, week]
        won = league.scores[:, 0, week] > league.scores[rows, opp, week]
        wins[:, 0] += won
        wins[rows, opp] += ~won
    made = (_ranks(wins, pf)[:, :PLAYOFF_TEAMS] == 0).any(axis=1)
    buckets = np.array([_bucket((m - MEAN) / league.team_sd) for m in league.means[:, 0]])
    table = np.full((len(STRENGTH_BUCKETS) + 1, WEEKS, WEEKS + 1), np.nan)
    for b in range(table.shape[0]):
        for week in range(WEEKS):
            for k in range(week + 1):
                sel = (buckets == b) & (before[:, week] == k)
                if sel.sum() >= 30:
                    table[b, week, k] = made[sel].mean()
            row = table[b, week, : week + 1]
            known = np.flatnonzero(~np.isnan(row))
            for k in range(week + 1):  # thin cells: the nearest record with data
                if np.isnan(row[k]):
                    row[k] = row[known[np.abs(known - k).argmin()]] if known.size else 0.5
    return table


def play(league, others_wins, others_pf, pools, lam_at, weight, pool_pick, table,
         reserve: int, release: tuple[float, int] | None = None) -> dict[str, np.ndarray]:
    s = league.scores.shape[0]
    sigma = league.game_sd * math.sqrt(2)
    made, title = np.zeros(s, bool), np.zeros(s, bool)
    wins_out, adds_out, released_at = np.zeros(s), np.zeros(s), np.full(s, -1)
    added = np.zeros((s, WEEKS + len(PLAYOFF_WEEKS)))  # my points from adds, week by week
    for i in range(s):
        wins, pf = others_wins[i].copy(), others_pf[i].copy()
        bucket = _bucket((league.means[i, 0] - MEAN) / league.team_sd)
        bank = np.zeros(WEEKS + HOLD_WEEKS + len(PLAYOFF_WEEKS))
        left, kept, my_wins, used = MAX_ADDS_PER_SEASON - reserve, reserve, 0, 0
        for week in range(WEEKS):
            if release and kept and week >= release[1] and table[bucket, week, my_wins] < release[0]:
                left, kept, released_at[i] = left + kept, 0, week
            opp = league.my_opp[i, week]
            margin = league.means[i, 0] + bank[week] - league.means[i, opp]
            lam = lam_at(left / (WEEKS - week))
            options = [list(m) for m in pools[pool_pick[i, week]]["moves"]]
            gain = 0.0
            for _ in range(MAX_ADDS_PER_WEEK):
                if left <= 0 or not options:
                    break
                p_now = _phi((margin + gain) / sigma)
                value, g, later, k = max((_phi((margin + gain + g) / sigma) - p_now + weight * later, g, later, k)
                                         for k, (g, later, *_) in enumerate(options))
                if value < lam:
                    break
                taken = options.pop(k)
                options = [o for o in options if o[2] != taken[2] and (o[3] is None or o[3] != taken[3])]
                gain += g
                bank[week + 1: week + 1 + HOLD_WEEKS] += later / HOLD_WEEKS
                left, used = left - 1, used + 1
            added[i, week] = bank[week] + gain
            won = league.scores[i, 0, week] + bank[week] + gain > league.scores[i, opp, week]
            pf[0] += bank[week] + gain
            my_wins += won
            wins[0] += won
            wins[opp] += not won
        seeds = _ranks(wins[None], pf[None])[0][:PLAYOFF_TEAMS]
        made[i] = 0 in seeds
        if made[i]:
            title[i] = _playoffs(league, i, seeds, bank, pools, pool_pick[i], left + kept, added[i])
        wins_out[i], adds_out[i] = my_wins, used
    return {"made": made, "title": title, "wins": wins_out, "adds": adds_out, "released": released_at,
            "added": added}


def _playoffs(league, i, seeds, bank, pools, picks, adds: int, added: np.ndarray | None = None) -> bool:
    """Reseeded rounds; my adds: those left, at most 2 a week, the best this-week gains."""
    alive = list(seeds)
    for r in range(len(PLAYOFF_WEEKS)):
        n = min(MAX_ADDS_PER_WEEK, adds)
        extra = [g for g in sorted((m[0] for m in pools[picks[(WEEKS + r) % WEEKS]]["moves"]), reverse=True)[:n]
                 if g > 0]
        adds -= len(extra)
        score = {t: league.means[i, t] + league.playoff_noise[i, r, t] for t in alive}
        score[0] += bank[WEEKS + r] + sum(extra)
        if added is not None:
            added[WEEKS + r] = bank[WEEKS + r] + sum(extra)
        half = len(alive) // 2
        winners = [h if score[h] >= score[l] else l for h, l in zip(alive[:half], alive[::-1][:half])]
        alive = [t for t in alive if t in winners]
        if 0 not in alive:
            return False
    return alive == [0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, default=4000)
    parser.add_argument("--table-seasons", type=int, default=40000)
    parser.add_argument("--strength", type=float, help="my team's strength in team sds (default: drawn)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    pools = [p for p in gm_state.load()["add_pools"].values() if p["moves"]]
    sigma, tau = pools[0]["sigma"], pools[0]["tau"]
    weight = addprice.later_weight(sigma, tau)
    paces = np.linspace(0.25, 2.0, 15)
    lam = {round(p, 3): addprice.solve(pools, p, weight) for p in paces}

    def lam_at(pace: float) -> float:
        return lam[round(min(paces, key=lambda x: abs(x - pace)), 3)]

    rng = np.random.default_rng(args.seed)
    big = League(args.table_seasons, sigma, tau, rng, args.strength)
    table = playoff_table(big, *big.others_wins())
    league = League(args.seasons, sigma, tau, rng, args.strength)
    pool_pick = rng.integers(0, len(pools), (args.seasons, WEEKS))
    # The other managers add too: each team gains, week by week, what my adds
    # gain under the rule (6 kept), so an average team is an average team again.
    calib = play(league, *league.others_wins(), pools, lam_at, weight, pool_pick, table, 6)["added"]
    boost = calib.mean(axis=0)
    league.scores[:, 1:, :] += boost[None, None, :WEEKS]
    league.means[:, 1:] += boost[:WEEKS].mean()
    league.playoff_noise[:, :, 1:] += (boost[WEEKS:] - boost[:WEEKS].mean())[None, :, None]
    others_wins, others_pf = league.others_wins()
    out_lev = {}
    policies = {f"reserve {r}": (r, None) for r in (6, 4, 2, 0)}
    policies |= {f"release <{p:.0%} from wk {w + 1}": (6, (p, w)) for p in (0.15, 0.3, 0.45) for w in (3, 7, 12)}
    # The 2026-10-03 alternative, on this league (the others adding too): this
    # week's P(win) change scaled by how much the week moves my playoff odds.
    lev_table, typical = sim_leverage.leverage_table(big, *big.others_wins(), "playoffs")
    for name, policy in (("price (sim_leverage)", "price"), ("leverage on playoffs", "leverage")):
        r = sim_leverage.play(league, others_wins, others_pf, pools, policy, lam_at, weight, lev_table, typical,
                              pool_pick)
        out_lev[name] = r
    print(f"{len(pools)} logged weeks of candidates, sigma {sigma:.1f}, tau {tau:.1f}; {args.seasons} seasons, "
          f"my strength {'drawn' if args.strength is None else f'{args.strength:+.1f} sd'}; the others' adds "
          f"{boost[:WEEKS].mean():.1f} pts a week, {boost[WEEKS:].mean():.1f} in playoff weeks")
    out = {name: play(league, others_wins, others_pf, pools, lam_at, weight, pool_pick, table, r, rel)
           for name, (r, rel) in policies.items()}
    n = args.seasons
    base = out["reserve 6"]
    for name, r in out.items():
        line = (f"  {name:22} playoffs {r['made'].mean():6.1%}  title {r['title'].mean():6.2%}  "
                f"wins {r['wins'].mean():5.2f}  regular-season adds {r['adds'].mean():4.1f}")
        if name != "reserve 6":
            for key in ("made", "title"):
                d = r[key].astype(float) - base[key].astype(float)
                line += f"  {key} {d.mean():+.4f} (+/- {d.std() / math.sqrt(n):.4f})"
        if (r["released"] >= 0).any():
            line += f"  released in {np.mean(r['released'] >= 0):.0%} of seasons"
        print(line)
    ref = out_lev["price (sim_leverage)"]
    for name, r in out_lev.items():
        line = f"  {name:22} playoffs {r['made'].mean():6.1%}  title {r['title'].mean():6.2%}  wins {r['wins'].mean():5.2f}"
        if r is not ref:
            for key in ("made", "title"):
                d = r[key].astype(float) - ref[key].astype(float)
                line += f"  {key} {d.mean():+.4f} (+/- {d.std() / math.sqrt(n):.4f})"
        print(line)


if __name__ == "__main__":
    main()
