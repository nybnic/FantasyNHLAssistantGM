"""Assistant GM entrypoint, run every 30 minutes by GitHub Actions, and right
after each Telegram message when the webhook relay (relay/) is set up.

Each run:
1. reads your Telegram taps and commands - Done/Skip on recommendations,
   /roster, /myteam (paste your Yahoo team page), /week, /opp (paste a team's
   Yahoo page), /taken, /trade, /help;
2. from noon on the first day of each fantasy week, again from Wednesday
   noon, and on /week, sends the matchup plan: expected score and win odds
   vs this week's opponent, the goalie minimum, and the add/drops worth
   making (a plan that fails stays due for the next run);
3. once tonight's briefing is due, plans tonight's lineup and sends it if a
   change is worth >= 0.5 expected points (or the full lineup until one has
   been confirmed). Later runs send at most one update, if new information
   (goalie confirmations, injuries) makes a clearly better lineup;
4. on a failure, still runs the other steps, and alerts you once a day.

Notify-only: it never touches Yahoo.

    python main.py                        # normal run (needs TELEGRAM_* env vars)
    python main.py --dry-run --force      # print tonight's plan now, send nothing
    python main.py --dry-run --force --now 2026-01-15T20:00:00+02:00   # replay a past night
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import math
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from clients import dfo_lines, goalie_client, health, nhl_client, screenshot
from clients.names import normalize_name
from config.league import (MAX_ADDS_PER_SEASON, MIN_GOALIE_GAMES_PER_WEEK, MY_TEAM, POST_DRAFT_WAIVERS_CLEAR,
                           SCHEDULE, SEASON_END, SEASON_START, TRADE_DEADLINE)
from config.settings import Settings, load_settings
from engine import addprice, briefing, ir, matchup, report, scorecard, trade
from league import draft, parse, positions, teams, weeks
from league import roster as roster_mod
from model import context
from notify import charts, telegram
from state import gm_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NHL_TIME = ZoneInfo("America/New_York")
HELP = (
    "Assistant GM commands:\n"
    "/roster - the roster I think you have\n"
    "/myteam - then send screenshots of your Yahoo team page or paste its text, to correct your roster\n"
    "/week - this week's matchup: expected score, win odds, adds worth making\n"
    "Matchup screenshots (the Yahoo app's Matchup tab, scrolled through) - both rosters and the live score, "
    "then an updated plan\n"
    "League > Transactions screenshots - every team's adds, drops and trades, so suggested free agents are free\n"
    "/opp - then paste your opponent's Yahoo team page, to update their roster "
    "(/opp Team Name for another team or a playoff opponent)\n"
    "/taken Name - a free agent I suggested is on someone's roster\n"
    "/trade - trades worth proposing. /trade Knight for Bouchard - what one trade does to you and to them "
    "(several players: Knight, Tuch for Makar)\n"
    "Tap Done on a recommendation once you've made it in Yahoo (on an add: Other drop if you dropped "
    "someone else), or Skip."
)
MYTEAM_HINT = "\n\nWrong? Send screenshots of your Yahoo team page, or /myteam and paste its text."
# Judgment call: screenshots sent within this of the previous one are one team page.
SCREENSHOT_WINDOW = dt.timedelta(minutes=5)
CHART_DIR = Path("data/charts")  # where a dry run saves the charts it would send
SITE_DIR = Path("site")  # the dashboard: index.html (ours) and data.json (written by each weekly plan)
CHART_WEEKS = 5  # an add's chart shows this many weeks after the current one
MIN_KNOWN_ROSTER = 10  # a league team's roster counts toward the matchup spread from this many players
WEEKLY_PLAN_TIME = dt.time(12, 0)  # local, on the week's first day: before any NHL game
# Judgment call: more new players than two weeks of adds in one roster update is
# a stale roster or a misread page, not adds (League > Transactions counts those).
MAX_NEW_AS_ADDS = 4


class Outbox:
    """Sends to Telegram, or prints in a dry run."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            print(f"\n----- Telegram message{' [' + ' | '.join(b[0] for b in buttons) + ']' if buttons else ''}\n{text}")
            return None
        return telegram.send_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text, buttons)

    def send_photo(self, png: bytes, caption: str | None = None,
                   buttons: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            CHART_DIR.mkdir(parents=True, exist_ok=True)
            path = CHART_DIR / f"{len(list(CHART_DIR.glob('*.png'))):02d}.png"
            path.write_bytes(png)
            label = f" [{' | '.join(b[0] for b in buttons)}]" if buttons else ""
            print(f"\n----- Telegram photo{label}: {path}" + (f"\n{caption}" if caption else ""))
            return None
        return telegram.send_photo(self.settings.telegram_bot_token, self.settings.telegram_chat_id, png,
                                   caption, buttons)


def sync_webhook(settings: Settings, now: dt.datetime) -> str | None:
    """Point Telegram's webhook at the relay when one is configured, else remove
    it so getUpdates works. Returns a problem if Telegram can't deliver to it."""
    token = settings.telegram_bot_token
    want = f"{settings.relay_url}/telegram" if settings.relay_url else ""
    info = telegram.webhook_info(token)
    if info.get("url", "") != want:
        if want:
            telegram.set_webhook(token, want, settings.webhook_secret)
        else:
            telegram.delete_webhook(token)
        logger.info("Telegram webhook %s", "pointed at the relay" if want else "removed; polling instead")
        return None
    recent_error = now.timestamp() - info.get("last_error_date", 0) < 3600
    if want and info.get("pending_update_count") and recent_error:
        return (f"Telegram can't reach the relay ({info.get('last_error_message')}); "
                "your messages will arrive once it's back.")
    return None


def report_relay(problem: str | None, state: dict, outbox: Outbox, now: dt.datetime) -> None:
    """Warn, once a day, when instant replies aren't working."""
    if not problem:
        return
    logger.warning(problem)
    today = now.date().isoformat()
    if state["relay_alert"] != today:
        state["relay_alert"] = today
        outbox.send(f"Instant replies are down: {problem}")


def process_updates(settings: Settings, state: dict, players: list, league: dict, outbox: Outbox) -> str | None:
    """Handle new messages and taps. Returns the relay's problem starting runs, if any."""
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    problem = None
    if settings.relay_url:
        updates, dispatch_error = telegram.get_relayed_updates(
            settings.relay_url, settings.relay_token, state["telegram_offset"])
        if dispatch_error:
            problem = (f"the relay can't start runs ({dispatch_error}), so replies wait for the "
                       "half-hourly runs. A 401 means its GitHub token needs renewing (see README).")
    else:
        updates = telegram.get_updates(token, state["telegram_offset"])
    new_screenshots = set()
    for update in updates:
        state["telegram_offset"] = update["update_id"] + 1
        if "callback_query" in update:
            query = update["callback_query"]
            if str(query["message"]["chat"]["id"]) != chat_id:
                continue
            action, _, rec_id = query.get("data", "").partition(":")
            rec = state["pending"].pop(rec_id, None)
            if rec is None:
                telegram.answer_callback(token, query["id"], "Already recorded")
                continue
            if action == "done" and rec["type"] == "lineup":
                roster_mod.apply_lineup(players, {int(pid): slot for pid, slot in rec["assignment"].items()})
            if action == "other" and rec["type"] == "add":
                # Made, with a drop of Nico's own: added now, the drop named in his next message.
                rec = {**rec, "drop": None, "drop_name": None}
                state["awaiting_drop"] = rec_id
                outbox.send(f"Who did you drop for {rec['add'].get('name')}? Send the name.")
            if action in ("done", "other") and rec["type"] == "add":
                apply_add(players, rec)
                gm_state.record_add(state, rec["add"]["id"], rec["add"].get("name"), _nhl_today(), "done")
                if rec["drop"] is not None:
                    teams.put_on_waivers(league, rec["drop"], _nhl_today())
            if action == "taken" and rec["type"] == "add":
                teams.mark_taken(league, [rec["add"]["id"]])
                state["week_requested"] = True  # the next best add, right away
            state["decisions"].append({
                "rec_id": rec_id, "type": rec["type"], "date": rec["date"],
                "decision": "done" if action == "other" else action,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                **(gm_state.add_players(rec) if rec["type"] == "add" else {}),
            })
            label = {"done": "Recorded: Done", "other": "Recorded: Done, which drop?",
                     "taken": "Taken: finding the next best"}.get(action, "Recorded: Skipped")
            telegram.mark_handled(token, chat_id, query["message"]["message_id"], label)
            telegram.answer_callback(token, query["id"], label)
        elif "message" in update:
            message = update["message"]
            if str(message["chat"]["id"]) != chat_id:
                _warn_other_chat(str(message["chat"]["id"]), chat_id, token)
                continue
            if _image(message):
                if kind := add_screenshot(message, settings, state, outbox):
                    new_screenshots.add(kind)
                continue
            text = message.get("text", "").strip()
            first_line, _, body = text.partition("\n")
            command, _, rest = first_line.partition(" ")
            command = command.lower().split("@")[0]
            if command == "/roster":
                outbox.send((roster_mod.describe(players) or "No roster yet.") + MYTEAM_HINT)
            elif command == "/myteam":
                myteam_command((rest + "\n" + body).strip(), state, players, league, outbox)
            elif command in ("/help", "/start"):
                outbox.send(HELP)
            elif command == "/week":
                state["week_requested"] = True
            elif command == "/opp":
                opp_command(rest.strip(), body, state, league, outbox)
            elif command == "/trade":
                state["trade_request"] = (rest + "\n" + body).strip()
            elif command == "/taken":
                taken_command(rest + "\n" + body, league, outbox)
            elif command == "/save":
                if state["matchup_shots"]:
                    finish_matchup(state, players, league, outbox, save=True)
                elif state["screenshots"]:
                    finish_screenshots(state, players, league, outbox, save=True)
                else:
                    outbox.send("No screenshots to save.")
            elif state["awaiting_drop"] and not text.startswith("/"):
                rec_id, state["awaiting_drop"] = state["awaiting_drop"], None
                other_drop(rec_id, text, state, players, league, outbox)
            elif state["awaiting"] and not text.startswith("/"):
                team, state["awaiting"] = state["awaiting"], None
                if team == MY_TEAM:
                    update_my_roster(text, state, players, league, outbox)
                else:
                    update_team(team, text, league, outbox)
    if "team" in new_screenshots:
        finish_screenshots(state, players, league, outbox)
    if "matchup" in new_screenshots:
        finish_matchup(state, players, league, outbox)
    if "transactions" in new_screenshots:
        finish_transactions(state, players, league, outbox)
    return problem


def other_drop(rec_id: str, text: str, state: dict, players: list, league: dict, outbox: Outbox) -> None:
    """The drop Nico made for an add tapped "Other drop": off my roster, onto
    waivers, and into the add's decision (the scorecard compares against him)."""
    found = parse.find_players(text, parse.registry())
    mine = {p.id: p for p in players}
    dropped = next((p for p in found.players if p.id in mine), None)
    if not dropped:
        outbox.send(f"I couldn't find {text.strip()!r} on your roster. Send /myteam with your team page "
                    "to correct it.")
        return
    players[:] = [p for p in players if p.id != dropped.id]
    teams.put_on_waivers(league, dropped.id, _nhl_today())
    for d in reversed(state["decisions"]):
        if d["rec_id"] == rec_id:
            d.update(drop=dropped.id, drop_name=mine[dropped.id].name)
            break
    outbox.send(f"Noted: you dropped {mine[dropped.id].name}.")


def _image(message: dict) -> str | None:
    """File id of a photo, or of an image sent as a file."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"]  # the largest size
    doc = message.get("document") or {}
    return doc["file_id"] if doc.get("mime_type", "").startswith("image/") else None


def _fresh(draft: dict | None, at: dt.datetime) -> bool:
    return bool(draft) and at - dt.datetime.fromisoformat(draft["at"]) < SCREENSHOT_WINDOW


def add_screenshot(message: dict, settings: Settings, state: dict, outbox: Outbox) -> str | None:
    """Read a screenshot into a draft: returns "team" (a team page) or
    "matchup", or None if it couldn't be read. Screenshots within
    SCREENSHOT_WINDOW of each other add up. A team page belongs to the team
    named by /opp or /myteam, else the draft's, else mine."""
    at = dt.datetime.fromtimestamp(message["date"], dt.timezone.utc)
    try:
        shot = screenshot.read(telegram.download_file(settings.telegram_bot_token, _image(message)))
    except (screenshot.ScreenshotError, telegram.TelegramError) as e:
        outbox.send(f"Couldn't read that screenshot: {e}")
        return None
    except requests.RequestException:  # its text can hold the download URL, which holds the token
        outbox.send("Couldn't download that screenshot from Telegram; try again.")
        return None
    if shot["kind"] == "transactions":
        if not shot["rows"]:
            outbox.send("I couldn't read any transactions in that screenshot.")
            return None
        state["transaction_rows"] += shot["rows"]
        return "transactions"
    if shot["kind"] == "matchup":
        draft = state["matchup_shots"] if _fresh(state["matchup_shots"], at) else {"rows": [], "labels": []}
        draft["rows"] += shot["rows"]
        draft["labels"] += shot["labels"][1]  # the opponent's side of the header
        draft["score"] = shot["score"] or draft.get("score")
        draft["projected"] = shot["projected"] or draft.get("projected")
        draft["at"] = at.isoformat()
        state["matchup_shots"] = draft
        return "matchup"
    draft = state["screenshots"]
    fresh = _fresh(draft, at)
    team = state["awaiting"] or (draft["team"] if fresh else MY_TEAM)
    state["awaiting"] = None
    if not fresh or draft["team"] != team:
        draft = {"team": team, "rows": []}
    if not shot["rows"]:
        outbox.send("I couldn't find any players in that screenshot. Send the Team or Matchup tab of the Yahoo app.")
        return None
    draft["rows"] += shot["rows"]
    draft["at"] = at.isoformat()
    state["screenshots"] = draft
    return "team"


def _tx_time(when: list) -> str:
    """ "2026-09-30T14:04" from (month, day, hour, minute), as shown on the phone."""
    month, day, hour, minute = when
    year = SEASON_START.year if month >= 7 else SEASON_START.year + 1
    return f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"


def _tx_nhl_date(when: str) -> dt.date:
    """The NHL date of a transaction time as the phone shows it (Helsinki)."""
    return dt.datetime.fromisoformat(when).replace(tzinfo=briefing.LOCAL).astimezone(NHL_TIME).date()


def _tx_key(row: dict) -> str:
    names = ",".join(normalize_name(p["name"]).replace(" ", "") for p in row["players"])
    teams_ = ",".join(normalize_name(t).replace(" ", "") for t in row["teams"])
    return f"{_tx_time(row['when'])}|{row['type']}|{teams_}|{names}"


def finish_transactions(state: dict, players: list, league: dict, outbox: Outbox) -> None:
    """Apply League > Transactions rows not seen before, oldest first: adds
    and claims put a player on that team (off the free agents), drops free him,
    trades swap rosters; my own team's moves update my roster too. Says what
    changed, and warns when the screenshots may not reach back to the last ones seen."""
    rows, state["transaction_rows"] = state["transaction_rows"], []
    seen = state["transactions_seen"]
    names = {normalize_name(t).replace(" ", ""): t for t in (*SCHEDULE, *league["teams"], MY_TEAM)}
    had_seen = bool(seen)
    overlap = any(_tx_key(row) in seen for row in rows)
    fresh, keys = [], set()
    for i, row in enumerate(rows):
        key = _tx_key(row)
        if key not in seen and key not in keys:
            keys.add(key)
            fresh.append((_tx_time(row["when"]), -i, key, row))  # same minute: the screen lists newest first
    registry = parse.registry()
    mine_ids = {p.id for p in players}
    rostered = teams.rostered_ids(league) | mine_ids
    changes: dict[str, list[str]] = {}
    problems = []

    def find(shown: dict, prefer: set[int]):
        found = parse.match_shown_names([{**shown, "team": "", "slot": None}], registry, prefer)
        if not found.players:
            problems.append(found.problems[0] if found.problems else shown["name"])
            return None
        learn_positions(found.players, found.tagged)
        return found.players[0]

    def on_team(team: str) -> set[int]:
        return mine_ids if team == MY_TEAM else {p.id for p in teams.players(league, team)}

    def add(team: str, p, when: str | None = None) -> None:
        """`when` (the row's time) for an add or claim; None for a trade, which costs no add."""
        teams.add_player(league, team, p)  # off every other roster; mine isn't in league.json
        if team == MY_TEAM:
            league["taken"] = [pid for pid in league["taken"] if pid != p.id]
            already_mine = any(q.id == p.id for q in players)
            if not already_mine:
                p.slot = roster_mod.BENCH if roster_mod.lineup_known(players) else None
                players.append(p)
            if when:
                gm_state.record_add(state, p.id, p.name, _tx_nhl_date(when), "transactions", already_mine)
        elif when:  # how much each team streams (opponent profiles, logged before they're used)
            state["league_adds"].setdefault(team, []).append(_tx_nhl_date(when).isoformat())
        changes.setdefault(team, []).append(f"+{p.name}")

    def drop(team: str, p, when: str | None = None) -> None:
        """`when` (the row's time) for a drop, which puts him on waivers; None for a trade."""
        if team == MY_TEAM:
            players[:] = [q for q in players if q.id != p.id]
        teams.remove_player(league, team, p.id)
        if when:
            teams.put_on_waivers(league, p.id, _tx_nhl_date(when))
        changes.setdefault(team, []).append(f"-{p.name}")

    for when, _, key, row in sorted(fresh):
        teams_ = [names.get(normalize_name(t).replace(" ", "")) for t in row["teams"]]
        if None in teams_:
            problems.append(f"team {row['teams'][teams_.index(None)]!r}")
            continue
        for shown in row["players"]:
            action = shown["action"]
            if action == "add":
                p = find(shown, {q["id"] for q in registry} - rostered)
                if p:
                    add(teams_[0], p, when)
            elif action == "drop":
                p = find(shown, on_team(teams_[0]))
                if p:
                    drop(teams_[0], p, when)
            else:  # traded away by teams_[side] to the other
                side = int(action.split(":")[1])
                p = find(shown, on_team(teams_[side]))
                if p:
                    drop(teams_[side], p)
                    add(teams_[1 - side], p)
        seen[key] = when

    if not fresh:
        outbox.send("Those transactions are all ones I already have.")
        return
    times = sorted(f[0] for f in fresh)
    lines = [f"Transactions: {len(fresh)} new ({_tx_label(times[0])} - {_tx_label(times[-1])})."]
    lines += [f"{team}: {', '.join(moves)}" for team, moves in changes.items()]
    pending = [rec["add"]["name"] for rec in state["pending"].values()
               if rec["type"] == "add" and rec["add"]["id"] in teams.rostered_ids(league)]
    if pending:
        lines.append(f"Taken since I suggested them: {', '.join(pending)}. Send /week for the next best.")
    if had_seen and not overlap:
        lines.append("These don't reach back to the last transactions I saw, so moves in between may be missing: "
                     "scroll down and send the older ones too.")
    if problems:
        lines.append("Couldn't place: " + "; ".join(problems))
    outbox.send("\n".join(lines))


def _tx_label(when: str) -> str:
    return dt.datetime.fromisoformat(when).strftime("%a %d %b %H:%M")


def _side(rows: list[dict], side: str) -> list[dict]:
    """One team's players from matchup rows, each once (screenshots overlap)."""
    seen, found = set(), []
    for row in rows:
        p = row[side]
        if p and p["name"] not in seen:
            seen.add(p["name"])
            found.append({**p, "slot": row["slot"]})
    return found


def _goalie_points(rows: list[dict]) -> float | None:
    """Points from the G slots, or None if no goalie row was read."""
    goalies = [r["points"] or 0.0 for r in rows if r["slot"] == "G"]
    return sum(goalies) if goalies else None


def finish_matchup(state: dict, players: list, league: dict, outbox: Outbox, save: bool = False) -> None:
    """Save what matchup screenshots show: the live score at once, and both
    rosters once my side looks whole (at most one player short) or on /save.
    Then plan the week again on this run."""
    draft = state["matchup_shots"]
    at = dt.datetime.fromisoformat(draft["at"])
    date = at.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    names = {normalize_name(t): t for t in (*SCHEDULE, *league["teams"])}
    named = [names[n] for n in map(normalize_name, draft["labels"]) if n in names and names[n] != MY_TEAM]
    opponent = named[0] if named else current_opponent(state, week) if week else None
    mine, theirs = _side(draft["rows"], "mine"), _side(draft["rows"], "theirs")
    lines = []
    if draft.get("score") and week:
        games = _safe(nhl_client.games_on, date, default=[])
        state["live_score"] = {
            "week": week, "opponent": opponent, "at": draft["at"],
            # Before the day's first puck the score covers exactly the days before it.
            "through": date.isoformat() if not games or at < games[0].start else None,
            "score": list(draft["score"]), "projected": list(draft["projected"] or []),
            "goalies": [_goalie_points(mine), _goalie_points(theirs)],
        }
        line = f"Week {week} vs {opponent or '?'}: {draft['score'][0]:.2f} - {draft['score'][1]:.2f}"
        if draft.get("projected"):
            line += f" (Yahoo projects {draft['projected'][0]:.0f} - {draft['projected'][1]:.0f})"
        lines.append(line)

    registry = parse.registry()
    mine_ids = {p.id for p in players}
    found = parse.match_shown_names(mine, registry, mine_ids)
    if players and found.players and sum(p.id in mine_ids for p in found.players) < len(found.players) / 2:
        state["matchup_shots"] = None
        outbox.send("\n".join(lines + ["The left team doesn't look like yours, so no rosters were saved. "
                                        "Send your own matchup (your team is on the left)."]))
        return
    if not save and len(found.players) < max(roster_mod.MIN_PASTED, len(players) - 1):
        outbox.send("\n".join(lines + [f"Read {len(found.players)} of your players so far. Send the rest of "
                                        "the matchup screenshots, or /save to save just these."]))
        return
    state["matchup_shots"] = None
    learn_positions(found.players, found.tagged)
    before = {p.id for p in players}
    changes = roster_mod.replace(players, found.players, found.tagged)
    lines.append("Your roster: " + ("; ".join(changes) if changes else "same as I had."))
    lines += count_new_players(state, league, before, players, date)
    if opponent and theirs:
        before = {p.id: p.name for p in teams.players(league, opponent)}
        found_them = parse.match_shown_names(theirs, registry, set(before))
        learn_positions(found_them.players, found_them.tagged)
        for p in found_them.players:
            p.slot = None  # other teams are assumed to set their best lineup
        teams.set_team(league, opponent, found_them.players, date)
        after = {p.id for p in found_them.players}
        new = [p.name for p in found_them.players if p.id not in before]
        gone = [name for pid, name in before.items() if pid not in after]
        changed = (f"new: {', '.join(new)}" if new else "") + ("; " if new and gone else "") + (
            f"gone: {', '.join(gone)}" if gone else "")
        lines.append(f"{opponent}: " + (changed if before and changed else f"{len(after)} players saved") + ".")
        found.problems += found_them.problems
    if found.problems:
        lines.append("Couldn't place: " + "; ".join(found.problems))
    state["week_requested"] = True
    outbox.send("\n".join(lines + ["Updated plan below."]))


def finish_screenshots(state: dict, players: list, league: dict, outbox: Outbox, save: bool = False) -> None:
    """Save the screenshot draft once it looks like the whole roster (at most
    one player short of what I had), or on /save."""
    team = state["screenshots"]["team"]
    current = players if team == MY_TEAM else teams.players(league, team)
    found = parse.match_shown_names(state["screenshots"]["rows"], parse.registry(), {p.id for p in current})
    n = len(found.players)
    if n > roster_mod.MAX_PLAYERS:
        state["screenshots"] = None
        outbox.send(f"Those screenshots show {n} players, more than a roster holds; nothing saved. Send them again.")
        return
    if not save and n < max(roster_mod.MIN_PASTED, len(current) - 1):
        problems = "\nCouldn't place: " + "; ".join(found.problems) if found.problems else ""
        outbox.send(f"{team}: read {n} players so far ({', '.join(p.name for p in found.players)}). "
                    f"Send the rest of the screenshots, or /save to save just these.{problems}")
        return
    if team == MY_TEAM:
        apply_my_roster(found, state, players, league, outbox)
    else:
        save_team(team, found, league, outbox)


def apply_add(players: list, rec: dict) -> None:
    """Done on an add/drop: your roster changes the same way, including the
    IR move that opened its spot (rec["ir"]: player id -> IR slot)."""
    known = roster_mod.lineup_known(players)
    roster_mod.apply_lineup(players, {int(pid): slot for pid, slot in rec.get("ir", {}).items()})
    if rec["drop"] is not None:
        players[:] = [p for p in players if p.id != rec["drop"]]
    if all(p.id != rec["add"]["id"] for p in players):
        players.append(roster_mod.RosterPlayer(**{**rec["add"], "slot": roster_mod.BENCH if known else None}))


def _nhl_today() -> dt.date:
    return dt.datetime.now(NHL_TIME).date()


def current_opponent(state: dict, week: int) -> str | None:
    return weeks.opponent(week) or state["opponents"].get(str(week))


def opp_command(team_arg: str, paste: str, state: dict, league: dict, outbox: Outbox) -> None:
    """/opp [Team Name], with a team's Yahoo page pasted below it or in the
    next message. Without a name it's this week's opponent; in the playoffs
    the name also records who you're playing."""
    today = _nhl_today()
    week = weeks.week_of(today) or (1 if today < weeks.week_span(1)[0] else None)
    names = {normalize_name(t): t for t in (*SCHEDULE, *league["teams"])}
    if team_arg:
        team = names.get(normalize_name(team_arg))
        if not team:
            outbox.send(f"No team called {team_arg!r}. Teams: {', '.join(sorted(names.values()))}")
            return
        if week and not weeks.opponent(week):
            state["opponents"][str(week)] = team
            state["week_requested"] = True  # the playoff week's plan, now its opponent is known
    else:
        team = current_opponent(state, week) if week else None
        if not team:
            outbox.send("Which team? Send /opp Team Name, with their Yahoo team page pasted below it.")
            return
    if paste.strip():
        update_team(team, paste, league, outbox)
    else:
        state["awaiting"] = team
        outbox.send(f"OK - now send screenshots of {team}'s Yahoo team page, or paste its text.")


def myteam_command(paste: str, state: dict, players: list, league: dict, outbox: Outbox) -> None:
    """/myteam, with your Yahoo team page pasted below it or in the next message."""
    if paste:
        update_my_roster(paste, state, players, league, outbox)
    else:
        state["awaiting"] = MY_TEAM
        outbox.send("OK - now send screenshots of your Yahoo team page, or paste its text "
                    "(or one player per line with the slot first: \"BN Nathan MacKinnon\").")


def update_my_roster(paste: str, state: dict, players: list, league: dict, outbox: Outbox) -> None:
    found = parse.find_players(paste, parse.registry())
    n = len(found.players)
    if not roster_mod.MIN_PASTED <= n <= roster_mod.MAX_PLAYERS:
        problems = "\nCouldn't place: " + "; ".join(found.problems) if found.problems else ""
        outbox.send(f"I found {n} players, but a roster has {roster_mod.MIN_PASTED}-{roster_mod.MAX_PLAYERS}. "
                    f"Your roster is unchanged: paste the whole team page.{problems}")
        return
    apply_my_roster(found, state, players, league, outbox)


def apply_my_roster(found: parse.Found, state: dict, players: list, league: dict, outbox: Outbox) -> None:
    learn_positions(found.players, found.tagged)
    before = {p.id for p in players}
    changes = roster_mod.replace(players, found.players, found.tagged)
    lines = [f"Roster saved: {len(found.players)} players."] + (changes or ["Same as I had."])
    lines += count_new_players(state, league, before, players, _nhl_today())
    if found.problems:
        lines.append("Couldn't place: " + "; ".join(found.problems))
    outbox.send("\n".join(lines) + "\n\n" + roster_mod.describe(players))


def count_new_players(state: dict, league: dict, before: set[int], players: list, date: dt.date) -> list[str]:
    """Players new on my roster since `before` (ids) were added, so they count
    against the add budget, unless another team had them: then it was likely
    a trade, which costs no add. Either way they're off that team now.
    Returns what to tell Nico."""
    if not before:
        return []  # the first roster I see: nothing was added
    new = [p for p in players if p.id not in before]
    if len(new) > MAX_NEW_AS_ADDS:
        return [f"{len(new)} players are new to me, too many to be adds, so none were counted. To count "
                "the adds among them, send your League > Transactions screenshots."]
    owner = {p.id: t for t in league["teams"] for p in teams.players(league, t)}
    counted, traded = [], []
    for p in new:
        if p.id in owner:
            teams.remove_player(league, owner[p.id], p.id)
            traded.append(f"{p.name} ({owner[p.id]})")
        elif gm_state.record_add(state, p.id, p.name, date, "roster"):
            counted.append(p.name)
    lines = []
    if counted:
        lines.append(f"Counted as adds: {', '.join(counted)} ({MAX_ADDS_PER_SEASON - len(state['adds'])} left).")
    if traded:
        lines.append(f"Not counted as adds, since another team had them (a trade?): {', '.join(traded)}. "
                     "If one was an add, send your League > Transactions screenshots.")
    return lines


def update_team(team: str, paste: str, league: dict, outbox: Outbox) -> None:
    found = parse.find_players(paste, parse.registry())
    if not found.players:
        outbox.send(f"I couldn't find any players in that. {team}'s roster is unchanged.")
        return
    save_team(team, found, league, outbox)


def save_team(team: str, found: parse.Found, league: dict, outbox: Outbox) -> None:
    learn_positions(found.players, found.tagged)
    for p in found.players:
        p.slot = None  # other teams are assumed to set their best lineup
    teams.set_team(league, team, found.players, _nhl_today())
    lines = [f"{team}: {len(found.players)} players saved."]
    lines += [f"  {p.name} ({p.team}, {'/'.join(p.positions)})" for p in found.players]
    if found.problems:
        lines.append("Couldn't place: " + "; ".join(found.problems))
    lines.append("Send /week for the updated forecast.")
    outbox.send("\n".join(lines))


def taken_command(text: str, league: dict, outbox: Outbox) -> None:
    found = parse.find_players(text.replace(",", "\n"), parse.registry())
    if not found.players:
        outbox.send("Who? Send /taken followed by the player's name.")
        return
    teams.mark_taken(league, [p.id for p in found.players])
    outbox.send("Noted, not a free agent: " + ", ".join(p.name for p in found.players))


def _warn_other_chat(sender: str, chat_id: str, token: str) -> None:
    """Say why a message went unanswered. Actions logs are public, so only the
    last digits of each ID are shown."""
    hint = " - that's the bot's own ID, not yours" if chat_id == token.split(":")[0] else ""
    logger.warning("Ignored a message from chat ...%s: TELEGRAM_CHAT_ID is ...%s%s",
                   sender[-3:], chat_id[-3:], hint)


def sync_teams(players: list) -> None:
    """Follow trades: take each player's current NHL team."""
    team_of = {p["id"]: p["team"] for p in nhl_client.current_rosters()}
    for p in players:
        p.team = team_of.get(p.id, p.team)


def _safe(fetch, *args, default=None):
    try:
        return fetch(*args)
    except Exception as exc:
        name = getattr(fetch, "__name__", str(fetch))
        logger.warning("%s failed; continuing without it", name, exc_info=True)
        health.report(name, f"failed ({type(exc).__name__})")
        return default


def briefing_step(state: dict, players: list, now: dt.datetime, force: bool, outbox: Outbox,
                  build_context=context.build) -> None:
    date = now.astimezone(NHL_TIME).date()
    games = nhl_client.games_on(date)
    if not games or not roster_mod.active(players):
        return
    key = date.isoformat()
    record = state["briefings"].get(key)
    if not force:
        if now < briefing.briefing_due(date, games[0].start) or briefing.quiet(now):
            return
        if now >= games[-1].start or (record and record["updates"] >= 1):
            return
        if record is None and briefing.briefing_closed(now, date):
            logger.info("Briefing window for %s closed before any run reached it", key)
            return

    ctx = build_context(date)
    team_lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in {p.team for p in players}}
    starters = _safe(goalie_client.get_starters, date, default={})
    result = briefing.plan(players, ctx, date, games, team_lines, starters, now)
    ir_note = _new_ir_note(state, players, ctx, team_lines)

    if record is None:
        message_text = briefing.start_active_text(result)  # one line when Start Active is fine
        state["briefings"][key] = {"sent": True, "recommended": result.optimal, "updates": 0}
    else:
        baseline = {int(pid): slot for pid, slot in record["recommended"].items()}
        worth_sending = result.value_of(result.optimal) - result.value_of(baseline) >= briefing.UPDATE_GAIN
        message_text = briefing.text(result, update_of=baseline) if worth_sending else None
        if worth_sending:
            record["recommended"] = result.optimal
            record["updates"] += 1

    if not message_text:
        logger.info("Lineup for %s already optimal (gain %.2f); nothing sent", key, result.gain)
        if ir_note:
            outbox.send(ir_note)
        return
    if ir_note:
        message_text += "\n\n" + ir_note
    rec_id = f"lineup-{key}-{now:%H%M}"
    message_id = outbox.send(message_text, [("Done", f"done:{rec_id}"), ("Skip", f"skip:{rec_id}")])
    state["pending"][rec_id] = {"type": "lineup", "date": key, "assignment": result.optimal, "message_id": message_id}


def _weakest(players: list, ctx, lines: dict):
    """The skater to drop for a player back from IR: my lowest long-run value."""
    return next((p for p in matchup.drop_candidates(roster_mod.active(players), ctx, lines) if not p.is_goalie), None)


def _new_ir_note(state: dict, players: list, ctx, team_lines: dict) -> str:
    """The IR lines the briefing hasn't said yet (each IR move or return once,
    while it stays true; the weekly plan repeats them all)."""
    ir_moves, back = ir.moves(players, team_lines), ir.returning(players, team_lines)
    keys = {f"{m.player.id}:{m.slot}": m for m in ir_moves} | {f"{p.id}:back": p for p in back}
    new = [k for k in keys if k not in state["ir_noted"]]
    state["ir_noted"] = sorted(keys)
    if not new:
        return ""
    weakest = _weakest(players, ctx, team_lines)
    return ir.text([m for k, m in keys.items() if k in new and isinstance(m, ir.IrMove)],
                   [p for k, p in keys.items() if k in new and not isinstance(p, ir.IrMove)], weakest)


def trade_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
               build_context=context.build) -> None:
    """Answer /trade: both teams' points per week before and after, or with
    no players named, the trades worth proposing."""
    request, state["trade_request"] = state["trade_request"], None
    if request is None:
        return
    date = now.astimezone(NHL_TIME).date()
    if date > TRADE_DEADLINE:
        outbox.send(f"The trade deadline was {TRADE_DEADLINE:%d %b}.")
        return
    others = {t: teams.players(league, t) for t in league["teams"]}
    parsed = trade.resolve(request, MY_TEAM, {MY_TEAM: players, **others}) if request else None
    if isinstance(parsed, str):
        outbox.send(parsed)
        return
    schedule = {d: nhl_client.games_on(d) for d in trade.horizon(date) if weeks.week_of(d)}
    if not schedule:
        outbox.send("No regular-season games left to judge a trade on.")
        return
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    pool, ctx, rounds = free_agents(players, league), build_context(date), draft.rounds()
    if parsed is None:
        outbox.send(trade.suggestions_text(trade.suggest(players, others, pool, ctx, schedule, lines, {}, rounds)))
        return
    give, get, partner = parsed
    result = trade.evaluate(players, others[partner], partner, give, get, pool, ctx, schedule, lines, {}, rounds)
    note = ""
    updated = teams.updated(league, partner)
    if updated and (date - dt.date.fromisoformat(updated)).days >= 1:
        note = (f"\n{partner}'s roster is from {dt.date.fromisoformat(updated):%d %b}: "
                f"send /opp {partner} with their Yahoo page if it has changed.")
    outbox.send(trade.text(result) + note)


def free_agents(players: list, league: dict) -> list:
    """Everyone on an NHL roster nobody in the league has, with Yahoo's
    position eligibility where a screenshot has shown it (league/positions.py)."""
    taken = teams.rostered_ids(league) | {p.id for p in players}
    known = positions.load()
    return [
        roster_mod.RosterPlayer(p["id"], p["name"], p["team"],
                                positions.eligible(known, p["id"], parse.NHL_TO_YAHOO_POS[p["position"]]))
        for p in nhl_client.current_rosters() if p["id"] not in taken
    ]


def learn_positions(found_players: list, tagged: set[int] | None = None) -> None:
    """Remember the positions Yahoo showed (all of them, or only `tagged` ids)."""
    known = positions.load()
    seen = {p.id: p.positions for p in found_players if tagged is None or p.id in tagged}
    if positions.learn(known, seen):
        positions.save(known)


def waiver_days(pool: list, league: dict, date: dt.date) -> dict[int, dt.date]:
    """Free agent id -> the first day he can play for me, for those on waivers:
    dropped in the last day or so, or everyone until the post-draft waivers cleared."""
    days = {pid: day for pid, day in teams.on_waivers(league, date).items()}
    if date < POST_DRAFT_WAIVERS_CLEAR:
        days.update({p.id: max(days.get(p.id, POST_DRAFT_WAIVERS_CLEAR), POST_DRAFT_WAIVERS_CLEAR) for p in pool})
    return days


@dataclass
class WeekInputs:
    """Everything the weekly plan is computed from (also used by
    scripts/explain_week.py)."""
    ctx: object
    days: list[dt.date]
    schedule: dict
    future: dict  # the weeks after this one (matchup.LONG_RUN_WEEKS), for long-run value
    lines: dict
    starters: dict
    me: matchup.TeamWeek
    them: matchup.TeamWeek
    pool: list
    season_used: int
    week_used: int
    max_moves: int
    pace: float | None  # adds a week the budget allows; None when the regular season's share is spent
    later_weight: float  # win probability per later point (addprice.later_weight)
    tau: float  # spread of the league's matchup margins
    weeks_after: int
    available_from: dict  # player id -> first day he can play for me: waivers
    so_far: tuple | None = None  # my banked points: a matchup screenshot's, else box scores by day's roster
    live: bool = False  # so_far comes from a matchup screenshot taken today
    # Then Yahoo's score and the box scores' best-lineup score for the same days
    # (how far each manager's real lineups fall short: opponent profiles).
    live_check: dict | None = None
    hold_days: int = 14  # days after this week a streamer is kept (matchup.hold_weeks)
    yahoo_projected: list | None = None  # Yahoo's projected finals, from the same screenshot
    price: addprice.AddPrice | None = None  # set by add_price once the week's moves are known


def week_inputs(date: dt.date, week: int, players: list, league: dict, state: dict,
                build_context, opponent: str) -> WeekInputs:
    ctx = build_context(date)
    days = weeks.days(week)
    schedule = {d: nhl_client.games_on(d) for d in days}
    future_days = [days[-1] + dt.timedelta(days=i) for i in range(1, 7 * matchup.LONG_RUN_WEEKS + 1)]
    future = {d: nhl_client.games_on(d) for d in future_days if weeks.week_of(d)}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    starters = _safe(goalie_client.get_starters, date, default={})
    season_used, week_used = matchup.adds_used(state["adds"], days)
    them_roster = teams.players(league, opponent)
    added_on = {a["id"]: dt.date.fromisoformat(a["date"]) for a in state["adds"] if a["id"] is not None}
    my_days = rosters_by_day(state, "me", roster_mod.active(players), days, date, added_on)
    their_days = rosters_by_day(state, opponent, them_roster, days, date)
    projected = None
    live = state["live_score"]
    is_live = bool(live and live["week"] == week and live["opponent"] == opponent
                   and live["through"] == date.isoformat())
    box = None
    if is_live:
        mine = _banked(live["score"][0], live["goalies"][0], players, ctx, days, my_days)
        theirs = _banked(live["score"][1], live["goalies"][1], them_roster, ctx, days, their_days)
        box = [sum(matchup._so_far(roster_mod.active(players), ctx, days, my_days)[:2]),
               sum(matchup._so_far(them_roster, ctx, days, their_days)[:2])]
        projected = live["projected"] or None
    else:
        mine = matchup._so_far(roster_mod.active(players), ctx, days, my_days)
        theirs = matchup._so_far(them_roster, ctx, days, their_days)
    me = matchup.project(MY_TEAM, players, ctx, schedule, lines, starters, so_far=mine)
    them = matchup.project(opponent, them_roster, ctx, schedule, lines, starters, so_far=theirs)
    remaining = sum(d >= date for d in days)
    pool = free_agents(players, league)
    tau = league_tau(players, league, ctx, future, lines, starters)
    sigma_week = math.sqrt((me.variance + them.variance) * 7 / max(remaining, 1))
    return WeekInputs(
        ctx=ctx, days=days, schedule=schedule, future=future, lines=lines, starters=starters,
        me=me, them=them, tau=tau, later_weight=addprice.later_weight(sigma_week, tau),
        pace=addprice.pace(MAX_ADDS_PER_SEASON - season_used, week),
        so_far=mine, live=is_live, yahoo_projected=projected,
        live_check={"through": date.isoformat(), "yahoo": list(live["score"]), "box": box} if is_live else None,
        hold_days=max(0, round(7 * matchup.hold_weeks(MAX_ADDS_PER_SEASON - season_used, week))
                      - sum(d >= date for d in days)),
        pool=pool,
        season_used=season_used, week_used=week_used,
        max_moves=matchup.max_moves(season_used, week_used),
        weeks_after=weeks.LAST_WEEK - week,
        available_from=waiver_days(pool, league, date),
    )


def league_tau(players: list, league: dict, ctx, future: dict, lines: dict, starters: dict) -> float:
    """The spread of matchup margins: how far apart two of the league's teams
    usually project in a week (each team's best lineup over the next full
    week; the margin's spread is sqrt(2) times the teams')."""
    week = {d: future[d] for d in sorted(future)[:7]}
    rosters = [players] + [teams.players(league, t) for t in league["teams"]]
    totals = [matchup.project("t", r, ctx, week, lines, starters, True).expected
              for r in rosters if len(roster_mod.active(r)) >= MIN_KNOWN_ROSTER]
    if len(totals) < 4 or len(week) < 7:
        return addprice.DEFAULT_TAU
    mean = sum(totals) / len(totals)
    return math.sqrt(2 * sum((t - mean) ** 2 for t in totals) / (len(totals) - 1))


def add_price(state: dict, week: int, wk: WeekInputs, ranked: list, date: dt.date) -> addprice.AddPrice | None:
    """The add's price this week, solved over this week's candidates and the
    ones logged in earlier weeks (state["add_pools"])."""
    if wk.pace is None:
        return None
    sigma_now = math.sqrt(wk.me.variance + wk.them.variance)
    remaining = sum(d >= date for d in wk.days)
    state["add_pools"][str(week)] = addprice.pool_entry(ranked, remaining, sigma_now, wk.tau)
    lam = addprice.solve(list(state["add_pools"].values()), wk.pace, wk.later_weight)
    return addprice.AddPrice(lam, wk.later_weight, wk.pace)


def _banked(total: float, goalie: float | None, roster: list, ctx, days: list[dt.date],
            history: dict | None = None) -> tuple[float, float, int]:
    """(skater points, goalie points, goalie games) so far: Yahoo's score,
    split by the G-slot rows (else by box scores); goalie games from box scores."""
    skater_bs, goalie_bs, goalie_games = matchup._so_far(roster_mod.active(roster), ctx, days, history)
    goalie = goalie_bs if goalie is None else goalie
    return total - goalie, goalie, goalie_games


def snapshot_rosters(state: dict, players: list, league: dict, now: dt.datetime) -> None:
    """Keep today's rosters, mine and my opponent's, as they are until the
    day's first puck: points banked later this week are scored with the
    players each team had that day, so a mid-week add or drop doesn't move them."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None or not players:
        return
    games = nhl_client.games_on(date)
    if games and now >= games[0].start:
        return  # lineups are locking: today's snapshot stays as it was
    opponent = current_opponent(state, week)
    state["day_rosters"][date.isoformat()] = {
        "me": [asdict(p) for p in roster_mod.active(players)],
        "them": {"team": opponent, "players": [asdict(p) for p in teams.players(league, opponent)]}
        if opponent else None,
    }


def rosters_by_day(state: dict, side: str, current: list, days: list[dt.date], today: dt.date,
                   added_on: dict[int, dt.date] | None = None) -> dict[dt.date, list]:
    """Each finished day's roster for `side` ("me" or the opponent's name):
    that day's snapshot plus today's players (a correction since may have
    shown someone the snapshot missed), less anyone added after that day
    (`added_on`: my adds ledger; the opponent's adds are unknown)."""
    out = {}
    for d in (d for d in days if d < today):
        snap = state["day_rosters"].get(d.isoformat()) or {}
        if side == "me":
            saved = snap.get("me") or []
        else:
            them = snap.get("them") or {}
            saved = them.get("players", []) if them.get("team") == side else []
        players = {p["id"]: roster_mod.RosterPlayer(**p) for p in saved}
        for p in current:
            if not (added_on and added_on.get(p.id, d) > d):
                players.setdefault(p.id, p)
        out[d] = list(players.values())
    return out


def week_result(state: dict, week: int, players: list, league: dict, ctx) -> dict | None:
    """The week's result as report.result_view has it, scored from box scores
    with each day's roster (days before ctx.today only). None without an opponent."""
    opponent = current_opponent(state, week)
    if not opponent:
        return None
    days = [d for d in weeks.days(week) if d < ctx.today]
    finished = days == weeks.days(week)  # else the goalie minimum isn't settled yet
    added_on = {a["id"]: dt.date.fromisoformat(a["date"]) for a in state["adds"] if a["id"] is not None}

    def daily(side: str, current: list, added: dict | None) -> tuple[list[float], bool]:
        history = rosters_by_day(state, side, current, days, ctx.today, added)
        rows = [matchup._so_far(current, ctx, [d], history) for d in days]
        made = sum(r[2] for r in rows) >= MIN_GOALIE_GAMES_PER_WEEK or not finished
        return [round(sk + (g if made else 0.0), 2) for sk, g, _ in rows], made

    mine, my_min = daily("me", roster_mod.active(players), added_on)
    theirs, their_min = daily(opponent, teams.players(league, opponent), None)
    first, last = weeks.days(week)[0].isoformat(), weeks.days(week)[-1].isoformat()
    adds = [a for a in state["adds"] if first <= a["date"] <= last]
    skipped = sum(1 for d in state["decisions"]
                  if d["type"] == "add" and d["decision"] == "skip" and first <= d["date"] <= last)
    plan = state["results"].get(str(week), {}).get("first")
    return report.result_view(week, opponent, days, mine, theirs, [my_min, their_min], plan, adds, skipped,
                              report.budget_view(state["adds"], week), finished,
                              scorecard.line(scorecard.score(state["decisions"], ctx, ctx.today)))


def report_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
                build_context=context.build, week: int | None = None) -> None:
    """Last week's result, from noon on the new week's first day (before its
    plan): only then are all its games in the box scores. `week` reports that
    week now (a dry run's --report), finished or not."""
    date = now.astimezone(NHL_TIME).date()
    if week is None:
        this = weeks.week_of(date)
        week = this - 1 if this else (weeks.LAST_WEEK if date > SEASON_END else None)
        if not week or "final" in state["results"].get(str(week), {}) or not roster_mod.active(players):
            return
        if briefing.quiet(now) or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    view = week_result(state, week, players, league, build_context(date))
    if view is None:
        logger.info("No opponent known for week %s; no result to report", week)
        return
    outbox.send(report.result_text(view))
    png = _safe(charts.result_chart, view)
    if png:
        outbox.send_photo(png)
    if not view["finished"]:
        return  # a dry run's look at a week still in progress
    entry = state["results"].setdefault(str(week), {"opponent": view["opponent"]})
    entry["final"] = {"score": [round(x, 2) for x in view["final"]], "goalie_min": view["goalie_min"],
                      "at": now.isoformat(timespec="minutes"), "source": "box scores"}


def _week_schedule(week: int) -> dict[dt.date, list]:
    return {d: nhl_client.games_on(d) for d in weeks.days(week)}


@dataclass
class NextWeek:
    """The week after this one: the streamer horizon and the grid's second half."""
    week: int | None
    schedule: dict
    mine: matchup.TeamWeek | None


def next_week(week: int, players: list, wk: WeekInputs) -> NextWeek:
    if week >= weeks.LAST_WEEK:
        return NextWeek(None, {}, None)
    sched = _week_schedule(week + 1)
    return NextWeek(week + 1, sched, matchup.project(MY_TEAM, players, wk.ctx, sched, wk.lines, wk.starters, True))


def add_candidates(wk: WeekInputs, nxt: NextWeek) -> list:
    """Free agents to judge, including streamers whose games fall on nights my
    lineup has their slot open, this week and next (also scripts/explain_week.py)."""
    open_days = matchup.open_positions(wk.me) | (matchup.open_positions(nxt.mine) if nxt.mine else {})
    return matchup.shortlist(wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.available_from,
                             open_days, wk.schedule | nxt.schedule)


def streamer_text(view: dict, streams: list[dict], price, chosen: list = ()) -> str:
    if not streams:
        return "No free agent adds points in your open slots this week or next."
    lines = ["Best streamer per position, this week + next:"]
    for row, st in zip(view["streamers"], streams):
        m = st["move"]
        verdict = ("recommended, see below" if row["recommended"]
                   else f"not recommended: {matchup.why_not(m, price, chosen)}")
        lines.append(f"{st['position']}: {m.add.name} ({m.add.team})" + (f" for {m.drop.name}" if m.drop else "")
                     + f": {row['slot_games']} games in open slots; net of the drop {m.week_gain:+.1f} pts this "
                     f"week, {st['next_gain']:+.1f} next ({verdict}).")
    return "\n".join(lines)


def week_views(state: dict, players: list, league: dict, week: int, wk: WeekInputs, nxt: NextWeek,
               ranked: list, moves: list) -> dict:
    """Every number the charts and the dashboard show, computed once: the
    decision map, the schedule grid with streamers, the add budget, and each
    shown add's week-by-week gain (keyed by report.move_key)."""
    price = wk.price if wk.max_moves else None
    decision = report.decision_view(week, ranked, moves, price)
    spans = [(week, current_opponent(state, week), wk.me, wk.them)]
    if nxt.week:
        opp = current_opponent(state, nxt.week)
        theirs = matchup.project(opp or "?", teams.players(league, opp) if opp else [], wk.ctx, nxt.schedule,
                                 wk.lines, wk.starters, True)
        spans.append((nxt.week, opp or "?", nxt.mine, theirs))
    streams = matchup.streamers(players, ranked, wk.ctx, wk.schedule, nxt.schedule, wk.lines, wk.starters, wk.so_far)
    schedule = report.schedule_view(players, spans, streams, moves)
    budget = report.budget_view(state["adds"], week)
    by_key = {report.move_key(m): m for m in ranked}
    by_key.update({report.move_key(m): m for m in moves + [st["move"] for st in streams]})
    chosen = {report.move_key(m) for m in moves}
    keys = [pt["key"] for pt in decision["points"]] + [r["key"] for r in schedule["streamers"]] + list(chosen)
    later = {w: _week_schedule(w) for w in range(week + 1, min(week + CHART_WEEKS, weeks.LAST_WEEK) + 1)}
    adds = {}
    for key in dict.fromkeys(keys):
        m = by_key[key]
        verdict = "Recommended" if key in chosen else "Not recommended: " + matchup.why_not(m, price, moves)
        gains = report.weekly_gains(players, m, wk.ctx, later, wk.lines, wk.starters)
        adds[key] = report.add_view(m, week, gains, budget, verdict)
    return {"decision": decision, "schedule": schedule, "budget": budget, "adds": adds,
            "streamer_text": streamer_text(schedule, streams, price, moves)}


def write_dashboard(views: dict, wk: WeekInputs, week: int, opponent: str, stance_text: str | None,
                    now: dt.datetime, dry_run: bool) -> Path:
    """site/data.json for the dashboard (site/index.html), published to GitHub
    Pages by the workflow. A dry run writes it next to its charts instead,
    with a copy of the page, to preview locally."""
    p_win = matchup.win_prob(wk.me, wk.them)
    data = {
        "generated": now.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
        "week": week, "opponent": opponent, "days": [wk.days[0].isoformat(), wk.days[-1].isoformat()],
        "summary": {
            "so_far": [wk.me.so_far, wk.them.so_far], "expected": [wk.me.expected, wk.them.expected],
            "yahoo": wk.yahoo_projected, "win": p_win, "stance": matchup.stance(p_win), "stance_text": stance_text,
            "adds_left": {"season": MAX_ADDS_PER_SEASON - wk.season_used, "week": wk.max_moves},
        },
        **{k: v for k, v in views.items() if k != "streamer_text"},
    }
    folder = CHART_DIR if dry_run else SITE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    if dry_run:
        (folder / "index.html").write_bytes((SITE_DIR / "index.html").read_bytes())
    path = folder / "data.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def _wait_text(keeper) -> str:
    if not keeper:
        return ""
    swap = f"{keeper.add.name} for {keeper.drop.name}" if keeper.drop else keeper.add.name
    return (f"\n\nCan wait until Monday, when your adds reset: {swap} ({keeper.week_gain:+.1f} pts this week, "
            f"{keeper.next_weeks:+.1f} over the next two). The risk: someone claims him first.")


def plan_due(record: dict | None, date: dt.date, week: int) -> bool:
    """The plan goes out once at the start of a week and once from mid-week."""
    if record is None:
        return True
    return date >= weeks.midweek(week) and "midweek" not in record


def record_plan(state: dict, week: int, opponent: str, me: matchup.TeamWeek, them: matchup.TeamWeek,
                now: dt.datetime) -> None:
    """Log the plan's numbers for the week's result to be checked against:
    the first plan of the week, and the latest."""
    plan = {"at": now.isoformat(timespec="minutes"),
            "expected": [round(me.expected, 2), round(them.expected, 2)],
            "so_far": [round(me.so_far, 2), round(them.so_far, 2)],
            "sd": [round(math.sqrt(me.variance), 2), round(math.sqrt(them.variance), 2)],
            "win": round(matchup.win_prob(me, them), 4)}
    entry = state["results"].setdefault(str(week), {"opponent": opponent})
    entry.setdefault("first", plan)
    entry["last"] = plan


def _plan_sent(state: dict, key: str, now: dt.datetime, midweek: bool) -> None:
    stamp = now.isoformat(timespec="minutes")
    record = state["weeks"].setdefault(key, {"sent": stamp})
    if midweek:
        record["midweek"] = stamp
    state["week_requested"] = False


def weekly_step(state: dict, players: list, league: dict, now: dt.datetime, force: bool, outbox: Outbox,
                build_context=context.build) -> None:
    """The matchup plan: from noon local on the week's first day, again from
    noon on its Wednesday (with the mid-week stance), and whenever you send
    /week or a matchup screenshot."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    requested = state["week_requested"]
    if week is None or not roster_mod.active(players):
        if requested:
            outbox.send("No roster yet." if not players else "No fantasy week in progress.")
        state["week_requested"] = False
        return
    key = str(week)
    if not (force or requested):
        if not plan_due(state["weeks"].get(key), date, week) or briefing.quiet(now) \
                or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    is_midweek = date >= weeks.midweek(week)
    opponent = current_opponent(state, week)
    if not opponent:
        outbox.send(f"Week {week} is a playoff week: who are you playing? Send /opp Team Name.")
        _plan_sent(state, key, now, is_midweek)  # asked once; /opp then /week brings the plan
        return

    p = plan_moves(state, players, league, date, week, opponent, build_context)
    wk, nxt, ranked, moves = p.wk, p.nxt, p.ranked, p.moves
    ir_text = ir.text(p.ir_moves, ir.returning(players, wk.lines), _weakest(players, wk.ctx, wk.lines))
    midweek = None
    if is_midweek or wk.live:
        chase = None
        if matchup.stance(matchup.win_prob(wk.me, wk.them)) == "chase":
            chase = matchup.biggest_swing(ranked)
        recommended = chase is not None and report.move_key(chase) in {report.move_key(m) for m in moves}
        midweek = matchup.midweek_text(wk.me, wk.them, chase, wk.price if wk.max_moves else None, recommended,
                                       moves)
    outbox.send(_action_line(moves, ir_text, wk.max_moves) + "\n\n"
                + matchup.text(week, wk.days, wk.me, wk.them, teams.updated(league, opponent), wk.season_used,
                               wk.week_used, date, wk.yahoo_projected)
                + (f"\n\n{midweek}" if midweek else "")
                + _wait_text(matchup.can_wait(ranked, moves, wk.price if wk.max_moves else None))
                + (f"\n\n{ir_text}" if ir_text else ""))
    # Only now: a run that fails before this (an NHL or DailyFaceoff outage) leaves
    # the plan due, so the next run sends it.
    _plan_sent(state, key, now, is_midweek)
    record_plan(state, week, opponent, wk.me, wk.them, now)
    if wk.live_check:  # one per screenshot day (a later plan the same day replaces it)
        entry = state["results"][str(week)]
        entry["live"] = [c for c in entry.get("live", []) if c["through"] != wk.live_check["through"]] + [wk.live_check]
    views = _safe(week_views, state, p.planned, league, week, wk, nxt, ranked, moves)
    if views:
        png = _safe(charts.decision_chart, views["decision"])
        if png:
            outbox.send_photo(png)
        png = _safe(charts.schedule_chart, views["schedule"])
        if png:
            outbox.send_photo(png, views["streamer_text"])
        decided = matchup.decided(matchup.win_prob(wk.me, wk.them))
        stance_text = midweek or (f"This week looks {decided}: save your adds for players worth keeping."
                                  if decided else None)
        _safe(write_dashboard, views, wk, week, opponent, stance_text, now, outbox.settings.dry_run)
    send_adds(state, p, moves, views, date, now, outbox)


@dataclass
class PlanMoves:
    """A week's add/drop search (the weekly plan's, and the evening's news check)."""
    wk: WeekInputs
    nxt: NextWeek
    ranked: list  # every move weighed
    moves: list  # the adds worth making now, best first
    ir_moves: list  # IR moves the adds assume (each frees a spot)
    planned: list  # the roster with them made
    open_spots: int  # spots open before any IR move


def plan_moves(state: dict, players: list, league: dict, date: dt.date, week: int, opponent: str,
               build_context) -> PlanMoves:
    wk = week_inputs(date, week, players, league, state, build_context, opponent)
    ir_moves = ir.moves(players, wk.lines)
    planned = ir.after(players, ir_moves)  # the adds assume the IR moves are made: a free spot each
    nxt = next_week(week, players, wk)
    candidates = add_candidates(wk, nxt)
    ranked = matchup.candidate_moves(planned, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters,
                                     wk.future, wk.weeks_after, wk.available_from, wk.so_far, wk.hold_days,
                                     wk.later_weight)
    wk.price = add_price(state, week, wk, ranked, date)
    moves = []
    if wk.max_moves and wk.price is not None:
        moves = matchup.best_moves(planned, wk.them, wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future,
                                   wk.weeks_after, wk.max_moves, wk.price, wk.available_from, wk.so_far,
                                   candidates, ranked, wk.hold_days)
    return PlanMoves(wk, nxt, ranked, moves, ir_moves, planned,
                     max(0, matchup.ACTIVE_SPOTS - len(roster_mod.active(players))))


def _action_line(moves: list, ir_text: str, adds_left: int = 1) -> str:
    """The plan's first line: what to do now."""
    if not moves:
        head = ("No adds left this week (they reset Monday)." if not adds_left
                else "No add is worth one of yours right now.")
        return head + (" See the IR note below." if ir_text else "")
    adds = "; ".join(f"add {m.add.name}" + (f" for {m.drop.name}" if m.drop else "")
                     + f" (win {matchup._pct(m.win_before)} -> {matchup._pct(m.win_after)})" for m in moves)
    return f"Do now: {adds}. Details below."


def send_adds(state: dict, p: PlanMoves, moves: list, views: dict | None, date: dt.date, now: dt.datetime,
              outbox: Outbox) -> None:
    """Each add as its own message with Done / Taken / Skip, and its chart."""
    for i, move in enumerate(moves):
        rec_id = f"add-{date.isoformat()}-{now:%H%M}-{i}"
        buttons = [("Done", f"done:{rec_id}"), ("Other drop", f"other:{rec_id}"), ("Taken", f"taken:{rec_id}"),
                   ("Skip", f"skip:{rec_id}")]
        view = views and views["adds"].get(report.move_key(move))
        png = _safe(charts.add_chart, view) if view else None
        # An add without a drop fills a spot already open, else the next IR move's.
        k = sum(m.drop is None for m in moves[:i]) - p.open_spots
        opens = p.ir_moves[k] if move.drop is None and 0 <= k < len(p.ir_moves) else None
        text = matchup.move_text(move, opens.player.name if opens else None)
        message_id = (outbox.send_photo(png, text, buttons) if png else outbox.send(text, buttons))
        state["pending"][rec_id] = {"type": "add", "date": date.isoformat(), "add": asdict(move.add),
                                    "drop": move.drop.id if move.drop else None,
                                    "drop_name": move.drop.name if move.drop else None, "message_id": message_id,
                                    "ir": {str(opens.player.id): opens.slot} if opens else {}}


def news_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
              build_context=context.build) -> None:
    """Between plans, once an evening: an add that now clears the price and
    hasn't been suggested this week (an injury, a confirmed goalie, a hot
    free agent since the plan). Not on a plan's day; only with adds left."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    games = nhl_client.games_on(date) if week else []
    if not games or not roster_mod.active(players) or state["news_checked"] == date.isoformat():
        return
    if now < briefing.briefing_due(date, games[0].start) or briefing.quiet(now) or now >= games[0].start:
        return
    record = state["weeks"].get(str(week), {})
    if any(record.get(k, "")[:10] == now.date().isoformat() for k in ("sent", "midweek")):
        return  # the plan went out today
    days = weeks.days(week)
    if not matchup.max_moves(*matchup.adds_used(state["adds"], days)) or not current_opponent(state, week):
        return
    state["news_checked"] = date.isoformat()
    first, last = days[0].isoformat(), days[-1].isoformat()
    offered = {(r["add"]["id"], r["drop"]) for r in state["pending"].values() if r["type"] == "add"}
    offered |= {(d.get("add"), d.get("drop")) for d in state["decisions"]
                if d["type"] == "add" and first <= d["date"] <= last}
    p = plan_moves(state, players, league, date, week, current_opponent(state, week), build_context)
    new = [m for m in p.moves if (m.add.id, m.drop.id if m.drop else None) not in offered]
    if not new:
        logger.info("News check %s: no new add clears the price", date)
        return
    outbox.send(f"News since the plan: {'an add now clears' if len(new) == 1 else f'{len(new)} adds now clear'} "
                f"the price (you're at {matchup._pct(matchup.win_prob(p.wk.me, p.wk.them))} to win).")
    send_adds(state, p, new, None, date, now, outbox)


def alert_health(state: dict, outbox: Outbox, now: dt.datetime) -> None:
    """Say which data sources had trouble this run, once a day per source."""
    today = now.date().isoformat()
    new = {s: d for s, d in health.problems().items() if state["health_alerts"].get(s) != today}
    if not new:
        return
    for source in new:
        state["health_alerts"][source] = today
    outbox.send("Data check:\n" + "\n".join(health.describe(s, d) for s, d in new.items()))


def run_steps(steps: list[tuple[str, Callable[[], object]]]) -> list[tuple[str, Exception]]:
    """Run every step even when an earlier one fails (a /trade crash mustn't
    cost tonight's briefing). Returns the failures, (step name, error)."""
    failures = []
    for name, step in steps:
        try:
            step()
        except Exception as exc:
            logger.exception("Step %s failed", name)
            failures.append((name, exc))
    return failures


def alert_failure(failures: list[tuple[str, Exception]], settings: Settings, state: dict, outbox: Outbox,
                  now: dt.datetime) -> None:
    """Tell Nico, once a day. The text never holds the bot token."""
    today = now.date().isoformat()
    if state["last_error"] == today:
        return
    state["last_error"] = today
    name, exc = failures[0]
    detail = str(exc).replace(settings.telegram_bot_token, "<token>") if settings.telegram_bot_token else str(exc)
    more = f" (and {len(failures) - 1} more step{'s' if len(failures) > 2 else ''})" if len(failures) > 1 else ""
    _safe(outbox.send, f"Assistant GM run failed in {name}{more}: {type(exc).__name__}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print messages; send and save nothing")
    parser.add_argument("--force", action="store_true", help="plan tonight's lineup regardless of the time")
    parser.add_argument("--now", help="pretend it's this ISO time (with offset), e.g. for replays")
    parser.add_argument("--report", type=int, help="report this week's result now, as Monday's report does")
    parser.add_argument("--trade", nargs="?", const="",
                        help='as /trade does: "Knight for Bouchard", or nothing for suggestions')
    args = parser.parse_args()

    settings = load_settings()
    settings.dry_run = settings.dry_run or args.dry_run
    if not settings.dry_run and not (settings.telegram_bot_token and settings.telegram_chat_id):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or use --dry-run).")
    if settings.relay_url and not (settings.relay_token and settings.webhook_secret):
        raise SystemExit("RELAY_URL needs RELAY_TOKEN and TELEGRAM_WEBHOOK_SECRET too.")
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    outbox = Outbox(settings)
    state = gm_state.load()
    players = roster_mod.load()
    league = teams.load()
    build_context = functools.lru_cache(maxsize=None)(context.build)
    if args.trade is not None:
        state["trade_request"] = args.trade

    def read_updates() -> None:
        webhook_problem = sync_webhook(settings, now)
        relay_problem = process_updates(settings, state, players, league, outbox)
        report_relay(webhook_problem or relay_problem, state, outbox, now)

    steps = [("updates", read_updates)] if not settings.dry_run else []
    steps += [
        ("NHL teams", lambda: sync_teams(players) if players else None),
        ("day rosters", lambda: snapshot_rosters(state, players, league, now)),
        ("trade", lambda: trade_step(state, players, league, now, outbox, build_context)),
        ("week report", lambda: report_step(state, players, league, now, outbox, build_context, args.report)),
        ("weekly plan", lambda: weekly_step(state, players, league, now, args.force, outbox, build_context)),
        ("briefing", lambda: briefing_step(state, players, now, args.force, outbox, build_context)),
        ("news", lambda: news_step(state, players, league, now, outbox, build_context)),
        ("data check", lambda: alert_health(state, outbox, now)),
    ]
    health.clear()
    try:
        failures = run_steps(steps)
        if failures:
            if not settings.dry_run:
                alert_failure(failures, settings, state, outbox, now)
            raise failures[0][1]
    finally:
        if not settings.dry_run:
            roster_mod.save(players)
            teams.save(league)
            gm_state.save(state, now.date())


if __name__ == "__main__":
    main()
