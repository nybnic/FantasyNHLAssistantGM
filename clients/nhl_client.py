"""Thin wrapper over the public NHL API (api-web.nhle.com).

Endpoint and field names verified against live responses: GET
/v1/schedule/{date} returns a week of games, each with id, gameType
(2 = regular season), startTimeUTC and awayTeam/homeTeam.abbrev (the
official 3-letter code).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from clients.cache import DAY, HOUR, cached_json, get

BASE_URL = "https://api-web.nhle.com/v1"


def current_teams() -> list[str]:
    """The 32 current team codes."""

    def fetch() -> list[str]:
        return sorted(t["teamAbbrev"]["default"] for t in get(f"{BASE_URL}/standings/now").json()["standings"])

    return cached_json("current_teams", 7 * DAY, fetch)


def current_rosters() -> list[dict]:
    """Every player on a current NHL roster: id, name, team, position, birth_date."""

    def fetch() -> list[dict]:
        players = []
        for team in current_teams():
            roster = get(f"{BASE_URL}/roster/{team}/current").json()
            for group in ("forwards", "defensemen", "goalies"):
                for p in roster.get(group, []):
                    players.append({
                        "id": p["id"],
                        "name": f"{p['firstName']['default']} {p['lastName']['default']}",
                        "team": team,
                        "position": p["positionCode"],
                        "birth_date": p.get("birthDate"),
                    })
        return players

    return cached_json("rosters_current_v2", DAY, fetch)


REGULAR_SEASON = 2


@dataclass
class ScheduledGame:
    game_id: int
    start: dt.datetime  # UTC
    home: str
    away: str


def games_on(date: dt.date) -> list[ScheduledGame]:
    """Regular-season games on `date` (the NHL's own, Eastern-time date)."""

    def fetch() -> list[dict]:
        for day in get(f"{BASE_URL}/schedule/{date.isoformat()}").json().get("gameWeek", []):
            if day.get("date") == date.isoformat():
                return [
                    {
                        "id": g["id"],
                        "start": g["startTimeUTC"],
                        "home": g["homeTeam"]["abbrev"],
                        "away": g["awayTeam"]["abbrev"],
                    }
                    for g in day.get("games", [])
                    if g.get("gameType") == REGULAR_SEASON
                ]
        return []

    rows = cached_json(f"schedule/{date.isoformat()}", 3 * HOUR, fetch)
    return sorted(
        (
            ScheduledGame(
                game_id=r["id"],
                start=dt.datetime.fromisoformat(r["start"].replace("Z", "+00:00")),
                home=r["home"],
                away=r["away"],
            )
            for r in rows
        ),
        key=lambda g: g.start,
    )
