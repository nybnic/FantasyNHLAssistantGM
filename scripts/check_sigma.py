"""Is P(win)'s spread right? Checked on the 2025-26 season (--season for another).

The weekly plan turns two teams' projected weeks into P(win) with a normal
model whose spread (sigma) comes from per-game variances (skater ~2.5 x xFP,
goalie start ~21) plus MODEL_SD_SHARE. If sigma is too small, every point
looks worth too much win probability, and so does every add.

Here: leagues of 16 teams snake-drafted on preseason projections (3 C, 2 LW,
2 RW, 4 D, 3 G each, NHL positions). Every Monday from week 3, each team's
week is projected the way engine/matchup.py does (projections from games
before that Monday, the best projected lineup each day, goalie start odds
from the team's starts so far, the goalie minimum) and scored on what the
lineup actually got. Skaters who didn't play in the week before count as out
(the bot sees injuries). Then:
- z = (actual - projected) / sigma per team-week: its SD should be ~1.
- Teams paired into matchups: predicted P(win) against how often it happened.

    python -m scripts.check_sigma            # 8 leagues
    python -m scripts.check_sigma --leagues 20
    python -m scripts.check_sigma --availability old   # skaters who played last week
        certain to play, the others left out (the replay before the return curves)
    python -m scripts.check_sigma --no-age             # priors not aged (replays before 2026-10-03)
    python -m scripts.check_sigma --ahead 2            # projected 2 weeks before (the plan's weeks ahead)
    python -m scripts.check_sigma --season 20242025
    python -m scripts.check_sigma --from-day 6 5 0     # the week's last night, last two, the whole week

`--from-day N`: projected the morning of the week's day N (0 = Monday, 6 = Sunday),
the days from N on only, on what was known then, as the plan reads a week in
progress (the days before are banked; a team-week whose goalie minimum isn't
met by then is left out). The tails table says whether a team behind by k
sigma catches up as often as the normal says: P(win) and every win-pt of a
week nearly decided come from there.

Default `--availability bot`: a skater who played his team's last game plays at
availability.HEALTHY_PLAY; one who missed it returns along the curve for how
many games he has missed (availability.return_curve), counted from that
game, as the bot projects a player DFO lists as out or IR.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from collections import defaultdict
from statistics import mean

from clients import nhl_stats
from config.league import GOALIE_WEIGHTS, MIN_GOALIE_GAMES_PER_WEEK, SKATER_WEIGHTS, fantasy_points
from engine import availability, lineup
from engine.matchup import (GOALIE_START_VARIANCE, MARGIN_REALIZES, MODEL_SD_SHARE, SKATER_VARIANCE_PER_XFP,
                            _at_least)
from model.projections import aged, goalie_priors, project_goalie, project_skater, skater_priors
from scripts.backtest import history_of

TARGET = 20252026
SHAPE = {"C": 3, "LW": 2, "RW": 2, "D": 4, "G": 3}
POS = {"C": "C", "L": "LW", "R": "RW", "D": "D"}
TAILS = (0.5, 1.0, 1.15, 1.44, 2.0, 2.5)  # 1.44 / 1.15: 2026-10-25, week 4's last night before / after Guhle


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def load(target: int = TARGET):
    history = history_of(target)
    skater_history = [nhl_stats.skater_games(s) for s in history]
    goalie_history = [nhl_stats.goalie_games(s) for s in history]
    s_priors, s_fallback = skater_priors(skater_history)
    g_priors, g_fallback = goalie_priors(goalie_history)
    skaters, goalies = nhl_stats.skater_games(target), nhl_stats.goalie_games(target)
    return s_priors, s_fallback, g_priors, g_fallback, skaters, goalies


def draft(pool: dict[str, list[tuple[float, int]]], teams: int, rng: random.Random) -> list[dict[str, list[int]]]:
    """Snake draft by preseason value, each team filling SHAPE; a little noise
    so leagues differ."""
    ranked = {pos: sorted(((v * rng.uniform(0.85, 1.15), pid) for v, pid in rows), reverse=True)
              for pos, rows in pool.items()}
    rosters = [{pos: [] for pos in SHAPE} for _ in range(teams)]
    order = list(range(teams))
    rng.shuffle(order)
    for rnd in range(sum(SHAPE.values())):
        for t in (order if rnd % 2 == 0 else order[::-1]):
            need = [p for p in SHAPE if len(rosters[t][p]) < SHAPE[p]]
            pos = max(need, key=lambda p: ranked[p][0][0] if ranked[p] else -1)
            if ranked[pos]:
                rosters[t][pos].append(ranked[pos].pop(0)[1])
    return rosters


def banked_starts(start_days: list[set[dt.date]], days: list[dt.date]) -> int:
    """Goalie games already in by `days`' end: a roster's goalies' starts, at most 2 a day (the G slots)."""
    return sum(min(2, sum(d in s for s in start_days)) for d in days)


def tails(zs: list[float], cuts=TAILS) -> list[tuple[float, float, float]]:
    """(k, share of z beyond k on one side, the normal's): both tails pooled, as a
    matchup's sides are in random order."""
    return [(k, sum(abs(z) > k for z in zs) / (2 * len(zs)), 1 - _phi(k)) for k in cuts]


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leagues", type=int, default=8)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--availability", choices=("bot", "old"), default="bot")
    parser.add_argument("--no-age", action="store_true", help="skater priors not aged (the bot ages them)")
    parser.add_argument("--ahead", type=int, default=0, help="project each week this many weeks before it")
    parser.add_argument("--season", type=int, default=TARGET, help="e.g. 20242025 (priors: the two before)")
    parser.add_argument("--from-day", type=int, nargs="+", default=[0], choices=range(7),
                        help="project each week from this day on (0 = Monday, 6 = Sunday); several run in turn")
    args = parser.parse_args()
    if args.ahead and args.from_day != [0]:
        parser.error("--ahead projects whole weeks: no --from-day with it")
    s_priors, s_fallback, g_priors, g_fallback, skaters, goalies = load(args.season)
    if not args.no_age:
        born = {}
        for season_id in (args.season, *history_of(args.season)):
            born.update(nhl_stats.skater_birth_dates(season_id))
        start = dt.date(args.season // 10_000, 10, 1)
        s_priors = {pid: aged(p, (start - born[pid]).days / 365.25 if pid in born else None)
                    for pid, p in s_priors.items()}

    by_skater, by_goalie = defaultdict(list), defaultdict(list)
    for g in skaters:
        by_skater[g.player_id].append(g)
    for g in goalies:
        by_goalie[g.player_id].append(g)
    team_dates = defaultdict(set)
    for g in skaters:
        team_dates[g.team].add(g.date)
    starts_by_team = defaultdict(list)  # (date, goalie id) of each start
    start_days = defaultdict(set)
    for g in goalies:
        if g.started:
            starts_by_team[g.team].append((g.date, g.player_id))
            start_days[g.player_id].add(g.date)
    position = {pid: POS[gs[0].position] for pid, gs in by_skater.items()}
    team_of = {pid: gs[-1].team for pid, gs in {**by_skater, **by_goalie}.items()}

    def s_prior(pid):
        return s_priors.get(pid) or s_fallback["D" if position[pid] == "D" else "F"]

    pool = defaultdict(list)
    for pid, gs in by_skater.items():
        if len(gs) >= 20:
            pool[position[pid]].append((project_skater(s_prior(pid), []).xfp, pid))
    for pid, gs in by_goalie.items():
        if sum(g.started for g in gs) >= 15:
            pool["G"].append((project_goalie(g_priors.get(pid, g_fallback), []).xfp * 0.55, pid))

    first, last = skaters[0].date, skaters[-1].date
    monday = first + dt.timedelta(days=14 + (7 - (first + dt.timedelta(days=14)).weekday()) % 7)
    monday += dt.timedelta(days=7 * args.ahead)  # the first week whose projection date is in week 3
    weeks = []
    while monday + dt.timedelta(days=7) <= last:
        weeks.append(monday)
        monday += dt.timedelta(days=7)

    for from_day in args.from_day:
        rng = random.Random(args.seed)
        team_weeks, matchups = [], []
        for _ in range(args.leagues):
            rosters = draft(pool, 16, rng)
            for monday in weeks:
                days = [monday + dt.timedelta(days=i) for i in range(from_day, 7)]
                # what the projection knows: the morning of its first day
                asof = monday + dt.timedelta(days=from_day - 7 * args.ahead)
                results = []
                for roster in rosters:
                    banked = banked_starts([start_days[pid] for pid in roster["G"]],
                                           [monday + dt.timedelta(days=i) for i in range(from_day)])
                    if from_day and banked < MIN_GOALIE_GAMES_PER_WEEK:
                        results.append(None)  # the minimum still open: banked goalie points at stake too
                        continue
                    proj, actual = {}, {}
                    for pos in ("C", "LW", "RW", "D"):
                        for pid in roster[pos]:
                            past = [g for g in by_skater[pid] if g.date < asof]
                            if args.availability == "old" and not any(
                                    g.date >= asof - dt.timedelta(days=7) for g in past):
                                continue  # out: the bot would see it
                            # Team games missed in a row before asof, as scripts/fit_absence.py counts them.
                            mine = {g.date for g in past}
                            team = past[-1].team if past else team_of[pid]  # his team then (trades)
                            team_past = sorted(d for d in team_dates[team] if d < asof)
                            missed = next((i for i, d in enumerate(reversed(team_past)) if d in mine), len(team_past))
                            curve = availability.return_curve(missed)
                            x = project_skater(s_prior(pid), past).xfp
                            played = {g.date: fantasy_points(g.stats, SKATER_WEIGHTS) for g in by_skater[pid]
                                      if monday <= g.date < monday + dt.timedelta(days=7)}
                            for d in days:
                                if d in team_dates[team]:
                                    if args.availability == "old":
                                        p = 1.0
                                    elif not missed:
                                        p = availability.HEALTHY_PLAY
                                    else:  # days since his team's last game, which he missed
                                        p = availability.ahead(curve, (d - team_past[-1]).days, 0.0)
                                    var = p * (SKATER_VARIANCE_PER_XFP * x + x * x) - (p * x) ** 2
                                    proj.setdefault(d, []).append((pid, (pos,), p * x, var, 1.0))
                                    actual[(d, pid)] = played.get(d, 0.0)
                    for pid in roster["G"]:
                        team = team_of[pid]
                        past_team = [(d, g) for d, g in starts_by_team[team] if d < asof]
                        share = (sum(g == pid for _, g in past_team[-15:]) / len(past_team[-15:])) if past_team else 0.5
                        if share == 0:
                            continue
                        x = project_goalie(g_priors.get(pid, g_fallback),
                                           [g for g in by_goalie[pid] if g.date < asof]).xfp
                        started = {g.date: fantasy_points(g.stats, GOALIE_WEIGHTS) for g in by_goalie[pid]
                                   if g.started and monday <= g.date < monday + dt.timedelta(days=7)}
                        for d in days:
                            if d in team_dates[team]:
                                var = share * (GOALIE_START_VARIANCE + x * x) - (share * x) ** 2
                                proj.setdefault(d, []).append((pid, ("G",), share * x, var, share))
                                actual[(d, pid)] = started.get(d)  # None: didn't start
                    mean_sk = var_sk = mean_g = var_g = act_sk = act_g = 0.0
                    probs, g_started = [], 0
                    for d, rows in proj.items():
                        assignment = lineup.optimize([lineup.Candidate(pid, p, m) for pid, p, m, _, _ in rows])
                        for pid, p, m, v, prob in rows:
                            if assignment.get(pid, lineup.BENCH) == lineup.BENCH:
                                continue
                            if p == ("G",):
                                mean_g, var_g = mean_g + m, var_g + v
                                probs.append(prob)
                                if actual[(d, pid)] is not None:
                                    act_g += actual[(d, pid)]
                                    g_started += 1
                            else:
                                mean_sk, var_sk = mean_sk + m, var_sk + v
                                act_sk += actual[(d, pid)]
                    min_prob = _at_least(probs, banked, MIN_GOALIE_GAMES_PER_WEEK)
                    expected = mean_sk + min_prob * mean_g
                    g_var = min_prob * (var_g + mean_g ** 2) - (min_prob * mean_g) ** 2
                    variance = var_sk + g_var + (MODEL_SD_SHARE * (mean_sk + mean_g)) ** 2
                    got = act_sk + (act_g if banked + g_started >= MIN_GOALIE_GAMES_PER_WEEK else 0.0)
                    results.append((expected, variance, got))
                    if variance > 0:
                        team_weeks.append((got - expected) / math.sqrt(variance))
                order = list(range(16))
                rng.shuffle(order)
                for a, b in zip(order[::2], order[1::2]):
                    if results[a] is None or results[b] is None:
                        continue
                    (ea, va, ga), (eb, vb, gb) = results[a], results[b]
                    if va + vb <= 0:
                        continue  # no games (the Olympic break)
                    matchups.append((_phi((ea - eb) / math.sqrt(va + vb)), ga > gb, ea - eb, ga - gb, va + vb))

        report(team_weeks, matchups, args.leagues, len(weeks), from_day)


def report(team_weeks: list[float], matchups: list[tuple], leagues: int, weeks: int, from_day: int) -> None:
    print(f"\n== from {('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[from_day]} on "
          f"({7 - from_day} day{'s' if from_day < 6 else ''} still to play) ==")
    sd_z = math.sqrt(mean(z * z for z in team_weeks) - mean(team_weeks) ** 2)
    print(f"{len(team_weeks)} team-weeks, {len(matchups)} matchups ({leagues} leagues x {weeks} weeks)")
    print(f"team week: mean error {mean(team_weeks):+.2f} sigma, SD of the error {sd_z:.2f} sigma (1.00 = right)")
    pred = [m[4] for m in matchups]
    real = mean((m[3] - m[2]) ** 2 for m in matchups)
    print(f"matchup margin: model sigma {math.sqrt(mean(pred)):.1f} pts, actual spread of (result - projection) "
          f"{math.sqrt(real):.1f} pts -> sigma x{math.sqrt(real / mean(pred)):.2f}")
    print("P(win) calibration:")
    bins = [(0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.01)]
    for lo, hi in bins:
        rows = [m for m in matchups if lo <= m[0] < hi]
        if rows:
            print(f"  predicted {lo:.0%}-{min(hi, 1):.0%}: {mean(m[0] for m in rows):5.1%} predicted, "
                  f"{mean(m[1] for m in rows):5.1%} won ({len(rows)} matchups)")
    xs, ys = [m[2] for m in matchups], [m[3] for m in matchups]
    mx, my = mean(xs), mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    print(f"margins: each projected point of margin shows up as {slope:.2f} points in results "
          f"(1.00 = projections not overconfident); projected margins spread {math.sqrt(mean((x - mx) ** 2 for x in xs)):.1f} pts")
    brier = mean((m[0] - m[1]) ** 2 for m in matchups)
    k = math.sqrt(real / mean(pred))
    brier_k = mean((_phi((m[2]) / (math.sqrt(m[4]) * k)) - m[1]) ** 2 for m in matchups)
    print(f"Brier score: {brier:.4f} as is, {brier_k:.4f} with sigma x{k:.2f} (lower is better)")
    # The margin as P(win) reads it (engine/matchup.margin): the part to play at MARGIN_REALIZES.
    zs = [(m[3] - MARGIN_REALIZES * m[2]) / math.sqrt(m[4]) for m in matchups]
    print(f"tails: how often the result beat the margin P(win) reads by more than k sigma (either side), "
          f"against the normal (P(win) of a team k sigma behind)")
    for k_, got, normal in tails(zs):
        print(f"  k {k_:4.2f}: {got:6.1%} happened, {normal:6.1%} normal")


if __name__ == "__main__":
    main_()
