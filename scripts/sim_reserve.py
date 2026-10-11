"""The playoff reserve: keep 6 adds for the playoffs always, fewer, or release
them when the playoffs look out of reach? A whole-league season sim, scored
in prize money.

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
  played without adds) is under P, all 6 join the regular-season budget;
- "leverage on playoffs": 6 kept, this week's P(win) change scaled by how
  much the week moves my playoff odds (sim_leverage's 2026-10-03 policy).
The other 15 managers add too: each team gains, week by week, what my adds
gain under the rule (calibrated in a first pass), so a team of average
strength is average. In every policy the adds left at the regular season's
end go to the playoffs too (Yahoo's 36 is for the whole season), at most 2 a
week, each a logged week's best this-week gain. Scored by the expected prize
(config.league.PRIZE_SHARES: 1st in the regular season 25%, champion 45%,
runner-up 20%, third 10%, the semifinal losers playing for third). Paired:
every policy sees the same seasons.

    python -m scripts.sim_reserve                    # 4000 seasons, my strength drawn
    python -m scripts.sim_reserve --seasons 10000 --strength -1

Result (2026-10-11, weeks 1-2's candidates, 10000 paired seasons each). The
rule (6 kept) earns 7.6% of the pool a season for a team of average strength
(an even share is 6.25%), 0.8% weak (-1 sd), 13.0% strong (+1 sd). Every
alternative earns less, in % of the pool (+/- SE): reserve 4 / 2 / 0 -0.9 /
-2.6 / -4.0 (0.07-0.12) average, -0.2 / -0.4 / -0.7 weak, -1.7 / -4.4 / -6.5
strong; releasing the 6 when P(playoffs) < 15% from week 13 -0.11 (0.02)
average, -0.26 (0.03) weak, 0.00 strong, and earlier or higher thresholds
lose more; leverage on playoffs -0.7 / -0.2 / -1.3 (it also gives up 1st in
the regular season: 5.7% -> 2.6% average). Releasing adds buys playoff
appearances (+2 to +6 pts) and a little more 1st place, but the title (45%),
runner-up (20%) and third (10%) are decided in elimination weeks where 2 adds
a week are worth ~10 pts each week, so the adds kept for them earn more.
Caveats: two logged weeks of candidates; an add's later points last 3 weeks;
the others add like me on average; third place assumes a 3rd-place game.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from config.league import MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK, PLAYOFF_TEAMS, PLAYOFF_WEEKS, PRIZE_SHARES
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
         reserve: int, release: tuple[float, int] | None = None,
         leverage: tuple[np.ndarray, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """One policy over every season. `leverage` (sim_leverage.leverage_table's
    table and typical): this week's P(win) change scaled by how much the week
    moves my playoff odds next to an average week."""
    s = league.scores.shape[0]
    sigma = league.game_sd * math.sqrt(2)
    made, first, place = np.zeros(s, bool), np.zeros(s, bool), np.zeros(s, int)
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
            scale = leverage[0][bucket, week, my_wins] / max(leverage[1][bucket], 1e-6) if leverage else 1.0
            opp = league.my_opp[i, week]
            margin = league.means[i, 0] + bank[week] - league.means[i, opp]
            lam = lam_at(left / (WEEKS - week))
            options = [list(m) for m in pools[pool_pick[i, week]]["moves"]]
            gain = 0.0
            for _ in range(MAX_ADDS_PER_WEEK):
                if left <= 0 or not options:
                    break
                p_now = _phi((margin + gain) / sigma)
                value, g, later, k = max((scale * (_phi((margin + gain + g) / sigma) - p_now) + weight * later,
                                          g, later, k) for k, (g, later, *_) in enumerate(options))
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
        made[i], first[i] = 0 in seeds, seeds[0] == 0
        if made[i]:
            place[i] = _playoffs(league, i, seeds, bank, pools, pool_pick[i], left + kept, added[i])
        wins_out[i], adds_out[i] = my_wins, used
    prize = (PRIZE_SHARES["regular_season"] * first + PRIZE_SHARES["champion"] * (place == 1)
             + PRIZE_SHARES["runner_up"] * (place == 2) + PRIZE_SHARES["third"] * (place == 3))
    return {"made": made, "first": first, "title": place == 1, "place": place, "prize": prize, "wins": wins_out,
            "adds": adds_out, "released": released_at, "added": added}


def _playoffs(league, i, seeds, bank, pools, picks, adds: int, added: np.ndarray | None = None) -> int:
    """My place: 1-4, or 0 out in the quarterfinal. Reseeded rounds (a tie goes
    to the higher seed); the semifinal losers play for third in the last week.
    My adds: those left, at most 2 a week, the best this-week gains."""
    alive, losers = list(seeds), []
    last = len(PLAYOFF_WEEKS) - 1
    for r in range(len(PLAYOFF_WEEKS)):
        third = r == last and 0 in losers
        if third:
            alive = losers
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
        if r == last:
            return (3 if 0 in winners else 4) if third else (1 if 0 in winners else 2)
        losers = [t for t in alive if t not in winners]
        alive = [t for t in alive if t in winners]
        if 0 not in alive and 0 not in (losers if r == last - 1 else []):
            return 0
    return 0


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
    lev = sim_leverage.leverage_table(big, *big.others_wins(), "playoffs")
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
    policies = {f"reserve {r}": (r, None, None) for r in (6, 4, 2, 0)}
    policies |= {f"release <{p:.0%} from wk {w + 1}": (6, (p, w), None) for p in (0.15, 0.3, 0.45) for w in (3, 7, 12)}
    policies["leverage on playoffs"] = (6, None, lev)
    print(f"{len(pools)} logged weeks of candidates, sigma {sigma:.1f}, tau {tau:.1f}; {args.seasons} seasons, "
          f"my strength {'drawn' if args.strength is None else f'{args.strength:+.1f} sd'}; the others' adds "
          f"{boost[:WEEKS].mean():.1f} pts a week, {boost[WEEKS:].mean():.1f} in playoff weeks")
    out = {name: play(league, others_wins, others_pf, pools, lam_at, weight, pool_pick, table, r, rel, lv)
           for name, (r, rel, lv) in policies.items()}
    n = args.seasons
    base = out["reserve 6"]
    print(f"  {'policy':22} {'playoffs':>8} {'1st':>6} {'title':>6} {'2nd':>6} {'3rd':>6} {'prize':>7}  "
          "prize vs reserve 6, % of the pool (+/- SE)")
    for name, r in out.items():
        d = r["prize"] - base["prize"]
        line = (f"  {name:22} {r['made'].mean():8.1%} {r['first'].mean():6.1%} {r['title'].mean():6.2%} "
                f"{np.mean(r['place'] == 2):6.2%} {np.mean(r['place'] == 3):6.2%} {r['prize'].mean():7.2%}")
        if name != "reserve 6":
            line += f"  {100 * d.mean():+.2f} (+/- {100 * d.std() / math.sqrt(n):.2f})"
        if (r["released"] >= 0).any():
            line += f"  released in {np.mean(r['released'] >= 0):.0%} of seasons"
        print(line)


if __name__ == "__main__":
    main()
