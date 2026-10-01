"""Run-to-run memory, committed back to the repo by the workflow:
- telegram_offset: next Telegram update id to read
- briefings: per game date, what was recommended/sent (so nothing repeats)
- pending: recommendations awaiting a Done/Skip tap
- decisions: every tap, kept for the weekly report
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
- week_requested: /week was sent; plan the week on this run
- trade_request: the text after /trade, judged on this run
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

STATE_FILE = Path("state/gm_state.json")
KEEP_DAYS = 14
KEEP_DECISIONS = 1000


def load(path: Path = STATE_FILE) -> dict:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    state.setdefault("telegram_offset", 0)
    state.setdefault("briefings", {})
    state.setdefault("pending", {})
    state.setdefault("decisions", [])
    state.setdefault("last_error", None)
    state.setdefault("relay_alert", None)
    state.setdefault("weeks", {})
    state.setdefault("opponents", {})
    state.setdefault("awaiting", None)
    state.setdefault("screenshots", None)
    state.setdefault("matchup_shots", None)
    state.setdefault("live_score", None)
    state.setdefault("week_requested", False)
    state.setdefault("trade_request", None)
    return state


def save(state: dict, today: dt.date, path: Path = STATE_FILE) -> None:
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["briefings"] = {d: b for d, b in state["briefings"].items() if d >= cutoff}
    state["pending"] = {k: v for k, v in state["pending"].items() if v["date"] >= cutoff}
    state["decisions"] = state["decisions"][-KEEP_DECISIONS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
