"""Game-level expectations: team strength, and a goalie's xFP for one start.

Goalie points depend mostly on the game, not the goalie: a goalie's own
track record barely predicts next week's points per start (backtest:
~50% pairwise, a coin flip). So one start is projected from the matchup:
- expected shots against = opponent's shot rate x own team's shot suppression
- expected goals against = shots against x (1 - goalie's regressed save %)
- expected goals for = own attack x opponent's defense, with home ice
- P(win) and P(shutout) from Poisson goal counts; regulation ties split 50/50
  in overtime/shootout.
Team ratings blend last season (regressed to the league average, since
rosters change) with this season's games.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from clients.nhl_stats import GoalieGame
from config.league import GOALIE_WEIGHTS, fantasy_points

TEAM_K_GAMES = 15  # current-season games needed to half-trust them over the prior
TEAM_PRIOR_REGRESSION = 1 / 3
SAVE_PCT_K_SHOTS = 1500  # save % stabilizes slowly
HISTORY_SAVE_PCT_K_SHOTS = 1000
DECISION_SHARE = 0.96  # a starter gets the decision in ~96% of his team's wins
# Fit on 2024-25 (2023-24 as history), then checked on 2025-26:
STARTER_SHOT_SHARE = 0.94  # starters get pulled / skip empty-net time
SHUTOUT_FACTOR = 0.90  # Poisson overstates P(0 goals against)
MAX_GOALS = 15


@dataclass
class TeamGame:
    game_id: int
    team: str
    opponent: str
    home: bool
    gf: float = 0.0
    ga: float = 0.0
    sf: float = 0.0
    sa: float = 0.0


@dataclass
class TeamRating:
    gf: float  # per game
    ga: float
    sf: float
    sa: float


@dataclass
class League:
    goals: float  # per team per game
    shots: float
    save_pct: float
    home_edge: float  # multiplier on home scoring (1/x on away)


def team_games(goalie_games: list[GoalieGame]) -> list[TeamGame]:
    """One row per team per game, rebuilt from goalie lines (GA excludes
    empty-net goals, which matches how goalies are scored)."""
    games: dict[tuple[int, str], TeamGame] = {}
    for g in goalie_games:
        key = (g.game_id, g.team)
        tg = games.setdefault(key, TeamGame(g.game_id, g.team, g.opponent, g.home))
        tg.ga += g.stats.get("ga", 0.0)
        tg.sa += g.shots_against
    for (game_id, team), tg in games.items():
        opp = games.get((game_id, tg.opponent))
        if opp:
            tg.gf, tg.sf = opp.ga, opp.sa
    return list(games.values())


def _per_game(rows: list[TeamGame]) -> dict[str, TeamRating]:
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])
    for r in rows:
        s = sums[r.team]
        s[0] += r.gf
        s[1] += r.ga
        s[2] += r.sf
        s[3] += r.sa
        s[4] += 1
    return {t: TeamRating(s[0] / s[4], s[1] / s[4], s[2] / s[4], s[3] / s[4]) for t, s in sums.items()}


def ratings(
    last_season: list[TeamGame], this_season: list[TeamGame], league_save_pct: float
) -> tuple[dict[str, TeamRating], League]:
    pool = this_season if len(this_season) >= 200 else last_season + this_season
    goals = sum(r.gf for r in pool) / len(pool)
    shots = sum(r.sf for r in pool) / len(pool)
    home_goals = [r.gf for r in pool if r.home]
    away_goals = [r.gf for r in pool if not r.home]
    home_edge = math.sqrt((sum(home_goals) / len(home_goals)) / (sum(away_goals) / len(away_goals)))
    league = League(goals=goals, shots=shots, save_pct=league_save_pct, home_edge=home_edge)

    prior = _per_game(last_season)
    current = _per_game(this_season)
    counts: dict[str, int] = defaultdict(int)
    for r in this_season:
        counts[r.team] += 1

    out = {}
    for team in set(prior) | set(current):
        base = prior.get(team)
        reg = TEAM_PRIOR_REGRESSION
        p = TeamRating(
            gf=(base.gf * (1 - reg) + goals * reg) if base else goals,
            ga=(base.ga * (1 - reg) + goals * reg) if base else goals,
            sf=(base.sf * (1 - reg) + shots * reg) if base else shots,
            sa=(base.sa * (1 - reg) + shots * reg) if base else shots,
        )
        c, n = current.get(team), counts[team]
        if not c:
            out[team] = p
            continue
        w = n / (n + TEAM_K_GAMES)
        out[team] = TeamRating(
            gf=w * c.gf + (1 - w) * p.gf,
            ga=w * c.ga + (1 - w) * p.ga,
            sf=w * c.sf + (1 - w) * p.sf,
            sa=w * c.sa + (1 - w) * p.sa,
        )
    return out, league


def save_pct(
    history: list[GoalieGame], this_season: list[GoalieGame], league_save_pct: float
) -> float:
    """A goalie's save %, regressed hard toward the league average."""
    h_saves = sum(g.stats.get("sv", 0.0) for g in history)
    h_shots = sum(g.shots_against for g in history)
    prior = (h_saves + HISTORY_SAVE_PCT_K_SHOTS * league_save_pct) / (h_shots + HISTORY_SAVE_PCT_K_SHOTS)
    saves = sum(g.stats.get("sv", 0.0) for g in this_season)
    shots = sum(g.shots_against for g in this_season)
    return (SAVE_PCT_K_SHOTS * prior + saves) / (SAVE_PCT_K_SHOTS + shots)


def _poisson(lam: float) -> list[float]:
    probs = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        probs.append(probs[-1] * lam / k)
    return probs


def goalie_start(
    team: str,
    opponent: str,
    home: bool,
    goalie_save_pct: float,
    team_ratings: dict[str, TeamRating],
    league: League,
) -> dict[str, float]:
    """Expected stat line for one start (gs, w, ga, sv, so) plus 'xfp'."""
    us, them = team_ratings[team], team_ratings[opponent]
    team_shots_against = league.shots * (them.sf / league.shots) * (us.sa / league.shots)
    team_goals_against = team_shots_against * (1 - goalie_save_pct)
    edge = league.home_edge if home else 1 / league.home_edge
    goals_for = league.goals * (us.gf / league.goals) * (them.ga / league.goals) * edge
    # Win/shutout odds are about the whole game; the starter's own saves and
    # goals against only cover the time he's in net.
    shots_against = STARTER_SHOT_SHARE * team_shots_against
    goals_against = shots_against * (1 - goalie_save_pct)

    p_for, p_against = _poisson(goals_for), _poisson(team_goals_against)
    p_win = p_tie = 0.0
    for x, px in enumerate(p_for):
        for y, py in enumerate(p_against):
            if x > y:
                p_win += px * py
            elif x == y:
                p_tie += px * py
    stats = {
        "gs": 1.0,
        "w": (p_win + 0.5 * p_tie) * DECISION_SHARE,
        "ga": goals_against,
        "sv": shots_against - goals_against,
        "so": p_against[0] * SHUTOUT_FACTOR,
    }
    stats["xfp"] = fantasy_points(stats, GOALIE_WEIGHTS)
    return stats
