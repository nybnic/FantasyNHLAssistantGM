"""Assistant GM entrypoint, run every 30 minutes by GitHub Actions, and right
after each Telegram message when the webhook relay (relay/) is set up.

Each run:
1. reads your Telegram taps and commands - Done/Skip on recommendations,
   /roster, /week, /opp (paste a team's Yahoo page), /taken, /trade, /help;
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
from zoneinfo import ZoneInfo

from clients import dfo_lines, goalie_client, nhl_client
from clients.names import normalize_name
from config.league import MAX_ADDS_PER_SEASON, MY_TEAM, POST_DRAFT_WAIVERS_CLEAR, SCHEDULE, TRADE_DEADLINE
from config.settings import Settings, load_settings
from engine import briefing, matchup, trade
from league import parse, teams, weeks
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
    "/week - this week's matchup: expected score, win odds, adds worth making\n"
    "/opp - then paste your opponent's Yahoo team page, to update their roster "
    "(/opp Team Name for another team or a playoff opponent)\n"
    "/taken Name - a free agent I suggested is on someone's roster\n"
    "/trade Knight for Bouchard - what a trade does to you and to them "
    "(several players: Knight, Tuch for Makar)\n"
    "Tap Done on a recommendation once you've made it in Yahoo, or Skip."
)
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
            text = message.get("text", "").strip()
            first_line, _, body = text.partition("\n")
            command, _, rest = first_line.partition(" ")
            command = command.lower().split("@")[0]
            if command == "/roster":
                outbox.send(roster_mod.describe(players) or "No roster yet - run scripts/seed_roster.py.")
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
            elif state["awaiting"] and not text.startswith("/"):
                team, state["awaiting"] = state["awaiting"], None
                update_team(team, text, league, outbox)
    return problem


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
        outbox.send(f"OK - now paste {team}'s Yahoo team page (copy the whole page; any format works).")


def update_team(team: str, paste: str, league: dict, outbox: Outbox) -> None:
    found = parse.find_players(paste, parse.registry())
    if not found.players:
        outbox.send(f"I couldn't find any players in that. {team}'s roster is unchanged.")
        return
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
    """Answer /trade: both teams' points per week before and after."""
    request, state["trade_request"] = state["trade_request"], None
    if request is None:
        return
    date = now.astimezone(NHL_TIME).date()
    if date > TRADE_DEADLINE:
        outbox.send(f"The trade deadline was {TRADE_DEADLINE:%d %b}.")
        return
    rosters = {MY_TEAM: players, **{t: teams.players(league, t) for t in league["teams"]}}
    parsed = trade.resolve(request, MY_TEAM, rosters)
    if isinstance(parsed, str):
        outbox.send(parsed)
        return
    give, get, partner = parsed
    schedule = {d: nhl_client.games_on(d) for d in trade.horizon(date) if weeks.week_of(d)}
    if not schedule:
        outbox.send("No regular-season games left to judge a trade on.")
        return
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    result = trade.evaluate(players, rosters[partner], partner, give, get, free_agents(players, league),
                            build_context(date), schedule, lines, starters={})
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
    return WeekInputs(
        ctx=ctx, days=days, schedule=schedule, future=future, lines=lines, starters=starters,
        me=matchup.project(MY_TEAM, players, ctx, schedule, lines, starters),
        them=matchup.project(opponent, teams.players(league, opponent), ctx, schedule, lines, starters),
        pool=free_agents(players, league),
        season_used=season_used, week_used=week_used,
        max_moves=matchup.max_moves(season_used, week_used),
        threshold=matchup.add_threshold(MAX_ADDS_PER_SEASON - season_used, week),
        weeks_after=weeks.LAST_WEEK - week,
        available_from=POST_DRAFT_WAIVERS_CLEAR if date < POST_DRAFT_WAIVERS_CLEAR else None,
    )


def weekly_step(state: dict, players: list, league: dict, now: dt.datetime, force: bool, outbox: Outbox,
                build_context=context.build) -> None:
    """The matchup plan, once per fantasy week (the first run from noon
    local on its first day) and whenever you send /week."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    requested, state["week_requested"] = state["week_requested"], False
    if week is None or not roster_mod.active(players):
        if requested:
            outbox.send("No roster yet." if not players else "No fantasy week in progress.")
        return
    key = str(week)
    if not (force or requested):
        if key in state["weeks"] or briefing.quiet(now) or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    state["weeks"][key] = {"sent": now.isoformat(timespec="minutes")}
    opponent = current_opponent(state, week)
    if not opponent:
        outbox.send(f"Week {week} is a playoff week: who are you playing? Send /opp Team Name.")
        return

    wk = week_inputs(date, week, players, league, state, build_context, opponent)
    moves = []
    if wk.max_moves and wk.threshold is not None:
        moves = matchup.best_moves(players, wk.them, wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future,
                                   wk.weeks_after, wk.max_moves, wk.threshold, wk.available_from)
    outbox.send(matchup.text(week, wk.days, wk.me, wk.them, teams.updated(league, opponent), wk.season_used,
                             wk.week_used, date)
                + ("" if moves else "\n\nNo free agent is worth one of your adds right now."))
    for i, move in enumerate(moves):
        rec_id = f"add-{date.isoformat()}-{now:%H%M}-{i}"
        message_id = outbox.send(matchup.move_text(move), [("Done", f"done:{rec_id}"), ("Skip", f"skip:{rec_id}")])
        state["pending"][rec_id] = {"type": "add", "date": date.isoformat(), "add": asdict(move.add),
                                    "drop": move.drop.id if move.drop else None, "message_id": message_id}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print messages; send and save nothing")
    parser.add_argument("--force", action="store_true", help="plan tonight's lineup regardless of the time")
    parser.add_argument("--now", help="pretend it's this ISO time (with offset), e.g. for replays")
    parser.add_argument("--trade", help='judge a trade, as /trade does: "Knight for Bouchard"')
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
    if args.trade:
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
