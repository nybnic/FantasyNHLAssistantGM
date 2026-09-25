"""Cross-references rostered goalies against the real NHL schedule and
starter confirmation, and only flags cases where an action is actually
needed - a bench goalie who's confirmed to start, or an active goalie who's
confirmed NOT to start.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from clients import goalie_client, nhl_client
from engine.models import Recommendation

BENCH_SLOT = "BN"


def _is_goalie(player: dict[str, Any]) -> bool:
    return player.get("position_type") == "G" or "G" in player.get("eligible_positions", [])


def _names_match(a: str, b: str) -> bool:
    norm = lambda s: s.lower().replace(".", "").strip()
    return norm(a) == norm(b)


def check_goalies(
    roster: list[dict[str, Any]],
    lookahead_days: int = 1,
    today: dt.date | None = None,
) -> list[Recommendation]:
    today = today or dt.date.today()
    goalies = [p for p in roster if _is_goalie(p)]
    if not goalies:
        return []

    recs: list[Recommendation] = []
    for offset in range(lookahead_days + 1):
        date = today + dt.timedelta(days=offset)
        teams_playing = nhl_client.teams_playing_on(date)
        starters = goalie_client.get_starters(date)

        for goalie in goalies:
            team = goalie.get("editorial_team_abbr")
            if team not in teams_playing:
                continue

            starter_info = starters.get(team)
            if not starter_info or not starter_info["confirmed"]:
                continue

            is_your_goalie_starting = _names_match(starter_info["goalie_name"], goalie["name"])
            on_bench = goalie.get("selected_position") == BENCH_SLOT

            if is_your_goalie_starting and on_bench:
                recs.append(
                    Recommendation(
                        key=f"goalie_start:{goalie['player_id']}:{date.isoformat()}",
                        category="goalie",
                        message=(
                            f"{goalie['name']} is confirmed starting for {team} on "
                            f"{date.isoformat()} but is on your bench - start him."
                        ),
                    )
                )
            elif not is_your_goalie_starting and not on_bench:
                recs.append(
                    Recommendation(
                        key=f"goalie_sit:{goalie['player_id']}:{date.isoformat()}",
                        category="goalie",
                        message=(
                            f"{goalie['name']} is NOT confirmed starting for {team} on "
                            f"{date.isoformat()} ({starter_info['goalie_name']} is) but "
                            f"he's in your active lineup - consider benching/streaming."
                        ),
                    )
                )
    return recs
