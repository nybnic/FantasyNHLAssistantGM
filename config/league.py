"""League rules for "Not for everyone!" (Yahoo H2H points, 16 teams).

Source of truth for every engine. Values below are the final settings as of
Sep 28 2026; update here if the commissioner changes anything.
"""
from __future__ import annotations

import datetime as dt
from typing import Mapping

TIMEZONE = "Europe/Helsinki"
LEAGUE_ID = 66759
LEAGUE_TEAMS = 16

# Fantasy points per stat. PPG/PPA/SHG/SHA are bonuses on top of the goal or
# assist itself, so a PP goal is worth g + ppg = 4.75.
SKATER_WEIGHTS: dict[str, float] = {
    "g": 4.0,
    "a": 2.75,
    "pm": 0.5,
    "pim": 0.3,
    "ppg": 0.75,
    "ppa": 0.5,
    "shg": 1.75,
    "sha": 1.5,
    "gwg": 2.0,
    "sog": 0.5,
    "fow": 0.1,
    "hit": 0.6,
    "blk": 0.8,
}

GOALIE_WEIGHTS: dict[str, float] = {
    "gs": 1.0,
    "w": 4.0,
    "ga": -1.0,
    "sv": 0.35,
    "so": 5.0,
}

STARTERS: dict[str, int] = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
BENCH_SLOTS = 2
# Which Yahoo injury statuses each injury slot accepts.
IR_SLOT_STATUSES: dict[str, set[str]] = {
    "IR": {"IR", "IR-LT", "IR-NR"},
    "IR+": {"IR", "IR-LT", "IR-NR", "DTD", "O"},
}

# "Weekly Deadline: Daily - Today": changes apply the same day and each
# player locks at his own game's start, so late games stay editable.
PER_GAME_LOCK = True

MAX_ADDS_PER_WEEK = 2
MAX_ADDS_PER_SEASON = 36
# Continual rolling list: dropped (and post-draft) players sit on waivers for
# 1 day; a successful claim sends you to the back of the priority list.
# Free agents off waivers can be added instantly.
WAIVER_DAYS = 1
INJURED_ADD_DIRECT_TO_IR = True

# Missing this minimum zeroes ALL goalie points for the week. Relief
# appearances count, but only while the goalie is in an active G slot.
MIN_GOALIE_GAMES_PER_WEEK = 3

REGULAR_SEASON_WEEKS = 23
PLAYOFF_WEEKS = (24, 25, 26)
PLAYOFF_TEAMS = 8
SEASON_END = dt.date(2027, 4, 4)
TRADE_DEADLINE = dt.date(2027, 3, 3)


def fantasy_points(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return sum(w * stats.get(k, 0.0) for k, w in weights.items())
