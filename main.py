"""Assistant GM entrypoint, run every 30 minutes by GitHub Actions, and right
after each Telegram message when the webhook relay (relay/) is set up.

Each run:
1. reads your Telegram taps - Done/Skip on recommendations, /roster, /help;
2. once tonight's briefing is due, plans tonight's lineup and sends it if a
   change is worth >= 0.5 expected points (or the full lineup until one has
   been confirmed). Later runs send at most one update, if new information
   (goalie confirmations, injuries) makes a clearly better lineup;
3. on a failure, alerts you once a day.

Notify-only: it never touches Yahoo.

    python main.py                        # normal run (needs TELEGRAM_* env vars)
    python main.py --dry-run --force      # print tonight's plan now, send nothing
    python main.py --dry-run --force --now 2026-01-15T20:00:00+02:00   # replay a past night
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from clients import dfo_lines, goalie_client, nhl_client
from config.settings import Settings, load_settings
from engine import briefing
from league import roster as roster_mod
from model import context
from notify import telegram
from state import gm_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NHL_TIME = ZoneInfo("America/New_York")
HELP = (
    "Assistant GM commands:\n"
    "/roster - the roster I think you have (tell me if it's wrong)\n"
    "Tap Done on a recommendation once you've made it in Yahoo, or Skip."
)


class Outbox:
    """Sends to Telegram, or prints in a dry run."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> int | None:
        if self.settings.dry_run:
            print(f"\n----- Telegram message{' [' + ' | '.join(b[0] for b in buttons) + ']' if buttons else ''}\n{text}")
            return None
        return telegram.send_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text, buttons)


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


def process_updates(settings: Settings, state: dict, players: list, outbox: Outbox) -> str | None:
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
            command = message.get("text", "").strip().split(" ")[0].lower()
            if command == "/roster":
                outbox.send(roster_mod.describe(players) or "No roster yet - run scripts/seed_roster.py.")
            elif command in ("/help", "/start"):
                outbox.send(HELP)
    return problem


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


def briefing_step(state: dict, players: list, now: dt.datetime, force: bool, outbox: Outbox) -> None:
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

    ctx = context.build(date)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print messages; send and save nothing")
    parser.add_argument("--force", action="store_true", help="plan tonight's lineup regardless of the time")
    parser.add_argument("--now", help="pretend it's this ISO time (with offset), e.g. for replays")
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

    try:
        if not settings.dry_run:
            webhook_problem = sync_webhook(settings, now)
            relay_problem = process_updates(settings, state, players, outbox)
            report_relay(webhook_problem or relay_problem, state, outbox, now)
        if players:
            sync_teams(players)
        briefing_step(state, players, now, args.force, outbox)
    except Exception as exc:
        today = now.date().isoformat()
        if not settings.dry_run and state["last_error"] != today:
            state["last_error"] = today
            _safe(outbox.send, f"Assistant GM run failed: {type(exc).__name__}: {exc}")
        raise
    finally:
        if not settings.dry_run:
            roster_mod.save(players)
            gm_state.save(state, now.date())


if __name__ == "__main__":
    main()
