"""DailyFaceoff's line charts, all 32 teams, once a game day: who's on which
line and PP unit, injured or a game-time decision. DFO keeps no history, so
role and stash questions (docs/plan-2026-10-03.md) can only be tested on what
we archive from now on.

Third-party data, so not in this public repo (Nico, 2026-10-03): the workflow
checks out the private nybnic/FantasyNHLAssistantGM-data into archive/ and
pushes what this step writes there. Without that checkout (no deploy key yet,
local runs) the step does nothing."""
from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

from clients import dfo_lines, nhl_client
from clients.names import normalize_name
from bot.common import NHL_TIME, _safe

logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path("archive")
# After morning skates, when DFO updates the charts; earlier on days whose
# first game starts before then (judgment call).
SNAPSHOT_TIME = dt.time(13, 0)
BEFORE_FIRST_PUCK = dt.timedelta(minutes=30)
COLUMNS = ["date", "team", "name", "id", "group", "slot", "injury", "gtd"]


def due(now: dt.datetime, games: list, archived: str | None) -> bool:
    date = now.astimezone(NHL_TIME).date()
    if not games or archived == date.isoformat():
        return False
    at = min(dt.datetime.combine(date, SNAPSHOT_TIME, NHL_TIME), games[0].start - BEFORE_FIRST_PUCK)
    return now >= at


def rows(date: dt.date, nhl_players: list[dict]) -> list[dict]:
    """Every team's chart with NHL ids matched by name within the team (blank
    when DFO spells a name the NHL doesn't)."""
    ids = {(p["team"], normalize_name(p["name"])): p["id"] for p in nhl_players}
    out = []
    for team in sorted({p["team"] for p in nhl_players}):
        for r in _safe(dfo_lines.rows, team, default=[]):
            out.append({"date": date.isoformat(), "team": team, "name": r["name"],
                        "id": ids.get((team, normalize_name(r["name"])), ""), "group": r["group"],
                        "slot": r["slot"], "injury": r["injury"] or "", "gtd": int(r["gtd"])})
    return out


def write(date: dt.date, new_rows: list[dict], root: Path) -> Path:
    path = root / "dfo_lines" / str(date.year) / f"{date.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(new_rows)
    return path


def archive_step(state: dict, now: dt.datetime, dry_run: bool, root: Path = ARCHIVE_DIR) -> None:
    if not (root / ".git").exists() and not dry_run:
        return  # the private repo isn't checked out here
    date = now.astimezone(NHL_TIME).date()
    if not due(now, nhl_client.games_on(date), state["dfo_archived"]):
        return
    new_rows = rows(date, nhl_client.current_rosters())
    if dry_run:
        print(f"----- DFO archive: {date}, {len(new_rows)} rows, "
              f"{len({r['team'] for r in new_rows})} teams (not written)")
        return
    if len({r["team"] for r in new_rows}) < 16:  # DFO mostly down: try again next run
        logger.warning("DFO archive for %s: only %d teams, not saved", date, len({r["team"] for r in new_rows}))
        return
    write(date, new_rows, root)
    state["dfo_archived"] = date.isoformat()
