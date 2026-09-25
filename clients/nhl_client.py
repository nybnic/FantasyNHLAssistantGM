"""Thin wrapper over the public NHL API (api-web.nhle.com).

Endpoint and field names verified against a live response during
development (GET /v1/schedule/{date} returns a week's worth of games, each
tagged with awayTeam.abbrev / homeTeam.abbrev - the official 3-letter
triCode also used by Yahoo's editorial_team_abbr and DailyFaceoff).
"""
from __future__ import annotations

import datetime as dt

import requests

BASE_URL = "https://api-web.nhle.com/v1"


def get_schedule(date: dt.date) -> dict:
    """Raw schedule response for the week containing `date`."""
    resp = requests.get(f"{BASE_URL}/schedule/{date.isoformat()}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def teams_playing_on(date: dt.date) -> set[str]:
    """NHL team triCodes (e.g. 'TOR', 'BOS') with a game on `date`."""
    schedule = get_schedule(date)
    for day in schedule.get("gameWeek", []):
        if day.get("date") == date.isoformat():
            teams: set[str] = set()
            for game in day.get("games", []):
                teams.add(game["awayTeam"]["abbrev"])
                teams.add(game["homeTeam"]["abbrev"])
            return teams
    return set()
