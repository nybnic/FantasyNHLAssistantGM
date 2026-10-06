import datetime as dt

from clients.nhl_client import ScheduledGame
from scripts import schedule


def test_one_line_per_day_with_games():
    def games_on(day):
        if day == dt.date(2026, 10, 6):
            start = dt.datetime(2026, 10, 6, 23, tzinfo=dt.timezone.utc)
            return [ScheduledGame(1, start, home="CHI", away="BUF"), ScheduledGame(2, start, home="TOR", away="OTT")]
        return []

    out = schedule.lines([2], games_on)
    assert out[0] == "Week 2"
    assert len(out) == 8  # Mon-Sun
    assert out[1] == "2026-10-05 Mon  0 "
    assert out[2] == "2026-10-06 Tue  2 BUF@CHI OTT@TOR"
