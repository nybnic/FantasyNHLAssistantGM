"""This week's head-to-head matchup: both teams' expected points, your
chance of winning, the 3-goalie-game minimum, and the add/drops that help.

The goal is winning the week, not piling up points, so moves are judged by
what they do to P(win):
- Each team's rest-of-week total is its best lineup on each remaining game
  day (the opponent is assumed to set an optimal lineup every day).
- Points so far come from box scores, with the lineup a sensible manager
  would have set that day (best by projection). Yahoo's score can differ by
  the bench decisions actually made.
- Uncertainty: per-game variance is ~2.5 x xFP for skaters and ~21 per
  goalie start (2025-26 game logs), plus an allowance for model error.
  P(win) treats the point difference as normal.
- Missing the goalie minimum zeroes all goalie points, so they count only
  in proportion to the chance of reaching it.
- A move that only helps this week (a streamer) must lift P(win); one that
  makes the roster better for the rest of the season just has to be worth
  the add, and not cost points this week (it can wait a week otherwise).
  Long-run value is the same whole-lineup projection run over the next two
  weeks' schedule (so an empty D slot or thin goalie depth counts), per week,
  times the weeks left, discounted for how much can change. Three goalies
  are always kept. Your 36 adds are paced over the season, with some kept back for
  the playoffs.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from clients.dfo_lines import LineInfo
from clients.names import normalize_name
from clients.nhl_client import ScheduledGame
from config.league import (BENCH_SLOTS, GOALIE_WEIGHTS, MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK,
                           MIN_GOALIE_GAMES_PER_WEEK, REGULAR_SEASON_WEEKS, SKATER_WEIGHTS, STARTERS,
                           fantasy_points)
from engine import availability, lineup
from league.roster import BENCH, RosterPlayer, active

SKATER_VARIANCE_PER_XFP = 2.5
GOALIE_START_VARIANCE = 21.0
TYPICAL_START_XFP = 8.8  # 2025-26 average points per goalie start
MODEL_SD_SHARE = 0.08  # projection error, as a share of a team's rest-of-week points (a guess)

ACTIVE_SPOTS = sum(STARTERS.values()) + BENCH_SLOTS
# Injuries, role changes and later adds make a long-run edge worth less
# than it projects to (a judgment call, not fitted).
LONG_RUN_DISCOUNT = 0.5
# Drops tried: your lowest long-run players per group (forwards, D, goalies).
# Per group, because per-player value ranks D low (fewer points a game), so a
# plain bottom 4 was mostly D and never tried a weak forward (Schenn, 2026-10-01).
DROPS_PER_GROUP = 2
# Keep three goalies: with two, one injury or a light schedule week risks
# the goalie minimum, which zeroes every goalie point that week.
MIN_GOALIES = 3
# Free agents shortlisted per position (so an empty D slot finds a D) by
# this week's value, plus a few by long-run value.
POOL_PER_POSITION = 8
POOL_LONG_TERM_PER_POSITION = 3
# Plus a few per skater position by schedule fit: points on the nights my best
# lineup leaves that slot open, this week and next (streamers).
POOL_FIT_PER_POSITION = 4
STREAMER_POSITIONS = ("C", "LW", "RW", "D")  # no goalie streaming: three goalies kept (decision log)
STREAMER_SHORTLIST = 4  # per position, judged on next week's lineup too
MIN_WIN_GAIN = 0.02  # a this-week-only move must add 2 points of win probability
# A long-run upgrade that costs points this week can wait for a week where it doesn't.
MAX_WEEK_COST = 1.0
BASE_ADD_SCORE = 3.0  # expected points a move must be worth at an even pace of adds
PLAYOFF_RESERVE = 6  # adds kept for playoff weeks
# A week this lopsided is decided: points gained in it don't change the result,
# so an add has to pay off later (adds are capped per season, so a skipped one
# isn't lost). Judgment calls (Nico, 2026-10-01), calibrate with real weeks.
CONCEDE_BELOW = 0.10
COAST_ABOVE = 0.90


@dataclass
class TeamWeek:
    name: str
    so_far: float  # points in the week's finished games
    expected: float  # whole week: so far + rest of week
    variance: float
    player_games: int  # lineup games left this week
    goalie_games_so_far: int
    goalie_starts_left: float  # expected
    goalie_min_prob: float  # chance of reaching MIN_GOALIE_GAMES_PER_WEEK
    # Each remaining day: expected points, and who plays in the best lineup
    # (player id -> (slot or BN, start probability: 1 for skaters)).
    by_day: dict[dt.date, float] = field(default_factory=dict)
    lineups: dict[dt.date, dict[int, tuple[str, float]]] = field(default_factory=dict)


@dataclass
class Move:
    add: RosterPlayer
    drop: RosterPlayer | None  # None when there's an open roster spot
    week_gain: float
    long_term: float  # discounted rest-of-season value gained (negative for a pure streamer)
    next_weeks: float  # gain over the two weeks after this one, undiscounted
    games: int  # the added player's games left this week
    win_before: float
    win_after: float
    week_counts: bool = True  # False when the week is already decided

    @property
    def score(self) -> float:
        return (self.week_gain if self.week_counts else 0.0) + self.long_term


def _position(p: RosterPlayer) -> str:
    return "D" if p.positions == ["D"] else "C"


def _game_of(games: list[ScheduledGame]) -> dict[str, ScheduledGame]:
    out = {}
    for g in games:
        out[g.home] = out[g.away] = g
    return out


def _player_day(p, ctx, date, game, played_yesterday, lines, starters,
                long_run: bool = False) -> tuple[float, float, float]:
    """(expected points, variance, start probability) for one game. Start
    probability is 1 for skaters; goalies' counts toward the minimum.
    `long_run` also discounts skaters by their projected games played, which
    today's injury report can't show for games weeks away."""
    team_lines = lines.get(p.team, {})
    info = team_lines.get(normalize_name(p.name))
    if p.is_goalie:
        team_starts, prior = ctx.team_starts.get(p.team, []), ctx.prior_start_share(p.id)
        prob = availability.goalie(p.id, p.name, date, info, starters.get(p.team) if date == ctx.today else None,
                                   team_starts, prior).prob
        if date > ctx.today and played_yesterday and prob:
            # Who starts the night before isn't known yet.
            prob = availability.second_of_back_to_back(availability.start_share(p.id, date, info, team_starts, prior))
        home = game.home == p.team
        x = ctx.goalie_start(p.id, p.team, game.away if home else game.home, home)["xfp"]
        return prob * x, prob * (GOALIE_START_VARIANCE + x * x) - (prob * x) ** 2, prob
    x = ctx.skater(p.id, _position(p)).xfp
    prob = availability.skater(info, bool(team_lines)).prob
    if long_run:
        prob *= ctx.durability(p.id)
    return prob * x, prob * (SKATER_VARIANCE_PER_XFP * x + x * x) - (prob * x) ** 2, 1.0


def _at_least(probs: list[float], so_far: int, need: int) -> float:
    """P(so_far + number of successes >= need), independent Bernoullis."""
    dist = [1.0]
    for p in probs:
        dist = [(dist[k] if k < len(dist) else 0.0) * (1 - p) + (dist[k - 1] * p if k else 0.0)
                for k in range(len(dist) + 1)]
    return sum(q for k, q in enumerate(dist) if so_far + k >= need)


def _so_far(roster, ctx, days) -> tuple[float, float, int]:
    """(skater points, goalie points, goalie games) in finished games this week."""
    skater_pts = goalie_pts = 0.0
    goalie_games = 0
    for date in (d for d in days if d < ctx.today):
        played = {}
        for p in roster:
            logs = ctx.goalie_games if p.is_goalie else ctx.skater_games
            game = next((g for g in logs.get(p.id, []) if g.date == date), None)
            if game:
                played[p.id] = (p, game)
        candidates = [
            lineup.Candidate(pid, tuple(p.positions),
                             TYPICAL_START_XFP * game.started if p.is_goalie else ctx.skater(pid, _position(p)).xfp)
            for pid, (p, game) in played.items()
        ]
        for pid, slot in lineup.optimize(candidates).items():
            if slot == BENCH:
                continue
            p, game = played[pid]
            if p.is_goalie:
                goalie_pts += fantasy_points(game.stats, GOALIE_WEIGHTS)
                goalie_games += 1
            else:
                skater_pts += fantasy_points(game.stats, SKATER_WEIGHTS)
    return skater_pts, goalie_pts, goalie_games


def project(name: str, roster: list[RosterPlayer], ctx, schedule: dict[dt.date, list[ScheduledGame]],
            lines: dict[str, dict[str, LineInfo]], starters: dict[str, dict], long_run: bool = False,
            joins: dict[int, dt.date] | None = None, so_far: tuple[float, float, int] | None = None) -> TeamWeek:
    """The team's week. `schedule` has every day of the fantasy week; days
    before ctx.today are scored from box scores, the rest projected.
    `joins` holds players who only count from a date on (a waiver claim).
    `so_far` (skater points, goalie points, goalie games) replaces the box
    scores: a trial roster keeps the points the real one banked, since an
    added player's earlier games never count for you."""
    joins = joins or {}
    roster = active(roster)
    days = sorted(schedule)
    skater_so_far, goalie_so_far, goalie_games = so_far or _so_far(roster, ctx, days)
    skater_mean = skater_var = goalie_mean = goalie_var = 0.0
    start_probs: list[float] = []
    player_games = 0
    day_skaters: dict[dt.date, float] = {}
    day_goalies: dict[dt.date, float] = {}
    lineups: dict[dt.date, dict[int, tuple[str, float]]] = {}
    for date in (d for d in days if d >= ctx.today):
        game_of = _game_of(schedule[date])
        yesterday = _game_of(schedule.get(date - dt.timedelta(days=1), []))
        values = {}
        for p in roster:
            game = game_of.get(p.team)
            if game and date >= joins.get(p.id, date):
                values[p.id] = _player_day(p, ctx, date, game, p.team in yesterday, lines, starters, long_run)
        candidates = [lineup.Candidate(p.id, tuple(p.positions), values[p.id][0])
                      for p in roster if p.id in values]
        by_id = {p.id: p for p in roster}
        assignment = lineup.optimize(candidates)
        lineups[date] = {pid: (slot, values[pid][2]) for pid, slot in assignment.items()}
        day_skaters[date] = day_goalies[date] = 0.0
        for pid, slot in assignment.items():
            if slot == BENCH:
                continue
            mean, var, prob = values[pid]
            player_games += 1
            if by_id[pid].is_goalie:
                goalie_mean += mean
                goalie_var += var
                day_goalies[date] += mean
                start_probs.append(prob)
            else:
                skater_mean += mean
                skater_var += var
                day_skaters[date] += mean

    min_prob = _at_least(start_probs, goalie_games, MIN_GOALIE_GAMES_PER_WEEK)
    goalie_total = goalie_so_far + goalie_mean
    # Goalie points only count if the minimum is met (a Bernoulli on top).
    goalie_week_var = min_prob * (goalie_var + goalie_total ** 2) - (min_prob * goalie_total) ** 2
    model_var = (MODEL_SD_SHARE * (skater_mean + goalie_mean)) ** 2
    return TeamWeek(
        name=name,
        so_far=skater_so_far + goalie_so_far,
        expected=skater_so_far + skater_mean + min_prob * goalie_total,
        variance=skater_var + goalie_week_var + model_var,
        player_games=player_games,
        goalie_games_so_far=goalie_games,
        goalie_starts_left=sum(start_probs),
        goalie_min_prob=min_prob,
        by_day={d: day_skaters[d] + min_prob * day_goalies[d] for d in day_skaters},
        lineups=lineups,
    )


def win_prob(me: TeamWeek, them: TeamWeek) -> float:
    sd = math.sqrt(me.variance + them.variance) or 1.0
    return 0.5 * (1 + math.erf((me.expected - them.expected) / (sd * math.sqrt(2))))


def decided(p_win: float) -> str | None:
    """"lost" or "won" when the week is beyond what an add changes, else None."""
    if p_win < CONCEDE_BELOW:
        return "lost"
    if p_win > COAST_ABOVE:
        return "won"
    return None


def stance(p_win: float) -> str:
    """How to play the rest of the week: "lost" or "won" (save adds),
    "chase" when behind, "protect" when ahead."""
    return decided(p_win) or ("chase" if p_win < 0.5 else "protect")


def _group(p: RosterPlayer) -> str:
    return "G" if p.is_goalie else "D" if p.positions == ["D"] else "F"


def drop_candidates(mine: list[RosterPlayer], ctx, lines: dict[str, dict[str, LineInfo]]) -> list[RosterPlayer]:
    """The players worth trying as a drop: the DROPS_PER_GROUP lowest long-run
    value per group, lowest first."""
    ranked = sorted(mine, key=lambda p: season_value(p, ctx, lines))
    picked = []
    for group in ("F", "D", "G"):
        picked += [p for p in ranked if _group(p) == group][:DROPS_PER_GROUP]
    return sorted(picked, key=lambda p: season_value(p, ctx, lines))


def season_value(p: RosterPlayer, ctx, lines: dict[str, dict[str, LineInfo]]) -> float:
    """Expected points per team game over the long run (injuries ignored)."""
    if p.is_goalie:
        info = lines.get(p.team, {}).get(normalize_name(p.name))
        share = availability.start_share(p.id, ctx.today, info, ctx.team_starts.get(p.team, []),
                                         ctx.prior_start_share(p.id))
        return share * TYPICAL_START_XFP
    return ctx.skater(p.id, _position(p)).xfp


def add_threshold(adds_left: int, week: int) -> float | None:
    """Points a move must be worth, given how many adds are left; None when
    the regular-season budget is spent (the rest are for the playoffs)."""
    if week > REGULAR_SEASON_WEEKS:
        return BASE_ADD_SCORE / 2
    spare = adds_left - PLAYOFF_RESERVE
    if spare <= 0:
        return None
    target_rate = (MAX_ADDS_PER_SEASON - PLAYOFF_RESERVE) / REGULAR_SEASON_WEEKS
    pace = spare / (REGULAR_SEASON_WEEKS - week + 1) / target_rate
    return BASE_ADD_SCORE / min(max(pace, 0.5), 2.0)


def _team_games(schedule: dict[dt.date, list[ScheduledGame]], today: dt.date) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in (d for d in schedule if d >= today):
        for team in _game_of(schedule[d]):
            counts[team] = counts.get(team, 0) + 1
    return counts


def open_positions(week: TeamWeek) -> dict[dt.date, set[str]]:
    """The starting slots my best lineup leaves empty, by day."""
    out = {}
    for d, day in week.lineups.items():
        used = [slot for slot, _ in day.values() if slot != BENCH]
        out[d] = {pos for pos, n in STARTERS.items() if used.count(pos) < n}
    return out


def shortlist(pool: list[RosterPlayer], ctx, schedule: dict[dt.date, list[ScheduledGame]],
              lines: dict[str, dict[str, LineInfo]], starters: dict[str, dict],
              available_from: dt.date | None = None, open_days: dict[dt.date, set[str]] | None = None,
              fit_schedule: dict[dt.date, list[ScheduledGame]] | None = None) -> list[RosterPlayer]:
    """The free agents worth a full evaluation: the best per position by this
    week's games, a few by long-run value, and with `open_days` (day -> open
    slots, over `fit_schedule`) a few by points on nights their slot is open."""
    remaining = [d for d in sorted(schedule) if d >= ctx.today and not (available_from and d < available_from)]
    team_games = _team_games(schedule, ctx.today)

    def week_alone(p: RosterPlayer) -> float:
        total = 0.0
        for d in remaining:
            game = _game_of(schedule[d]).get(p.team)
            if game:
                yesterday = _game_of(schedule.get(d - dt.timedelta(days=1), []))
                total += _player_day(p, ctx, d, game, p.team in yesterday, lines, starters)[0]
        return total

    picked: dict[int, RosterPlayer] = {}
    for position in STARTERS:
        group = [p for p in pool if position in p.positions]
        playing = [p for p in group if team_games.get(p.team)]
        for p in sorted(playing, key=week_alone, reverse=True)[:POOL_PER_POSITION]:
            picked[p.id] = p
        for p in sorted(group, key=lambda p: season_value(p, ctx, lines), reverse=True)[:POOL_LONG_TERM_PER_POSITION]:
            picked[p.id] = p
    if open_days:
        fit_schedule = fit_schedule or schedule

        def fit(p: RosterPlayer) -> float:
            total = 0.0
            for d, slots in open_days.items():
                game = _game_of(fit_schedule.get(d, [])).get(p.team)
                if game and slots & set(p.positions) and not (available_from and d < available_from):
                    total += _player_day(p, ctx, d, game, False, lines, starters, True)[0]
            return total

        for position in STREAMER_POSITIONS:
            group = [p for p in pool if position in p.positions]
            for p in sorted(group, key=fit, reverse=True)[:POOL_FIT_PER_POSITION]:
                picked[p.id] = p
    return list(picked.values())


def candidate_moves(
    roster: list[RosterPlayer],
    opponent: TeamWeek,
    candidates: list[RosterPlayer],
    ctx,
    schedule: dict[dt.date, list[ScheduledGame]],
    lines: dict[str, dict[str, LineInfo]],
    starters: dict[str, dict],
    future: dict[dt.date, list[ScheduledGame]],
    weeks_after: int,
    available_from: dt.date | None = None,
    so_far: tuple[float, float, int] | None = None,
) -> list[Move]:
    """Every add/drop worth considering, best first. Left out: dropping below
    MIN_GOALIES, and moves costing more than MAX_WEEK_COST this week.
    `future` is the schedule of the days after this week used to judge the
    long run (two weeks is plenty); `weeks_after` is how many weeks are left.
    `available_from` is the first day an added player can play (waivers)."""
    future_weeks = len(future) / 7 or 1.0
    team_games = _team_games(schedule, ctx.today)
    mine = active(roster)
    so_far = so_far or _so_far(mine, ctx, sorted(schedule))
    current = project("me", roster, ctx, schedule, lines, starters, so_far=so_far)
    current_future = project("me", roster, ctx, future, lines, starters, True).expected if future else 0.0
    before = win_prob(current, opponent)
    # An open roster spot comes first: on a tie, keep everyone.
    drops: list[RosterPlayer | None] = [None] if len(mine) < ACTIVE_SPOTS else []
    drops += drop_candidates(mine, ctx, lines)
    moves = []
    for add in candidates:
        joins = {add.id: available_from} if available_from else None
        for drop in drops:
            if drop and drop.is_goalie and not add.is_goalie and sum(p.is_goalie for p in mine) <= MIN_GOALIES:
                continue
            trial = _swap(roster, add, drop)
            week = project("me", trial, ctx, schedule, lines, starters, joins=joins, so_far=so_far)
            if week.expected - current.expected < -MAX_WEEK_COST:
                continue
            later = (project("me", trial, ctx, future, lines, starters, True).expected - current_future
                     if future else 0.0)
            moves.append(Move(
                add=add, drop=drop,
                week_gain=week.expected - current.expected,
                long_term=LONG_RUN_DISCOUNT * later / future_weeks * weeks_after,
                next_weeks=later,
                games=team_games.get(add.team, 0),
                win_before=before, win_after=win_prob(week, opponent),
                week_counts=decided(before) is None,
            ))
    # Stable sort: on equal scores the earlier (open spot first) wins.
    return sorted(moves, key=lambda m: m.score, reverse=True)


def rejection(move: Move, threshold: float) -> str | None:
    """Why a move isn't worth an add, or None if it is. It must be worth
    `threshold` points and either lift this week's win odds or pay off visibly
    soon: a long-run edge too small to show in two weeks is noise. In a
    decided week (see `decided`) only the paying-off-soon route counts."""
    if not move.week_counts and move.next_weeks < 2 * threshold:
        return f"the week is {decided(move.win_before)} ({_pct(move.win_before)}) and it doesn't pay off within two weeks"
    if move.score < threshold:
        return f"worth {move.score:.1f} pts, under the {threshold:.1f} an add costs"
    if move.win_after - move.win_before < MIN_WIN_GAIN and move.next_weeks < 2 * threshold:
        return "neither lifts this week's win odds nor pays off within two weeks"
    return None


def _swap(roster: list[RosterPlayer], add: RosterPlayer, drop: RosterPlayer | None) -> list[RosterPlayer]:
    return [p for p in roster if drop is None or p.id != drop.id] + [
        RosterPlayer(add.id, add.name, add.team, add.positions, BENCH)]


def best_moves(
    roster: list[RosterPlayer],
    opponent: TeamWeek,
    pool: list[RosterPlayer],
    ctx,
    schedule: dict[dt.date, list[ScheduledGame]],
    lines: dict[str, dict[str, LineInfo]],
    starters: dict[str, dict],
    future: dict[dt.date, list[ScheduledGame]],
    weeks_after: int,
    max_moves: int,
    threshold: float,
    available_from: dt.date | None = None,
    so_far: tuple[float, float, int] | None = None,
    candidates: list[RosterPlayer] | None = None,
    ranked: list[Move] | None = None,
) -> list[Move]:
    """Up to `max_moves` add/drops worth making, best first; each one is
    judged with the previous ones already made. `candidates` and `ranked`
    (their moves on the current roster) save recomputing them."""
    if candidates is None:
        candidates = shortlist(pool, ctx, schedule, lines, starters, available_from)
    so_far = so_far or _so_far(active(roster), ctx, sorted(schedule))  # banked before any move
    moves: list[Move] = []
    for i in range(max_moves):
        if not (i == 0 and ranked is not None):
            ranked = candidate_moves(roster, opponent, candidates, ctx, schedule, lines, starters, future,
                                     weeks_after, available_from, so_far)
        if not ranked or rejection(ranked[0], threshold):
            break
        best = ranked[0]
        moves.append(best)
        candidates = [p for p in candidates if p.id != best.add.id]
        roster = _swap(roster, best.add, best.drop)
    return moves


def biggest_swing(ranked: list[Move]) -> Move | None:
    """The add that lifts this week's win odds most, recommended or not, if
    it lifts them by MIN_WIN_GAIN: what chasing would cost, for Nico to weigh."""
    best = max(ranked, key=lambda m: m.win_after - m.win_before, default=None)
    return best if best and best.win_after - best.win_before >= MIN_WIN_GAIN else None


def streamers(
    roster: list[RosterPlayer],
    ranked: list[Move],
    ctx,
    schedule: dict[dt.date, list[ScheduledGame]],
    next_schedule: dict[dt.date, list[ScheduledGame]],
    lines: dict[str, dict[str, LineInfo]],
    starters: dict[str, dict],
    so_far: tuple[float, float, int] | None = None,
) -> list[dict]:
    """Per skater position, the free agent who adds the most points to my
    lineup over this week and next (games on nights the slot is full add
    nothing), with his best drop: {"position", "move", "next_gain", "this_week",
    "next_week"} (the trial lineups' weeks). Positions where nobody helps are left out."""
    base_next = project("me", roster, ctx, next_schedule, lines, starters, True).expected if next_schedule else 0.0
    out, used = [], set()
    for position in STREAMER_POSITIONS:
        best: dict[int, Move] = {}
        for m in ranked:
            if m.add.positions[0] != position or m.add.id in used:
                continue
            if m.add.id not in best or m.week_gain + m.next_weeks / 2 > best[m.add.id].week_gain + best[m.add.id].next_weeks / 2:
                best[m.add.id] = m
        shortlisted = sorted(best.values(), key=lambda m: m.week_gain + m.next_weeks / 2, reverse=True)
        top = None
        for m in shortlisted[:STREAMER_SHORTLIST]:
            trial = _swap(roster, m.add, m.drop)
            nxt = project("me", trial, ctx, next_schedule, lines, starters, True) if next_schedule else None
            gain = (nxt.expected - base_next) if nxt else 0.0
            if m.week_gain + gain > 0 and (top is None or m.week_gain + gain > top["move"].week_gain + top["next_gain"]):
                top = {"position": position, "move": m, "next_gain": gain, "next_week": nxt,
                       "this_week": project("me", trial, ctx, schedule, lines, starters, so_far=so_far)}
        if top:
            used.add(top["move"].add.id)
            out.append(top)
    return out


def why_not(move: Move, threshold: float | None) -> str:
    """Why a move isn't a recommended add, in words Nico can weigh."""
    if threshold is None:
        return "no adds left"
    if move.drop and move.long_term < 0:
        return f"dropping {move.drop.name} costs about {-move.long_term:.0f} pts over the rest of the season"
    return rejection(move, threshold) or "a better add is recommended"


def midweek_text(me: TeamWeek, them: TeamWeek, chase: Move | None, threshold: float | None,
                 recommended: bool) -> str | None:
    """The mid-week stance in a few lines; None in a decided week (text() covers it)."""
    p_win = win_prob(me, them)
    gap = me.expected - them.expected
    st = stance(p_win)
    if st == "protect":
        return (f"Mid-week: ahead ({_pct(p_win)}), protect the lead: you're {gap:.0f} expected points up. "
                "No need to chase; make only the adds listed below, if any.")
    if st != "chase":
        return None
    lines = [f"Mid-week: behind ({_pct(p_win)}) but close, so chase: you trail by {-gap:.0f} expected points."]
    if chase is None:
        lines.append("No free agent moves your odds much, so there's nothing to chase with.")
        return "\n".join(lines)
    swing = (f"Biggest swing: add {chase.add.name} ({_games(chase.games)} left)"
             + (f" for {chase.drop.name}" if chase.drop else "")
             + f", win {_pct(chase.win_before)} -> {_pct(chase.win_after)}.")
    if recommended:
        lines.append(swing + " That's the add below.")
    else:
        why = why_not(chase, threshold)
        lines.append(swing + f" Not a recommended add ({why}), so it's your call.")
    return "\n".join(lines)


def adds_used(decisions: list[dict], week_days: list[dt.date]) -> tuple[int, int]:
    """(this season, this week) adds you've confirmed with Done."""
    done = [d for d in decisions if d["type"] == "add" and d["decision"] == "done"]
    first, last = week_days[0].isoformat(), week_days[-1].isoformat()
    return len(done), sum(1 for d in done if first <= d["date"] <= last)


def max_moves(season_used: int, week_used: int) -> int:
    return max(0, min(MAX_ADDS_PER_WEEK - week_used, MAX_ADDS_PER_SEASON - season_used))


def _games(n: int) -> str:
    return f"{n} game{'' if n == 1 else 's'}"


def _pct(p: float) -> str:
    return f"{min(max(p, 0.01), 0.99):.0%}"


def text(week: int, days: list[dt.date], me: TeamWeek, them: TeamWeek, opponent_updated: str | None,
         season_used: int, week_used: int, today: dt.date, yahoo_projected: list | None = None) -> str:
    span = f"{days[0]:%a %d %b} - {days[-1]:%a %d %b}"
    lines = [f"Week {week} ({span}) vs {them.name}"]
    if me.so_far or them.so_far:
        lines.append(f"So far {'' if yahoo_projected else 'about '}{me.so_far:.0f} - {them.so_far:.0f}")
    p_win = win_prob(me, them)
    yahoo = f" (Yahoo: {yahoo_projected[0]:.0f} - {yahoo_projected[1]:.0f})" if yahoo_projected else ""
    lines.append(f"Expected {me.expected:.0f} - {them.expected:.0f}{yahoo}: {_pct(p_win)} to win")
    if decided(p_win) == "lost":
        lines.append("This week looks lost: don't spend adds chasing it, only on players worth keeping.")
    elif decided(p_win) == "won":
        lines.append("This week looks won: no adds needed for it, only on players worth keeping.")
    lines.append(f"Lineup games left, setting the best lineup every day: you {me.player_games}, "
                 f"them {them.player_games}")
    goalie_games = me.goalie_games_so_far + me.goalie_starts_left
    status = "on track" if me.goalie_min_prob >= 0.9 else "AT RISK - pick up a goalie who plays this week"
    lines.append(f"Goalie games: ~{goalie_games:.1f} (min {MIN_GOALIE_GAMES_PER_WEEK}), "
                 f"{_pct(me.goalie_min_prob)} to make it: {status}")
    lines.append(f"Adds: {MAX_ADDS_PER_SEASON - season_used} left this season, "
                 f"{max_moves(season_used, week_used)} this week")
    if opponent_updated:
        age = (today - dt.date.fromisoformat(opponent_updated)).days
        if age >= 1:
            lines.append("")
            lines.append(f"Their roster is from {dt.date.fromisoformat(opponent_updated):%d %b}. If they've "
                         "made moves, send /opp with their Yahoo team page pasted after it.")
    return "\n".join(lines)


def move_text(move: Move) -> str:
    p = move.add
    head = f"Add {p.name} ({p.team}, {'/'.join(p.positions)}, {_games(move.games)} left this week)"
    drop = f"drop {move.drop.name}" if move.drop else "into your open roster spot"
    detail = [f"{move.week_gain:+.1f} pts this week, win {_pct(move.win_before)} -> {_pct(move.win_after)}"]
    if move.next_weeks >= 1:
        detail.append(f"{move.next_weeks:+.1f} over the next two weeks, so keep him")
    elif move.next_weeks < -1:
        detail.append("a streamer: drop him again when his games are done")
    return f"{head}, {drop}.\n" + "; ".join(detail) + (
        ".\nTap Done once it's made in Yahoo. If he's taken, send /taken " + p.name)
