"""Backtest the xFP model on the 2025-26 season against simple baselines.

Every Monday from week 3 on, each player is projected using only data from
before that Monday (priors from 2023-24 and 2024-25, plus 2025-26 games so
far) and compared with the fantasy points he actually scored that week.

Metrics:
- MAE: games-weighted mean absolute error of fantasy points per game
  (per start for goalies). Lower is better, but weekly results are very noisy
  so the floor is high and differences look small.
- Pairwise: of all pairs of same-position players (2+ games that week), how
  often the model ranks them the same way as the actual result. This is the
  decision that matters: who to start, add or drop.
- Bias: mean projected minus mean actual.

Evaluated on "relevant" players: the top 400 skaters by 2025-26 fantasy
points per game (min 20 GP), roughly the rostered pool plus the waiver band.
Every model is scored on the same player-weeks.

    python -m scripts.backtest
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import mean

from clients import nhl_stats
from config.league import GOALIE_WEIGHTS, SKATER_WEIGHTS, fantasy_points
from model import games as games_model
from model.projections import (
    SKATER_STATS,
    Params,
    goalie_priors,
    project_goalie,
    project_skater,
    skater_priors,
)

TARGET = 20252026
HISTORY = (20242025, 20232024)
RELEVANT_SKATERS = 400
MIN_GAMES_RELEVANT = 20


def skater_fp(game) -> float:
    return fantasy_points(game.stats, SKATER_WEIGHTS)


def goalie_fp(game) -> float:
    return fantasy_points(game.stats, GOALIE_WEIGHTS)


def mondays(first: dt.date, last: dt.date) -> list[dt.date]:
    day = first + dt.timedelta(days=14)
    day += dt.timedelta(days=(7 - day.weekday()) % 7)
    out = []
    while day + dt.timedelta(days=7) <= last:
        out.append(day)
        day += dt.timedelta(days=7)
    return out


def per_game_blend(prior, past, k_scale=1.0) -> float:
    """Classic approach: blend fantasy points per game directly, no TOI split."""
    prior_fpg = fantasy_points(
        {s: prior.per_toi[s] * (prior.pp_toi if s in ("ppg", "ppa") else prior.toi) for s in SKATER_STATS},
        SKATER_WEIGHTS,
    )
    k = 25 * k_scale
    return (k * prior_fpg + sum(skater_fp(g) for g in past)) / (k + len(past))


def pairwise_accuracy(rows: list[tuple[float, float]]) -> tuple[int, int]:
    agree = total = 0
    for i in range(len(rows)):
        pi, ai = rows[i]
        for j in range(i + 1, len(rows)):
            pj, aj = rows[j]
            if ai == aj or pi == pj:
                continue
            total += 1
            agree += (pi > pj) == (ai > aj)
    return agree, total


def evaluate(records: list[dict], models: list[str], group_key: str, min_games: int = 2) -> None:
    print(f"{'model':<24}{'MAE':>8}{'pairwise':>10}{'bias':>8}")
    for model in models:
        weight = sum(r["n"] for r in records)
        mae = sum(abs(r[model] - r["actual"]) * r["n"] for r in records) / weight
        bias = sum((r[model] - r["actual"]) * r["n"] for r in records) / weight
        agree = total = 0
        buckets = defaultdict(list)
        for r in records:
            if r["n"] >= min_games:
                buckets[(r["week"], r[group_key])].append((r[model], r["actual"]))
        for rows in buckets.values():
            a, t = pairwise_accuracy(rows)
            agree += a
            total += t
        pairwise = f"{agree / total:.1%}" if total else "n/a"
        print(f"{model:<24}{mae:>8.3f}{pairwise:>10}{bias:>+8.3f}")


def backtest_skaters() -> None:
    history = [nhl_stats.skater_games(s) for s in HISTORY]
    priors, fallback = skater_priors(history)
    games = nhl_stats.skater_games(TARGET)

    by_player: dict[int, list] = defaultdict(list)
    for g in games:
        by_player[g.player_id].append(g)

    season_fpg = {
        pid: mean(skater_fp(g) for g in gs) for pid, gs in by_player.items() if len(gs) >= MIN_GAMES_RELEVANT
    }
    relevant = set(sorted(season_fpg, key=season_fpg.get, reverse=True)[:RELEVANT_SKATERS])

    variants = {
        "model": Params(),
        "model k x0.5": Params(k_scale=0.5),
        "model toi halflife 3": Params(toi_halflife_games=3.0),
        "model toi halflife 12": Params(toi_halflife_games=12.0),
    }
    records = []
    for monday in mondays(games[0].date, games[-1].date):
        week_end = monday + dt.timedelta(days=7)
        for pid in relevant:
            player_games = by_player[pid]
            upcoming = [g for g in player_games if monday <= g.date < week_end]
            if not upcoming:
                continue
            past = [g for g in player_games if g.date < monday]
            prior = priors.get(pid) or fallback["D" if player_games[0].position == "D" else "F"]
            prior_only = project_skater(prior, []).xfp
            record = {
                "week": monday,
                "group": "D" if player_games[0].position == "D" else "F",
                "n": len(upcoming),
                "actual": mean(skater_fp(g) for g in upcoming),
                "prior only": prior_only,
                "season to date": mean(skater_fp(g) for g in past) if past else prior_only,
                "last 10": mean(skater_fp(g) for g in past[-10:]) if past else prior_only,
                "per-game blend": per_game_blend(prior, past),
            }
            for name, params in variants.items():
                record[name] = project_skater(prior, past, params).xfp
            records.append(record)

    print(f"\nSKATERS: {len(records)} player-weeks, {len(relevant)} relevant players, "
          f"priors for {sum(p in priors for p in relevant)}")
    evaluate(records, ["prior only", "season to date", "last 10", "per-game blend", *variants], "group")


def backtest_goalie_starts() -> None:
    """Every start is scored on its own: the decision is which goalie to
    start on a given night, so the matchup is known in advance."""
    history = [nhl_stats.goalie_games(s) for s in HISTORY]
    priors, fallback = goalie_priors(history)
    last_season_teams = games_model.team_games(history[0])
    history_by_goalie: dict[int, list] = defaultdict(list)
    for season in history:
        for g in season:
            history_by_goalie[g.player_id].append(g)
    hist_shots = sum(g.shots_against for g in history[0])
    league_sv = sum(g.stats["sv"] for g in history[0]) / hist_shots

    games = nhl_stats.goalie_games(TARGET)
    by_goalie: dict[int, list] = defaultdict(list)
    for g in games:
        by_goalie[g.player_id].append(g)

    records = []
    for monday in mondays(games[0].date, games[-1].date):
        week_end = monday + dt.timedelta(days=7)
        past_games = [g for g in games if g.date < monday]
        team_ratings, league = games_model.ratings(last_season_teams, games_model.team_games(past_games), league_sv)
        for start in games:
            if not (monday <= start.date < week_end and start.started):
                continue
            pid = start.player_id
            past = [g for g in by_goalie[pid] if g.date < monday]
            sv = games_model.save_pct(history_by_goalie[pid], past, league_sv)
            matchup = games_model.goalie_start(start.team, start.opponent, start.home, sv, team_ratings, league)
            records.append({
                "week": monday,
                "group": "G",
                "n": 1,
                "actual": goalie_fp(start),
                "league average": 0.0,  # filled below
                "goalie track record": project_goalie(priors.get(pid, fallback), past).xfp,
                "game model": matchup["xfp"],
            })
    league_avg = mean(r["actual"] for r in records)
    for r in records:
        r["league average"] = league_avg

    print(f"\nGOALIE STARTS: {len(records)} starts (league average {league_avg:.2f} pts/start, "
          f"home edge x{league.home_edge:.3f})")
    evaluate(records, ["league average", "goalie track record", "game model"], "group", min_games=1)


if __name__ == "__main__":
    backtest_skaters()
    backtest_goalie_starts()
