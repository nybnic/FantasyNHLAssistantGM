"""Every team's goalies as the bot sees them today: DFO status and news
timeline, and each one's share of his team's starts with injured teammates'
starts redistributed (availability.ABSENT_TO_TEAMMATES) and without. Lists
the teams where the two differ (an injured goalie), all teams with --all.

    python -m scripts.goalie_shares [--all]
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot.common import nhl_today
from clients import dfo_lines, nhl_client
from clients.names import normalize_name
from engine import availability, matchup
from model import context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    today = nhl_today()
    ctx = context.build(today)
    for team in sorted(nhl_client.current_teams()):
        lines = dfo_lines.team_lines(team)
        goalies = ctx.team_goalies(team)
        rows = []
        for pid, name in goalies:
            p = type("G", (), {"id": pid, "team": team, "name": name})()
            info = lines.get(normalize_name(name))
            starts = ctx.team_starts.get(team, [])
            before = availability.start_share(pid, today, info, starts, ctx.prior_start_share(pid))
            after = availability.start_share(pid, today, info, starts, ctx.prior_start_share(pid),
                                             *matchup.goalie_absences(p, ctx, lines, today))
            week = availability.start_share(pid, today, info, starts, ctx.prior_start_share(pid),
                                            *matchup.goalie_absences(p, ctx, lines, today + dt.timedelta(days=7)))
            status = (info.injury or "") if info else "not on DFO's chart"
            until = info.out_until if info else None
            rows.append((name, status, "season" if until == dt.date.max else (until or ""), before, after, week))
        if args.all or any(abs(r[3] - r[4]) > 0.005 for r in rows):
            print(f"\n{team}")
            for name, status, until, before, after, week in rows:
                print(f"  {name:24} {status:8} {str(until):10} share {before:.0%} -> {after:.0%} today, "
                      f"{week:.0%} in a week")


if __name__ == "__main__":
    main()
