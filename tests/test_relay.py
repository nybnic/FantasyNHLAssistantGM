"""The relay's crons start the runs that send the evening briefing (GitHub's
own schedule proved too unreliable), so they must land inside its window,
19:30-20:30 Helsinki."""
import datetime as dt
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine import briefing

ROOT = Path(__file__).resolve().parents[1]
NHL_TIME = ZoneInfo("America/New_York")


def cron_times(cron: str, date: dt.date) -> list[dt.datetime]:
    """UTC times a "*/N a-b * * *" cron fires on a date (the only form used)."""
    minute, hour, *rest = cron.split()
    assert rest == ["*", "*", "*"] and minute.startswith("*/"), cron
    first, _, last = hour.partition("-")
    return [
        dt.datetime.combine(date, dt.time(h, m), dt.timezone.utc)
        for h in range(int(first), int(last or first) + 1)
        for m in range(0, 60, int(minute[2:]))
    ]


def relay_crons() -> list[str]:
    return tomllib.loads((ROOT / "relay/wrangler.toml").read_text(encoding="utf-8"))["triggers"]["crons"]


@pytest.mark.parametrize("date", [dt.date(2026, 10, 1), dt.date(2027, 1, 14)])  # summer and winter time
@pytest.mark.parametrize("first_puck", [dt.time(19, 0), dt.time(13, 0)])  # evening slate, weekend matinee
def test_relay_crons_fire_inside_the_briefing_window(date, first_puck):
    start = dt.datetime.combine(date, first_puck, NHL_TIME)
    due = briefing.briefing_due(date, start)
    fires = sorted({t for cron in relay_crons() for t in cron_times(cron, date)})
    in_window = [t for t in fires if due <= t < start and not briefing.quiet(t) and not briefing.briefing_closed(t, date)]
    assert len(in_window) >= 2, f"briefing due {due}, crons fire at {fires}"
