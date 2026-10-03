"""Data problems seen during a run: a source that failed, or an old cached
copy used instead. The run reports them on Telegram, once a day per source
(main.alert_health), with what each means for the advice: the fallbacks are
quiet otherwise (no DFO line charts = every skater counted healthy).
"""
from __future__ import annotations

# Source -> what a problem with it does to the advice.
SOURCES = {
    "DailyFaceoff line charts": "injuries and scratches unknown, so those players count as healthy",
    "DailyFaceoff starting goalies": "goalie starts from recent shares only, no confirmations",
    "DailyFaceoff projections": "preseason projections missing or old",
    "NHL schedule": "game days may be out of date",
    "NHL rosters": "free agents and players' teams may be out of date",
    "NHL stats": "this season's games may be missing from the projections",
    "Data archive": "DFO line charts and Yahoo matchup rows aren't being saved",
}
# Disk-cache names (clients/cache.py) and fetch functions (main._safe) -> source.
_BY_NAME = {
    "dfo_lines/": "DailyFaceoff line charts", "dfo_teams": "DailyFaceoff line charts",
    "team_lines": "DailyFaceoff line charts",
    "dfo_starters/": "DailyFaceoff starting goalies", "get_starters": "DailyFaceoff starting goalies",
    "dfo_projections": "DailyFaceoff projections",
    "schedule/": "NHL schedule", "games_on": "NHL schedule",
    "rosters_current": "NHL rosters", "current_teams": "NHL rosters", "current_rosters": "NHL rosters",
    "nhl_stats/": "NHL stats",
    "archive": "Data archive",
}

_problems: dict[str, list[str]] = {}


def source_of(name: str) -> str:
    return next((source for key, source in _BY_NAME.items() if name.startswith(key)), name)


def report(name: str, detail: str) -> None:
    """A problem with the data source behind `name` (a cache name or a fetch
    function's); other names (a chart, a message) aren't data and are ignored."""
    source = source_of(name)
    if source in SOURCES:
        _problems.setdefault(source, []).append(detail)


def problems() -> dict[str, list[str]]:
    return dict(_problems)


def clear() -> None:
    _problems.clear()


def describe(source: str, details: list[str]) -> str:
    """'DailyFaceoff line charts: failed 32 times (injuries ... healthy).'"""
    counts: dict[str, int] = {}
    for d in details:
        counts[d] = counts.get(d, 0) + 1
    what = "; ".join(f"{d} ({n}x)" if n > 1 else d for d, n in list(counts.items())[:3])
    impact = SOURCES.get(source)
    return f"{source}: {what}" + (f". Meanwhile {impact}." if impact else ".")
