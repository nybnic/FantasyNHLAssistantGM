"""How the bot's add suggestions turned out, one by one: the added player's
points against the drop's over the 14 days after (engine/scorecard.py).

    python -m scripts.scorecard
    python -m scripts.scorecard --now 2026-11-02T12:00:00+02:00

Read-only. Uses the committed state/ files, so `git pull` first.
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot import common
from engine import scorecard
from model import context
from state import gm_state


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", help="ISO time with offset (default: now)")
    args = parser.parse_args()
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(common.NHL_TIME).date()
    state = gm_state.load()
    scored = scorecard.score(state["decisions"], context.build(today), today)
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
