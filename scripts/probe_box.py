"""The box scores behind this week's banked points: rows per game date in the
cached NHL stats windows, the same windows fetched fresh, and my players'
games this week as the model context holds them (Check: probe-box).

    python -m scripts.probe_box
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

from bot.common import nhl_date
from clients import nhl_stats
from clients.cache import CACHE_DIR, get
from model.context import current_season_id
from league import weeks


def per_date(rows: list[dict]) -> str:
    c = Counter(r["gameDate"][:10] for r in rows)
    return " ".join(f"{d[5:]}:{n}" for d, n in sorted(c.items()))


def main() -> None:
    today = nhl_date(dt.datetime.now(dt.timezone.utc))
    season = current_season_id(today)
    print("NHL date", today, "season", season)
    for path in sorted((CACHE_DIR / "nhl_stats").glob(f"*_{season}_*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        age = (dt.datetime.now().timestamp() - path.stat().st_mtime) / 3600
        print(f"cached {path.name}: {len(rows)} rows, {age:.1f} h old | {per_date(rows)}")
    for w_start, w_end in nhl_stats._span(season, None, None, today)[-2:]:
        for report in ("skater/summary", "goalie/summary"):
            params = {"isAggregate": "false", "isGame": "true", "limit": -1,
                      "cayenneExp": f"seasonId={season} and gameTypeId=2 "
                                    f'and gameDate>="{w_start}" and gameDate<="{w_end}"'}
            body = get(f"{nhl_stats.STATS_URL}/{report}", params).json()
            rows = body["data"]
            print(f"fresh {report} {w_start}..{w_end}: {len(rows)} rows, total {body.get('total')} | {per_date(rows)}")
    exp = (f"seasonId={season} and gameTypeId=2 "
           f'and gameDate>="2026-11-04" and gameDate<="2026-11-21"')
    for limit in (-1, 0, 500, 1000, 5000, 10000):
        for sort in (None, '[{"property":"gameId","direction":"ASC"},{"property":"playerId","direction":"ASC"}]'):
            params = {"isAggregate": "false", "isGame": "true", "limit": limit, "start": 0, "cayenneExp": exp}
            if sort:
                params["sort"] = sort
            try:
                body = get(f"{nhl_stats.STATS_URL}/skater/summary", params).json()
                print(f"limit {limit} sort {bool(sort)}: {len(body['data'])} rows, total {body.get('total')}")
            except Exception as exc:  # noqa: BLE001
                print(f"limit {limit} sort {bool(sort)}: {type(exc).__name__} {str(exc)[:200]}")
    params = {"isAggregate": "false", "isGame": "true", "limit": 100, "start": 3900, "cayenneExp": exp,
              "sort": '[{"property":"gameId","direction":"ASC"},{"property":"playerId","direction":"ASC"}]'}
    body = get(f"{nhl_stats.STATS_URL}/skater/summary", params).json()
    print(f"start 3900: {len(body['data'])} rows | {per_date(body['data'])}")
    params = {"isAggregate": "false", "isGame": "false", "limit": -1, "cayenneExp": f"seasonId={season}"}
    body = get(f"{nhl_stats.STATS_URL}/skater/bios", params).json()
    print(f"bios: {len(body['data'])} rows, total {body.get('total')}")
    week = weeks.week_of(today)
    days = weeks.days(week)
    from state import gm_state
    state = gm_state.load()
    ids = {int(i) for d in days for i in state.get("my_days", {}).get(d.isoformat(), [])}
    skaters = [g for g in nhl_stats.skater_games(season, days[0], days[-1], today=today) if g.player_id in ids]
    goalies = [g for g in nhl_stats.goalie_games(season, days[0], days[-1], today=today) if g.player_id in ids]
    print(f"week {week}: my skater games {len(skaters)}, goalie games {len(goalies)}")
    for g in skaters + goalies:
        print(" ", g.date, g.name, g.team, g.stats)


if __name__ == "__main__":
    main()
