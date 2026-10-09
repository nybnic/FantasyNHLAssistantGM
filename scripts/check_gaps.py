"""How much of a projected gap between two fringe skaters shows up in results?

Every add is a gap: the added player's projected points minus the dropped
one's. The plan counts those gaps this week, the next two weeks and the long
run. If projections overstate differences among waiver-level players (they
regress, and the band is narrow), every gain is overstated, and most of all
the long run, which multiplies a small per-game gap by the weeks left.

Every Monday of a season (from its third week), each skater who played in
the 30 days before is projected as the bot does (aged prior + games so far,
healthy at HEALTHY_PLAY, an absent one along the return curve for the games
he has missed), for each window: this week, next week, the week after, and
weeks 3-6 (the long run's horizon). Points for the window = his team's games
in it x his odds of playing x his xFP. Kept: the "band" of skaters ranked
BAND by projected xFP that Monday (the rostered fringe and the free agents
worth adding in a 16-team league). Then, per window, the within-Monday slope
of actual points on projected points: a projected gap of 1 point between two
band players shows up as `slope` points. Fit on one season, checked on the other.

    python -m scripts.check_gaps                       # 2024-25 and 2025-26
    python -m scripts.check_gaps --band 1 120          # the stars, for contrast
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
from collections import defaultdict

from clients import nhl_stats
from config.league import SKATER_WEIGHTS, fantasy_points
from engine import availability
from model.projections import aged, project_skater, skater_priors
from scripts.backtest import history_of

SEASONS = (20242025, 20252026)
BAND = (120, 450)  # projected-xFP ranks among active skaters: ~176 skaters are rostered in 16 teams
WINDOWS = {"this week": (0, 7), "week +1": (7, 14), "week +2": (14, 21), "weeks 3-6": (21, 49)}
ACTIVE_DAYS = 30


def records(season: int, band: tuple[int, int]) -> list[dict]:
    """One row per (Monday, band skater): projected and actual points per window."""
    history = [nhl_stats.skater_games(s) for s in history_of(season)]
    priors, fallback = skater_priors(history)
    games = nhl_stats.skater_games(season)
    born = {}
    for s in (season, *history_of(season)):
        born.update(nhl_stats.skater_birth_dates(s))
    start = dt.date(season // 10_000, 10, 1)
    by_player, team_dates = defaultdict(list), defaultdict(set)
    for g in games:
        by_player[g.player_id].append(g)
        team_dates[g.team].add(g.date)
    first, last = games[0].date, games[-1].date
    horizon = max(end for _, end in WINDOWS.values())
    monday = first + dt.timedelta(days=14)
    monday += dt.timedelta(days=(7 - monday.weekday()) % 7)
    rows = []
    while monday + dt.timedelta(days=horizon) <= last:
        week = []
        for pid, gs in by_player.items():
            past = [g for g in gs if g.date < monday]
            if not past or (monday - past[-1].date).days > ACTIVE_DAYS:
                continue
            team = past[-1].team
            team_past = sorted(d for d in team_dates[team] if d < monday)
            played = {g.date for g in past}
            missed = next((i for i, d in enumerate(reversed(team_past)) if d in played), len(team_past))
            prior = priors.get(pid) or fallback["D" if gs[0].position == "D" else "F"]
            born_on = born.get(pid)
            x = project_skater(aged(prior, (start - born_on).days / 365.25 if born_on else None), past).xfp
            curve = availability.return_curve(missed) if missed else None
            row = {"monday": monday, "pid": pid, "xfp": x, "D": gs[0].position == "D"}
            for name, (lo, hi) in WINDOWS.items():
                days = [monday + dt.timedelta(days=i) for i in range(lo, hi)]
                proj = 0.0
                for d in days:
                    if d in team_dates[team]:
                        p = (availability.ahead(curve, (d - team_past[-1]).days, 0.0) if curve
                             else availability.HEALTHY_PLAY)
                        proj += p * x
                row[name] = (proj, sum(fantasy_points(g.stats, SKATER_WEIGHTS) for g in gs if g.date in days))
            week.append(row)
        week.sort(key=lambda r: -r["xfp"])
        rows += week[band[0] - 1:band[1]]
        monday += dt.timedelta(days=7)
    return rows


def slope(rows: list[dict], window: str) -> tuple[float, float, float, int]:
    """(slope, its standard error, residual SD, n): actual on projected,
    both demeaned within each Monday (gaps between players the same day)."""
    by_monday = defaultdict(list)
    for r in rows:
        by_monday[r["monday"]].append(r[window])
    xs, ys = [], []
    for pairs in by_monday.values():
        mx = sum(p for p, _ in pairs) / len(pairs)
        my = sum(a for _, a in pairs) / len(pairs)
        xs += [p - mx for p, _ in pairs]
        ys += [a - my for _, a in pairs]
    sxx = sum(x * x for x in xs)
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    resid = math.sqrt(sum((y - b * x) ** 2 for x, y in zip(xs, ys)) / (len(xs) - 2))
    return b, resid / math.sqrt(sxx), resid, len(xs)


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--band", type=int, nargs=2, default=BAND, help="projected-xFP ranks kept, e.g. 120 450")
    args = parser.parse_args()
    for season in SEASONS:
        rows = records(season, tuple(args.band))
        print(f"\n{season}, ranks {args.band[0]}-{args.band[1]}: {len(rows)} player-Mondays")
        print(f"  {'window':10} {'slope':>6} {'+/-':>5} {'resid SD':>9} {'proj gap SD':>12}")
        for window in WINDOWS:
            b, se, resid, n = slope(rows, window)
            by_monday = defaultdict(list)
            for r in rows:
                by_monday[r["monday"]].append(r[window][0])
            spread = math.sqrt(sum(sum((p - sum(ps) / len(ps)) ** 2 for p in ps) for ps in by_monday.values()) / n)
            print(f"  {window:10} {b:6.2f} {se:5.2f} {resid:9.1f} {spread:12.1f}")


if __name__ == "__main__":
    main_()
