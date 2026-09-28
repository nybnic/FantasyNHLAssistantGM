"""Game-level player stats from the NHL stats REST API (api.nhle.com).

One record per player per game, carrying every stat the league scores:
  skater/summary     -> goals, assists, plusMinus, penaltyMinutes, ppGoals,
                        ppPoints, shGoals, shPoints, gameWinningGoals, shots
  skater/realtime    -> hits, blockedShots
  skater/faceoffwins -> totalFaceoffWins
  skater/timeonice   -> timeOnIce, ppTimeOnIce (seconds)
  goalie/summary     -> gamesStarted, wins, goalsAgainst, saves, shutouts,
                        shotsAgainst

Game-level queries (isGame=true) are capped at 10,000 rows, so a season is
fetched in date windows (20 days ~ 5,400 skater rows).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from clients.cache import HOUR, cached_json, get

STATS_URL = "https://api.nhle.com/stats/rest/en"
ROW_CAP = 10_000
WINDOW_DAYS = 20
# A window is re-fetched until this many days after it ends (late stat corrections).
SETTLE_DAYS = 2

SKATER_REPORTS = ("skater/summary", "skater/realtime", "skater/faceoffwins", "skater/timeonice")


@dataclass
class SkaterGame:
    player_id: int
    name: str
    position: str  # C, L, R or D
    team: str
    opponent: str
    date: dt.date
    game_id: int
    stats: dict[str, float] = field(default_factory=dict)
    toi: float = 0.0  # seconds
    pp_toi: float = 0.0


@dataclass
class GoalieGame:
    player_id: int
    name: str
    team: str
    opponent: str
    date: dt.date
    game_id: int
    started: bool
    home: bool
    stats: dict[str, float] = field(default_factory=dict)
    shots_against: float = 0.0


def season_window(season_id: int) -> tuple[dt.date, dt.date]:
    """Calendar span that contains a regular season, e.g. 20252026."""
    first_year = season_id // 10_000
    return dt.date(first_year, 9, 25), dt.date(first_year + 1, 4, 30)


def _windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + dt.timedelta(days=WINDOW_DAYS - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + dt.timedelta(days=1)
    return windows


def _report_rows(report: str, season_id: int, start: dt.date, end: dt.date, today: dt.date) -> list[dict]:
    def fetch() -> list[dict]:
        params = {
            "isAggregate": "false",
            "isGame": "true",
            "limit": -1,
            "cayenneExp": (
                f"seasonId={season_id} and gameTypeId=2 "
                f'and gameDate>="{start.isoformat()}" and gameDate<="{end.isoformat()}"'
            ),
        }
        rows = get(f"{STATS_URL}/{report}", params).json()["data"]
        if len(rows) >= ROW_CAP:
            raise RuntimeError(f"{report} {start}..{end} hit the {ROW_CAP}-row cap; shrink WINDOW_DAYS")
        return rows

    settled = end + dt.timedelta(days=SETTLE_DAYS) < today
    name = f"nhl_stats/{report.replace('/', '_')}_{season_id}_{start.isoformat()}_{end.isoformat()}"
    return cached_json(name, None if settled else 6 * HOUR, fetch)


def _span(season_id: int, start: dt.date | None, end: dt.date | None, today: dt.date):
    season_start, season_end = season_window(season_id)
    start = max(start or season_start, season_start)
    end = min(end or season_end, season_end, today)
    return _windows(start, end) if start <= end else []


def skater_games(
    season_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
    today: dt.date | None = None,
) -> list[SkaterGame]:
    today = today or dt.date.today()
    games: dict[tuple[int, int], SkaterGame] = {}
    for w_start, w_end in _span(season_id, start, end, today):
        rows = {r: _report_rows(r, season_id, w_start, w_end, today) for r in SKATER_REPORTS}
        for row in rows["skater/summary"]:
            key = (row["playerId"], row["gameId"])
            games[key] = SkaterGame(
                player_id=row["playerId"],
                name=row["skaterFullName"],
                position=row["positionCode"],
                team=row["teamAbbrev"],
                opponent=row["opponentTeamAbbrev"],
                date=dt.date.fromisoformat(row["gameDate"][:10]),
                game_id=row["gameId"],
                stats={
                    "g": row["goals"],
                    "a": row["assists"],
                    "pm": row["plusMinus"],
                    "pim": row["penaltyMinutes"],
                    "ppg": row["ppGoals"],
                    "ppa": row["ppPoints"] - row["ppGoals"],
                    "shg": row["shGoals"],
                    "sha": row["shPoints"] - row["shGoals"],
                    "gwg": row["gameWinningGoals"],
                    "sog": row["shots"],
                },
            )
        for row in rows["skater/realtime"]:
            game = games.get((row["playerId"], row["gameId"]))
            if game:
                game.stats["hit"] = row["hits"] or 0
                game.stats["blk"] = row["blockedShots"] or 0
        for row in rows["skater/faceoffwins"]:
            game = games.get((row["playerId"], row["gameId"]))
            if game:
                game.stats["fow"] = row["totalFaceoffWins"] or 0
        for row in rows["skater/timeonice"]:
            game = games.get((row["playerId"], row["gameId"]))
            if game:
                game.toi = row["timeOnIce"] or 0.0
                game.pp_toi = row["ppTimeOnIce"] or 0.0
    return sorted(games.values(), key=lambda g: (g.date, g.game_id, g.player_id))


def goalie_games(
    season_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
    today: dt.date | None = None,
) -> list[GoalieGame]:
    today = today or dt.date.today()
    games = []
    for w_start, w_end in _span(season_id, start, end, today):
        for row in _report_rows("goalie/summary", season_id, w_start, w_end, today):
            games.append(
                GoalieGame(
                    player_id=row["playerId"],
                    name=row["goalieFullName"],
                    team=row["teamAbbrev"],
                    opponent=row["opponentTeamAbbrev"],
                    date=dt.date.fromisoformat(row["gameDate"][:10]),
                    game_id=row["gameId"],
                    started=bool(row["gamesStarted"]),
                    home=row["homeRoad"] == "H",
                    stats={
                        "gs": row["gamesStarted"],
                        "w": row["wins"],
                        "ga": row["goalsAgainst"],
                        "sv": row["saves"],
                        "so": row["shutouts"],
                    },
                    shots_against=row["shotsAgainst"] or 0.0,
                )
            )
    return sorted(games, key=lambda g: (g.date, g.game_id, g.player_id))
