"""Should a week's leverage weigh the add decision? A whole-league season sim.

The add price (engine/addprice.py) treats every regular-season week alike.
The alternative ("leverage") scales this week's change in P(win) by how much
a win this week moves my playoff odds next to an average week
(engine/season.py's leverage), so bubble weeks get adds and weeks that are
already locked in or out don't. Same budget, same price solver, same pace.

Each simulated season: 16 teams with strengths spread like the league's
(tau), weekly scores with the matchup spread (sigma), my schedule random but
fixed for the season, the others paired at random each week; top 8 by wins
then points for; reseeded playoffs (1 v 8 ...), where both policies spend
the 6 reserved adds the same way. My add candidates each week are a logged
week's (state["add_pools"]): an add's points move this week's margin, its
later points arrive over the next HOLD_WEEKS. Both policies face the same
draws (paired).

The bot's real leverage comes from simulating the season from the standings
each week; here it's a lookup table built the same way from no-add seasons
(by week, my wins so far and my strength): close to what the bot would see.

    python -m scripts.sim_leverage                   # 2000 seasons
    python -m scripts.sim_leverage --seasons 10000 --strength 1 --target title --playoff-weight 2 4

Result (2026-10-03, week 1's candidates, 10000 paired seasons per row; title
odds vs the add price as it is):
- leverage on P(playoffs): playoffs +0.6 to +1.6 pts, title -0.3 to -1.0
  (weak, average, strong team, drawn): it saves adds in weeks already locked
  in or out, which are the late weeks whose adds carry points into the playoffs
  (playoff carry-over 26 -> 21 pts).
- leverage on P(title): title -1.0 to -2.6 (the noisier table makes it worse).
- playoff-week points counted 2x (4x, 8x identical: every candidate already
  passes at 2x): title -0.4 to -0.9. It spends the budget in weeks 21-22 and has
  none left for week 23, whose adds land fully in the playoffs.
So the price as it is, spending evenly to the end, wins the most titles here.
Caveats: one logged week of candidates; an add's later points arrive over 3
weeks (real keepers last), which is exactly what drives the playoff carry-over.
Re-run with --logged weeks once 6+ are logged and standings are read.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from config.league import LEAGUE_TEAMS, MAX_ADDS_PER_WEEK, PLAYOFF_TEAMS, PLAYOFF_WEEKS, REGULAR_SEASON_WEEKS
from engine import addprice
from state import gm_state

WEEKS = REGULAR_SEASON_WEEKS
BUDGET = 30  # 36 less the playoff reserve, from week 1
RESERVE = addprice.PLAYOFF_RESERVE
HOLD_WEEKS = 3
MEAN = 200.0  # a team's weekly points (the level doesn't matter, only spreads)
STRENGTH_BUCKETS = (-1.0, 1.0)  # my strength in team-sd units: weak / middle / strong


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _ranks(wins: np.ndarray, pf: np.ndarray) -> np.ndarray:
    """Teams in seed order (wins, then points for) per season row."""
    return np.lexsort((-pf, -wins))


def _bucket(strength_sd: float) -> int:
    return int(np.searchsorted(STRENGTH_BUCKETS, strength_sd))


class League:
    """One batch of seasons' draws, shared by every policy."""

    def __init__(self, seasons: int, sigma: float, tau: float, rng: np.random.Generator, my_shift: float | None):
        team_sd, game_sd = tau / math.sqrt(2), sigma / math.sqrt(2)
        self.team_sd, self.game_sd = team_sd, game_sd
        self.means = MEAN + rng.normal(0, team_sd, (seasons, LEAGUE_TEAMS))
        if my_shift is not None:  # me at a fixed strength (team sds above the mean)
            self.means[:, 0] = MEAN + my_shift * team_sd
        self.scores = self.means[:, :, None] + rng.normal(0, game_sd, (seasons, LEAGUE_TEAMS, WEEKS))
        self.my_opp = np.stack([rng.permutation(np.arange(1, LEAGUE_TEAMS))[np.arange(WEEKS) % 15]
                                for _ in range(seasons)])
        order = rng.random((seasons, WEEKS, LEAGUE_TEAMS - 2)).argsort(axis=2)
        self.pair_order = order  # positions among the 14 others, paired 0-1, 2-3, ...
        self.playoff_noise = rng.normal(0, game_sd, (seasons, len(PLAYOFF_WEEKS), LEAGUE_TEAMS))

    def others_wins(self) -> tuple[np.ndarray, np.ndarray]:
        """Wins and points for of every team from games not involving me, and
        my opponents' points for (their games against me are added later)."""
        s, n, w = self.scores.shape
        wins = np.zeros((s, n))
        rows = np.arange(s)
        for week in range(w):
            opp = self.my_opp[:, week]
            everyone = np.tile(np.arange(1, n), (s, 1))
            others = everyone[everyone != opp[:, None]].reshape(s, n - 2)
            order = np.take_along_axis(others, self.pair_order[:, week, :], axis=1)
            for a, b in zip(order[:, 0::2].T, order[:, 1::2].T):
                a_won = self.scores[rows, a, week] > self.scores[rows, b, week]
                wins[rows, a] += a_won
                wins[rows, b] += ~a_won
        return wins, self.scores.sum(axis=2)


def leverage_table(league: League, others_wins: np.ndarray, pf: np.ndarray,
                   target: str = "playoffs") -> tuple[np.ndarray, np.ndarray]:
    """(table, typical): table[bucket, week, my wins before it] = P(playoffs |
    win it) - P(playoffs | lose it), from these seasons played without adds;
    typical[bucket] = the average week's leverage as seasons actually go."""
    s = league.scores.shape[0]
    rows = np.arange(s)
    my_won = np.zeros((s, WEEKS), dtype=bool)
    wins = others_wins.copy()
    for week in range(WEEKS):
        opp = league.my_opp[:, week]
        won = league.scores[:, 0, week] > league.scores[rows, opp, week]
        my_won[:, week] = won
        wins[:, 0] += won
        wins[rows, opp] += ~won
    seeds = _ranks(wins, pf)[:, :PLAYOFF_TEAMS]
    made = (seeds == 0).any(axis=1)
    if target == "title":  # the title, no adds in the playoffs either
        none = [{"moves": []}]
        picks = np.zeros(len(PLAYOFF_WEEKS) + WEEKS, dtype=int)
        bank = np.zeros(WEEKS + HOLD_WEEKS + len(PLAYOFF_WEEKS))
        made = np.array([m and _playoffs(league, i, seeds[i], bank, none, picks) for i, m in enumerate(made)])
    buckets = np.array([_bucket((m - MEAN) / league.team_sd) for m in league.means[:, 0]])
    before = np.cumsum(my_won, axis=1) - my_won
    table = np.full((len(STRENGTH_BUCKETS) + 1, WEEKS, WEEKS + 1), np.nan)
    for b in range(table.shape[0]):
        for week in range(WEEKS):
            for k in range(week + 1):
                sel = (buckets == b) & (before[:, week] == k)
                won, lost = sel & my_won[:, week], sel & ~my_won[:, week]
                if won.sum() >= 30 and lost.sum() >= 30:
                    table[b, week, k] = made[won].mean() - made[lost].mean()
    # Thin cells: the nearest record that has data, else the week's average.
    for b in range(table.shape[0]):
        for week in range(WEEKS):
            row = table[b, week, : week + 1]
            known = np.flatnonzero(~np.isnan(row))
            for k in range(week + 1):
                if np.isnan(row[k]):
                    row[k] = row[known[np.abs(known - k).argmin()]] if known.size else 0.0
    lived = table[buckets[:, None], np.arange(WEEKS)[None, :], before]
    typical = np.array([lived[buckets == b].mean() if (buckets == b).any() else 1.0 for b in range(table.shape[0])])
    return table, typical


def play(league: League, others_wins: np.ndarray, others_pf: np.ndarray, pools: list[dict], policy: str,
         lam_at, weight: float, table: np.ndarray, typical_by_bucket: np.ndarray,
         pool_pick: np.ndarray, playoff_weight: float = 1.0) -> dict[str, np.ndarray]:
    """policy: "never", "price" (the add price as it is), "leverage" (this
    week's win odds scaled by its leverage), "playoff" (the price, with later
    points that land in playoff weeks counted `playoff_weight` times)."""
    s = league.scores.shape[0]
    sigma = league.game_sd * math.sqrt(2)
    made, title, wins_out, adds_out = np.zeros(s, bool), np.zeros(s, bool), np.zeros(s), np.zeros(s)
    for i in range(s):
        wins, pf = others_wins[i].copy(), others_pf[i].copy()
        bucket = _bucket((league.means[i, 0] - MEAN) / league.team_sd)
        typical = max(typical_by_bucket[bucket], 1e-6)
        bank = np.zeros(WEEKS + HOLD_WEEKS + len(PLAYOFF_WEEKS))
        left, my_wins, used = BUDGET, 0, 0
        for week in range(WEEKS):
            opp = league.my_opp[i, week]
            margin = league.means[i, 0] + bank[week] - league.means[i, opp]
            pace = left / (WEEKS - week)
            scale = (table[bucket, week, my_wins] / typical) if policy == "leverage" else 1.0
            # Of an add's later points (spread over the next HOLD_WEEKS), the share in playoff weeks.
            in_playoffs = max(0, week + HOLD_WEEKS - WEEKS + 1) / HOLD_WEEKS
            later_scale = 1 + (playoff_weight - 1) * in_playoffs if policy == "playoff" else 1.0
            options = [list(m) for m in pools[pool_pick[i, week]]["moves"]]
            gain = 0.0
            for _ in range(MAX_ADDS_PER_WEEK):
                if left <= 0 or not options or policy == "never":
                    break
                p_now = _phi((margin + gain) / sigma)
                values = [(scale * (_phi((margin + gain + g) / sigma) - p_now) + weight * later * later_scale, g, later, k)
                          for k, (g, later, *_) in enumerate(options)]
                value, g, later, k = max(values)
                if value < lam_at(pace):
                    break
                taken = options.pop(k)
                options = [o for o in options if o[2] != taken[2] and (o[3] is None or o[3] != taken[3])]
                gain += g
                bank[week + 1: week + 1 + HOLD_WEEKS] += later / HOLD_WEEKS
                left, used = left - 1, used + 1
            mine = league.scores[i, 0, week] + bank[week] + gain
            won = mine > league.scores[i, opp, week]
            pf[0] += bank[week] + gain
            my_wins += won
            wins[0] += won
            wins[opp] += not won
        seeds = _ranks(wins[None], pf[None])[0][:PLAYOFF_TEAMS]
        made[i] = 0 in seeds
        if made[i]:
            title[i] = _playoffs(league, i, seeds, bank, pools, pool_pick[i])
        wins_out[i], adds_out[i] = my_wins, used
    return {"made": made, "title": title, "wins": wins_out, "adds": adds_out}


def _playoffs(league: League, i: int, seeds: np.ndarray, bank: np.ndarray, pools, picks) -> bool:
    """Reseeded rounds; my adds: the 6 reserved, 2 a week, the best this-week gains."""
    alive = list(seeds)
    for r in range(len(PLAYOFF_WEEKS)):
        extra = sorted((m[0] for m in pools[picks[(WEEKS + r) % WEEKS]]["moves"]), reverse=True)[:MAX_ADDS_PER_WEEK]
        score = {t: league.means[i, t] + league.playoff_noise[i, r, t] for t in alive}
        score[0] = score.get(0, 0.0) + bank[WEEKS + r] + sum(g for g in extra if g > 0)
        half = len(alive) // 2
        winners = [h if score[h] >= score[l] else l for h, l in zip(alive[:half], alive[::-1][:half])]
        alive = [t for t in alive if t in winners]
        if 0 not in alive:
            return False
    return alive == [0]


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, default=2000)
    parser.add_argument("--table-seasons", type=int, default=40000)
    parser.add_argument("--strength", type=float, help="my team's strength in team sds (default: drawn like the rest)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--target", choices=("playoffs", "title"), default="playoffs",
                        help="what the leverage policy's week weight measures")
    parser.add_argument("--playoff-weight", type=float, nargs="*", default=[2.0, 4.0],
                        help="the playoff policy: later points in playoff weeks count this many times")
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
    table, typical = leverage_table(big, *big.others_wins(), args.target)
    league = League(args.seasons, sigma, tau, rng, args.strength)
    others_wins, others_pf = league.others_wins()
    pool_pick = rng.integers(0, len(pools), (args.seasons, WEEKS))
    print(f"{len(pools)} logged week(s) of candidates, sigma {sigma:.1f}, tau {tau:.1f}; {args.seasons} seasons, "
          f"my strength {'drawn' if args.strength is None else f'{args.strength:+.1f} sd'}, "
          f"leverage on {args.target}")
    out = {p: play(league, others_wins, others_pf, pools, p, lam_at, weight, table, typical, pool_pick)
           for p in ("never", "price", "leverage")}
    for k in args.playoff_weight:
        out[f"playoff x{k:g}"] = play(league, others_wins, others_pf, pools, "playoff", lam_at, weight, table,
                                      typical, pool_pick, k)
    n = args.seasons
    for policy, r in out.items():
        print(f"  {policy:11} playoffs {r['made'].mean():6.1%}  title {r['title'].mean():6.2%}  "
              f"wins {r['wins'].mean():5.2f}  adds {r['adds'].mean():4.1f}")
    for policy in (p for p in out if p not in ("never", "price")):
        for key in ("made", "title"):
            d = out[policy][key].astype(float) - out["price"][key].astype(float)
            print(f"  {policy} - price, {key:6}: {d.mean():+.4f} (+/- {d.std() / math.sqrt(n):.4f}, paired)")


if __name__ == "__main__":
    main_()
