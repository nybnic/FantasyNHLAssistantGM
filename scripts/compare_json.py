"""Two or more players side by side, as JSON: this season's games and fantasy
points by stat, the latest projection (state/xfp_log.csv), and their NHL
teams' games in the fantasy playoffs (weeks 24-26) and the rest of the
regular season, with how many fall on light nights. Read-only.

    python -m scripts.compare_json out.json "Alex Laferriere" "Mattias Samuelsson"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter

from clients import nhl_client, nhl_stats
from clients.names import normalize_name
from config.league import SKATER_WEIGHTS, fantasy_points
from league import weeks as fantasy_weeks
from scripts.depth import latest_rows

SEASON = 20262027
LIGHT = 7  # a night with at most this many games (of 16 possible) is light: fewer slots taken


def schedule(first: int, last: int) -> dict[dt.date, list]:
    start, end = fantasy_weeks.week_span(first)[0], fantasy_weeks.week_span(last)[1]
    return {start + dt.timedelta(days=i): nhl_client.games_on(start + dt.timedelta(days=i))
            for i in range((end - start).days + 1)}


def team_weeks(nights: dict[dt.date, list], team: str) -> dict:
    by_week: dict[int, list[str]] = {}
    light = 0
    for day, games in sorted(nights.items()):
        if any(team in (g.home, g.away) for g in games):
            week = fantasy_weeks.week_of(day)
            opp = next(g.away if g.home == team else "@" + g.home for g in games if team in (g.home, g.away))
            by_week.setdefault(week, []).append(f"{day:%a %d %b} {opp}" + (" (light)" if len(games) <= LIGHT else ""))
            light += len(games) <= LIGHT
    return {"games": sum(len(v) for v in by_week.values()), "light": light,
            "by_week": {str(w): v for w, v in by_week.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    want = {normalize_name(n) for n in args.names}
    rows = latest_rows()
    today = dt.date.today()
    games = [g for g in nhl_stats.skater_games(SEASON, today=today) if normalize_name(g.name) in want]
    now_week = fantasy_weeks.week_of(today) or 1
    playoffs = schedule(24, 26)
    rest = schedule(now_week + 1, 23)
    out = []
    for name in args.names:
        mine = [g for g in games if normalize_name(g.name) == normalize_name(name)]
        totals = Counter()
        for g in mine:
            totals.update(g.stats)
        per_game = [round(fantasy_points(g.stats, SKATER_WEIGHTS), 1) for g in mine]
        by_stat = {k: round(totals[k] * w, 1) for k, w in SKATER_WEIGHTS.items() if totals[k]}
        proj = next((r for r in rows.values() if normalize_name(r["name"]) == normalize_name(name)), {})
        team = mine[-1].team if mine else proj.get("team", "")
        out.append({
            "name": name, "team": team, "games": len(mine),
            "points": round(sum(per_game), 1), "per_game": per_game,
            "stats": dict(totals), "points_by_stat": by_stat,
            "toi_min": round(sum(g.toi for g in mine) / max(len(mine), 1) / 60, 1),
            "pp_toi_min": round(sum(g.pp_toi for g in mine) / max(len(mine), 1) / 60, 2),
            "projection": proj,
            "playoffs": team_weeks(playoffs, team),
            "rest_of_regular_season": team_weeks(rest, team),
        })
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"date": str(today), "light_night_max_games": LIGHT, "players": out}, f, ensure_ascii=False, indent=1, default=str)
    for p in out:
        print(p["name"], p["games"], p["points"], p["playoffs"]["games"])


if __name__ == "__main__":
    main()
