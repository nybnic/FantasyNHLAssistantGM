"""Run-to-run memory, committed back to the repo by the workflow:
- telegram_offset: next Telegram update id to read
- briefings: per game date, what was recommended/sent (so nothing repeats)
- pending: recommendations awaiting a Done/Skip tap
- decisions: every tap, kept for the weekly report
- adds: every add I made ({id, name, date: NHL date, source}). The add budget
  counts these, however the bot learned of the add: a Done tap, my row in
  League > Transactions, or a new player in my team page or matchup (unless
  another team had him: likely a trade). One add learned twice counts once
- last_error: date of the last failure alert (one alert per day)
- relay_alert: date of the last "instant replies are down" alert
- weeks: per fantasy week, when its matchup plan was sent
- opponents: playoff opponents you've named with /opp (weeks 24-26)
- awaiting: the team whose Yahoo page you're about to paste after /opp or /myteam
- screenshots: rows read from recent team-page screenshots, collected until
  they make a whole roster ({team, rows, at})
- matchup_shots: rows and score read from recent matchup screenshots, collected
  until my side makes a whole roster ({rows, labels, score, projected, at})
- live_score: the latest matchup screenshot's score ({week, opponent, at,
  through: the day it was taken if before that day's first puck, score,
  projected, goalies: points from G slots per team})
- transaction_rows: transactions read from screenshots this run, not yet applied
- transactions_seen: each applied transaction's key -> its time, so overlapping
  screenshots apply nothing twice (the newest 300 kept)
- add_pools: per fantasy week, the candidate adds the plan weighed (full-week
  gain, later points, add id, drop id) with sigma and tau: the add price is
  solved over them (engine/addprice.py), the newest 12 weeks kept
- day_rosters: per NHL date, my active roster and my opponent's as they were
  before that day's first puck ({"me": [...], "them": {"team", "players"}}):
  the week's banked points are scored with each day's players (the last 14 days)
- ir_noted: the IR moves and returns the briefing has mentioned ("id:slot",
  "id:back"), so each is said once while it holds
- results: per fantasy week, the plans sent ("first", "last": {at, expected,
  so_far, sd per team, win}) and, once the week is over, the result ("final":
  {score, goalie_min, at, source}); what calibrating the model is checked on
- week_requested: /week was sent; plan the week on this run
- trade_request: the text after /trade, judged on this run
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

NHL_TIME = ZoneInfo("America/New_York")

STATE_FILE = Path("state/gm_state.json")
KEEP_DAYS = 14
KEEP_DECISIONS = 1000
KEEP_TRANSACTIONS = 300
KEEP_POOL_WEEKS = 12
# Judgment call: re-adding a player you dropped within a week is rare, while
# learning of one add twice (Done, then a screenshot) is common.
SAME_ADD_DAYS = 7


# Week 1 predates the results log: its plans as the dashboard's data.json had
# them in git history (84226cf, 801be98). Each team's sd is the margin's, backed
# out of P(win), split evenly (the file kept no variances).
WEEK_1 = {
    "opponent": "Bahelin Boys",
    "first": {"at": "2026-10-01T09:00+00:00", "expected": [152.9, 155.0], "so_far": [13.4, 51.5],
              "sd": [25.3, 25.3], "win": 0.476},
    "last": {"at": "2026-10-01T15:51+00:00", "expected": [162.88, 155.0], "so_far": [13.4, 51.5],
             "sd": [25.7, 25.7], "win": 0.586},
}


def load(path: Path = STATE_FILE) -> dict:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    state.setdefault("telegram_offset", 0)
    state.setdefault("briefings", {})
    state.setdefault("pending", {})
    state.setdefault("decisions", [])
    if "adds" not in state:  # before the ledger, Done taps were the only record
        state["adds"] = [
            {"id": None, "name": None, "source": "done",
             "date": dt.datetime.fromisoformat(d["at"]).astimezone(NHL_TIME).date().isoformat()}
            for d in state["decisions"] if d["type"] == "add" and d["decision"] == "done"
        ]
    state.setdefault("last_error", None)
    state.setdefault("relay_alert", None)
    state.setdefault("weeks", {})
    state.setdefault("opponents", {})
    state.setdefault("awaiting", None)
    state.setdefault("screenshots", None)
    state.setdefault("matchup_shots", None)
    state.setdefault("live_score", None)
    state.setdefault("transaction_rows", [])
    state.setdefault("transactions_seen", {})
    state.setdefault("add_pools", {})
    state.setdefault("day_rosters", {})
    state.setdefault("ir_noted", [])
    state.setdefault("results", {"1": WEEK_1})
    state.setdefault("week_requested", False)
    state.setdefault("trade_request", None)
    return state


def record_add(state: dict, player_id: int, name: str | None, date: dt.date, source: str,
               already_mine: bool = False) -> bool:
    """An add I made: `player_id` joined my roster on NHL date `date`. One add
    is often learned twice (a Done tap, then a screenshot of it, days apart),
    so an add of the same player within SAME_ADD_DAYS of a recorded one is
    that add. `already_mine`: he was on my roster before this news, so it may
    be an add from before the ledger (no player id), which it then names.
    Returns whether it was new."""
    def near(a: dict) -> bool:
        return abs((dt.date.fromisoformat(a["date"]) - date).days) <= SAME_ADD_DAYS

    if any(a["id"] == player_id and near(a) for a in state["adds"]):
        return False
    unnamed = next((a for a in state["adds"] if a["id"] is None and near(a)), None) if already_mine else None
    if unnamed:
        unnamed.update(id=player_id, name=name)
        return False
    state["adds"].append({"id": player_id, "name": name, "date": date.isoformat(), "source": source})
    return True


def save(state: dict, today: dt.date, path: Path = STATE_FILE) -> None:
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["briefings"] = {d: b for d, b in state["briefings"].items() if d >= cutoff}
    state["day_rosters"] = {d: r for d, r in state["day_rosters"].items() if d >= cutoff}
    state["pending"] = {k: v for k, v in state["pending"].items() if v["date"] >= cutoff}
    state["decisions"] = state["decisions"][-KEEP_DECISIONS:]
    seen = sorted(state["transactions_seen"].items(), key=lambda kv: kv[1])[-KEEP_TRANSACTIONS:]
    state["transactions_seen"] = dict(seen)
    state["add_pools"] = {w: p for w, p in state["add_pools"].items()
                          if int(w) > max(map(int, state["add_pools"]), default=0) - KEEP_POOL_WEEKS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
