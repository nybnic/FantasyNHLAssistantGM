"""Thin wrapper over yahoo_fantasy_api for the pieces Assistant GM needs.

Verified against yahoo_fantasy_api's actual source (installed package, not
just docs) during development - see league.py's `roster()`, `positions()`,
`free_agents()`, `stat_categories()` and `team_key()` for the exact return
shapes this wraps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yahoo_fantasy_api as yfa
from yahoo_oauth import OAuth2

GAME_CODE = "nhl"


@dataclass
class YahooLeague:
    game: yfa.Game
    league: yfa.League
    team: yfa.Team
    team_key: str


def connect(session: OAuth2, league_key: str) -> YahooLeague:
    """Connect to the configured NHL league and the logged-in user's team.

    :param league_key: Full Yahoo league key, e.g. "453.l.12345" (game-id
        prefix + league id). Obtain it with scripts/setup_yahoo_oauth.py.
    """
    game = yfa.Game(session, GAME_CODE)
    league = game.to_league(league_key)
    team_key = league.team_key()
    team = league.to_team(team_key)
    return YahooLeague(game=game, league=league, team=team, team_key=team_key)


def get_roster(yl: YahooLeague) -> list[dict[str, Any]]:
    """Today's roster. Each entry has: player_id, name, position_type,
    eligible_positions, selected_position, status, editorial_team_abbr."""
    return yl.team.roster()


def get_roster_slots(yl: YahooLeague) -> dict[str, dict[str, Any]]:
    """League roster slot layout, e.g. {'G': {'position_type': 'G', 'count': 2},
    'BN': {'count': 2}, 'IR': {'count': 3}}."""
    return yl.league.positions()


def get_free_agents(yl: YahooLeague, position: str) -> list[dict[str, Any]]:
    """Free agents eligible for `position` (e.g. 'G', 'C', 'D')."""
    return yl.league.free_agents(position)


def get_stat_categories(yl: YahooLeague) -> list[dict[str, Any]]:
    """[Phase 2] Live scoring categories, e.g.
    [{'display_name': 'G', 'position_type': 'P'}, ...]."""
    return yl.league.stat_categories()
