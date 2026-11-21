"""The box scores behind this week's banked points (Check: probe-box): rows
per game date in the cached NHL stats windows of this season, what the API
answers for the latest window now (one request, and its reported total), and
my players' games this week as the model reads them.

Written for 2026-11-21, when the API began answering 100 rows a query and the
week banked 3-0 against Yahoo's 149-120 (clients/nhl_stats.py now pages).

    python -m scripts.probe_box
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

from bot.common import nhl_date
from clients import nhl_stats
from clients.cache import CACHE_DIR, get
from league import weeks
from model.context import current_season_id
from state import gm_state


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
    w_start, w_end = nhl_stats._span(season, None, None, today)[-1]
    for report in ("skater/summary", "goalie/summary"):
        params = {"isAggregate": "false", "isGame": "true", "start": 0, "limit": -1,
                  "cayenneExp": f"seasonId={season} and gameTypeId=2 "
                                f'and gameDate>="{w_start}" and gameDate<="{w_end}"'}
        body = get(f"{nhl_stats.STATS_URL}/{report}", params).json()
        print(f"one request, {report} {w_start}..{w_end}, limit -1: {len(body['data'])} rows of {body.get('total')}")
    days = weeks.days(weeks.week_of(today))
    state = gm_state.load()
    ids = {int(i) for d in days for i in state.get("my_days", {}).get(d.isoformat(), [])}
    skaters = [g for g in nhl_stats.skater_games(season, days[0], days[-1], today=today) if g.player_id in ids]
    goalies = [g for g in nhl_stats.goalie_games(season, days[0], days[-1], today=today) if g.player_id in ids]
    print(f"this week: my skater games {len(skaters)}, goalie games {len(goalies)}")
    for g in skaters + goalies:
        print(" ", g.date, g.name, g.team, g.stats)


if __name__ == "__main__":
    main()
