"""The breakout watch (Nico, 2026-10-13): free agents whose role just changed,
flagged as it happens, beside the plan rather than in it. Every run (every 30
minutes, and on each Telegram message) it looks for:
- a goalie whose team's starter is injured: at once (Nico, 2026-10-13: "the
  injury info of a starter should weigh much and trigger immediately");
- a goalie expected to start far more than projected before the season: his
  share of the starts ahead (his team's last 10, injured teammates' starts
  redistributed, availability.absences) against his preseason share. Not DFO's
  No. 1 on its own: DFO lists tonight's starter there, so a backup's one start
  made him "No. 1" (Nico, 2026-10-13: "not a watch if a FA gets one start");
- a skater DFO moved up to a top-6 line, the top D pair or the first power
  play, from the first game there (Nico, 2026-10-13);
- a skater whose ice time jumped: his last 3 games 3+ minutes over the games
  before (model/CLAUDE.md: such players are under-projected by ~0.25-0.3 pts a
  game the next week; the model follows ice time on purpose slowly).
Each signal is valued by the plan (a quiet plan run that scores him, so a
move that now beats the plan changes it with its reason) and sent once, at
most every REALERT_DAYS per player, all his signals in one alert; in quiet
hours it waits. A promotion alone is sent only when his best move is worth
ALERT_SHARE of the add price (Nico, 2026-10-14: 26 alerts in a day, mostly
line shuffles); the rest are logged (state.watch.log) for the dashboard.
Goalies and ice-time jumps are always sent.
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

# A goalie's expected share of the starts ahead this much over his preseason
# share, and at least GOALIE_MIN_SHARE: one start moves the blended share ~0.07,
# so it takes an injury or a run of starts (judgment calls).
SHARE_OVER_PROJECTED = 0.20
GOALIE_MIN_SHARE = 0.40
PROMOTION_HOLDS = 0  # days a DFO promotion must last before it's flagged: none (Nico)
STARTER_SHARE = 0.5  # a teammate with this share of the starts is the team's starter
TOP_GROUPS = ("f1", "f2", "d1", "pp1")  # DFO groups a promotion into is flagged
TOI_JUMP = 180.0  # seconds a game, last 3 over the (up to 10) games before
TOI_MIN_GAMES = 8
REALERT_DAYS = 14
MAX_ALERTS = 3  # per message
# A promotion is sent when his best move is worth this share of the add price,
# else only logged (Nico, 2026-10-14: "50% enough"; a judgment call: check it
# against state.watch.log once the DFO promotion test has its weeks).
ALERT_SHARE = 0.5
GATED = ("promoted",)  # the kinds the value gate applies to
LOG_DAYS = 14  # the watch log the dashboard shows


@dataclass
class Signal:
    player: object  # RosterPlayer
    kind: str  # "goalie", "promoted", "ice time"
    reason: str
    numbers: dict = field(default_factory=dict)


def _injured_mates(team: str, pid: int, ctx, lines: dict, today: dt.date) -> list[tuple[str, float]]:
    """His injured teammates: (what DFO says, that teammate's share of the starts)."""
    out = []
    starts = ctx.team_starts.get(team, [])
    for mate_id, name in ctx.team_goalies(team):
        info = lines.get(normalize_name(name))
        if mate_id == pid or not info or not availability._injured(info):
            continue
        until = info.out_until
        how = ("out for the season" if until == SEASON else
               f"out until about {messages.date_text(until)}" if until else f"out ({info.injury})")
        out.append((f"{name} {how}",
                    availability.start_share(mate_id, today, info, starts, ctx.prior_start_share(mate_id))))
    return out


def goalie_signals(ctx, fa_goalies: list, lines_by_team: dict, today: dt.date) -> list[Signal]:
    """Free-agent goalies whose team's starter is injured (at once), or who are
    expected to start far more than projected: the share the plan uses (recent
    starts, injured teammates' starts) against his preseason share (DFO's
    projected starts; without one, his depth's)."""
    out = []
    for g in fa_goalies:
        lines = lines_by_team.get(g.team, {})
        info = lines.get(normalize_name(g.name))
        if info and availability._injured(info):
            continue
        starts, prior = ctx.team_starts.get(g.team, []), ctx.prior_start_share(g.id)
        projected = prior if prior is not None else availability.start_share(g.id, today, info, [], None)
        expected = availability.start_share(g.id, today, info, starts, prior,
                                            *matchup.goalie_absences(g, ctx, lines, today))
        mates = _injured_mates(g.team, g.id, ctx, lines, today)
        starter_out = any(share >= STARTER_SHARE for _, share in mates)
        if not starter_out and (expected - projected < SHARE_OVER_PROJECTED or expected < GOALIE_MIN_SHARE):
            continue
        recent = [pid for d, pid in starts if d < today][-availability.RECENT_STARTS:]
        reason = (", ".join(("his starter " if share >= STARTER_SHARE else "") + text for text, share in mates)
                  or f"he has started {recent.count(g.id)} of his team's last {len(recent)}")
        out.append(Signal(g, "goalie", reason, {"projected": projected, "expected": expected,
                                                "starter_out": starter_out}))
    return out


def promotions(fa_skaters: list, lines_by_team: dict, seen: dict, today: dt.date) -> tuple[list[Signal], dict]:
    """Skaters in TOP_GROUPS who moved there since an earlier run and are still
    there PROMOTION_HOLDS days on. `seen`: id -> {"groups", "since": {group:
    the day he was first seen there, or None when he already was at his first
    sighting}}. Returns the signals and the record now."""
    out, now = [], {}
    for p in fa_skaters:
        info = lines_by_team.get(p.team, {}).get(normalize_name(p.name))
        if not info or availability._injured(info) or p.is_goalie:
            continue
        groups = sorted(info.groups)
        prev = seen.get(str(p.id))
        prev_groups = prev if isinstance(prev, list) else (prev or {}).get("groups", [])
        prev_since = {} if isinstance(prev, list) else (prev or {}).get("since", {})
        since = {}
        for g in TOP_GROUPS:
            if g not in groups:
                continue
            if g in prev_since:
                since[g] = prev_since[g]
            else:
                since[g] = today.isoformat() if prev is not None and g not in prev_groups else None
        now[str(p.id)] = {"groups": groups, "since": since}
        days = {g: (today - dt.date.fromisoformat(d)).days for g, d in since.items() if d}
        held = [g for g, n in days.items() if n >= PROMOTION_HOLDS]
        if held:
            out.append(Signal(p, "promoted", "moved up to " + " and ".join(messages.group_text(g) for g in held),
                              {"groups": held, "days": min(days[g] for g in held)}))
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
    """The signals for players not alerted (any kind) in the last REALERT_DAYS."""
    last = {}
    for k, d in alerted.items():
        pid = k.partition(":")[0]
        last[pid] = max(last.get(pid, d), d)

    def fresh(s: Signal) -> bool:
        d = last.get(str(s.player.id))
        return d is None or (today - dt.date.fromisoformat(d)).days >= REALERT_DAYS
    return [s for s in signals if fresh(s)]


def by_player(signals: list[Signal]) -> list[list[Signal]]:
    """The signals grouped per player, in first-seen order."""
    out: dict[int, list[Signal]] = {}
    for s in signals:
        out.setdefault(s.player.id, []).append(s)
    return list(out.values())


def worth_sending(sigs: list[Signal], value: tuple | None, valued: bool) -> bool:
    """A player's alert goes out unless it's only a promotion (GATED) and the
    plan, which valued it (`valued`), puts his best move under ALERT_SHARE
    of the add price. value: (best move's value, the add price, in the plan)."""
    if not valued or any(s.kind not in GATED for s in sigs):
        return True
    return value is not None and (value[2] or value[0] >= ALERT_SHARE * value[1])


def log_entries(groups: list[list[Signal]], values: dict, sent: bool, today: dt.date) -> list[dict]:
    """The watch log's rows (state.watch.log, shown on the dashboard)."""
    out = []
    for sigs in groups:
        p = sigs[0].player
        v = values.get(p.id)
        out.append({"date": today.isoformat(), "id": p.id, "name": p.name, "team": p.team,
                    "positions": "/".join(p.positions), "kinds": [s.kind for s in sigs],
                    "why": " ".join(messages.watch_why(s) for s in sigs), "verdict": messages.watch_value(v),
                    "value": round(v[0], 4) if v else None, "price": round(v[1], 4) if v else None,
                    "sent": sent})
    return out


def watch_view(state: dict, today: dt.date) -> list[dict]:
    """The dashboard's watch list: the last LOG_DAYS of signals, newest first."""
    log = (state.get("watch") or {}).get("log", [])
    return [r for r in reversed(log) if (today - dt.date.fromisoformat(r["date"])).days < LOG_DAYS]


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
    promoted, groups = promotions(skaters, lines_by_team, watch["groups"], today)
    signals = goalie_signals(ctx, goalies, lines_by_team, today) + promoted + ice_time_signals(ctx, skaters)
    watch["groups"] = groups
    new = due(signals, watch["alerted"], today)
    if not new or briefing.quiet(now):
        return new  # in quiet hours they stay due: the first run after sends them
    values, valued = {}, bool(current_opponent(state, week))
    if valued:  # all of them, so a promotion can be judged before it's sent
        state["watch_score"] = sorted({s.player.id for s in new})
        run = value_moves or _value_by_plan
        values = _safe(run, state, players, league, now, outbox, build_context, default={}) or {}
        state["watch_score"] = []
    groups = by_player(new)
    send = [g for g in groups if worth_sending(g, values.get(g[0].player.id), valued)]
    held = [g for g in groups if g not in send]  # logged, not sent: no re-check, the plan scores him anyway
    send = sorted(send, key=lambda g: (all(s.kind != "goalie" for s in g),
                                       -(values.get(g[0].player.id) or (-9,))[0]))[:MAX_ALERTS]
    if send:
        outbox.send(messages.watch_text([s for g in send for s in g], values))
    for s in [s for g in send + held for s in g]:  # the rest over MAX_ALERTS stay due for the next run
        watch["alerted"][f"{s.player.id}:{s.kind}"] = today.isoformat()
    log = watch.setdefault("log", []) + log_entries(send, values, True, today) + log_entries(held, values, False, today)
    watch["log"] = [r for r in log if (today - dt.date.fromisoformat(r["date"])).days < LOG_DAYS]
    settings = getattr(outbox, "settings", None)
    if settings is not None:  # the dashboard's list now, not at the next plan run
        from bot import weekly
        _safe(weekly.patch_dashboard, {"watch": watch_view(state, today)}, settings.dry_run)
    return [s for g in send for s in g]


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
