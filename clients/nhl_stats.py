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
fetched in date windows (20 days ~ 5,400 skater rows). Since 2026-11-20 the API
answers at most 100 rows a request whatever `limit` asks (`limit=-1` had
returned them all), so every query is paged by `start` and checked against
the `total` it reports.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

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
    """A window fetched with fewer games than an earlier fetch of it had, or
    fewer rows than the total the API reports."""


# The most rows the API returns a request (2026-11-20 on; `limit` above it is
# ignored). A cached copy of exactly one page is what the silent truncation
# left behind (2026-11-20/21), so it's fetched again.
PAGE_ROWS = 100
PAGE_WORKERS = 6
# A stable order, so pages don't overlap or skip rows.
GAME_SORT = json.dumps([{"property": "gameId", "direction": "ASC"}, {"property": "playerId", "direction": "ASC"}])
PLAYER_SORT = json.dumps([{"property": "playerId", "direction": "ASC"}])

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


def _all_rows(report: str, params: dict, sort: str) -> list[dict]:
    """Every row of a query, page by page. Raises if the pages don't add up to
    the total the API reports, so a short answer is never cached as complete
    (cached_json then keeps a recent copy, and the health check says so)."""
    def page(start: int) -> dict:
        return get(f"{STATS_URL}/{report}", {**params, "sort": sort, "start": start, "limit": PAGE_ROWS}).json()

    first = page(0)
    rows, total = list(first["data"]), first.get("total")
    if total is None:
        total = len(rows)
    if total > len(rows) > 0:  # step by what one request got, should the cap change again
        starts = range(len(rows), total, len(rows))
        with ThreadPoolExecutor(PAGE_WORKERS) as pool:
            for body in pool.map(page, starts):
                rows += body["data"]
    if len(rows) != total:
        raise PartialReport(f"{report}: {len(rows)} rows read, the API reports {total}")
    return rows


def _report_rows(report: str, season_id: int, start: dt.date, end: dt.date, today: dt.date) -> list[dict]:
    def fetch() -> list[dict]:
        params = {
            "isAggregate": "false",
            "isGame": "true",
            "cayenneExp": (
                f"seasonId={season_id} and gameTypeId=2 "
                f'and gameDate>="{start.isoformat()}" and gameDate<="{end.isoformat()}"'
            ),
        }
        rows = _all_rows(report, params, GAME_SORT)
        if len(rows) >= ROW_CAP:
            raise RuntimeError(f"{report} {start}..{end} hit the {ROW_CAP}-row cap; shrink WINDOW_DAYS")
        for copy_end, copy in earlier:
            if (n := _games_through(rows, copy_end)) < len(copy):
                raise PartialReport(f"{report} {start}..{end}: {n} games through {copy_end}, {len(copy)} before")
        return rows

    settled = end + dt.timedelta(days=SETTLE_DAYS) < today
    recent = end >= today - dt.timedelta(days=1)
    prefix = f"{report.replace('/', '_')}_{season_id}_{start.isoformat()}"
    name = f"nhl_stats/{prefix}_{end.isoformat()}"
    earlier = _earlier_copies(prefix, end)
    try:
        rows = cached_json(name, None if settled else FRESH_RECENT if recent else FRESH_SETTLING, fetch,
                           valid=_not_one_page)
    except (PartialReport, requests.RequestException):
        if not earlier:
            raise
        rows = None
    # The API answered with games missing (from the night of Nov 19-20: 100 rows of each
    # query, so no skater games after Nov 3 and some goalies' missing, 0 goalie games this
    # week and a false "goalie minimum at risk"): a window's games only ever grow, so the
    # fullest copy wins. Checked after the cache too: a short answer saved before this
    # check (or kept by cached_json's fallback) mustn't stand or become the bar.
    if rows is None or any(_games_through(rows, copy_end) < len(copy) for copy_end, copy in earlier):
        copy_end, rows = max(earlier, key=lambda c: len(c[1]))
        logger.warning("NHL stats %s came back short; using the copy through %s", name, copy_end)
        health.report(name, f"games missing from a fetch, using the copy through {copy_end:%d %b}")
    return rows


def _not_one_page(rows: list) -> bool:
    return len(rows) != PAGE_ROWS


def _earlier_copies(prefix: str, end: dt.date) -> list[tuple[dt.date, list[dict]]]:
    """Every cached fetch of the same window (same report and start) with an end up to
    `end`, as (its end, its rows): the window's end moves with today."""
    out = []
    for path in (cache.CACHE_DIR / "nhl_stats").glob(f"{prefix}_*.json"):
        try:
            copy_end = dt.date.fromisoformat(path.stem[len(prefix) + 1:])
            if copy_end <= end:
                out.append((copy_end, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def _games_through(rows: list[dict], end: dt.date) -> int:
    return sum(1 for r in rows if r["gameDate"][:10] <= end.isoformat())


def skater_birth_dates(season_id: int) -> dict[int, dt.date]:
    """Birth date of every skater who played in a season (skater/bios)."""

    def fetch() -> list[dict]:
        params = {"isAggregate": "false", "isGame": "false", "cayenneExp": f"seasonId={season_id}"}
        return [{"id": r["playerId"], "born": r["birthDate"]}
                for r in _all_rows("skater/bios", params, PLAYER_SORT) if r.get("birthDate")]

    return {r["id"]: dt.date.fromisoformat(r["born"])
            for r in cached_json(f"nhl_stats/skater_bios_{season_id}", None, fetch, valid=_not_one_page)}


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
