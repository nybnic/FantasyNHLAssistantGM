"""The breakout watch (Nico, 2026-10-13): free agents whose role just changed,
flagged as it happens, beside the plan rather than in it. Every run (every 30
minutes, and on each Telegram message) it looks for:
- a goalie whose share of his team's starts jumps because teammates are
  injured (availability.absences: the model's own redistribution), or whom
  DFO now lists as his team's No. 1 while the model has him under half;
- a skater DFO moved up to a top line, the top D pair or the first power
  play since the last run;
- a skater whose ice time jumped: his last 3 games 3+ minutes over the games
  before (model/CLAUDE.md: such players are under-projected by ~0.25-0.3 pts a
  game the next week; the model follows ice time on purpose slowly).
Each signal is valued by the plan (a quiet plan run that scores him, so a
move that now beats the plan changes it with its reason) and sent once, at
most every REALERT_DAYS per player and kind; in quiet hours it waits.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from bot import messages
from bot.common import NHL_TIME, Outbox, _safe, current_opponent, free_agents
from clients import dfo_lines, nhl_client
from clients.injury_news import SEASON
from clients.names import normalize_name
from engine import availability, briefing, matchup
from league import weeks

logger = logging.getLogger(__name__)

SHARE_JUMP = 0.15  # a goalie's start share up this much from injured teammates
NO1_UNDER = 0.5  # DFO's No. 1 while the model gives him less than this share
TOP_GROUPS = ("f1", "f2", "d1", "pp1")  # DFO groups a promotion into is flagged
TOI_JUMP = 180.0  # seconds a game, last 3 over the (up to 10) games before
TOI_MIN_GAMES = 8
REALERT_DAYS = 14
MAX_ALERTS = 3  # per message


@dataclass
class Signal:
    player: object  # RosterPlayer
    kind: str  # "goalie", "no1", "promoted", "ice time"
    reason: str
    numbers: dict = field(default_factory=dict)


def _injured_mates(team: str, pid: int, ctx, lines: dict) -> list[str]:
    out = []
    for mate_id, name in ctx.team_goalies(team):
        info = lines.get(normalize_name(name))
        if mate_id == pid or not info or not availability._injured(info):
            continue
        until = info.out_until
        how = ("out for the season" if until == SEASON else
               f"out until about {messages.date_text(until)}" if until else f"out ({info.injury})")
        out.append(f"{name} {how}")
    return out


def goalie_signals(ctx, fa_goalies: list, lines_by_team: dict, today: dt.date) -> list[Signal]:
    out = []
    for g in fa_goalies:
        lines = lines_by_team.get(g.team, {})
        info = lines.get(normalize_name(g.name))
        if info and availability._injured(info):
            continue
        starts, prior = ctx.team_starts.get(g.team, []), ctx.prior_start_share(g.id)
        before = availability.start_share(g.id, today, info, starts, prior)
        after = availability.start_share(g.id, today, info, starts, prior,
                                         *matchup.goalie_absences(g, ctx, lines, today))
        numbers = {"before": before, "after": after}
        if after - before >= SHARE_JUMP:
            mates = _injured_mates(g.team, g.id, ctx, lines)
            out.append(Signal(g, "goalie", ", ".join(mates) or "a teammate out", numbers))
        elif info and info.goalie_depth == 1 and after < NO1_UNDER:
            out.append(Signal(g, "no1", "DFO lists him as the No. 1", numbers))
    return out


def promotions(fa_skaters: list, lines_by_team: dict, seen: dict) -> tuple[list[Signal], dict]:
    """Skaters moved into TOP_GROUPS since the groups in `seen` (id -> groups);
    returns the signals and the groups now. A skater seen for the first time
    sets his groups without a signal."""
    out, now = [], {}
    for p in fa_skaters:
        info = lines_by_team.get(p.team, {}).get(normalize_name(p.name))
        if not info or availability._injured(info):
            continue
        groups = sorted(info.groups)
        now[str(p.id)] = groups
        before = seen.get(str(p.id))
        new = [g for g in TOP_GROUPS if g in groups and before is not None and g not in before]
        if new:
            out.append(Signal(p, "promoted", "moved up to " + " and ".join(messages.group_text(g) for g in new),
                              {"groups": new}))
    return out, now


def ice_time_signals(ctx, fa_skaters: list) -> list[Signal]:
    out = []
    for p in fa_skaters:
        games = sorted((g for g in ctx.skater_games.get(p.id, []) if g.date < ctx.today), key=lambda g: g.date)
        if len(games) < TOI_MIN_GAMES:
            continue
        last, before = games[-3:], games[-13:-3]
        recent = sum(g.toi for g in last) / len(last)
        usual = sum(g.toi for g in before) / len(before)
        if recent - usual >= TOI_JUMP:
            out.append(Signal(p, "ice time", "more ice time", {"recent": recent, "usual": usual}))
    return out


def due(signals: list[Signal], alerted: dict, today: dt.date) -> list[Signal]:
    """The signals not sent for this player and kind in the last REALERT_DAYS."""
    def fresh(s: Signal) -> bool:
        last = alerted.get(f"{s.player.id}:{s.kind}")
        return last is None or (today - dt.date.fromisoformat(last)).days >= REALERT_DAYS
    return [s for s in signals if fresh(s)]


def watch_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox, build_context,
               value_moves=None) -> list[Signal]:
    """Look for role changes among free agents and say so, once. `value_moves`
    (ids -> PlanMoves) values them; by default a quiet plan run that scores them."""
    today = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(today)
    if week is None or not players:
        return []
    watch = state.setdefault("watch", {"groups": {}, "alerted": {}})
    pool = free_agents(players, league)
    lines_by_team = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    ctx = build_context(today)
    goalies = [p for p in pool if p.is_goalie]
    skaters = [p for p in pool if not p.is_goalie]
    promoted, groups = promotions(skaters, lines_by_team, watch["groups"])
    signals = goalie_signals(ctx, goalies, lines_by_team, today) + promoted + ice_time_signals(ctx, skaters)
    watch["groups"] = groups
    new = due(signals, watch["alerted"], today)
    if not new or briefing.quiet(now):
        return new  # in quiet hours they stay due: the first run after sends them
    new = sorted(new, key=lambda s: s.kind != "goalie")[:MAX_ALERTS]
    values = {}
    if current_opponent(state, week):
        state.setdefault("watch_score", [])
        state["watch_score"] = sorted({s.player.id for s in new})
        run = value_moves or _value_by_plan
        values = _safe(run, state, players, league, now, outbox, build_context, default={}) or {}
        state["watch_score"] = []
    outbox.send(messages.watch_text(new, values))
    for s in new:
        watch["alerted"][f"{s.player.id}:{s.kind}"] = today.isoformat()
    return new


def _value_by_plan(state, players, league, now, outbox, build_context) -> dict:
    """A quiet plan run that also scores the watched players (state["watch_score"]):
    a plan change it brings is sent with its reason. Returns id -> (the best
    move's value, the add price, whether it's in the plan)."""
    from bot import weekly
    p = weekly.run_plan(state, players, league, now, outbox, build_context, "quiet")
    planned = {q.move.add.id for q in p.plan}
    out = {}
    for m in p.ranked:
        best = out.get(m.add.id)
        if best is None or m.value > best[0]:
            out[m.add.id] = (m.value, p.wk.price.lam, m.add.id in planned)
    return out
