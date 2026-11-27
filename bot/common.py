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
from league import parse, positions, teams, weeks
from league import roster as roster_mod
from notify import telegram

logger = logging.getLogger(__name__)

NHL_TIME = ZoneInfo("America/New_York")


CHART_DIR = Path("data/charts")  # where a dry run saves the charts it would send


def _buttons(buttons: list[tuple[str, str]] | None, links: list[tuple[str, str]] | None = None) -> str:
    """A dry run's printout of a message's buttons: its own, its links, then the dashboard link every message has."""
    return " [" + " | ".join([b[0] for b in buttons or []] + [f"{label} ({url})" for label, url in links or []]
                             + [f"{telegram.DASHBOARD_BUTTON} (link)"]) + "]"


class Outbox:
    """Sends to Telegram, or prints in a dry run."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None, html: bool = False,
             links: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            print(f"\n----- Telegram message{_buttons(buttons, links)}\n{text}")
            return None
        return telegram.send_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text, buttons,
                                     html, links)

    def mark(self, message_id: int | None, label: str) -> None:
        """Replace a message's buttons with an inert label (a replaced add card)."""
        if self.settings.dry_run:
            print(f"\n----- Telegram buttons of message {message_id} -> [{label}]")
        elif message_id is not None:
            telegram.mark_handled(self.settings.telegram_bot_token, self.settings.telegram_chat_id, message_id,
                                  label)

    def send_photo(self, png: bytes, caption: str | None = None, buttons: list[tuple[str, str]] | None = None,
                   html: bool = False, links: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            CHART_DIR.mkdir(parents=True, exist_ok=True)
            path = CHART_DIR / f"{len(list(CHART_DIR.glob('*.png'))):02d}.png"
            path.write_bytes(png)
            print(f"\n----- Telegram photo{_buttons(buttons, links)}: {path}" + (f"\n{caption}" if caption else ""))
            return None
        return telegram.send_photo(self.settings.telegram_bot_token, self.settings.telegram_chat_id, png,
                                   caption, buttons, html, links)


def nhl_today() -> dt.date:
    return dt.datetime.now(NHL_TIME).date()


def nhl_date(now: dt.datetime) -> dt.date:
    """The NHL date a plan is made for: Eastern time's, and the next one once
    that day's last game has started. Nothing can change that day after its last
    puck: planning for it then (Helsinki's early morning, before Eastern
    midnight) counted its played games as still to come (2026-10-15)."""
    date = now.astimezone(NHL_TIME).date()
    games = _safe(nhl_client.games_on, date, default=[])
    return date + dt.timedelta(days=1) if games and now >= games[-1].start else date


def remember_mine(state: dict, players: list, date: dt.date) -> None:
    """Note today as the last day each of my players was on my roster
    (bot/ingest.count_new_players: someone mine lately isn't a new add)."""
    for p in players:
        state["seen_mine"][str(p.id)] = date.isoformat()


def forget_mine(state: dict, player_id: int) -> None:
    """I dropped him: a later re-add of him is an add, not a correction."""
    state["seen_mine"].pop(str(player_id), None)


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


# A tag Nico sets (/dtd, /out) lapses once his team has played, or after this
# long whatever (a judgment call: Yahoo's tags are day-to-day news).
TAG_DAYS = 7
TAG_STATUS = {"dtd": "day-to-day", "out": "out"}


def tag_lapsed(tag: dict, date: dt.date) -> bool:
    """Whether an injury tag set on tag["date"] no longer holds on `date`: his
    team has played a game since (then he played or sat, and DFO's chart says
    which), or it's older than TAG_DAYS."""
    since = dt.date.fromisoformat(tag["date"])
    if (date - since).days > TAG_DAYS:
        return True
    days = [since + dt.timedelta(days=i) for i in range((date - since).days)]
    return any(tag["team"] in (g.home, g.away) for d in days for g in _safe(nhl_client.games_on, d, default=[]))


def team_lines(team_codes, state: dict | None = None, date: dt.date | None = None) -> dict:
    """DFO's line charts by team (clients/dfo_lines.team_lines), with Nico's
    injury tags (state["injury_tags"], /dtd and /out: Yahoo had Guhle day-to-day
    on 2026-11-27 while DFO listed nothing) laid over the players DFO has no
    injury news for. DFO's own news wins, and a team whose chart didn't load
    keeps none (an entry there would make its unlisted players look scratched).
    Lapsed tags are dropped from the state."""
    from dataclasses import replace

    from clients import dfo_lines
    from clients.names import normalize_name

    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in team_codes}
    if state is None or date is None:
        return lines
    tags = state.setdefault("injury_tags", {})
    for pid, tag in list(tags.items()):
        if tag_lapsed(tag, date):
            del tags[pid]
            continue
        chart = lines.get(tag["team"])
        if not chart:
            continue
        key = normalize_name(tag["name"])
        info = chart.get(key)
        if info is not None and (info.injury or "ir" in info.groups):
            continue
        chart[key] = replace(info, injury=tag["status"]) if info else dfo_lines.LineInfo(injury=tag["status"])
    return lines


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
