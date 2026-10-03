"""Telegram in: taps on recommendations, commands, pasted Yahoo pages, and screenshots
(team pages, matchups, transactions: app, website or league chat) turned into rosters, adds and waivers.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

import requests

from clients import nhl_client, screenshot
from clients.names import normalize_name
from config.league import (MAX_ADDS_PER_SEASON, MY_TEAM, SCHEDULE, SEASON_START)
from config.settings import Settings
from engine import briefing
from league import parse, teams, weeks
from league import roster as roster_mod
from notify import telegram
from state import gm_state
from bot import common
from bot.common import NHL_TIME, Outbox, _safe, current_opponent, learn_positions
from bot.dfo_archive import ARCHIVE_DIR

logger = logging.getLogger(__name__)

HELP = (
    "Assistant GM commands:\n"
    "/roster - the roster I think you have\n"
    "/myteam - then send screenshots of your Yahoo team page or paste its text, to correct your roster\n"
    "/week - this week's matchup: expected score, win odds, adds worth making\n"
    "Matchup screenshots (the Yahoo app's Matchup tab, scrolled through) - both rosters and the live score, "
    "then an updated plan\n"
    "Transactions screenshots (League > Transactions in the app or website, or the league chat) - every team's "
    "adds, drops and trades, so suggested free agents are free\n"
    "Standings and All Matchups screenshots (League tab) - the season odds start from the real standings and "
    "this week's pairings\n"
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
# One move seen twice: the same team, type and players within this of each other.
# The league chat dates moves sent together by the first one's time (15 min early
# on 2026-09-30), and "13m ago" is a minute off. Judgment call: wide enough for a
# wrong time zone, while a team re-adding a player it dropped that same day is rare.
TX_SAME_MOVE = dt.timedelta(hours=12)


# Judgment call: more new players than two weeks of adds in one roster update is
# a stale roster or a misread page, not adds (League > Transactions counts those).
MAX_NEW_AS_ADDS = 4
# A player on my roster within this many days who shows up "new" is the bot
# catching up (a misread screenshot, a missed row), not an add. Judgment call.
SEEN_MINE_DAYS = 30


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
                gm_state.record_add(state, rec["add"]["id"], rec["add"].get("name"), common.nhl_today(), "done")
                if rec["drop"] is not None:
                    teams.put_on_waivers(league, rec["drop"], common.nhl_today())
                    common.forget_mine(state, rec["drop"])
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
    if "standings" in new_screenshots:
        finish_standings(state, league, outbox)
    if "scoreboard" in new_screenshots:
        finish_scoreboard(state, league, outbox)
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
    teams.put_on_waivers(league, dropped.id, common.nhl_today())
    common.forget_mine(state, dropped.id)
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
        shot = screenshot.read(telegram.download_file(settings.telegram_bot_token, _image(message)),
                               now=at.astimezone(briefing.LOCAL))
    except (screenshot.ScreenshotError, telegram.TelegramError) as e:
        outbox.send(f"Couldn't read that screenshot: {e}")
        return None
    except requests.RequestException:  # its text can hold the download URL, which holds the token
        outbox.send("Couldn't download that screenshot from Telegram; try again.")
        return None
    if shot["kind"] == "transactions":
        if not shot["rows"]:
            outbox.send("I couldn't read any transactions in that screenshot. Send League > Transactions "
                        "(app or website) or the league chat, with each move's date in view.")
            return None
        state["transaction_rows"] += shot["rows"]
        sent = at.astimezone(briefing.LOCAL).strftime("%Y-%m-%dT%H:%M")
        state["transaction_rows_at"] = max(state.get("transaction_rows_at") or "", sent)
        return "transactions"
    if shot["kind"] == "standings":
        state["standings_rows"] += shot["rows"]
        return "standings"
    if shot["kind"] == "scoreboard":
        state["scoreboard_shots"].append({"week": shot["week"], "teams": shot["teams"], "pairs": shot["pairs"]})
        return "scoreboard"
    if shot["kind"] == "matchup":
        draft = (state["matchup_shots"] if _fresh(state["matchup_shots"], at)
                 else {"rows": [], "labels": [], "first_at": at.isoformat()})
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


def _tx_name(name: str) -> str:
    """ "astolarz" from "A. Stolarz" or "Anthony Stolarz": the app shows initials,
    the website and the league chat full names."""
    first, *rest = normalize_name(name).split() or [""]
    return first[:1] + "".join(rest) if rest else first


def _tx_key(row: dict) -> str:
    names = ",".join(_tx_name(p["name"]) for p in row["players"])
    teams_ = ",".join(normalize_name(t).replace(" ", "") for t in row["teams"])
    return f"{_tx_time(row['when'])}|{row['type']}|{teams_}|{names}"


def _tx_move(key: str) -> tuple[dt.datetime, str]:
    """A key's time, and the move itself (type, teams, players) in any order."""
    when, kind, teams_, names = key.split("|")
    return dt.datetime.fromisoformat(when), f"{kind}|{sorted(teams_.split(','))}|{sorted(names.split(','))}"


def _seen_move(key: str, moves: dict[str, list[dt.datetime]]) -> bool:
    when, move = _tx_move(key)
    return any(abs(when - t) <= TX_SAME_MOVE for t in moves.get(move, []))


def finish_transactions(state: dict, players: list, league: dict, outbox: Outbox) -> None:
    """Apply League > Transactions rows not seen before, oldest first: adds
    and claims put a player on that team (off the free agents), drops free him,
    trades swap rosters; my own team's moves update my roster too. Says what
    changed, and warns when the screenshots may not reach back to the last ones seen.
    A move already seen in another layout, at a time within TX_SAME_MOVE, is the same move.
    A set that reaches back to the moves already seen and up to the newest of
    them (the list's top, as it is scrolled first) makes every roster current
    as of when it was sent: league["moves_through"]."""
    rows, state["transaction_rows"] = state["transaction_rows"], []
    sent_at, state["transaction_rows_at"] = state.get("transaction_rows_at"), None
    seen = state["transactions_seen"]
    newest_seen = max(seen.values(), default=None)
    names = {normalize_name(t).replace(" ", ""): t for t in (*SCHEDULE, *league["teams"], MY_TEAM)}
    had_seen = bool(seen)
    moves: dict[str, list[dt.datetime]] = {}
    for key in seen:
        when, move = _tx_move(key)
        moves.setdefault(move, []).append(when)
    overlap = any(_seen_move(_tx_key(row), moves) for row in rows)
    fresh = []
    for i, row in enumerate(rows):
        key = _tx_key(row)
        if not _seen_move(key, moves):
            when, move = _tx_move(key)
            moves.setdefault(move, []).append(when)
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
            common.forget_mine(state, p.id)
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

    newest_shown = max((_tx_time(row["when"]) for row in rows), default=None)
    top_in_view = newest_shown is not None and (newest_seen is None or dt.datetime.fromisoformat(newest_shown)
                                                + TX_SAME_MOVE >= dt.datetime.fromisoformat(newest_seen))
    if sent_at and (overlap or not had_seen) and top_in_view and not problems:
        league["moves_through"] = max(league.get("moves_through") or "", sent_at)
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


def _league_team(shown: str, league: dict) -> str | None:
    """The league's name for a team as a screenshot shows it ("Jattilaisentie
    Giants": OCR drops the umlauts)."""
    names = {normalize_name(t).replace(" ", ""): t for t in (*SCHEDULE, *league["teams"], MY_TEAM)}
    return names.get(normalize_name(shown).replace(" ", ""))


def finish_standings(state: dict, league: dict, outbox: Outbox) -> None:
    """League > Standings rows into state["standings"]: the season simulation
    starts from them (engine/season.py). The weeks they include are the most
    games any team has played; rows for the same weeks add up across screenshots."""
    rows, state["standings_rows"] = state["standings_rows"], []
    teams_, problems = {}, []
    for row in rows:
        team = _league_team(row["team"], league)
        if team:
            teams_[team] = {k: row[k] for k in ("w", "l", "t", "pf")}
        else:
            problems.append(row["team"])
    if not teams_:
        outbox.send("I couldn't match any team in those standings." + (f" Read: {', '.join(problems)}" if problems else ""))
        return
    week = max(r["w"] + r["l"] + r["t"] for r in teams_.values())
    old = state["standings"] or {}
    if old.get("week") == week:
        teams_ = {**old["teams"], **teams_}
    state["standings"] = {"week": week, "teams": teams_, "at": common.nhl_today().isoformat()}
    mine = teams_.get(MY_TEAM)
    lines = [f"Standings saved: {len(teams_)} of 16 teams, {week} week{'' if week == 1 else 's'} played."
             + (f" You: {mine['w']}-{mine['l']}-{mine['t']}, {mine['pf']:.2f} points for." if mine else "")]
    if len(teams_) < 16:
        lines.append("Scroll down and send the rest, so the season odds see every team.")
    if problems:
        lines.append("Couldn't match: " + ", ".join(problems))
    outbox.send("\n".join(lines))


def finish_scoreboard(state: dict, league: dict, outbox: Outbox) -> None:
    """All Matchups screenshots into state["league_weeks"][week]: the week's
    pairings (the season simulation plays them instead of random ones) and each
    team's score and Yahoo projection, as of today, plus the first ones seen
    ("first": before any games when sent on Monday, Yahoo's forecast to check)."""
    shots, state["scoreboard_shots"] = state["scoreboard_shots"], []
    today = common.nhl_today()
    weeks_seen, problems = set(), []
    for shot in shots:
        week = shot["week"] or weeks.week_of(today)
        if not week:
            continue
        entry = state["league_weeks"].setdefault(str(week), {"pairs": [], "scores": {}})
        names = [_league_team(t["team"], league) for t in shot["teams"]]
        problems += [t["team"] for t, n in zip(shot["teams"], names) if n is None]
        for team, row in zip(names, shot["teams"]):
            if team:
                now = {"score": row["score"], "projected": row["projected"], "date": today.isoformat()}
                first = entry["scores"].get(team, {}).get("first") or dict(now)
                entry["scores"][team] = {**now, "first": first}
        for i, j in shot["pairs"]:
            pair = sorted((names[i], names[j])) if names[i] and names[j] else None
            if pair and pair not in entry["pairs"]:
                entry["pairs"].append(pair)
        weeks_seen.add(week)
    if not weeks_seen:
        outbox.send("I couldn't tell which week those matchups are.")
        return
    lines = [f"Week {w} matchups: {len(state['league_weeks'][str(w)]['pairs'])} of 8 pairings, "
             f"{len(state['league_weeks'][str(w)]['scores'])} of 16 scores saved." for w in sorted(weeks_seen)]
    if problems:
        lines.append("Couldn't match: " + ", ".join(problems))
    outbox.send("\n".join(lines))


def _tx_label(when: str) -> str:
    return dt.datetime.fromisoformat(when).strftime("%a %d %b %H:%M")


def _side(rows: list[dict], side: str) -> list[dict]:
    """One team's players from matchup rows, each once (screenshots overlap).
    Rows without a slot are left out: the Totals view also lists players
    dropped earlier in the week, who aren't on the roster any more."""
    seen, found = set(), []
    for row in rows:
        p = row[side]
        if p and row["slot"] and p["name"] not in seen:
            seen.add(p["name"])
            found.append({**p, "slot": row["slot"]})
    return found


def _goalie_points(rows: list[dict]) -> float | None:
    """Points from the G slots, or None if no goalie row was read."""
    goalies = [r["points"] or 0.0 for r in rows if r["slot"] == "G"]
    return sum(goalies) if goalies else None


MATCHUP_COLUMNS = ["at", "week", "side", "fantasy_team", "slot", "name", "team", "positions", "points", "projected"]


def archive_matchup(draft: dict, week: int, opponent: str | None, root: Path | None = None) -> Path | None:
    """Every player row a matchup screenshot shows, Yahoo's week projection
    included, to the private data archive (third-party data, Nico 2026-10-03):
    one file per set of screenshots, rewritten as more of the set arrives."""
    root = root or ARCHIVE_DIR
    if not (root / ".git").exists() or not draft.get("rows"):
        return None
    first = dt.datetime.fromisoformat(draft.get("first_at") or draft["at"])
    path = root / "yahoo_matchup" / str(first.year) / f"{first.astimezone(dt.timezone.utc):%Y-%m-%dT%H%M}Z.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen, out = set(), []
    for row in draft["rows"]:
        for side, team in (("mine", MY_TEAM), ("theirs", opponent or "")):
            p = row.get(side)
            if p and (side, p["name"]) not in seen:
                seen.add((side, p["name"]))
                out.append({"at": draft["at"], "week": week, "side": side, "fantasy_team": team,
                            "slot": row.get("slot") or "", "name": p["name"], "team": p.get("team", ""),
                            "positions": "/".join(p.get("positions") or []), "points": p.get("points"),
                            "projected": p.get("projected")})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MATCHUP_COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    return path


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
        if draft.get("projected"):  # Yahoo's first forecast of the week, kept for checking
            state["results"].setdefault(str(week), {"opponent": opponent}).setdefault("yahoo_first", {
                k: state["live_score"][k] for k in ("at", "through", "score", "projected")})
        _safe(archive_matchup, draft, week, opponent)
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
    shown, kept = with_unseen(found.players, players)
    changes = roster_mod.replace(players, shown, found.tagged)
    lines.append("Your roster: " + ("; ".join(changes) if changes else "same as I had."))
    if kept:
        lines.append(f"Not in these screenshots, so kept: {', '.join(kept)}. If you dropped "
                     f"{'him' if len(kept) == 1 else 'them'}, send League > Transactions screenshots.")
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
        apply_my_roster(found, state, players, league, outbox, keep_unseen=True)
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
        slot = rec.get("add_slot") or (roster_mod.BENCH if known else None)  # an IR stash goes to his IR slot
        players.append(roster_mod.RosterPlayer(**{**rec["add"], "slot": slot}))


def opp_command(team_arg: str, paste: str, state: dict, league: dict, outbox: Outbox) -> None:
    """/opp [Team Name], with a team's Yahoo page pasted below it or in the
    next message. Without a name it's this week's opponent; in the playoffs
    the name also records who you're playing."""
    today = common.nhl_today()
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


def apply_my_roster(found: parse.Found, state: dict, players: list, league: dict, outbox: Outbox,
                    keep_unseen: bool = False) -> None:
    """Make my roster what Yahoo shows. `keep_unseen` (screenshots, which can
    miss a row): players they don't show are kept if they show fewer than I have."""
    learn_positions(found.players, found.tagged)
    before = {p.id for p in players}
    shown, kept = with_unseen(found.players, players) if keep_unseen else (found.players, [])
    changes = roster_mod.replace(players, shown, found.tagged)
    lines = [f"Roster saved: {len(shown)} players."] + (changes or ["Same as I had."])
    if kept:
        lines.append(f"Not in these screenshots, so kept: {', '.join(kept)}. If you dropped "
                     f"{'him' if len(kept) == 1 else 'them'}, send League > Transactions screenshots.")
    lines += count_new_players(state, league, before, players, common.nhl_today())
    if found.problems:
        lines.append("Couldn't place: " + "; ".join(found.problems))
    outbox.send("\n".join(lines) + "\n\n" + roster_mod.describe(players))


def with_unseen(shown: list, players: list) -> tuple[list, list[str]]:
    """Screenshots that show fewer players than I have most likely missed a
    row (a bench goalie below the fold): keep my players they don't show, as
    they were. A real drop comes with an add, a Transactions row or a Done tap.
    Returns (the roster to save, names kept)."""
    seen = {p.id for p in shown}
    unseen = [p for p in players if p.id not in seen]
    if len(shown) >= len(players) or not unseen:
        return shown, []
    kept = [roster_mod.RosterPlayer(p.id, p.name, p.team, list(p.positions), p.slot) for p in unseen]
    return list(shown) + kept, [p.name for p in unseen]


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
    counted, traded, back = [], [], []
    for p in new:
        last_mine = state["seen_mine"].get(str(p.id))
        if last_mine and (date - dt.date.fromisoformat(last_mine)).days <= SEEN_MINE_DAYS:
            back.append(p.name)  # a correction: the bot had lost him, he never left
        elif p.id in owner:
            teams.remove_player(league, owner[p.id], p.id)
            traded.append(f"{p.name} ({owner[p.id]})")
        elif gm_state.record_add(state, p.id, p.name, date, "roster"):
            counted.append(p.name)
    lines = []
    if counted:
        lines.append(f"Counted as adds: {', '.join(counted)} ({MAX_ADDS_PER_SEASON - len(state['adds'])} left).")
    if back:
        lines.append(f"Back on your roster, not counted as adds (yours within {SEEN_MINE_DAYS} days): "
                     f"{', '.join(back)}.")
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
    teams.set_team(league, team, found.players, common.nhl_today())
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
