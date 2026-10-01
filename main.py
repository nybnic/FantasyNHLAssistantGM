"""Assistant GM entrypoint, run every 30 minutes by GitHub Actions, and right
after each Telegram message when the webhook relay (relay/) is set up.

Each run:
1. reads your Telegram taps and commands - Done/Skip on recommendations,
   /roster, /myteam (paste your Yahoo team page), /week, /opp (paste a team's
   Yahoo page), /taken, /trade, /help;
2. on the first day of each fantasy week (and on /week), sends the matchup
   plan: expected score and win odds vs this week's opponent, the goalie
   minimum, and the add/drops worth making;
3. once tonight's briefing is due, plans tonight's lineup and sends it if a
   change is worth >= 0.5 expected points (or the full lineup until one has
   been confirmed). Later runs send at most one update, if new information
   (goalie confirmations, injuries) makes a clearly better lineup;
4. on a failure, alerts you once a day.

Notify-only: it never touches Yahoo.

    python main.py                        # normal run (needs TELEGRAM_* env vars)
    python main.py --dry-run --force      # print tonight's plan now, send nothing
    python main.py --dry-run --force --now 2026-01-15T20:00:00+02:00   # replay a past night
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from clients import dfo_lines, goalie_client, nhl_client, screenshot
from clients.names import normalize_name
from config.league import MAX_ADDS_PER_SEASON, MY_TEAM, POST_DRAFT_WAIVERS_CLEAR, SCHEDULE, TRADE_DEADLINE
from config.settings import Settings, load_settings
from engine import briefing, matchup, report, trade
from league import draft, parse, teams, weeks
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
    "/opp - then paste your opponent's Yahoo team page, to update their roster "
    "(/opp Team Name for another team or a playoff opponent)\n"
    "/taken Name - a free agent I suggested is on someone's roster\n"
    "/trade - trades worth proposing. /trade Knight for Bouchard - what one trade does to you and to them "
    "(several players: Knight, Tuch for Makar)\n"
    "Tap Done on a recommendation once you've made it in Yahoo, or Skip."
)
MYTEAM_HINT = "\n\nWrong? Send screenshots of your Yahoo team page, or /myteam and paste its text."
# Judgment call: screenshots sent within this of the previous one are one team page.
SCREENSHOT_WINDOW = dt.timedelta(minutes=5)
CHART_DIR = Path("data/charts")  # where a dry run saves the charts it would send
CHART_WEEKS = 5  # an add's chart shows this many weeks after the current one
WEEKLY_PLAN_TIME = dt.time(12, 0)  # local, on the week's first day: before any NHL game


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
            if action == "done" and rec["type"] == "add":
                apply_add(players, rec)
            state["decisions"].append({
                "rec_id": rec_id, "type": rec["type"], "date": rec["date"], "decision": action,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            })
            label = "Recorded: Done" if action == "done" else "Recorded: Skipped"
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
                myteam_command((rest + "\n" + body).strip(), state, players, outbox)
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
            elif state["awaiting"] and not text.startswith("/"):
                team, state["awaiting"] = state["awaiting"], None
                if team == MY_TEAM:
                    update_my_roster(text, players, outbox)
                else:
                    update_team(team, text, league, outbox)
    if "team" in new_screenshots:
        finish_screenshots(state, players, league, outbox)
    if "matchup" in new_screenshots:
        finish_matchup(state, players, league, outbox)
    return problem


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
    changes = roster_mod.replace(players, found.players, found.tagged)
    lines.append("Your roster: " + ("; ".join(changes) if changes else "same as I had."))
    if opponent and theirs:
        before = {p.id: p.name for p in teams.players(league, opponent)}
        found_them = parse.match_shown_names(theirs, registry, set(before))
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
        apply_my_roster(found, players, outbox)
    else:
        save_team(team, found, league, outbox)


def apply_add(players: list, rec: dict) -> None:
    """Done on an add/drop: your roster changes the same way."""
    known = roster_mod.lineup_known(players)
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


def myteam_command(paste: str, state: dict, players: list, outbox: Outbox) -> None:
    """/myteam, with your Yahoo team page pasted below it or in the next message."""
    if paste:
        update_my_roster(paste, players, outbox)
    else:
        state["awaiting"] = MY_TEAM
        outbox.send("OK - now send screenshots of your Yahoo team page, or paste its text "
                    "(or one player per line with the slot first: \"BN Nathan MacKinnon\").")


def update_my_roster(paste: str, players: list, outbox: Outbox) -> None:
    found = parse.find_players(paste, parse.registry())
    n = len(found.players)
    if not roster_mod.MIN_PASTED <= n <= roster_mod.MAX_PLAYERS:
        problems = "\nCouldn't place: " + "; ".join(found.problems) if found.problems else ""
        outbox.send(f"I found {n} players, but a roster has {roster_mod.MIN_PASTED}-{roster_mod.MAX_PLAYERS}. "
                    f"Your roster is unchanged: paste the whole team page.{problems}")
        return
    apply_my_roster(found, players, outbox)


def apply_my_roster(found: parse.Found, players: list, outbox: Outbox) -> None:
    changes = roster_mod.replace(players, found.players, found.tagged)
    lines = [f"Roster saved: {len(found.players)} players."] + (changes or ["Same as I had."])
    if found.problems:
        lines.append("Couldn't place: " + "; ".join(found.problems))
    outbox.send("\n".join(lines) + "\n\n" + roster_mod.describe(players))


def update_team(team: str, paste: str, league: dict, outbox: Outbox) -> None:
    found = parse.find_players(paste, parse.registry())
    if not found.players:
        outbox.send(f"I couldn't find any players in that. {team}'s roster is unchanged.")
        return
    save_team(team, found, league, outbox)


def save_team(team: str, found: parse.Found, league: dict, outbox: Outbox) -> None:
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
    except Exception:
        logger.warning("%s failed; continuing without it", getattr(fetch, "__name__", fetch), exc_info=True)
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

    if record is None:
        worth_sending = result.current is None or result.gain >= briefing.MIN_GAIN
        message_text = briefing.text(result) if worth_sending else None
        state["briefings"][key] = {"sent": worth_sending, "recommended": result.optimal, "updates": 0}
    else:
        baseline = {int(pid): slot for pid, slot in record["recommended"].items()}
        worth_sending = result.value_of(result.optimal) - result.value_of(baseline) >= briefing.UPDATE_GAIN
        message_text = briefing.text(result, update_of=baseline) if worth_sending else None
        if worth_sending:
            record["recommended"] = result.optimal
            record["updates"] += 1

    if not message_text:
        logger.info("Lineup for %s already optimal (gain %.2f); nothing sent", key, result.gain)
        return
    rec_id = f"lineup-{key}-{now:%H%M}"
    message_id = outbox.send(message_text, [("Done", f"done:{rec_id}"), ("Skip", f"skip:{rec_id}")])
    state["pending"][rec_id] = {"type": "lineup", "date": key, "assignment": result.optimal, "message_id": message_id}


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
    taken = teams.rostered_ids(league) | {p.id for p in players}
    return [
        roster_mod.RosterPlayer(p["id"], p["name"], p["team"], [parse.NHL_TO_YAHOO_POS[p["position"]]])
        for p in nhl_client.current_rosters() if p["id"] not in taken
    ]


@dataclass
class WeekInputs:
    """Everything the weekly plan is computed from (also used by
    scripts/explain_week.py)."""
    ctx: object
    days: list[dt.date]
    schedule: dict
    future: dict  # the two weeks after this one, for long-run value
    lines: dict
    starters: dict
    me: matchup.TeamWeek
    them: matchup.TeamWeek
    pool: list
    season_used: int
    week_used: int
    max_moves: int
    threshold: float | None
    weeks_after: int
    available_from: dt.date | None
    so_far: tuple | None = None  # my banked points from a matchup screenshot (else box scores)
    yahoo_projected: list | None = None  # Yahoo's projected finals, from the same screenshot


def week_inputs(date: dt.date, week: int, players: list, league: dict, state: dict,
                build_context, opponent: str) -> WeekInputs:
    ctx = build_context(date)
    days = weeks.days(week)
    schedule = {d: nhl_client.games_on(d) for d in days}
    future_days = [days[-1] + dt.timedelta(days=i) for i in range(1, 15)]
    future = {d: nhl_client.games_on(d) for d in future_days if weeks.week_of(d)}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    starters = _safe(goalie_client.get_starters, date, default={})
    season_used, week_used = matchup.adds_used(state["decisions"], days)
    them_roster = teams.players(league, opponent)
    mine = theirs = projected = None
    live = state["live_score"]
    if live and live["week"] == week and live["opponent"] == opponent and live["through"] == date.isoformat():
        mine = _banked(live["score"][0], live["goalies"][0], players, ctx, days)
        theirs = _banked(live["score"][1], live["goalies"][1], them_roster, ctx, days)
        projected = live["projected"] or None
    return WeekInputs(
        ctx=ctx, days=days, schedule=schedule, future=future, lines=lines, starters=starters,
        me=matchup.project(MY_TEAM, players, ctx, schedule, lines, starters, so_far=mine),
        them=matchup.project(opponent, them_roster, ctx, schedule, lines, starters, so_far=theirs),
        so_far=mine, yahoo_projected=projected,
        pool=free_agents(players, league),
        season_used=season_used, week_used=week_used,
        max_moves=matchup.max_moves(season_used, week_used),
        threshold=matchup.add_threshold(MAX_ADDS_PER_SEASON - season_used, week),
        weeks_after=weeks.LAST_WEEK - week,
        available_from=POST_DRAFT_WAIVERS_CLEAR if date < POST_DRAFT_WAIVERS_CLEAR else None,
    )


def _banked(total: float, goalie: float | None, roster: list, ctx, days: list[dt.date]) -> tuple[float, float, int]:
    """(skater points, goalie points, goalie games) so far: Yahoo's score,
    split by the G-slot rows (else by box scores); goalie games from box scores."""
    skater_bs, goalie_bs, goalie_games = matchup._so_far(roster_mod.active(roster), ctx, days)
    goalie = goalie_bs if goalie is None else goalie
    return total - goalie, goalie, goalie_games


def _week_schedule(week: int) -> dict[dt.date, list]:
    return {d: nhl_client.games_on(d) for d in weeks.days(week)}


def _send_week_charts(state: dict, players: list, league: dict, week: int, wk: WeekInputs, ranked: list,
                      moves: list, outbox: Outbox) -> None:
    """The week chart (win odds with each add, the race) and the schedule grid
    for this week and next. A chart that fails is logged and skipped."""
    png = _safe(lambda: charts.week_chart(report.week_view(week, wk.me, wk.them, ranked, moves)))
    if png:
        outbox.send_photo(png)

    def schedule() -> bytes:
        spans = [(week, current_opponent(state, week), wk.me, wk.them)]
        if week < weeks.LAST_WEEK:
            nxt = week + 1
            sched = _week_schedule(nxt)
            opp = current_opponent(state, nxt)
            mine = matchup.project(MY_TEAM, players, wk.ctx, sched, wk.lines, wk.starters, True)
            theirs = matchup.project(opp or "?", teams.players(league, opp) if opp else [], wk.ctx, sched,
                                     wk.lines, wk.starters, True)
            spans.append((nxt, opp or "?", mine, theirs))
        return charts.schedule_chart(report.schedule_view(players, spans))

    png = _safe(schedule)
    if png:
        outbox.send_photo(png)


def _add_chart(players: list, move, week: int, wk: WeekInputs, budget: dict) -> bytes:
    later = {w: _week_schedule(w) for w in range(week + 1, min(week + CHART_WEEKS, weeks.LAST_WEEK) + 1)}
    gains = report.weekly_gains(players, move, wk.ctx, later, wk.lines, wk.starters)
    return charts.add_chart(report.add_view(move, week, gains, budget))


def plan_due(record: dict | None, date: dt.date, week: int) -> bool:
    """The plan goes out once at the start of a week and once from mid-week."""
    if record is None:
        return True
    return date >= weeks.midweek(week) and "midweek" not in record


def weekly_step(state: dict, players: list, league: dict, now: dt.datetime, force: bool, outbox: Outbox,
                build_context=context.build) -> None:
    """The matchup plan: from noon local on the week's first day, again from
    noon on its Wednesday (with the mid-week stance), and whenever you send
    /week or a matchup screenshot."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    requested, state["week_requested"] = state["week_requested"], False
    if week is None or not roster_mod.active(players):
        if requested:
            outbox.send("No roster yet." if not players else "No fantasy week in progress.")
        return
    key = str(week)
    if not (force or requested):
        if not plan_due(state["weeks"].get(key), date, week) or briefing.quiet(now) \
                or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    record = state["weeks"].setdefault(key, {"sent": now.isoformat(timespec="minutes")})
    is_midweek = date >= weeks.midweek(week)
    if is_midweek:
        record["midweek"] = now.isoformat(timespec="minutes")
    opponent = current_opponent(state, week)
    if not opponent:
        outbox.send(f"Week {week} is a playoff week: who are you playing? Send /opp Team Name.")
        return

    wk = week_inputs(date, week, players, league, state, build_context, opponent)
    candidates = matchup.shortlist(wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.available_from)
    ranked = matchup.candidate_moves(players, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters,
                                     wk.future, wk.weeks_after, wk.available_from, wk.so_far)
    moves = []
    if wk.max_moves and wk.threshold is not None:
        moves = matchup.best_moves(players, wk.them, wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future,
                                   wk.weeks_after, wk.max_moves, wk.threshold, wk.available_from, wk.so_far,
                                   candidates, ranked)
    midweek = None
    if is_midweek or wk.so_far is not None:
        chase = next((m for m in moves if m.win_after - m.win_before >= matchup.MIN_WIN_GAIN), None)
        recommended = chase is not None
        if not chase and matchup.stance(matchup.win_prob(wk.me, wk.them)) == "chase":
            chase = matchup.biggest_swing(ranked)
        midweek = matchup.midweek_text(wk.me, wk.them, chase, wk.threshold if wk.max_moves else None, recommended)
    outbox.send(matchup.text(week, wk.days, wk.me, wk.them, teams.updated(league, opponent), wk.season_used,
                             wk.week_used, date, wk.yahoo_projected)
                + (f"\n\n{midweek}" if midweek else "")
                + ("" if moves else "\n\nNo free agent is worth one of your adds right now."))
    _send_week_charts(state, players, league, week, wk, ranked, moves, outbox)
    budget = report.budget_view(state["decisions"], week)
    for i, move in enumerate(moves):
        rec_id = f"add-{date.isoformat()}-{now:%H%M}-{i}"
        buttons = [("Done", f"done:{rec_id}"), ("Skip", f"skip:{rec_id}")]
        png = _safe(_add_chart, players, move, week, wk, budget)
        message_id = (outbox.send_photo(png, matchup.move_text(move), buttons) if png
                      else outbox.send(matchup.move_text(move), buttons))
        state["pending"][rec_id] = {"type": "add", "date": date.isoformat(), "add": asdict(move.add),
                                    "drop": move.drop.id if move.drop else None, "message_id": message_id}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print messages; send and save nothing")
    parser.add_argument("--force", action="store_true", help="plan tonight's lineup regardless of the time")
    parser.add_argument("--now", help="pretend it's this ISO time (with offset), e.g. for replays")
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

    try:
        if not settings.dry_run:
            webhook_problem = sync_webhook(settings, now)
            relay_problem = process_updates(settings, state, players, league, outbox)
            report_relay(webhook_problem or relay_problem, state, outbox, now)
        if players:
            sync_teams(players)
        trade_step(state, players, league, now, outbox, build_context)
        weekly_step(state, players, league, now, args.force, outbox, build_context)
        briefing_step(state, players, now, args.force, outbox, build_context)
    except Exception as exc:
        today = now.date().isoformat()
        if not settings.dry_run and state["last_error"] != today:
            state["last_error"] = today
            detail = str(exc).replace(settings.telegram_bot_token, "<token>") if settings.telegram_bot_token else exc
            _safe(outbox.send, f"Assistant GM run failed: {type(exc).__name__}: {detail}")
        raise
    finally:
        if not settings.dry_run:
            roster_mod.save(players)
            teams.save(league)
            gm_state.save(state, now.date())


if __name__ == "__main__":
    main()
