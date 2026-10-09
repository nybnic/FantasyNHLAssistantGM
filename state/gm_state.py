"""Run-to-run memory, committed back to the repo by the workflow. One dict in
memory, two files on disk: ledger.json holds the season's record (LEDGER_KEYS:
adds, decisions, league moves, rosters by day, standings, scores, results,
forecasts, add candidates), gm_state.json the run's bookkeeping. The latest
plan's numbers are in board.json (bot/board.py). The keys:
- telegram_offset: next Telegram update id to read
- briefings: per game date, what was recommended/sent (so nothing repeats)
- pending: recommendations awaiting a Done/Skip tap
- decisions: every tap, kept for the weekly report; an add's also names its add
  and drop (engine/scorecard.py), and an add suggestion nobody tapped is
  logged as "none" when it expires
- adds: every add I made ({id, name, date: NHL date, source}). The add budget
  counts these, however the bot learned of the add: a Done tap, my row in
  League > Transactions, or a new player in my team page or matchup (unless
  another team had him: likely a trade). One add learned twice counts once
- last_error: date of the last failure alert (one alert per day)
- relay_alert: date of the last "instant replies are down" alert
- weeks: per fantasy week, when its matchup plan was sent
- opponents: playoff opponents you've named with /opp (weeks 24-26)
- awaiting: the team whose Yahoo page you're about to paste after /opp or /myteam
- awaiting_drop: the add recommendation tapped "Other drop", whose drop's name comes next
- screenshots: rows read from recent team-page screenshots, collected until
  they make a whole roster ({team, rows, at})
- matchup_shots: rows and score read from recent matchup screenshots, collected
  until my side makes a whole roster ({rows, labels, score, projected, at, first_at})
- live_score: the latest matchup screenshot's score ({week, opponent, at,
  through: the day it was taken if before that day's first puck, score,
  projected, goalies: points from G slots per team})
- transaction_rows: transactions read from screenshots this run, not yet applied
- transaction_rows_at: when the newest of those screenshots was sent (Helsinki time)
- transactions_seen: each applied transaction's key -> its time, so overlapping
  screenshots apply nothing twice; also the season's log of league moves (the newest 3000 kept)
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
  {score, goalie_min, at, source}) and, for plans built on a matchup screenshot,
  Yahoo's score next to the box scores' best-lineup one ("live": [{through,
  yahoo, box, yahoo_projected}]), and Yahoo's first forecast of the week from a
  matchup screenshot ("yahoo_first": {at, through, score, projected}), Yahoo's
  original projection from the website ("yahoo_orig": [mine, theirs]);
  what calibrating the model is checked on
- league_adds: each other team's adds (NHL dates), from Transactions screenshots:
  how much each streams (opponent profiles; logged, not yet used)
- dfo_archived: the last NHL date whose DFO line charts went to the private archive (bot/dfo_archive.py)
- xfp_logged: the last fantasy week whose projections bot/xfp_log.py appended to state/xfp_log.csv
- health_alerts: per data source, the day its trouble was last reported
- repairs_done: the one-time repairs already applied (state/repairs.py)
- seen_mine: player id -> the last NHL date he was on my roster (a player back
  on it within 30 days is a correction, not an add)
- news_checked: the NHL date the evening news check (an add newly worth it) last ran
- week_requested: /week was sent; plan the week on this run
- trade_request: the text after /trade, judged on this run
- standings: the latest League > Standings ({week: the last week they include,
  teams: {team: {w, l, t, pf}}, at}), where the season simulation starts (engine/season.py)
- standings_rows, scoreboard_shots, web_matchups: rows read from those screenshots this run, not yet saved
- league_weeks: per fantasy week, from All Matchups screenshots: the league's
  pairings (each a sorted pair of team names) and each team's latest score and
  Yahoo projection ({score, projected, date, first: the first ones seen, Yahoo's
  forecast when sent before Monday's games, orig_proj / live_proj: Yahoo's
  original and live projections from its website's matchup page}); and our projection of all 16 teams
  at the week's first plan ("ours": {at, moves_through, teams: {team: {expected,
  sd, so_far}}}), to check both forecasts against the results
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

NHL_TIME = ZoneInfo("America/New_York")

STATE_FILE = Path("state/gm_state.json")
# The ledger: what happened and what the bot forecast, the season's record
# (docs/plan-2026-10-09.md). In memory it's one dict with the rest; on disk
# these keys live in ledger.json next to the state file, which keeps the run's
# own bookkeeping (Telegram offset, pending cards, screenshot buffers, flags).
LEDGER_KEYS = ("adds", "decisions", "transactions_seen", "league_adds", "day_rosters", "standings",
               "live_score", "results", "league_weeks", "add_pools", "seen_mine", "opponents")
KEEP_DAYS = 14
KEEP_DECISIONS = 1000
KEEP_TRANSACTIONS = 3000  # a season of league moves (who, when) is the opponent and stash evidence
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


# Add suggestions tapped before decisions named their players, from the
# pending records in git history (2026-10-01).
PAST_ADD_PLAYERS = {
    "add-2026-09-29-0922-0": (8476892, "Colton Parayko", None, None),
    "add-2026-09-29-0246-0": (8476892, "Colton Parayko", None, None),
    "add-2026-10-01-0715-0": (8481668, "Arturs Silovs", 8483703, "Sergei Murashov"),
    "add-2026-10-01-0900-0": (8481668, "Arturs Silovs", 8483703, "Sergei Murashov"),
    "add-2026-10-01-1256-0": (8481617, "Vasily Podkolzin", 8475170, "Brayden Schenn"),
    "add-2026-10-01-1406-0": (8480855, "Jack McBain", 8475170, "Brayden Schenn"),
}


def add_players(rec: dict) -> dict:
    """The add and drop of an add recommendation, as a decision records them."""
    return {"add": rec["add"]["id"], "add_name": rec["add"].get("name"), "drop": rec.get("drop"),
            "drop_name": rec.get("drop_name")}


def ledger_path(path: Path) -> Path:
    return path.with_name("ledger.json")


def load(path: Path = STATE_FILE) -> dict:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    ledger = ledger_path(path)
    if ledger.exists():
        # A ledger key still in the state file was written there by a run from
        # before the split, after the ledger's last write: it is the newer.
        for key, value in json.loads(ledger.read_text(encoding="utf-8")).items():
            state.setdefault(key, value)
    state.setdefault("telegram_offset", 0)
    state.setdefault("briefings", {})
    state.setdefault("pending", {})
    state.setdefault("decisions", [])
    for d in state["decisions"]:
        if d.get("rec_id") in PAST_ADD_PLAYERS and "add" not in d:
            d.update(zip(("add", "add_name", "drop", "drop_name"), PAST_ADD_PLAYERS[d["rec_id"]]))
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
    state.setdefault("awaiting_drop", None)
    state.setdefault("screenshots", None)
    state.setdefault("matchup_shots", None)
    state.setdefault("live_score", None)
    state.setdefault("transaction_rows", [])
    state.setdefault("transaction_rows_at", None)
    state.setdefault("transactions_seen", {})
    state.setdefault("add_pools", {})
    state.setdefault("day_rosters", {})
    state.setdefault("ir_noted", [])
    state.setdefault("health_alerts", {})
    state.setdefault("seen_mine", {})
    state.setdefault("league_adds", {})
    state.setdefault("xfp_logged", None)  # the last week bot/xfp_log.py logged
    state.setdefault("dfo_archived", None)  # the last NHL date bot/dfo_archive.py saved
    state.setdefault("news_checked", None)
    state.setdefault("results", {"1": WEEK_1})
    state.setdefault("week_requested", False)
    state.setdefault("trade_request", None)
    state.setdefault("standings", None)
    state.setdefault("standings_rows", [])
    state.setdefault("scoreboard_shots", [])
    state.setdefault("league_weeks", {})
    state.setdefault("web_matchups", [])
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
    for rec_id, rec in state["pending"].items():
        if rec["date"] < cutoff and rec["type"] == "add":  # never tapped: the scorecard still judges it
            state["decisions"].append({"rec_id": rec_id, "type": "add", "date": rec["date"], "decision": "none",
                                       "at": None, **add_players(rec)})
    state["pending"] = {k: v for k, v in state["pending"].items() if v["date"] >= cutoff}
    state["decisions"] = state["decisions"][-KEEP_DECISIONS:]
    seen = sorted(state["transactions_seen"].items(), key=lambda kv: kv[1])[-KEEP_TRANSACTIONS:]
    state["transactions_seen"] = dict(seen)
    state["add_pools"] = {w: p for w, p in state["add_pools"].items()
                          if int(w) > max(map(int, state["add_pools"]), default=0) - KEEP_POOL_WEEKS}
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = {k: v for k, v in state.items() if k in LEDGER_KEYS}
    run = {k: v for k, v in state.items() if k not in LEDGER_KEYS}
    ledger_path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    path.write_text(json.dumps(run, indent=2, sort_keys=True), encoding="utf-8")
