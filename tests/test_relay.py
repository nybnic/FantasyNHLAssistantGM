"""The relay's crons start the runs that send the evening briefing (GitHub's
own schedule proved too unreliable), so they must land inside its window,
19:30-20:30 Helsinki, and the daily plan's, the half hour before Yahoo's day turns."""
import datetime as dt
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import weekly
from engine import briefing

ROOT = Path(__file__).resolve().parents[1]
NHL_TIME = ZoneInfo("America/New_York")


def cron_times(cron: str, date: dt.date) -> list[dt.datetime]:
    """UTC times a cron fires on a date: minutes "*/N" or "a,b", hours "a-b" or "a,b"
    (the only forms used)."""
    minute, hour, *rest = cron.split()
    assert rest == ["*", "*", "*"], cron
    minutes = range(0, 60, int(minute[2:])) if minute.startswith("*/") else [int(m) for m in minute.split(",")]
    first, _, last = hour.partition("-")
    hours = range(int(first), int(last) + 1) if last else [int(h) for h in hour.split(",")]
    return [dt.datetime.combine(date, dt.time(h, m), dt.timezone.utc) for h in hours for m in minutes]


def workflow_crons() -> list[str]:
    text = (ROOT / ".github/workflows/assistant_gm.yml").read_text(encoding="utf-8")
    return [line.split('"')[1] for line in text.splitlines() if line.strip().startswith("- cron:")]


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


# Pacific and Helsinki summer time; PST, Helsinki winter; the March weeks US summer time starts first.
@pytest.mark.parametrize("date", [dt.date(2026, 10, 1), dt.date(2027, 1, 14), dt.date(2027, 3, 20)])
@pytest.mark.parametrize("crons", [relay_crons, workflow_crons])
def test_crons_fire_twice_in_the_half_hour_before_yahoos_day_turns(date, crons):
    fires = sorted({t for cron in crons() for t in cron_times(cron, date)})
    in_window = [t for t in fires if weekly.daily_plan_due({"daily_plan": None}, t)]
    assert len(in_window) >= 2, f"crons fire at {fires}"
    assert not any(briefing.quiet(t) for t in in_window)
