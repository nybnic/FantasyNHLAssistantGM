"""The goalie news hours: 19-22 local (Nico, 2026-10-27), when beat reporters
confirm tonight's starters after the morning skates (12-15 Eastern), every run
reads DailyFaceoff's starting goalies fresh. When a starter is new, changes or
is confirmed, the plan is weighed again (bot/weekly.plan_check_step, quietly:
a message only if the plan changes, or a goalie add waiting on his start can go).
The briefing and the plan read the same fresh copy (the cache), so they see it too.

GamedayTweets was asked for first: its page is behind a Cloudflare bot check
(403 from GitHub's runners, 2026-10-27), so DailyFaceoff, already a source, it is.
"""
from __future__ import annotations

import datetime as dt
import logging

from bot.common import _safe, current_opponent, nhl_date
from clients import goalie_client, nhl_client
from engine import briefing
from league import roster as roster_mod
from league import weeks

logger = logging.getLogger(__name__)

NEWS_START = dt.time(19, 0)  # local
NEWS_END = dt.time(22, 0)
FRESH = 60.0  # seconds: a copy from this run's minute, not the half-hourly cache


def news_hours(now: dt.datetime) -> bool:
    return NEWS_START <= now.astimezone(briefing.LOCAL).time() < NEWS_END


def changes(old: dict, new: dict) -> list[str]:
    """The teams whose starter is new, another goalie, or newly confirmed."""
    return sorted(t for t, s in new.items() if old.get(t) != s)


def news_step(state: dict, players: list, now: dt.datetime) -> None:
    if not news_hours(now) or not roster_mod.active(players):
        return
    date = nhl_date(now)
    week = weeks.week_of(date)
    games = nhl_client.games_on(date)
    if week is None or not current_opponent(state, week) or not games or now >= games[0].start:
        return
    starters = _safe(goalie_client.get_starters, date, FRESH, default={})
    if not starters:
        return  # nothing read (or the page down): keep what was seen
    seen = state["goalie_news"] if (state["goalie_news"] or {}).get("date") == date.isoformat() else {}
    changed = changes(seen.get("starters", {}), starters)
    state["goalie_news"] = {"date": date.isoformat(), "starters": starters}
    if changed:
        logger.info("Goalie news for %s: %s", date, ", ".join(
            f"{t} {starters[t]['goalie_name']}{' (confirmed)' if starters[t]['confirmed'] else ''}" for t in changed))
        state["goalie_recheck"] = True
