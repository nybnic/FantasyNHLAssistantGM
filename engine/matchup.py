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
from dataclasses import dataclass

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
DROP_CANDIDATES = 4  # your players with the lowest long-run value
# Keep three goalies: with two, one injury or a light schedule week risks
# the goalie minimum, which zeroes every goalie point that week.
MIN_GOALIES = 3
# Free agents shortlisted per position (so an empty D slot finds a D) by
# this week's value, plus a few by long-run value.
POOL_PER_POSITION = 8
POOL_LONG_TERM_PER_POSITION = 3
MIN_WIN_GAIN = 0.02  # a this-week-only move must add 2 points of win probability
# A long-run upgrade that costs points this week can wait for a week where it doesn't.
MAX_WEEK_COST = 1.0
BASE_ADD_SCORE = 3.0  # expected points a move must be worth at an even pace of adds
PLAYOFF_RESERVE = 6  # adds kept for playoff weeks


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

    @property
    def score(self) -> float:
        return self.week_gain + self.long_term


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
            joins: dict[int, dt.date] | None = None) -> TeamWeek:
    """The team's week. `schedule` has every day of the fantasy week; days
    before ctx.today are scored from box scores, the rest projected.
    `joins` holds players who only count from a date on (a waiver claim)."""
    joins = joins or {}
    roster = active(roster)
    days = sorted(schedule)
    skater_so_far, goalie_so_far, goalie_games = _so_far(roster, ctx, days)
    skater_mean = skater_var = goalie_mean = goalie_var = 0.0
    start_probs: list[float] = []
    player_games = 0
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
        for pid, slot in lineup.optimize(candidates).items():
            if slot == BENCH:
                continue
            mean, var, prob = values[pid]
            player_games += 1
            if by_id[pid].is_goalie:
                goalie_mean += mean
                goalie_var += var
                start_probs.append(prob)
            else:
                skater_mean += mean
                skater_var += var

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
    )


def win_prob(me: TeamWeek, them: TeamWeek) -> float:
    sd = math.sqrt(me.variance + them.variance) or 1.0
    return 0.5 * (1 + math.erf((me.expected - them.expected) / (sd * math.sqrt(2))))


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
) -> list[Move]:
    """Up to `max_moves` add/drops, best first, each worth `threshold` points
    and (unless it helps beyond this week) at least MIN_WIN_GAIN of P(win).
    `future` is the schedule of the days after this week used to judge the
    long run (two weeks is plenty); `weeks_after` is how many weeks are left.
    `available_from` is the first day an added player can play (waivers)."""
    future_weeks = len(future) / 7 or 1.0
    remaining = [d for d in sorted(schedule) if d >= ctx.today]
    team_games = {}
    for d in remaining:
        for team in _game_of(schedule[d]):
            team_games[team] = team_games.get(team, 0) + 1

    def week_alone(p: RosterPlayer) -> float:
        total = 0.0
        for d in remaining:
            if available_from and d < available_from:
                continue
            game = _game_of(schedule[d]).get(p.team)
            if game:
                yesterday = _game_of(schedule.get(d - dt.timedelta(days=1), []))
                total += _player_day(p, ctx, d, game, p.team in yesterday, lines, starters)[0]
        return total

    shortlist: dict[int, RosterPlayer] = {}
    for position in STARTERS:
        group = [p for p in pool if position in p.positions]
        playing = [p for p in group if team_games.get(p.team)]
        for p in sorted(playing, key=week_alone, reverse=True)[:POOL_PER_POSITION]:
            shortlist[p.id] = p
        by_season = sorted(group, key=lambda p: season_value(p, ctx, lines), reverse=True)
        for p in by_season[:POOL_LONG_TERM_PER_POSITION]:
            shortlist[p.id] = p

    moves: list[Move] = []
    roster = list(roster)
    for _ in range(max_moves):
        mine = active(roster)
        current = project("me", roster, ctx, schedule, lines, starters)
        current_future = project("me", roster, ctx, future, lines, starters, True).expected if future else 0.0
        before = win_prob(current, opponent)
        # An open roster spot comes first: on a tie, keep everyone.
        drops: list[RosterPlayer | None] = [None] if len(mine) < ACTIVE_SPOTS else []
        drops += sorted(mine, key=lambda p: season_value(p, ctx, lines))[:DROP_CANDIDATES]
        best = None
        for add in shortlist.values():
            joins = {add.id: available_from} if available_from else None
            for drop in drops:
                if (drop and drop.is_goalie and not add.is_goalie
                        and sum(p.is_goalie for p in mine) <= MIN_GOALIES):
                    continue
                trial = [p for p in roster if drop is None or p.id != drop.id] + [RosterPlayer(
                    add.id, add.name, add.team, add.positions, BENCH)]
                week = project("me", trial, ctx, schedule, lines, starters, joins=joins)
                if week.expected - current.expected < -MAX_WEEK_COST:
                    continue
                later = (project("me", trial, ctx, future, lines, starters, True).expected - current_future
                         if future else 0.0)
                move = Move(
                    add=add, drop=drop,
                    week_gain=week.expected - current.expected,
                    long_term=LONG_RUN_DISCOUNT * later / future_weeks * weeks_after,
                    next_weeks=later,
                    games=team_games.get(add.team, 0),
                    win_before=before, win_after=win_prob(week, opponent),
                )
                if best is None or move.score > best.score:
                    best = move
        # Worth the add, and either lifts this week's win odds or pays off
        # visibly soon: a long-run edge too small to show in two weeks is noise.
        if (best is None or best.score < threshold
                or (best.win_after - best.win_before < MIN_WIN_GAIN and best.next_weeks < 2 * threshold)):
            break
        moves.append(best)
        shortlist.pop(best.add.id)
        roster = [p for p in roster if best.drop is None or p.id != best.drop.id] + [RosterPlayer(
            best.add.id, best.add.name, best.add.team, best.add.positions, BENCH)]
    return moves


def adds_used(decisions: list[dict], week_days: list[dt.date]) -> tuple[int, int]:
    """(this season, this week) adds you've confirmed with Done."""
    done = [d for d in decisions if d["type"] == "add" and d["decision"] == "done"]
    first, last = week_days[0].isoformat(), week_days[-1].isoformat()
    return len(done), sum(1 for d in done if first <= d["date"] <= last)


def max_moves(season_used: int, week_used: int) -> int:
    return max(0, min(MAX_ADDS_PER_WEEK - week_used, MAX_ADDS_PER_SEASON - season_used))


def _pct(p: float) -> str:
    return f"{min(max(p, 0.01), 0.99):.0%}"


def text(week: int, days: list[dt.date], me: TeamWeek, them: TeamWeek, opponent_updated: str | None,
         season_used: int, week_used: int, today: dt.date) -> str:
    span = f"{days[0]:%a %d %b} - {days[-1]:%a %d %b}"
    lines = [f"Week {week} ({span}) vs {them.name}"]
    if me.so_far or them.so_far:
        lines.append(f"So far about {me.so_far:.0f} - {them.so_far:.0f}")
    lines.append(f"Expected {me.expected:.0f} - {them.expected:.0f}: {_pct(win_prob(me, them))} to win")
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
    head = f"Add {p.name} ({p.team}, {'/'.join(p.positions)}, {move.games} games left this week)"
    drop = f"drop {move.drop.name}" if move.drop else "into your open roster spot"
    detail = [f"{move.week_gain:+.1f} pts this week, win {_pct(move.win_before)} -> {_pct(move.win_after)}"]
    if move.next_weeks >= 1:
        detail.append(f"{move.next_weeks:+.1f} over the next two weeks, so keep him")
    elif move.next_weeks < -1:
        detail.append("a streamer: drop him again when his games are done")
    return f"{head}, {drop}.\n" + "; ".join(detail) + (
        ".\nTap Done once it's made in Yahoo. If he's taken, send /taken " + p.name)
