"""Do early-season results count enough? (Nico, 2026-10-26: Evangelista 5.5 pts a
game in 8 games, projected 4.3; Kantserov 3.4 in 11, projected 5.4.)

The model updates each stat from the prior by `SKATER_K` games of evidence. If it
gives a player's season so far too little weight, the gap between his results
and his preseason projection keeps showing up after the Monday he's projected:
players running hot keep beating their projection, cold ones keep missing it.

Every Monday of a season from its third week, each skater who played in the 30
days before with 5+ games so far is projected as the bot does (aged prior +
games so far; the live prior is half DFO, which isn't archived, so here it is
history alone). Per player-Monday:
- form = his points a game so far - his preseason projection (no games),
- miss = his points a game in the window ahead - his projection that Monday.
Then the slope of miss on form, both demeaned within each Monday, weighted by
his games in the window, with standard errors clustered by player. Slope 0: the
model weighs results right. Above 0: it under-reacts (a hot start is more real
than it thinks; lower `SKATER_K`). Below 0: it over-reacts.

Then the same rows under `SKATER_K` x 0.5 / 0.75 / 1 / 1.5: MAE and pairwise
(within Monday and F/D, 2+ games in the window), as `scripts/backtest.py` scores.

Split by games so far (5-12, 13-25, 26+), by the projection's band (ranks
120-450 that Monday: the adds and drops, as `check_gaps`) and by NHL history
(newcomers take the position fallback here; live, DFO's projection).

    python -m scripts.check_form                  # 2024-25 and 2025-26
    python -m scripts.check_form --season 20252026
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
from collections import defaultdict

from clients import nhl_stats
from config.league import SKATER_WEIGHTS, fantasy_points
from model.projections import Params, aged, project_skater, skater_priors
from scripts.backtest import history_of, pairwise_accuracy

SEASONS = (20242025, 20252026)
WINDOWS = {"next week": (0, 7), "next 4 weeks": (0, 28), "next 12 weeks": (0, 84)}
K_SCALES = (0.5, 0.75, 1.0, 1.5)
BAND = (120, 450)
MIN_GAMES = 5
ACTIVE_DAYS = 30
GAMES_BUCKETS = ((5, 12), (13, 25), (26, 82))
EARLY_DAYS = 70  # Mondays up to ~week 10: where a start's weight matters most


def fp(game) -> float:
    return fantasy_points(game.stats, SKATER_WEIGHTS)


def records(season: int) -> list[dict]:
    history = [nhl_stats.skater_games(s) for s in history_of(season)]
    priors, fallback = skater_priors(history)
    games = nhl_stats.skater_games(season)
    born = {}
    for s in (season, *history_of(season)):
        born.update(nhl_stats.skater_birth_dates(s))
    start = dt.date(season // 10_000, 10, 1)
    by_player = defaultdict(list)
    for g in games:
        by_player[g.player_id].append(g)
    first, last = games[0].date, games[-1].date
    horizon = max(hi for _, hi in WINDOWS.values())
    monday = first + dt.timedelta(days=14)
    monday += dt.timedelta(days=(7 - monday.weekday()) % 7)
    rows = []
    while monday + dt.timedelta(days=horizon) <= last:
        week = []
        for pid, gs in by_player.items():
            past = [g for g in gs if g.date < monday]
            if len(past) < MIN_GAMES or (monday - past[-1].date).days > ACTIVE_DAYS:
                continue
            group = "D" if gs[0].position == "D" else "F"
            born_on = born.get(pid)
            prior = aged(priors.get(pid) or fallback[group], (start - born_on).days / 365.25 if born_on else None)
            row = {"monday": monday, "pid": pid, "group": group, "gp": len(past), "newcomer": pid not in priors,
                   "early": (monday - first).days <= EARLY_DAYS,
                   "form": sum(fp(g) for g in past) / len(past) - project_skater(prior, []).xfp,
                   "proj": {k: project_skater(prior, past, Params(k_scale=k)).xfp for k in K_SCALES}}
            for name, (lo, hi) in WINDOWS.items():
                ahead = [g for g in gs if monday + dt.timedelta(days=lo) <= g.date < monday + dt.timedelta(days=hi)]
                row[name] = (len(ahead), sum(fp(g) for g in ahead) / len(ahead) if ahead else None)
            week.append(row)
        week.sort(key=lambda r: -r["proj"][1.0])
        for rank, r in enumerate(week, 1):
            r["band"] = BAND[0] <= rank <= BAND[1]
        rows += week
        monday += dt.timedelta(days=7)
    return rows


def slope(rows: list[dict], window: str) -> tuple[float, float, int]:
    """(slope of miss on form, its player-clustered SE, player-Mondays)."""
    by_monday = defaultdict(list)
    for r in rows:
        n, actual = r[window]
        if n:
            by_monday[r["monday"]].append((r["pid"], n, r["form"], actual - r["proj"][1.0]))
    pts = []
    for items in by_monday.values():
        w = sum(n for _, n, _, _ in items)
        mx = sum(n * x for _, n, x, _ in items) / w
        my = sum(n * y for _, n, _, y in items) / w
        pts += [(pid, n, x - mx, y - my) for pid, n, x, y in items]
    if len(pts) < 30:
        return math.nan, math.nan, len(pts)
    sxx = sum(n * x * x for _, n, x, _ in pts)
    b = sum(n * x * y for _, n, x, y in pts) / sxx
    score = defaultdict(float)
    for pid, n, x, y in pts:
        score[pid] += n * x * (y - b * x)
    return b, math.sqrt(sum(s * s for s in score.values())) / sxx, len(pts)


def accuracy(rows: list[dict], window: str, k: float) -> tuple[float, float, float]:
    """(MAE per game, weighted by games; pairwise; bias) for SKATER_K x k."""
    err = bias = weight = 0.0
    buckets = defaultdict(list)
    for r in rows:
        n, actual = r[window]
        if not n:
            continue
        err += n * abs(r["proj"][k] - actual)
        bias += n * (r["proj"][k] - actual)
        weight += n
        if n >= 2:
            buckets[(r["monday"], r["group"])].append((r["proj"][k], actual))
    agree = total = 0
    for pairs in buckets.values():
        a, t = pairwise_accuracy(pairs)
        agree += a
        total += t
    return err / weight, agree / total if total else math.nan, bias / weight


def report(season: int, rows: list[dict]) -> None:
    early = [r for r in rows if r["early"]]
    subsets = [("all, weeks 3-10", early), ("band 120-450, weeks 3-10", [r for r in early if r["band"]]),
               *((f"{lo}-{hi} games so far, weeks 3-10", [r for r in early if lo <= r["gp"] <= hi])
                 for lo, hi in GAMES_BUCKETS[:2]),
               ("newcomers, weeks 3-10", [r for r in early if r["newcomer"]]),
               ("all, week 11 on", [r for r in rows if not r["early"]])]
    print(f"\n{season}: {len(rows)} player-Mondays ({len(early)} in weeks 3-10)")
    print("Slope of the miss ahead on form so far (0 = results weighed right; + = under-reacts), +/- SE")
    print(f"  {'rows':34}" + "".join(f"{w:>22}" for w in WINDOWS))
    for name, sub in subsets:
        cells = []
        for w in WINDOWS:
            b, se, n = slope(sub, w)
            cells.append(f"{b:+6.3f} +/-{se:5.3f} ({n:>5})")
        print(f"  {name:34}" + "".join(f"{c:>22}" for c in cells))
    print("SKATER_K scale: MAE / pairwise / bias, weeks 3-10, same player-Mondays")
    for name, sub in subsets[:2] + subsets[4:5]:
        print(f"  {name}")
        for w in WINDOWS:
            cells = []
            for k in K_SCALES:
                mae, pair, bias = accuracy(sub, w, k)
                cells.append(f"x{k:<4g} {mae:.3f} {pair:.1%} {bias:+.2f}")
            print(f"    {w:14}" + "   ".join(cells))


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, nargs="*", default=list(SEASONS))
    args = parser.parse_args()
    for season in args.season:
        report(season, records(season))


if __name__ == "__main__":
    main_()
