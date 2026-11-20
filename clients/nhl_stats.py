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
import json
import logging
from dataclasses import dataclass, field

from clients import cache, health
from clients.cache import HOUR, cached_json, get

STATS_URL = "https://api.nhle.com/stats/rest/en"
ROW_CAP = 10_000
WINDOW_DAYS = 20
# A window is re-fetched until this many days after it ends (late stat corrections).
SETTLE_DAYS = 2
# How long a fetch of a window still settling is kept: hourly while it holds
# yesterday (a plan run made after the last puck fetches a night whose games may
# not all be in yet: bot.common.nhl_date), else 6 hours.
FRESH_RECENT, FRESH_SETTLING = 1 * HOUR, 6 * HOUR
logger = logging.getLogger(__name__)


class PartialReport(RuntimeError):
    """A window fetched with fewer games than an earlier fetch of it had."""

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
        if earlier and (n := _games_through(rows, earlier[0])) < len(earlier[1]):
            raise PartialReport(f"{report} {start}..{end}: {n} games through {earlier[0]}, {len(earlier[1])} before")
        return rows

    settled = end + dt.timedelta(days=SETTLE_DAYS) < today
    recent = end >= today - dt.timedelta(days=1)
    prefix = f"{report.replace('/', '_')}_{season_id}_{start.isoformat()}"
    name = f"nhl_stats/{prefix}_{end.isoformat()}"
    earlier = _earlier_copy(prefix, end)
    try:
        return cached_json(name, None if settled else FRESH_RECENT if recent else FRESH_SETTLING, fetch)
    except PartialReport:
        # The API answered with games missing (2026-11-20 06:42 UTC: no skater games after
        # Nov 3 and some goalies' missing, so 0 goalie games this week and a false "goalie
        # minimum at risk"): a window's games only ever grow, so keep the fuller copy.
        logger.warning("NHL stats %s came back short; using the copy through %s", name, earlier[0], exc_info=True)
        health.report(name, f"games missing from a fetch, using the copy through {earlier[0]:%d %b}")
        return earlier[1]


def _earlier_copy(prefix: str, end: dt.date) -> tuple[dt.date, list[dict]] | None:
    """The cached fetch of the same window (same report and start) with the latest
    end up to `end`, as (its end, its rows): the window's end moves with today."""
    best = None
    for path in (cache.CACHE_DIR / "nhl_stats").glob(f"{prefix}_*.json"):
        try:
            copy_end = dt.date.fromisoformat(path.stem[len(prefix) + 1:])
        except ValueError:
            continue
        if copy_end <= end and (best is None or copy_end > best[0]):
            best = (copy_end, path)
    if best is None:
        return None
    try:
        return best[0], json.loads(best[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _games_through(rows: list[dict], end: dt.date) -> int:
    return sum(1 for r in rows if r["gameDate"][:10] <= end.isoformat())


def skater_birth_dates(season_id: int) -> dict[int, dt.date]:
    """Birth date of every skater who played in a season (skater/bios)."""

    def fetch() -> list[dict]:
        params = {"isAggregate": "false", "isGame": "false", "limit": -1, "cayenneExp": f"seasonId={season_id}"}
        return [{"id": r["playerId"], "born": r["birthDate"]}
                for r in get(f"{STATS_URL}/skater/bios", params).json()["data"] if r.get("birthDate")]

    return {r["id"]: dt.date.fromisoformat(r["born"])
            for r in cached_json(f"nhl_stats/skater_bios_{season_id}", None, fetch)}


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
