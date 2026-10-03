"""Do the opponent and the arena change a skater's points enough to model?

Two effects on a skater's per-game stats, each a multiplier per stat:
- arena: some buildings' scorers record more hits and blocks (and shots) than
  others, for both teams. Factor = the stat's rate per minute of ice time in
  games played there over the league's.
- opponent: playing a team that gives up many shots, hits or blocks. Factor =
  the rate by skaters facing that team over the league's.
A projection already holds the arenas and opponents of the games it was built
on, so each factor is taken relative to their average over his recent games.
Both are shrunk toward 1 (SHRINK_GAMES games of league-average evidence) and,
as of each Monday, use only the season before plus this season's games
before that Monday: what the bot would know.

Scored like scripts/backtest.py (same projection, same relevant players, same
Mondays): each player-week's actual points per game against the model's
projection, plain and with each game's factors. Reported: MAE, pairwise (who
to start or add) and bias, plus the paired difference in absolute error with
its standard error over weeks (the weeks, not the player-weeks, are the
independent units), and how big the adjustment is.

    python -m scripts.backtest_venue                      # 2025-26
    python -m scripts.backtest_venue --season 20242025

Decision rule (CLAUDE.md, evidence over intuition): implement only if it beats
the plain model on MAE and pairwise in both seasons by more than its noise.
Result (2026-10-03, Check workflow): it doesn't. 2025-26 all three variants
are slightly worse (both: +0.006 +/- 0.002 MAE), 2024-25 within noise;
pairwise unchanged. Not implemented (model/CLAUDE.md, evidence table).
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
from collections import defaultdict
from statistics import mean

from clients import nhl_stats
from config.league import SKATER_WEIGHTS, fantasy_points
from model.projections import project_skater, skater_priors
from scripts.backtest import (MIN_GAMES_RELEVANT, RELEVANT_SKATERS, evaluate, history_of, mondays, skater_fp)

STATS = ("sog", "hit", "blk", "g", "a")  # the ones an opponent or a scorer plausibly moves
SHRINK_GAMES = 5  # league-average games of evidence each factor starts from (a judgment call)
LAST_SEASON_WEIGHT = 0.5  # the season before counts half: rosters and scorers change
NEUTRAL_GAMES = 82  # a player's recent games whose arenas and opponents his projection already holds


def home_teams(season: int) -> dict[int, str]:
    """game id -> home team, from the goalie logs (skater logs don't say)."""
    return {g.game_id: g.team if g.home else g.opponent for g in nhl_stats.goalie_games(season)}


class Rates:
    """Per-stat totals and ice time, by arena and by opponent, accumulated as games are added."""

    def __init__(self) -> None:
        self.league = defaultdict(float)
        self.arena: dict[str, dict] = defaultdict(lambda: defaultdict(float))
        self.opponent: dict[str, dict] = defaultdict(lambda: defaultdict(float))
        self.games_toi = 0.0  # league ice time per game (both teams' skaters), for the shrink
        self.games = 0

    def add(self, g, arena: str, weight: float = 1.0) -> None:
        if not g.toi:
            return
        for table in (self.league, self.arena[arena], self.opponent[g.opponent]):
            table["toi"] += weight * g.toi
            for s in STATS:
                table[s] += weight * g.stats.get(s, 0)

    def factor(self, table: dict, stat: str, per_game_toi: float) -> float:
        if not self.league["toi"] or not self.league[stat]:
            return 1.0
        rate = self.league[stat] / self.league["toi"]
        k = SHRINK_GAMES * per_game_toi
        return (table[stat] + k * rate) / (table["toi"] + k) / rate


def backtest(target: int) -> None:
    last = history_of(target)
    history = [nhl_stats.skater_games(s) for s in last]
    priors, fallback = skater_priors(history)
    games = nhl_stats.skater_games(target)
    homes = {**home_teams(last[0]), **home_teams(target)}

    def arena(g) -> str | None:
        return homes.get(g.game_id)

    base = Rates()
    for g in history[0]:
        if arena(g):
            base.add(g, arena(g), LAST_SEASON_WEIGHT)
    per_game_toi = base.league["toi"] / max(len({g.game_id for g in history[0]}), 1) / LAST_SEASON_WEIGHT

    last_season: dict[int, list] = defaultdict(list)
    for g in history[0]:
        last_season[g.player_id].append(g)
    by_player: dict[int, list] = defaultdict(list)
    for g in games:
        by_player[g.player_id].append(g)
    season_fpg = {pid: mean(skater_fp(g) for g in gs) for pid, gs in by_player.items() if len(gs) >= MIN_GAMES_RELEVANT}
    relevant = set(sorted(season_fpg, key=season_fpg.get, reverse=True)[:RELEVANT_SKATERS])

    records, adjustments = [], []
    done = 0  # games of this season already in the rates
    for monday in mondays(games[0].date, games[-1].date):
        while done < len(games) and games[done].date < monday:
            if arena(games[done]):
                base.add(games[done], arena(games[done]))
            done += 1
        week_end = monday + dt.timedelta(days=7)
        for pid in relevant:
            upcoming = [g for g in by_player[pid] if monday <= g.date < week_end]
            if not upcoming:
                continue
            past = [g for g in by_player[pid] if g.date < monday]
            group = "D" if upcoming[0].position == "D" else "F"
            proj = project_skater(priors.get(pid) or fallback[group], past)
            plain = proj.xfp
            variants = {"arena": [], "opponent": [], "both": []}
            # His projection already holds the arenas and opponents of the games
            # it was built from (last season's and this season's so far): divide
            # their average out, then apply each upcoming game's.
            seen = [g for g in last_season[pid] + past if arena(g)][-NEUTRAL_GAMES:]
            usual_arena = {s: mean(base.factor(base.arena[arena(g)], s, per_game_toi) for g in seen) if seen else 1.0
                           for s in STATS}
            usual_opp = {s: mean(base.factor(base.opponent[g.opponent], s, per_game_toi) for g in seen) if seen
                         else 1.0 for s in STATS}
            for g in upcoming:
                a = arena(g)
                f_arena = {s: (base.factor(base.arena[a], s, per_game_toi) if a else 1.0) / usual_arena[s]
                           for s in STATS}
                f_opp = {s: base.factor(base.opponent[g.opponent], s, per_game_toi) / usual_opp[s] for s in STATS}
                for name, f in (("arena", f_arena), ("opponent", f_opp),
                                ("both", {s: f_arena[s] * f_opp[s] for s in STATS})):
                    stats = {s: v * f.get(s, 1.0) for s, v in proj.per_game.items()}
                    variants[name].append(fantasy_points(stats, SKATER_WEIGHTS))
            record = {"week": monday, "group": group, "n": len(upcoming),
                      "actual": mean(skater_fp(g) for g in upcoming), "plain": plain}
            record.update({name: mean(v) for name, v in variants.items()})
            adjustments += [x - plain for x in variants["both"]]
            records.append(record)

    print(f"\nSKATERS {target}: {len(records)} player-weeks, {len(relevant)} relevant players")
    evaluate(records, ["plain", "arena", "opponent", "both"], "group")
    sd = math.sqrt(mean(a * a for a in adjustments))
    print(f"Adjustment per game (both): RMS {sd:.3f} pts, largest {max(adjustments):+.2f} / {min(adjustments):+.2f}")
    for name in ("arena", "opponent", "both"):
        by_week = defaultdict(list)
        for r in records:
            by_week[r["week"]].append((abs(r[name] - r["actual"]) - abs(r["plain"] - r["actual"])) * r["n"])
        weekly = [sum(v) / sum(r["n"] for r in records if r["week"] == w) for w, v in by_week.items()]
        m = mean(weekly)
        se = math.sqrt(sum((x - m) ** 2 for x in weekly) / (len(weekly) - 1) / len(weekly))
        print(f"  {name:9} MAE minus plain: {m:+.4f} (+/- {se:.4f} over {len(weekly)} weeks; negative is better)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, nargs="*", default=[20252026, 20242025])
    for season in parser.parse_args().season:
        backtest(season)
