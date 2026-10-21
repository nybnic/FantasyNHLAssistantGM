"""How the moves turned out (engine/scorecard.py): each move I made against the
team just before it, week by week while held, its first three weeks against the
plan's forecast; the season against keeping the drafted roster; then the add
suggestions one by one, the added player's points against the drop's over the
14 days after.

    python -m scripts.scorecard
    python -m scripts.scorecard --now 2026-11-02T12:00:00+02:00

Read-only. Uses the committed state/ files, so `git pull` first.
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot import common, weekly
from engine import scorecard
from league import roster as roster_mod
from league import teams, weeks
from model import context
from state import gm_state, repairs


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", help="ISO time with offset (default: now)")
    args = parser.parse_args()
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(common.NHL_TIME).date()
    state, players = gm_state.load(), roster_mod.load()
    repairs.apply(state, players, teams.load())  # in memory: one the bot hasn't run yet
    ctx = context.build(today)
    week = weeks.week_of(today)
    last = (week or weeks.LAST_WEEK + 1) - 1
    weekly.season_values(state, players, last, ctx)
    names = state.get("my_players", {})
    print(f"Each move against the team just before it (finished weeks while held; the first "
          f"{scorecard.CHECK_WEEKS} against the plan's forecast)")
    print(f"  {'move':52} {'from':6} {'pts by week':24} {'total':>6} {'wins':>6}  {'first 3':>14}  forecast")
    for m in weekly.made_moves(state, players):
        start = weeks.week_of(m.day)
        by_week = weekly.move_by_week(state, m, list(range(start, last + 1)), ctx) if start <= last else []
        if by_week is None:
            print(f"  {m.key}: a week without the daily roster record")
            continue
        c = scorecard.move_check(m, by_week, state["move_forecasts"].get(m.key), names)
        label = c["add"] + (f" for {c['drop']}" if c["drop"] else " (open spot)") + (f", kept {c['cut']}" if c["cut"]
                                                                                    else "")
        f = c.get("forecast")
        print(f"  {label[:52]:52} {m.day:%d %b} {' '.join(f'{w['pts']:+.0f}' for w in by_week)[:24]:24} "
              f"{sum(w['pts'] for w in by_week):+6.1f} {sum(w['wins'] or 0 for w in by_week):+6.2f}  "
              f"{c['pts']:+6.1f} {c['wins']:+6.2f}  "
              + (f"{f['pts']:+.1f} pts {f['wins']:+.2f} (value {f['value']:+.2f}, price {f['price']:.2f}, {f['date']})"
                 if f else "none saved"))
    print()
    print("The season against doing nothing (the drafted roster), by week")
    print(f"  {'week':4} {'yours':>7} {'drafted':>7} {'added':>6} {'margin':>7} {'wins':>6} {'forecast':>8}  result")
    season = []
    for w in range(1, last + 1):
        v = state["results"].get(str(w), {}).get("value")
        if not v:
            print(f"  {w:4} (no daily record of the roster, or no result yet)")
            continue
        season.append(v)
        print(f"  {w:4} {v['actual']:7.1f} {v['drafted']:7.1f} {v['added']:+6.1f} {v['margin']:+7.1f} "
              f"{v.get('wins', 0.0):+6.2f} {v['forecast'] if v.get('forecast') is not None else '':>8}  "
              f"{scorecard.outcome(v)}")
    print((scorecard.value_line(season[-1], season) or "") if season else "Nothing to score yet.")
    checked = weekly.check_moves(state, players, last, ctx)  # in memory, as Monday's result would
    print(scorecard.checks_line(list(state["moves_checked"].values())) or "No move has had its first weeks yet.")
    print()
    scored = scorecard.score(state["decisions"], ctx, today)
    waiting = sum(1 for d in state["decisions"] if d["type"] == "add" and d.get("add") is not None) - len(scored)
    print(f"{len(scored)} suggestions scored ({scorecard.WINDOW_DAYS} days after each, raw points); "
          f"{waiting} decisions still inside their window or repeats.")
    print(f"  {'date':10} {'decision':8} {'add':22} {'pts':>6}  {'drop':22} {'pts':>6}  {'gain':>6}")
    for s in scored:
        print(f"  {s.date.isoformat():10} {s.decision:8} {s.add:22} {s.add_points:6.1f}  {s.drop or 'open spot':22} "
              f"{s.drop_points:6.1f}  {s.gain:+6.1f}")
    print(scorecard.line(scored) or "Nothing scored yet.")


if __name__ == "__main__":
    main_()
