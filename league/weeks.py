"""The fantasy calendar: which Yahoo week a date falls in, and its days.

Dates are NHL (Eastern) dates, the same ones nhl_client.games_on uses.
"""
from __future__ import annotations

import datetime as dt

from config.league import DOUBLE_WEEK, PLAYOFF_WEEKS, REGULAR_SEASON_WEEKS, SCHEDULE, SEASON_END, SEASON_START

LAST_WEEK = PLAYOFF_WEEKS[-1]


def week_span(week: int) -> tuple[dt.date, dt.date]:
    """First and last day of `week`, inclusive."""
    if not 1 <= week <= LAST_WEEK:
        raise ValueError(f"no fantasy week {week}")
    first_monday = SEASON_START + dt.timedelta(days=7 - SEASON_START.weekday())
    if week == 1:
        return SEASON_START, first_monday - dt.timedelta(days=1)
    extra = 1 if week > DOUBLE_WEEK else 0
    start = first_monday + dt.timedelta(weeks=week - 2 + extra)
    end = start + dt.timedelta(weeks=2 if week == DOUBLE_WEEK else 1, days=-1)
    return start, min(end, SEASON_END)


def week_of(date: dt.date) -> int | None:
    """The fantasy week containing `date`, or None outside the season."""
    if not SEASON_START <= date <= SEASON_END:
        return None
    return next(w for w in range(1, LAST_WEEK + 1) if week_span(w)[1] >= date)


def days(week: int) -> list[dt.date]:
    start, end = week_span(week)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def midweek(week: int) -> dt.date:
    """When the mid-week update is due: the week's first Wednesday after its first day."""
    return next(d for d in days(week)[1:] if d.weekday() == 2)


def opponent(week: int) -> str | None:
    """Your opponent in a regular-season week; None in the playoffs."""
    return SCHEDULE[week - 1] if week <= REGULAR_SEASON_WEEKS else None
