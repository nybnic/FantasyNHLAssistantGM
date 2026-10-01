"""Tonight's lineup plan and the evening briefing message.

Timing (per-game lock, user in Finland): the briefing goes out 19:30-20:30
local time, earlier only if the day's first puck drop is within the hour
(weekend matinees); a missed window means no briefing that day, but updates
can follow until 23:00. Nothing is sent 23:00-08:00.
Players whose game has already started are locked where they are.

The briefing is a diff against Yahoo's Start Active Players, which Nico taps
first: it starts everyone with a game tonight, as far as slots allow. What
it can't know, the bot says: a goalie who isn't starting, a scratched or
injured player, and on an overflow night (more players with games than
slots) who should sit. Assumed: Start Active ignores injury tags and, on an
overflow night, sits the lower-ranked player (modeled by season value).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from clients.dfo_lines import LineInfo
from clients.names import normalize_name
from clients.nhl_client import ScheduledGame
from config.league import STARTERS, TIMEZONE
from engine import availability, lineup
from league.roster import BENCH, RosterPlayer, active, lineup_known
from model.context import ModelContext

LOCAL = ZoneInfo(TIMEZONE)
# Nico's window (2026-10-01): the briefing goes out 19:30-20:30 local, earlier
# only if a game starts within the hour. Missed window = no briefing that day.
BRIEFING_TIME = dt.time(19, 30)
BRIEFING_CLOSES = dt.time(20, 30)
EARLIEST_BRIEFING = dt.time(12, 0)
LEAD_TIME = dt.timedelta(minutes=60)
QUIET_START = dt.time(23, 0)
QUIET_END = dt.time(8, 0)
MIN_GAIN = 0.5  # expected points; smaller changes aren't worth a message
UPDATE_GAIN = 1.0  # after the briefing, only a clearly better lineup is worth a ping
GOALIE_SEASON_VALUE = 9.0  # tie-break only: typical points per start


@dataclass
class PlayerTonight:
    player: RosterPlayer
    game: ScheduledGame | None
    value: float  # expected points tonight
    note: str
    locked: bool
    season: float = 0.0  # expected points per game, the overflow tie-break

    @property
    def opponent_text(self) -> str:
        if not self.game:
            return "no game"
        home = self.game.home == self.player.team
        return f"vs {self.game.away}" if home else f"@ {self.game.home}"


@dataclass
class LineupPlan:
    date: dt.date
    first_start: dt.datetime | None
    players: list[PlayerTonight]
    optimal: dict[int, str]
    current: dict[int, str] | None  # None until a lineup has been confirmed
    gain: float  # optimal minus current, expected points tonight
    start_active: dict[int, str] = field(default_factory=dict)  # what Yahoo's Start Active would set

    def value_of(self, assignment: dict[int, str]) -> float:
        return sum(p.value for p in self.players if assignment.get(p.player.id, BENCH) != BENCH)


def briefing_due(date: dt.date, first_start: dt.datetime) -> dt.datetime:
    evening = dt.datetime.combine(date, BRIEFING_TIME, LOCAL)
    earliest = dt.datetime.combine(date, EARLIEST_BRIEFING, LOCAL)
    return max(min(evening, first_start - LEAD_TIME), earliest)


def briefing_closed(now: dt.datetime, date: dt.date) -> bool:
    """Too late for tonight's first briefing (later updates are still allowed)."""
    return now >= dt.datetime.combine(date, BRIEFING_CLOSES, LOCAL)


def quiet(now: dt.datetime) -> bool:
    local = now.astimezone(LOCAL).time()
    return local >= QUIET_START or local < QUIET_END


def plan(
    roster: list[RosterPlayer],
    ctx: ModelContext,
    date: dt.date,
    games: list[ScheduledGame],
    team_lines: dict[str, dict[str, LineInfo]],
    dfo_starters: dict[str, dict],
    now: dt.datetime,
) -> LineupPlan:
    game_of = {}
    for g in games:
        game_of[g.home] = game_of[g.away] = g
    players = []
    candidates = []
    active_candidates = []  # as Start Active sees them: a game or not
    capacity = dict(STARTERS)
    known = lineup_known(roster)
    for p in active(roster):
        game = game_of.get(p.team)
        lines = team_lines.get(p.team, {})
        info = lines.get(normalize_name(p.name))
        if p.is_goalie:
            avail = availability.goalie(
                p.id, p.name, date, info, dfo_starters.get(p.team),
                ctx.team_starts.get(p.team, []), ctx.prior_start_share(p.id),
            )
            per_start = ctx.goalie_start(p.id, p.team, game.away if game.home == p.team else game.home,
                                         game.home == p.team)["xfp"] if game else 0.0
            val, season = avail.prob * per_start, (ctx.prior_start_share(p.id) or 0.5) * GOALIE_SEASON_VALUE
        else:
            proj = ctx.skater(p.id, "D" if p.positions == ["D"] else "C")
            avail = availability.skater(info, bool(lines))
            val, season = (avail.prob * proj.xfp if game else 0.0), proj.xfp
        locked = known and game is not None and game.start <= now
        players.append(PlayerTonight(p, game, val, avail.note if game else "", locked, season))
        if locked:
            if p.slot in capacity:
                capacity[p.slot] -= 1
            continue
        candidates.append(lineup.Candidate(p.id, tuple(p.positions), val, season, p.slot))
        active_candidates.append(lineup.Candidate(p.id, tuple(p.positions), 1.0 if game else 0.0, season))

    optimal = lineup.optimize(candidates, capacity)
    start_active = lineup.optimize(active_candidates, capacity)
    for pt in players:
        if pt.locked:
            optimal[pt.player.id] = start_active[pt.player.id] = pt.player.slot
    current = {p.id: p.slot for p in active(roster)} if known else None
    result = LineupPlan(date, games[0].start if games else None, players, optimal, current, 0.0, start_active)
    result.gain = result.value_of(optimal) - (result.value_of(current) if current else 0.0)
    return result


def _row(pt: PlayerTonight, slot: str) -> str:
    extra = f", {pt.note}" if pt.note else ""
    points = f"{pt.value:.1f}" if pt.game else "-"
    return f"{slot:<3} {pt.player.name} {pt.opponent_text}{extra}  {points}"


def start_active_text(result: LineupPlan) -> str:
    """Tonight's briefing: one line when Start Active is fine, else the
    overrides on top of it, each with its reason."""
    day = result.date.strftime("%a %d %b")
    gain = result.value_of(result.optimal) - result.value_of(result.start_active)
    if gain < MIN_GAIN:
        return f"Tonight ({day}): tap Start Active, nothing to change."
    by_id = {pt.player.id: pt for pt in result.players}
    starts, benches = [], []  # slot shuffles between starters change no points: Yahoo sorts those out
    for pid, slot in result.optimal.items():
        before = result.start_active.get(pid, BENCH)
        if before == slot:
            continue
        pt = by_id[pid]
        if before == BENCH:
            note = f", {pt.note}" if pt.note else ""
            starts.append(f"Start {pt.player.name} at {slot} ({pt.opponent_text}{note}): {pt.value:.1f} pts")
        elif slot == BENCH:
            why = pt.note or ("no game" if not pt.game else f"{pt.value:.1f} pts expected")
            benches.append(f"Bench {pt.player.name} ({why})")
    changes = benches + starts
    lines = [f"Tonight ({day}): tap Start Active, then {len(changes)} change{'' if len(changes) == 1 else 's'} "
             f"(+{gain:.1f} expected pts):"] + [f"- {c}" for c in changes]
    if result.first_start:
        lines.append(f"First puck {result.first_start.astimezone(LOCAL):%H:%M} your time. "
                     "Tap Done once it's set in Yahoo.")
    return "\n".join(lines)


def text(result: LineupPlan, update_of: dict[int, str] | None = None) -> str:
    """Briefing message. With `update_of` (the lineup recommended earlier
    tonight), it's a follow-up listing only what changed since then."""
    day = result.date.strftime("%a %d %b")
    by_id = {pt.player.id: pt for pt in result.players}
    lines = []
    baseline = update_of or result.current
    if baseline is None:
        lines.append(f"Set this lineup for tonight ({day}): {result.value_of(result.optimal):.1f} expected pts")
        for slot in (*STARTERS, BENCH):
            for pid, s in result.optimal.items():
                if s == slot:
                    lines.append(_row(by_id[pid], s))
    else:
        gain = result.value_of(result.optimal) - result.value_of(baseline)
        title = "Lineup update" if update_of else "Lineup for tonight"
        lines.append(f"{title} ({day}): +{gain:.1f} expected pts")
        for pid, slot in result.optimal.items():
            before = baseline.get(pid)
            if before == slot:
                continue
            pt = by_id[pid]
            if before == BENCH:
                lines.append("Start " + _row(pt, slot))
            elif slot == BENCH:
                lines.append("Bench " + _row(pt, before or "?"))
            else:
                lines.append(f"Move  {pt.player.name} {before} -> {slot}")
    goalies = [pt for pt in result.players if pt.player.is_goalie and pt.game]
    if goalies:
        lines.append("")
        lines.append("Goalies tonight:")
        for pt in goalies:
            lines.append(f"  {pt.player.name} {pt.opponent_text}: {pt.note}, {pt.value:.1f} expected pts")
    if result.first_start:
        lines.append("")
        lines.append(f"First puck drop {result.first_start.astimezone(LOCAL):%H:%M} your time. "
                     "Tap Done once it's set in Yahoo.")
    return "\n".join(lines)
