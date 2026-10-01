"""What the bot's steps share: the Telegram outbox (or a dry run's printout), NHL dates,
this week's opponent, fetches that may fail, the free agents, and Yahoo positions.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from zoneinfo import ZoneInfo


from clients import health, nhl_client
from config.settings import Settings
from engine import matchup
from league import parse, positions, teams, weeks
from league import roster as roster_mod
from notify import telegram

logger = logging.getLogger(__name__)

NHL_TIME = ZoneInfo("America/New_York")


CHART_DIR = Path("data/charts")  # where a dry run saves the charts it would send


class Outbox:
    """Sends to Telegram, or prints in a dry run."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            print(f"\n----- Telegram message{' [' + ' | '.join(b[0] for b in buttons) + ']' if buttons else ''}\n{text}")
            return None
        return telegram.send_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text, buttons)

    def send_photo(self, png: bytes, caption: str | None = None,
                   buttons: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            CHART_DIR.mkdir(parents=True, exist_ok=True)
            path = CHART_DIR / f"{len(list(CHART_DIR.glob('*.png'))):02d}.png"
            path.write_bytes(png)
            label = f" [{' | '.join(b[0] for b in buttons)}]" if buttons else ""
            print(f"\n----- Telegram photo{label}: {path}" + (f"\n{caption}" if caption else ""))
            return None
        return telegram.send_photo(self.settings.telegram_bot_token, self.settings.telegram_chat_id, png,
                                   caption, buttons)


def nhl_today() -> dt.date:
    return dt.datetime.now(NHL_TIME).date()


def current_opponent(state: dict, week: int) -> str | None:
    return weeks.opponent(week) or state["opponents"].get(str(week))


def _safe(fetch, *args, default=None):
    try:
        return fetch(*args)
    except Exception as exc:
        name = getattr(fetch, "__name__", str(fetch))
        logger.warning("%s failed; continuing without it", name, exc_info=True)
        health.report(name, f"failed ({type(exc).__name__})")
        return default


def _weakest(players: list, ctx, lines: dict):
    """The skater to drop for a player back from IR: my lowest long-run value."""
    return next((p for p in matchup.drop_candidates(roster_mod.active(players), ctx, lines) if not p.is_goalie), None)


def free_agents(players: list, league: dict) -> list:
    """Everyone on an NHL roster nobody in the league has, with Yahoo's
    position eligibility where a screenshot has shown it (league/positions.py)."""
    taken = teams.rostered_ids(league) | {p.id for p in players}
    known = positions.load()
    return [
        roster_mod.RosterPlayer(p["id"], p["name"], p["team"],
                                positions.eligible(known, p["id"], parse.NHL_TO_YAHOO_POS[p["position"]]))
        for p in nhl_client.current_rosters() if p["id"] not in taken
    ]


def learn_positions(found_players: list, tagged: set[int] | None = None) -> None:
    """Remember the positions Yahoo showed (all of them, or only `tagged` ids)."""
    known = positions.load()
    seen = {p.id: p.positions for p in found_players if tagged is None or p.id in tagged}
    if positions.learn(known, seen):
        positions.save(known)
