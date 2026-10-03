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

from clients import dfo_lines, goalie_client, nhl_client
from engine import availability, matchup
from league import weeks
from league.roster import RosterPlayer
from model import games as games_model
from bot.common import NHL_TIME, _safe

LOG_FILE = Path("state/xfp_log.csv")
# xfp: a skater's points per game played; xfp_start: a goalie's per start, over
# this week's opponents. week_games / week_xfp: his team's games left this week
# and his expected points in them (odds of playing, or of starting, included):
# what a per-player projection like Yahoo's matchup page shows.
COLUMNS = ["week", "date", "id", "name", "team", "pos", "owner",
           "xfp", "toi", "pp_toi", "games", "durability", "start_share", "save_pct",
           "xfp_start", "week_games", "week_xfp"]


def owners(players: list, league: dict) -> dict[int, str]:
    """Player id -> "me", or the league team that has him (everyone else is a free agent)."""
    out = {p["id"]: team for team, entry in league["teams"].items() for p in entry["players"]}
    out.update({p.id: "me" for p in players})
    return out


def week_inputs(date: dt.date, week: int) -> tuple[dict, dict, dict]:
    """(schedule from `date` to the week's end, DFO lines, confirmed starters)."""
    schedule = {d: nhl_client.games_on(d) for d in weeks.days(week) if d >= date}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    return schedule, lines, _safe(goalie_client.get_starters, date, default={})


def week_values(p: dict, ctx, schedule: dict, lines: dict, starters: dict) -> dict:
    """{"week_games", "week_xfp", and for a goalie "xfp_start"}, as the weekly
    plan projects each game (matchup._player_day)."""
    player = RosterPlayer(p["id"], p["name"], p["team"], ["G" if p["position"] == "G" else
                                                          "D" if p["position"] == "D" else "C"])
    first = matchup._first_games(schedule, ctx.today)
    games, total, per_start = 0, 0.0, []
    for date in sorted(schedule):
        game = matchup._game_of(schedule[date]).get(p["team"])
        if not game:
            continue
        yesterday = matchup._game_of(schedule.get(date - dt.timedelta(days=1), []))
        mean, _, _ = matchup._player_day(player, ctx, date, game, p["team"] in yesterday, lines, starters,
                                         next_game=first.get(p["team"]) == date)
        games += 1
        total += mean
        if player.is_goalie:
            home = game.home == p["team"]
            per_start.append(ctx.goalie_start(p["id"], p["team"], game.away if home else game.home, home)["xfp"])
    out = {"week_games": games, "week_xfp": round(total, 3)}
    if per_start:
        out["xfp_start"] = round(sum(per_start) / len(per_start), 3)
    return out


def rows(date: dt.date, week: int, nhl_players: list[dict], owned: dict[int, str], ctx,
         inputs: tuple[dict, dict, dict] | None = None) -> list[dict]:
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
        if inputs:
            row.update(week_values(p, ctx, *inputs))
        out.append(row)
    return out


def append(new_rows: list[dict], path: Path = LOG_FILE) -> None:
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not fresh:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            old_rows = list(reader)
        if reader.fieldnames != COLUMNS:  # new columns: rewrite, earlier weeks leave them blank
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(old_rows)
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
    new_rows = rows(date, week, nhl_client.current_rosters(), owners(players, league), build_context(date),
                    week_inputs(date, week))
    if dry_run:
        mine = [r for r in new_rows if r["owner"] == "me"]
        starts = [r["xfp_start"] for r in new_rows if r.get("xfp_start")]
        print(f"----- xfp log: week {week}, {len(new_rows)} players (not written); mine: "
              + ", ".join(f"{r['name']} {r.get('week_xfp', 0):.1f}" for r in mine)
              + (f"; goalies' points per start {min(starts):.1f}-{max(starts):.1f}" if starts else ""))
        return
    append(new_rows, path)
    state["xfp_logged"] = week
