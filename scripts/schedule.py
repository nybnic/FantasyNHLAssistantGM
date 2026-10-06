"""Every NHL game, day by day, for fantasy weeks: who plays when, and how busy
each night is (a quiet night is easier to fill from the bench or a streamer).

    python -m scripts.schedule            # this week and next
    python -m scripts.schedule 3 4        # weeks 3 and 4

Read-only. One line per day: date, game count, then AWAY@HOME for each game.
"""
from __future__ import annotations

import datetime as dt
import sys

from bot import common
from clients import nhl_client
from league import weeks


def lines(week_numbers: list[int], games_on=nhl_client.games_on) -> list[str]:
    out = []
    for week in week_numbers:
        out.append(f"Week {week}")
        for day in weeks.days(week):
            games = games_on(day)
            out.append(f"{day.isoformat()} {day:%a} {len(games):2} " + " ".join(f"{g.away}@{g.home}" for g in games))
    return out


def main() -> None:
    if len(sys.argv) > 1:
        week_numbers = [int(a) for a in sys.argv[1:]]
    else:
        today = dt.datetime.now(dt.timezone.utc).astimezone(common.NHL_TIME).date()
        week = weeks.week_of(today)
        if week is None:
            raise SystemExit(f"{today} is outside the fantasy season")
        week_numbers = [w for w in (week, week + 1) if w <= weeks.LAST_WEEK]
    print("\n".join(lines(week_numbers)))


if __name__ == "__main__":
    main()
