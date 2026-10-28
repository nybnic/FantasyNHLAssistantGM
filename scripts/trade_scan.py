"""Every trade of my players for one or two of a team's, judged as /trade judges one.

    python -m scripts.trade_scan --give "Steven Stamkos" --team "Bottom three"

Prints them best for me first: points a week for me and for them (the add price
charged as /trade charges it), and whether it leaves them short of starters.
Needs the NHL and DailyFaceoff APIs (run it from the Check workflow: trade-scan).
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools

from bot.common import NHL_TIME, _safe, free_agents
from bot.daily import add_price, sync_teams
from clients import dfo_lines, nhl_client
from config.league import MY_TEAM
from engine import trade
from league import draft, teams, weeks
from league import roster as roster_mod
from model import context
from state import gm_state, repairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--give", required=True, help='my players, comma-separated: "Steven Stamkos"')
    parser.add_argument("--team", required=True, help="the other team")
    parser.add_argument("--max-get", type=int, default=2, help="their players per trade (1 or 2)")
    args = parser.parse_args()

    state, players, league = gm_state.load(), roster_mod.load(), teams.load()
    repairs.apply(state, players, league)
    sync_teams(players)
    others = {t: teams.players(league, t) for t in league["teams"]}
    parsed = trade.resolve(f"{args.give} for {next(p.name for p in others[args.team])}", MY_TEAM,
                           {MY_TEAM: players, **others})
    if isinstance(parsed, str):
        raise SystemExit(parsed)
    give = parsed[0]
    date = dt.datetime.now(dt.timezone.utc).astimezone(NHL_TIME).date()
    schedule = {d: nhl_client.games_on(d) for d in trade.horizon(date) if weeks.week_of(d)}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    pool, ctx, rounds, price = free_agents(players, league), context.build(date), draft.rounds(), add_price()
    theirs = roster_mod.active(others[args.team])
    gets = [list(c) for n in range(1, args.max_get + 1) for c in itertools.combinations(theirs, n)]
    results = [trade.evaluate(players, others[args.team], args.team, give, get, pool, ctx, schedule, lines, {},
                              rounds, price) for get in gets]
    print(f"{', '.join(p.name for p in give)} for {args.team}'s players: {len(results)} trades, pts/week over "
          f"the next {trade.HORIZON_DAYS // 7} weeks (me / them), best for me first")
    for r in sorted(results, key=lambda r: -r.me.per_week):
        notes = []
        if r.me.adds and len(r.me.adds) > len(r.me.adds_before):
            notes.append(f"I add {', '.join(p.name for p in r.me.adds if p not in r.me.adds_before)}")
        if r.me.drops:
            notes.append(f"I drop {', '.join(p.name for p in r.me.drops)}")
        if r.them.drops:
            notes.append(f"they drop {', '.join(p.name for p in r.them.drops)}")
        if r.them.short:
            notes.append("leaves them short: expect a no")
        print(f"{r.me.per_week:+6.1f} / {r.them.per_week:+6.1f}  {', '.join(p.name for p in r.get):45} "
              + "; ".join(notes))


if __name__ == "__main__":
    main()
