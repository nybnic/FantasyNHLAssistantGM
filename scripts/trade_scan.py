"""Trades with one team, each judged as /trade judges one.

    python -m scripts.trade_scan --give "Steven Stamkos" --team "Bottom three"   # him for 1-2 of theirs
    python -m scripts.trade_scan --team Randy                                    # every 1-for-1
    python -m scripts.trade_scan --team Randy --trades "Vejmelka, Landeskog > J.T. Miller | Pionk > Wilson"

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
    parser.add_argument("--give", help='my players, comma-separated: "Steven Stamkos" (else every 1-for-1)')
    parser.add_argument("--trades", help='trades to judge too, MINE > THEIRS, separated by |')
    parser.add_argument("--team", required=True, help="the other team")
    parser.add_argument("--max-get", type=int, default=2, help="their players per trade (1 or 2)")
    args = parser.parse_args()

    state, players, league = gm_state.load(), roster_mod.load(), teams.load()
    repairs.apply(state, players, league)
    sync_teams(players)
    others = {t: teams.players(league, t) for t in league["teams"]}
    rosters = {MY_TEAM: players, **others}
    theirs = roster_mod.active(others[args.team])
    trades: list[tuple[list, list]] = []
    if args.give:
        parsed = trade.resolve(f"{args.give} for {theirs[0].name}", MY_TEAM, rosters)
        if isinstance(parsed, str):
            raise SystemExit(parsed)
        trades += [(parsed[0], list(c)) for n in range(1, args.max_get + 1) for c in itertools.combinations(theirs, n)]
    else:
        trades += [([mine], [their]) for mine in roster_mod.active(players) for their in theirs]
    for text in (args.trades or "").split("|"):
        if text.strip():
            parsed = trade.resolve(text.replace(">", " for "), MY_TEAM, rosters)
            if isinstance(parsed, str):
                raise SystemExit(f"{text.strip()}: {parsed}")
            trades.append((parsed[0], parsed[1]))
    date = dt.datetime.now(dt.timezone.utc).astimezone(NHL_TIME).date()
    schedule = {d: nhl_client.games_on(d) for d in trade.horizon(date) if weeks.week_of(d)}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    pool, ctx, rounds, price = free_agents(players, league), context.build(date), draft.rounds(), add_price()
    results = [trade.evaluate(players, others[args.team], args.team, give, get, pool, ctx, schedule, lines, {},
                              rounds, price) for give, get in trades]
    print(f"Trades with {args.team}: {len(results)}, pts/week over the next {trade.HORIZON_DAYS // 7} weeks "
          "(me / them), best for me first; drafted rounds given / got")
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
        if r.me.add_cost:
            notes.append(f"add charged {r.me.add_cost:.1f}")
        swap = f"{', '.join(p.name for p in r.give)} for {', '.join(p.name for p in r.get)}"
        print(f"{r.me.per_week:+6.1f} / {r.them.per_week:+6.1f}  {swap:55} "
              f"{trade._rounds(r.give_rounds)} / {trade._rounds(r.get_rounds)}  " + "; ".join(notes))


if __name__ == "__main__":
    main()
