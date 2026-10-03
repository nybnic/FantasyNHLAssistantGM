"""Is P(win)'s spread right? Checked on the 2025-26 season.

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
from engine.matchup import GOALIE_START_VARIANCE, MODEL_SD_SHARE, SKATER_VARIANCE_PER_XFP, _at_least
from model.projections import aged, goalie_priors, project_goalie, project_skater, skater_priors

TARGET = 20252026
HISTORY = (20242025, 20232024)
SHAPE = {"C": 3, "LW": 2, "RW": 2, "D": 4, "G": 3}
POS = {"C": "C", "L": "LW", "R": "RW", "D": "D"}


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def load():
    skater_history = [nhl_stats.skater_games(s) for s in HISTORY]
    goalie_history = [nhl_stats.goalie_games(s) for s in HISTORY]
    s_priors, s_fallback = skater_priors(skater_history)
    g_priors, g_fallback = goalie_priors(goalie_history)
    skaters, goalies = nhl_stats.skater_games(TARGET), nhl_stats.goalie_games(TARGET)
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


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leagues", type=int, default=8)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--availability", choices=("bot", "old"), default="bot")
    parser.add_argument("--no-age", action="store_true", help="skater priors not aged (the bot ages them)")
    args = parser.parse_args()
    s_priors, s_fallback, g_priors, g_fallback, skaters, goalies = load()
    if not args.no_age:
        born = {}
        for season_id in (TARGET, *HISTORY):
            born.update(nhl_stats.skater_birth_dates(season_id))
        start = dt.date(TARGET // 10_000, 10, 1)
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
    for g in goalies:
        if g.started:
            starts_by_team[g.team].append((g.date, g.player_id))
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
    weeks = []
    while monday + dt.timedelta(days=7) <= last:
        weeks.append(monday)
        monday += dt.timedelta(days=7)

    rng = random.Random(args.seed)
    team_weeks, matchups = [], []
    for _ in range(args.leagues):
        rosters = draft(pool, 16, rng)
        for monday in weeks:
            days = [monday + dt.timedelta(days=i) for i in range(7)]
            results = []
            for roster in rosters:
                proj, actual = {}, {}
                for pos in ("C", "LW", "RW", "D"):
                    for pid in roster[pos]:
                        past = [g for g in by_skater[pid] if g.date < monday]
                        if args.availability == "old" and not any(
                                g.date >= monday - dt.timedelta(days=7) for g in past):
                            continue  # out: the bot would see it
                        # Team games missed in a row before Monday, as scripts/fit_absence.py counts them.
                        mine = {g.date for g in past}
                        team = past[-1].team if past else team_of[pid]  # his team then (trades)
                        team_past = sorted(d for d in team_dates[team] if d < monday)
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
                    past_team = [(d, g) for d, g in starts_by_team[team] if d < monday]
                    share = (sum(g == pid for _, g in past_team[-15:]) / len(past_team[-15:])) if past_team else 0.5
                    if share == 0:
                        continue
                    x = project_goalie(g_priors.get(pid, g_fallback),
                                       [g for g in by_goalie[pid] if g.date < monday]).xfp
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
                min_prob = _at_least(probs, 0, MIN_GOALIE_GAMES_PER_WEEK)
                expected = mean_sk + min_prob * mean_g
                g_var = min_prob * (var_g + mean_g ** 2) - (min_prob * mean_g) ** 2
                variance = var_sk + g_var + (MODEL_SD_SHARE * (mean_sk + mean_g)) ** 2
                got = act_sk + (act_g if g_started >= MIN_GOALIE_GAMES_PER_WEEK else 0.0)
                results.append((expected, variance, got))
                if variance > 0:
                    team_weeks.append((got - expected) / math.sqrt(variance))
            if any(v <= 0 for _, v, _ in results):
                continue  # a week without games (the Olympic break)
            order = list(range(16))
            rng.shuffle(order)
            for a, b in zip(order[::2], order[1::2]):
                (ea, va, ga), (eb, vb, gb) = results[a], results[b]
                matchups.append((_phi((ea - eb) / math.sqrt(va + vb)), ga > gb, ea - eb, ga - gb, va + vb))

    sd_z = math.sqrt(mean(z * z for z in team_weeks) - mean(team_weeks) ** 2)
    print(f"{len(team_weeks)} team-weeks, {len(matchups)} matchups ({args.leagues} leagues x {len(weeks)} weeks)")
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


if __name__ == "__main__":
    main_()
