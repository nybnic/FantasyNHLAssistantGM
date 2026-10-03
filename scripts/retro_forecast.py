"""Our week 1 forecast, rebuilt after the fact: the bot's first plan came
mid-week (Thu Oct 1), so week 1 has no forecast of ours from before its
games. Every team plays its drafted roster (adds opened Sep 30), projected as
of Sep 29 by today's code: the model only sees games before that date, but no
injury report or confirmed goalies (DFO keeps no history), so everyone counts
as healthy. Prints the 16 teams' expected points and spread as JSON; state
gets them through a repair (state/repairs.py), marked as rebuilt.

    python -m scripts.retro_forecast
"""
from __future__ import annotations

import json
import math

from clients import nhl_client
from engine import matchup
from league import draft, parse, weeks
from model import context
from scripts.seed_league import sections


def main() -> None:
    days = weeks.days(1)
    ctx = context.build(days[0])
    schedule = {d: nhl_client.games_on(d) for d in days}
    registry = parse.registry()
    out = {}
    for team, text in sections(draft.DRAFT_FILE.read_text(encoding="utf-8")).items():
        found = parse.find_players(text, registry)
        week = matchup.project(team, found.players, ctx, schedule, {}, {})
        out[team] = {"expected": round(week.expected, 2), "sd": round(math.sqrt(week.variance), 2),
                     "players": len(found.players), "lineup_games": week.player_games,
                     "goalie_min": round(week.goalie_min_prob, 3)}
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
