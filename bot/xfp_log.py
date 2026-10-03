"""What the model believed, week by week: every NHL roster player's projection
at the week's first run, appended to state/xfp_log.csv (committed with the
state). Checks the model in-season against what happened (the code and the
data caches change, so a projection can't be reproduced later) and is what
role and stash questions get tested on (docs/plan-2026-10-03.md).
Our own numbers only: no third-party rows (public repo)."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from clients import nhl_client
from engine import availability
from league import weeks
from model import games as games_model
from bot.common import NHL_TIME

LOG_FILE = Path("state/xfp_log.csv")
COLUMNS = ["week", "date", "id", "name", "team", "pos", "owner",
           "xfp", "toi", "pp_toi", "games", "durability", "start_share", "save_pct"]


def owners(players: list, league: dict) -> dict[int, str]:
    """Player id -> "me", or the league team that has him (everyone else is a free agent)."""
    out = {p["id"]: team for team, entry in league["teams"].items() for p in entry["players"]}
    out.update({p.id: "me" for p in players})
    return out


def rows(date: dt.date, week: int, nhl_players: list[dict], owned: dict[int, str], ctx) -> list[dict]:
    out = []
    for p in nhl_players:
        row = {"week": week, "date": date.isoformat(), "id": p["id"], "name": p["name"], "team": p["team"],
               "pos": p["position"], "owner": owned.get(p["id"], "FA")}
        if p["position"] == "G":
            past = [g for g in ctx.goalie_games.get(p["id"], []) if g.date < ctx.today]
            row["start_share"] = round(availability.start_share(p["id"], date, None, ctx.team_starts.get(p["team"], []),
                                                                ctx.prior_start_share(p["id"])), 3)
            row["save_pct"] = round(games_model.save_pct(ctx.goalie_history.get(p["id"], []), past,
                                                         ctx.league.save_pct), 4)
            row["games"] = len(past)
        else:
            proj = ctx.skater(p["id"], "D" if p["position"] == "D" else "F")
            row.update(xfp=round(proj.xfp, 3), toi=round(proj.toi / 60, 2), pp_toi=round(proj.pp_toi / 60, 2),
                       games=proj.games, durability=round(ctx.durability(p["id"]), 3))
        out.append(row)
    return out


def append(new_rows: list[dict], path: Path = LOG_FILE) -> None:
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerows(new_rows)


def log_step(state: dict, players: list, league: dict, now: dt.datetime, build_context, dry_run: bool,
             path: Path = LOG_FILE) -> None:
    """Once per fantasy week, at its first run (Monday morning, or the first run
    after an outage, with that date)."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None or state["xfp_logged"] == week:
        return
    new_rows = rows(date, week, nhl_client.current_rosters(), owners(players, league), build_context(date))
    if dry_run:
        print(f"----- xfp log: week {week}, {len(new_rows)} players (not written)")
        return
    append(new_rows, path)
    state["xfp_logged"] = week
