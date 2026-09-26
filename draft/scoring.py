"""League scoring and roster layout ("Not for everyone!", Yahoo H2H points).

Copied from the league settings page - update here if the commissioner
changes anything.
"""
from __future__ import annotations

from typing import Mapping

SKATER_WEIGHTS: dict[str, float] = {
    "g": 4.5,     # goals
    "a": 3.0,     # assists
    "pm": 0.5,    # plus/minus
    "pim": 0.3,   # penalty minutes
    "ppg": 0.75,  # powerplay goals (on top of the goal itself)
    "ppa": 0.5,   # powerplay assists
    "shg": 1.5,   # shorthanded goals
    "sha": 1.25,  # shorthanded assists
    "gwg": 2.0,   # game-winning goals
    "sog": 0.5,   # shots on goal
    "fow": 0.2,   # faceoffs won
    "hit": 0.6,   # hits
    "blk": 0.8,   # blocked shots
}

GOALIE_WEIGHTS: dict[str, float] = {
    "gs": 1.0,    # games started
    "w": 4.0,     # wins
    "ga": -1.0,   # goals against
    "sv": 0.35,   # saves
    "so": 5.0,    # shutouts
}

LEAGUE_TEAMS = 16
# Starting slots per team; BN 2 + IR + IR+ are not starters.
STARTERS: dict[str, int] = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
BENCH_SLOTS = 2
IR_SLOTS = 2
DRAFT_ROUNDS = 14  # every active slot (12 starters + 2 bench); IR slots aren't drafted
MIN_GOALIE_GAMES_PER_WEEK = 3
MAX_ADDS_PER_WEEK = 2
MAX_ADDS_PER_SEASON = 36


def fantasy_points(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return sum(w * stats.get(k, 0.0) for k, w in weights.items())


def skater_points(stats: Mapping[str, float]) -> float:
    return fantasy_points(stats, SKATER_WEIGHTS)


def goalie_points(stats: Mapping[str, float]) -> float:
    return fantasy_points(stats, GOALIE_WEIGHTS)
