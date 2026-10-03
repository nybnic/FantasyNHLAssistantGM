"""The evening briefing (a diff against Yahoo's Start Active), /trade, following NHL trades,
and the daily data check.
"""
from __future__ import annotations

import datetime as dt
import logging


from clients import dfo_lines, goalie_client, health, nhl_client
from config.league import (MY_TEAM, TRADE_DEADLINE)
from engine import briefing, ir, trade
from league import draft, teams
from league import roster as roster_mod
from model import context
from bot.common import NHL_TIME, Outbox, _safe, free_agents, _weakest

logger = logging.getLogger(__name__)

def sync_teams(players: list) -> None:
    """Follow trades: take each player's current NHL team."""
    team_of = {p["id"]: p["team"] for p in nhl_client.current_rosters()}
    for p in players:
        p.team = team_of.get(p.id, p.team)


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
    days = trade.horizon(date)
    schedule = nhl_client.games_between(days[0], days[-1]) if days else {}
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
                "send League > Transactions screenshots (or /opp with their page) if it has changed.")
    outbox.send(trade.text(result) + note)


def alert_health(state: dict, outbox: Outbox, now: dt.datetime) -> None:
    """Say which data sources had trouble this run, once a day per source."""
    today = now.date().isoformat()
    new = {s: d for s, d in health.problems().items() if state["health_alerts"].get(s) != today}
    if not new:
        return
    for source in new:
        state["health_alerts"][source] = today
    outbox.send("Data check:\n" + "\n".join(health.describe(s, d) for s, d in new.items()))
