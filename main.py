"""Assistant GM entrypoint, run every 30 minutes by GitHub Actions, and right
after each Telegram message when the webhook relay (relay/) is set up.

Each run, step by step (a failing step doesn't stop the others; Nico gets
one alert a day):
1. Telegram in (bot/ingest.py): taps on recommendations, commands (/roster,
   /myteam, /week, /opp, /taken, /trade, /help), pasted Yahoo pages and
   screenshots;
2. today's rosters, kept until the first puck (banked points), and the NHL
   teams of my players (trades);
3. /trade (bot/daily.py);
4. on a new week's first day, last week's result (bot/weekly.py);
5. the weekly plan from noon on the week's first day, again from Wednesday
   noon, and on /week: expected score, win odds, the goalie minimum, the
   add/drops worth making (a plan that fails stays due for the next run);
6. the evening briefing, a diff against Yahoo's Start Active (bot/daily.py);
7. the evening news check: an add that newly clears the price;
8. the data check: sources down or stale, once a day.

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
from typing import Callable

from bot.common import Outbox, _safe, nhl_today, remember_mine
from bot.daily import alert_health, briefing_step, sync_teams, trade_step
from bot.ingest import process_updates, report_relay, sync_webhook
from bot.weekly import news_step, report_step, snapshot_rosters, weekly_step
from clients import health
from config.settings import Settings, load_settings
from league import roster as roster_mod
from league import teams
from model import context
from state import gm_state, repairs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
    repairs.apply(state, players)
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
        ("who's mine", lambda: remember_mine(state, players, nhl_today())),
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
