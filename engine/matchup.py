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
- A move's value, in win probability, is this week's change in P(win) plus
  its later points times what a point is worth in a typical week. It is made
  when that beats the add's price (engine/addprice.py), which paces the 36
  adds, some kept back for the playoffs. Later points are the same
  whole-lineup projection run over the next LONG_RUN_WEEKS (so an empty D
  slot or thin goalie depth counts), per week, times the weeks left,
  discounted; a swap into one of my streaming spots is credited only its
  scheduled gain while the streamer would be held (hold_weeks).
- A move that costs points this week, or a keeper that gains little this
  week, waits for next week's adds. Two goalies or three is judged by
  value too: each week's goalie minimum is priced, never less than two kept.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass, field

from clients.dfo_lines import LineInfo
from clients.names import normalize_name
from clients.nhl_client import ScheduledGame
from config.league import (PLAYOFF_WEEKS, BENCH_SLOTS, GOALIE_WEIGHTS, MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK,
                           MIN_GOALIE_GAMES_PER_WEEK, REGULAR_SEASON_WEEKS, SKATER_WEIGHTS, STARTERS,
                           fantasy_points)
from engine import availability, ir, lineup
from engine.addprice import PLAYOFF_RESERVE, AddPrice
from league import weeks
from league.roster import BENCH, IR_SLOTS, RosterPlayer, active

SKATER_VARIANCE_PER_XFP = 2.5
GOALIE_START_VARIANCE = 21.0
TYPICAL_START_XFP = 8.8  # 2025-26 average points per goalie start
MODEL_SD_SHARE = 0.08  # projection error, as a share of a team's rest-of-week points (a guess)

ACTIVE_SPOTS = sum(STARTERS.values()) + BENCH_SLOTS
# Projected margins between teams realize at 0.77 (2024-25) / 0.84 (2025-26)
# of their size (scripts/check_sigma.py, aged priors), the spread of results
# around them right (x0.94 / x0.97): P(win) reads the part still to play at
# the average, 0.80. Points already banked count in full.
MARGIN_REALIZES = 0.80
# Projected 1 or 2 weeks before (check_sigma --ahead): 0.76 / 0.74 a week
# before, 0.67 / 0.73 two weeks before, spread x0.97-1.03. The weeks ahead's
# margins are read at the averages.
MARGIN_REALIZES_AHEAD = {1: 0.75, 2: 0.70}
# A projected gap between two fringe skaters (ranks 120-450, the adds and
# drops) realizes at 1.02 this week, 0.95 next week and 0.89 the week after
# (scripts/check_gaps.py, 2024-25 / 2025-26 averaged, +/- 0.02); a move's gain
# in the weeks ahead is counted at that size. Weeks 3-6: 0.84, 7-20: ~0.75,
# which the long run's discount already covers (below).
GAP_REALIZES = {0: 1.0, 1: 0.95, 2: 0.89}
# The weeks after this one played out against their real opponents (Nico,
# 2026-10-09): a move's points there count by what they do to that week's P(win).
AHEAD_WEEKS = 2
# Injuries, role changes and later adds make a long-run edge worth less
# than it projects to. Calibration alone takes ~0.75-0.84 of it out to 20
# weeks (check_gaps); the rest of the 0.5 is for swaps that end the hold early
# (a judgment call).
LONG_RUN_DISCOUNT = 0.5
# The long run is judged on the weeks after this one: averaged over 6 (one
# week's schedule swings a swap by 5-10 pts; 2 weeks times the season turned
# that noise into "-55 pts", 2026-10-01), while the add rule's "pays off soon"
# test and streaming use the next 2 (STREAM_WEEKS) as scheduled.
LONG_RUN_WEEKS = 6
STREAM_WEEKS = 2
# My streaming spots: the skaters closest to what waivers offer at their
# position. A player added into one is swapped out again within the time it
# takes the add pace to cycle the spots (3 spots at ~1.3 adds a week: ~2.3
# weeks, see hold_weeks), so the swap is credited its scheduled gain over that
# hold only, never the season (Nico, 2026-10-01). Judgment calls; calibrate.
STREAMING_SPOTS = 3
# Plus a third goalie, when I carry one: he's swapped goalie for goalie like
# any streaming spot. Two goalies are both core (the minimum rests on them).
GOALIE_STREAMING_SPOTS = 1
REPLACEMENT_SAMPLE = 3  # free agents averaged for a position's replacement level
# Drops tried: your lowest long-run players per group (forwards, D, goalies).
# Per group, because per-player value ranks D low (fewer points a game), so a
# plain bottom 4 was mostly D and never tried a weak forward (Schenn, 2026-10-01).
DROPS_PER_GROUP = 2
# Two goalies or three is judged by value (Nico, 2026-10-02): the projection
# prices each week's goalie minimum and a goalie lost for the week
# (availability.GOALIE_KEEP), so a third goalie is kept, dropped or added like
# any other move. Below two, the minimum (3 starts a week) is out of reach.
MIN_GOALIES = 2
# Free agents shortlisted per position (so an empty D slot finds a D) by
# this week's value, plus a few by long-run value.
POOL_PER_POSITION = 8
POOL_LONG_TERM_PER_POSITION = 3
# Plus a few per skater position by schedule fit: points on the nights my best
# lineup leaves that slot open, this week and next (streamers).
POOL_FIT_PER_POSITION = 4
STREAMER_POSITIONS = ("C", "LW", "RW", "D", "G")  # a goalie fits the nights a G slot is open
STREAMER_SHORTLIST = 4  # per position, judged on next week's lineup too
# Injured free agents weighed as IR stashes (straight into an empty IR or IR+
# slot, Yahoo allows it): the best few by season value (a judgment call).
STASH_CANDIDATES = 6
# Display only: the smallest win-odds lift worth naming as "the biggest swing".
# Whether a move is worth an add is engine/addprice.py's call.
MIN_WIN_GAIN = 0.02
# A long-run upgrade that costs points this week can wait for a week where it doesn't.
MAX_WEEK_COST = 1.0
# A keeper that gains less than this this week can wait for next week's adds
# (they reset Monday): this week's go to moves that pay this week. Same idea as
# MAX_WEEK_COST; the risk is someone claiming him meanwhile (Nico, 2026-10-01).
KEEPER_WAITS_BELOW = 1.0
KEEPER_LONG_RUN = 5.0  # long-run points above which an add is called a keeper, not a streamer (wording only)
# A week this lopsided is called decided in the messages ("save your adds").
# The add price needs no cut-off: an add barely moves a decided week's odds.
CONCEDE_BELOW = 0.10
COAST_ABOVE = 0.90
# Within this of 50% the week is called even, not "ahead" or "behind" (wording
# only, a judgment call): "ahead (50%), 0 points up" read as a contradiction.
EVEN_WITHIN = 0.03
# Free agents are everyone not on a known roster, so league moves unseen for
# this long make suggestions go stale: the plan asks for Transactions
# screenshots, as it does when the opponent's roster is this old (a judgment
# call; Nico sends them weekly).
LEAGUE_MOVES_STALE_DAYS = 2


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
    long_term: float  # later points: while held (a streaming spot) or the discounted long run
    next_weeks: float  # gain over the two weeks after this one, undiscounted
    games: int  # the added player's games left this week
    win_before: float
    win_after: float
    later_weight: float = 0.0  # win probability per later point (addprice.later_weight)
    plays_from: dt.date | None = None  # on waivers: the first day a claim of him plays
    ir_slot: str | None = None  # an injured add stashed straight into this empty IR slot (no drop now)
    later_drop: RosterPlayer | None = None  # a stash's drop once he's back: the cheapest to lose
    # The weeks after this one (AHEAD_WEEKS), against their real opponents:
    # the change in each one's P(win), and the gain counted (realized size, points).
    ahead_wins: tuple[float, ...] = ()
    ahead_pts: float = 0.0
    # An add into a spot an injured player's IR move opened: who takes it back
    # (his later points count until then, see ir_crunch).
    until_back: str = ""

    @property
    def later_value(self) -> float:
        return self.later_weight * self.long_term

    @property
    def value(self) -> float:
        """The win probability the move buys: this week's, the next weeks'
        against their opponents, plus later points'."""
        return self.win_after - self.win_before + sum(self.ahead_wins) + self.later_value


@dataclass
class WeekAhead:
    """A week after this one as my current roster would play it: its days, my
    margin over that week's opponent as it realizes (MARGIN_REALIZES_AHEAD), and the
    margin's spread. `margin` is None when the opponent isn't known (playoffs):
    then a point counts at a typical week's worth (later_weight)."""
    week: int
    days: frozenset
    margin: float | None
    sd: float
    opponent: str | None = None


def week_ahead(week: int, schedule: dict[dt.date, list[ScheduledGame]], me: TeamWeek, them: TeamWeek | None,
               opponent: str | None = None, weeks_out: int = 1) -> WeekAhead:
    """From both teams' projections of that week (long run: durability in),
    made `weeks_out` weeks before it."""
    if them is None:
        return WeekAhead(week, frozenset(schedule), None, math.sqrt(me.variance), opponent)
    shrink = MARGIN_REALIZES_AHEAD.get(weeks_out, min(MARGIN_REALIZES_AHEAD.values()))
    return WeekAhead(week, frozenset(schedule), shrink * (me.expected - them.expected),
                     math.sqrt(me.variance + them.variance) or 1.0, opponent)


def _position(p: RosterPlayer) -> str:
    return "D" if p.positions == ["D"] else "C"


def _game_of(games: list[ScheduledGame]) -> dict[str, ScheduledGame]:
    out = {}
    for g in games:
        out[g.home] = out[g.away] = g
    return out


def _player_day(p, ctx, date, game, played_yesterday, lines, starters,
                long_run: bool = False, next_game: bool = False) -> tuple[float, float, float]:
    """(expected points, variance, start probability) for one game. Start
    probability is 1 for skaters; goalies' counts toward the minimum.
    `long_run` also discounts skaters by their projected games played, which
    today's injury report can't show for games weeks away. `next_game`: his
    team's first game from today (its last start's result moves who starts it)."""
    team_lines = lines.get(p.team, {})
    info = team_lines.get(normalize_name(p.name))
    days_ahead = (date - ctx.today).days
    if p.is_goalie:
        team_starts, prior = ctx.team_starts.get(p.team, []), ctx.prior_start_share(p.id)
        avail = availability.goalie(p.id, p.name, date, info, starters.get(p.team) if date == ctx.today else None,
                                    team_starts, prior, days_ahead,
                                    ctx.last_results.get(p.team) if next_game else None)
        prob = avail.prob
        if date > ctx.today and played_yesterday and prob and avail.note not in ("IR", "out"):
            # Who starts the night before isn't known yet.
            prob = availability.second_of_back_to_back(availability.start_share(p.id, date, info, team_starts, prior))
        home = game.home == p.team
        x = ctx.goalie_start(p.id, p.team, game.away if home else game.home, home)["xfp"]
        return prob * x, prob * (GOALIE_START_VARIANCE + x * x) - (prob * x) ** 2, prob
    x = ctx.skater(p.id, _position(p)).xfp
    status = availability.skater(info, bool(team_lines))
    if days_ahead and status.prob < availability.HEALTHY_PLAY:  # how long he's been out sets his return
        status = availability.skater(info, bool(team_lines), days_ahead, ctx.games_missed(p.id, p.team))
    prob = status.prob
    if long_run:
        prob *= durability(ctx, p, x)
    return prob * x, prob * (SKATER_VARIANCE_PER_XFP * x + x * x) - (prob * x) ** 2, 1.0


def durability(ctx, p: RosterPlayer, x: float) -> float:
    """The share of his long-run points a team keeps given the games he's
    projected to miss (ctx.durability: DFO's projected games played). A missed
    game is filled from free agents (IR, or a drop and a streamer: Nico,
    2026-10-09), so it costs only his edge over a replacement-level player at
    his position (ctx.replacement_xfp, set by the weekly plan): a fringe
    player's durability barely matters, a star's does. Without a replacement
    level, his whole projection (as before). Untested: DFO's projected games
    played aren't archived for past seasons."""
    dur = ctx.durability(p.id)
    repl = (getattr(ctx, "replacement_xfp", None) or {}).get(_position(p))
    if repl is None or x <= 0 or dur >= 1.0:
        return dur
    return 1.0 - (1.0 - dur) * max(0.0, x - repl) / x


def _at_least(probs: list[float], so_far: int, need: int) -> float:
    """P(so_far + number of successes >= need), independent Bernoullis."""
    dist = [1.0]
    for p in probs:
        dist = [(dist[k] if k < len(dist) else 0.0) * (1 - p) + (dist[k - 1] * p if k else 0.0)
                for k in range(len(dist) + 1)]
    return sum(q for k, q in enumerate(dist) if so_far + k >= need)


def _so_far(roster, ctx, days, history: dict[dt.date, list[RosterPlayer]] | None = None) -> tuple[float, float, int]:
    """(skater points, goalie points, goalie games) in finished games this
    week, scoring each day with that day's roster from `history` (else today's)."""
    skater_pts = goalie_pts = 0.0
    goalie_games = 0
    for date in (d for d in days if d < ctx.today):
        played = {}
        for p in (history or {}).get(date, roster):
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
            joins: dict[int, dt.date] | None = None, so_far: tuple[float, float, int] | None = None,
            leaves: dict[int, dt.date] | None = None) -> TeamWeek:
    """The team's week. `schedule` has every day of the fantasy week; days
    before ctx.today are scored from box scores, the rest projected.
    `joins` holds players who only count from a date on (a waiver claim),
    `leaves` players who count only before one (that claim's drop).
    `so_far` (skater points, goalie points, goalie games) replaces the box
    scores: a trial roster keeps the points the real one banked, since an
    added player's earlier games never count for you.
    A schedule spanning several fantasy weeks (the long run) is projected week
    by week and summed: the goalie minimum holds for each week, not the span."""
    by_week: dict[int | None, dict] = {}
    for d in sorted(schedule):
        by_week.setdefault(weeks.week_of(d), {})[d] = schedule[d]
    if len(by_week) > 1:
        parts = [_project_week(name, roster, ctx, part, lines, starters, long_run, joins, so_far if i == 0 else None,
                               leaves)
                 for i, part in enumerate(by_week.values())]
        return TeamWeek(
            name=name, so_far=parts[0].so_far, expected=sum(p.expected for p in parts),
            variance=sum(p.variance for p in parts), player_games=sum(p.player_games for p in parts),
            goalie_games_so_far=parts[0].goalie_games_so_far,
            goalie_starts_left=sum(p.goalie_starts_left for p in parts),
            goalie_min_prob=min(p.goalie_min_prob for p in parts),  # the riskiest week
            by_day={d: v for p in parts for d, v in p.by_day.items()},
            lineups={d: v for p in parts for d, v in p.lineups.items()},
        )
    return _project_week(name, roster, ctx, schedule, lines, starters, long_run, joins, so_far, leaves)


def _goalie_states(goalies: list[RosterPlayer], keep: dict[int, float]) -> list[tuple[float, set[int]]]:
    """(probability, goalies available) for each combination of my goalies
    being there all week or not (`keep`: each one's odds of being there)."""
    states = [(1.0, set())]
    for g in goalies:
        a = keep.get(g.id, 1.0)
        states = [(p * a, s | {g.id}) for p, s in states] + ([(p * (1 - a), s) for p, s in states] if a < 1 else [])
    return states


def _first_games(schedule: dict[dt.date, list[ScheduledGame]], today: dt.date) -> dict[str, dt.date]:
    """Team -> its next game's date, when `schedule` starts by today (a later
    week's schedule doesn't hold anyone's next game)."""
    first: dict[str, dt.date] = {}
    if not schedule or min(schedule) > today:
        return first
    for date in sorted(d for d in schedule if d >= today):
        for team in _game_of(schedule[date]):
            first.setdefault(team, date)
    return first


def _project_week(name, roster, ctx, schedule, lines, starters, long_run, joins, so_far, leaves=None) -> TeamWeek:
    joins, leaves = joins or {}, leaves or {}
    roster = active(roster)
    days = sorted(schedule)
    skater_so_far, goalie_so_far, goalie_games = so_far or _so_far(roster, ctx, days)
    skater_mean = skater_var = 0.0
    player_games = 0
    day_skaters: dict[dt.date, float] = {}
    goalie_days: dict[dt.date, dict[int, tuple[float, float, float]]] = {}
    lineups: dict[dt.date, dict[int, tuple[str, float]]] = {}
    goalies = [p for p in roster if p.is_goalie]
    upcoming = [d for d in days if d >= ctx.today]
    first_game = _first_games(schedule, ctx.today)
    for date in upcoming:
        game_of = _game_of(schedule[date])
        yesterday = _game_of(schedule.get(date - dt.timedelta(days=1), []))
        values = {}
        for p in roster:
            game = game_of.get(p.team)
            if game and joins.get(p.id, date) <= date < leaves.get(p.id, date + dt.timedelta(days=1)):
                values[p.id] = _player_day(p, ctx, date, game, p.team in yesterday, lines, starters, long_run,
                                           first_game.get(p.team) == date)
        # Only goalies fill G slots, so skaters and goalies are set apart.
        candidates = [lineup.Candidate(p.id, tuple(p.positions), values[p.id][0])
                      for p in roster if p.id in values and not p.is_goalie]
        assignment = lineup.optimize(candidates, {s: n for s, n in STARTERS.items() if s != "G"})
        lineups[date] = {pid: (slot, 1.0) for pid, slot in assignment.items()}
        day_skaters[date] = 0.0
        for pid, slot in assignment.items():
            if slot != BENCH:
                mean, var, _ = values[pid]
                player_games += 1
                skater_mean += mean
                skater_var += var
                day_skaters[date] += mean
        goalie_days[date] = {g.id: values[g.id] for g in goalies if g.id in values}

    # The long run weighs losing a goalie for the week (injury, a lost job):
    # each is there with GOALIE_KEEP odds at the week's midpoint, and on each
    # day the two best of those there start. This week, DFO's news covers it.
    keep = {}
    if long_run and upcoming:
        mid = (upcoming[len(upcoming) // 2] - ctx.today).days
        keep = {g.id: availability.ahead(availability.GOALIE_KEEP, mid, 1.0) for g in goalies}
    expected_goalie = second_moment = starts_left = min_prob = goalie_mean = 0.0
    day_goalies = {d: 0.0 for d in upcoming}
    for p_state, there in _goalie_states(goalies, keep):
        probs, mean, var, per_day = [], 0.0, 0.0, {}
        for date in upcoming:
            playing = sorted(((v, pid) for pid, v in goalie_days[date].items() if pid in there), reverse=True)
            started = playing[:STARTERS["G"]]
            per_day[date] = sum(v[0] for v, _ in started)
            for (m, v, prob), pid in started:
                mean += m
                var += v
                probs.append(prob)
            if len(there) == len(goalies):  # the lineup shown: everyone there
                for rank, ((m, v, prob), pid) in enumerate(playing):
                    lineups[date][pid] = ("G" if rank < STARTERS["G"] else BENCH, prob)
                player_games += len(started)
        p_min = _at_least(probs, goalie_games, MIN_GOALIE_GAMES_PER_WEEK)
        total = goalie_so_far + mean
        expected_goalie += p_state * p_min * total
        second_moment += p_state * p_min * (var + total ** 2)
        starts_left += p_state * sum(probs)
        min_prob += p_state * p_min
        goalie_mean += p_state * mean
        for d in upcoming:
            day_goalies[d] += p_state * p_min * per_day[d]
    goalie_week_var = second_moment - expected_goalie ** 2
    model_var = (MODEL_SD_SHARE * (skater_mean + goalie_mean)) ** 2
    return TeamWeek(
        name=name,
        so_far=skater_so_far + goalie_so_far,
        expected=skater_so_far + skater_mean + expected_goalie,
        variance=skater_var + goalie_week_var + model_var,
        player_games=player_games,
        goalie_games_so_far=goalie_games,
        goalie_starts_left=starts_left,
        goalie_min_prob=min_prob,
        by_day={d: day_skaters[d] + day_goalies[d] for d in upcoming},
        lineups=lineups,
    )


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def margin(me: TeamWeek, them: TeamWeek) -> float:
    """My expected margin as it realizes: banked points in full, the rest at MARGIN_REALIZES."""
    banked = me.so_far - them.so_far
    return banked + MARGIN_REALIZES * (me.expected - them.expected - banked)


def win_prob(me: TeamWeek, them: TeamWeek, gain: float = 0.0, variance: float | None = None) -> float:
    """P(win). `gain`: a move's points this week on top (a fringe gap realizes
    in full this week, GAP_REALIZES), with the trial roster's `variance`."""
    sd = math.sqrt((me.variance if variance is None else variance) + them.variance) or 1.0
    return _phi((margin(me, them) + GAP_REALIZES[0] * gain) / sd)


def win_after(current: TeamWeek, trial: TeamWeek, them: TeamWeek) -> float:
    """P(win) with a move made: the trial roster's gain over the current one."""
    return win_prob(current, them, trial.expected - current.expected, trial.variance)


def ahead_value(gain_by_day: dict[dt.date, float], ahead: list[WeekAhead], later_weight: float,
                counted: set | None = None) -> tuple[tuple[float, ...], float]:
    """A move's worth in the weeks ahead: per week, the change in P(win) its
    gain (realized size) makes against that week's opponent, and the points
    counted. `counted`: only these days count (a streamer's hold)."""
    wins, pts = [], 0.0
    for k, w in enumerate(ahead, start=1):
        g = GAP_REALIZES[k] * sum(v for d, v in gain_by_day.items() if d in w.days and (counted is None or d in counted))
        pts += g
        wins.append(later_weight * g if w.margin is None
                    else _phi((w.margin + g) / w.sd) - _phi(w.margin / w.sd))
    return tuple(wins), pts


def decided(p_win: float) -> str | None:
    """"lost" or "won" when the week is beyond what an add changes, else None."""
    if p_win < CONCEDE_BELOW:
        return "lost"
    if p_win > COAST_ABOVE:
        return "won"
    return None


def stance(p_win: float) -> str:
    """How to play the rest of the week: "lost" or "won" (save adds),
    "even" in a toss-up, "chase" when behind, "protect" when ahead."""
    if abs(p_win - 0.5) < EVEN_WITHIN:
        return "even"
    return decided(p_win) or ("chase" if p_win < 0.5 else "protect")


def _group(p: RosterPlayer) -> str:
    return "G" if p.is_goalie else "D" if p.positions == ["D"] else "F"


def streaming_spots(mine: list[RosterPlayer], free_agents: list[RosterPlayer], ctx,
                    lines: dict[str, dict[str, LineInfo]]) -> set[int]:
    """Ids of my STREAMING_SPOTS skaters with the least long-run value above
    the best free agents at their position (so D and forwards compare fairly),
    and my GOALIE_STREAMING_SPOTS weakest goalies beyond MIN_GOALIES."""
    def replacement(position: str) -> float:
        values = sorted((season_value(p, ctx, lines) for p in free_agents
                         if position in p.positions and not p.is_goalie), reverse=True)[:REPLACEMENT_SAMPLE]
        return sum(values) / len(values) if values else 0.0

    levels = {pos: replacement(pos) for pos in ("C", "LW", "RW", "D")}
    skaters = [p for p in mine if not p.is_goalie]
    by_gap = sorted(skaters, key=lambda p: season_value(p, ctx, lines) - levels.get(p.positions[0], 0.0))
    goalies = sorted((p for p in mine if p.is_goalie), key=lambda p: season_value(p, ctx, lines))
    spare_goalies = goalies[:GOALIE_STREAMING_SPOTS] if len(goalies) > MIN_GOALIES else []
    return {p.id for p in by_gap[:STREAMING_SPOTS] + spare_goalies}


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


def hold_weeks(adds_left: int, week: int) -> float:
    """How long a streamer stays: my streaming spots over the adds a week the
    budget allows from here (the playoff reserve kept until the playoffs)."""
    if week > REGULAR_SEASON_WEEKS:
        spare, weeks_left = adds_left, PLAYOFF_WEEKS[-1] - week + 1
    else:
        spare, weeks_left = adds_left - PLAYOFF_RESERVE, REGULAR_SEASON_WEEKS - week + 1
    rate = min(max(spare / max(weeks_left, 1), 0.5), 2.0)
    return (STREAMING_SPOTS + GOALIE_STREAMING_SPOTS) / rate


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


def _too_early(available_from: dict[int, dt.date] | None, p: RosterPlayer, date: dt.date) -> bool:
    first = (available_from or {}).get(p.id)
    return bool(first and date < first)


def shortlist(pool: list[RosterPlayer], ctx, schedule: dict[dt.date, list[ScheduledGame]],
              lines: dict[str, dict[str, LineInfo]], starters: dict[str, dict],
              available_from: dict[int, dt.date] | None = None, open_days: dict[dt.date, set[str]] | None = None,
              fit_schedule: dict[dt.date, list[ScheduledGame]] | None = None) -> list[RosterPlayer]:
    """The free agents worth a full evaluation: the best per position by this
    week's games, a few by long-run value, and with `open_days` (day -> open
    slots, over `fit_schedule`) a few by points on nights their slot is open.
    `available_from`: player id -> first day he can play for me (waivers)."""
    remaining = [d for d in sorted(schedule) if d >= ctx.today]
    team_games = _team_games(schedule, ctx.today)

    def week_alone(p: RosterPlayer) -> float:
        total = 0.0
        for d in (d for d in remaining if not _too_early(available_from, p, d)):
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
                if game and slots & set(p.positions) and not _too_early(available_from, p, d):
                    total += _player_day(p, ctx, d, game, False, lines, starters, True)[0]
            return total

        for position in STREAMER_POSITIONS:
            group = [p for p in pool if position in p.positions]
            for p in sorted(group, key=fit, reverse=True)[:POOL_FIT_PER_POSITION]:
                picked[p.id] = p
    for p in stash_pool(pool, ctx, lines):
        picked[p.id] = p
    return list(picked.values())


def _ir_status(p: RosterPlayer, lines: dict[str, dict[str, LineInfo]]) -> str | None:
    return ir.likely_status(lines.get(p.team, {}).get(normalize_name(p.name)))


def stash_pool(pool: list[RosterPlayer], ctx, lines: dict[str, dict[str, LineInfo]]) -> list[RosterPlayer]:
    """The STASH_CANDIDATES best injured free agents by season value: IR stashes."""
    injured = [p for p in pool if _ir_status(p, lines)]
    return sorted(injured, key=lambda p: season_value(p, ctx, lines), reverse=True)[:STASH_CANDIDATES]


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
    available_from: dict[int, dt.date] | None = None,
    so_far: tuple[float, float, int] | None = None,
    hold_days: int = 7 * STREAM_WEEKS,
    later_weight: float = 0.0,
    ahead: list[WeekAhead] | None = None,
) -> list[Move]:
    """Every add/drop worth considering, best first. Left out: dropping below
    MIN_GOALIES, and moves costing more than MAX_WEEK_COST this week.
    `future` is the schedule of the days after this week used to judge the
    long run (LONG_RUN_WEEKS); `weeks_after` is how many weeks are left.
    `available_from`: player id -> the first day he can play for me (waivers).
    `hold_days`: days after this week a streamer is kept (hold_weeks).
    `ahead`: the next weeks against their opponents (week_ahead); the long
    run then covers the weeks after them."""
    ahead = ahead or []
    soon = set(sorted(future)[:7 * STREAM_WEEKS])
    held = set(sorted(future)[:hold_days])
    team_games = _team_games(schedule, ctx.today)
    mine = active(roster)
    so_far = so_far or _so_far(mine, ctx, sorted(schedule))
    current = project("me", roster, ctx, schedule, lines, starters, so_far=so_far)
    current_future = project("me", roster, ctx, future, lines, starters, True) if future else None
    spots = streaming_spots(mine, candidates, ctx, lines)
    before = win_prob(current, opponent)
    # An open roster spot comes first: on a tie, keep everyone.
    drops: list[RosterPlayer | None] = [None] if len(mine) < ACTIVE_SPOTS else []
    drops += drop_candidates(mine, ctx, lines)
    moves, gains = [], {}
    for add in candidates:
        plays_from = (available_from or {}).get(add.id)
        tried = []
        for drop in drops:
            if drop and drop.is_goalie and not add.is_goalie and sum(p.is_goalie for p in mine) <= MIN_GOALIES:
                continue
            trial = _swap(roster, add, drop)
            during, joins, leaves = _deferred(roster, add, drop, plays_from)
            week = project("me", during, ctx, schedule, lines, starters, joins=joins, so_far=so_far, leaves=leaves)
            gain = {}
            if current_future:
                trial_future = project("me", trial, ctx, future, lines, starters, True)
                gain = {d: trial_future.by_day.get(d, 0.0) - current_future.by_day.get(d, 0.0) for d in future}
            tried.append((drop, week, gain))
        for drop, week, gain in tried:
            if week.expected - current.expected < -MAX_WEEK_COST:
                continue
            # A streaming spot is swapped again after the hold: its scheduled gain, no season.
            streamer = bool(drop and drop.id in spots)
            ahead_wins, ahead_pts, long_term = horizon(gain, future, ahead, weeks_after, later_weight,
                                                       held if streamer else None)
            moves.append(Move(
                add=add, drop=drop,
                week_gain=week.expected - current.expected,
                long_term=long_term,
                next_weeks=sum(v for d, v in gain.items() if d in soon),
                games=team_games.get(add.team, 0),
                win_before=before, win_after=win_after(current, week, opponent),
                later_weight=later_weight,
                plays_from=plays_from,
                ahead_wins=ahead_wins, ahead_pts=ahead_pts,
            ))
            gains[id(moves[-1])] = (gain, held if streamer else None)
    moves = ir_returns(moves, gains, roster, ctx, future, lines, starters, ahead, weeks_after, later_weight)
    stashes = [p for p in candidates if _ir_status(p, lines)]
    if stashes and current_future and ir.free_slots(roster):
        moves += _stash_moves(roster, opponent, stashes, ctx, schedule, lines, starters, future, weeks_after,
                              available_from, so_far, current, current_future, later_weight, team_games, ahead)
    # Stable sort: on equal values the earlier (open spot first) wins.
    return sorted(moves, key=lambda m: m.value, reverse=True)


# The moves re-valued with players in IR slots coming back (the best by value
# so far; the rest can't reach the top): a judgment call on cost.
IR_RETURN_MOVES = 30
CUT_OPTIONS = 3  # the cheapest players by season value tried as the one cut when they're back


def ir_back(hurt: list[RosterPlayer], ctx, lines: dict[str, dict[str, LineInfo]], future: dict) -> dict[dt.date, float]:
    """By day, the odds every player in an IR slot is back: each one's odds of
    playing follow the return curves for his status and games missed, as a
    stash's do (independent)."""
    def back(p: RosterPlayer, day: dt.date) -> float:
        info = lines.get(p.team, {}).get(normalize_name(p.name))
        return availability.skater(info, bool(lines.get(p.team)), (day - ctx.today).days,
                                   ctx.games_missed(p.id, p.team)).prob

    return {d: math.prod(back(p, d) for p in hurt) for d in future}


def with_returns(roster: list[RosterPlayer], ctx, future: dict, lines: dict, starters: dict) -> TeamWeek:
    """The roster once the players in IR slots are back: if that's more than
    the active spots, the cheapest to lose are cut (of the CUT_OPTIONS lowest
    by season value, whichever leaves the best lineup; never below
    MIN_GOALIES), projected over `future`."""
    back = [dataclasses.replace(p, slot=BENCH) for p in roster if p.slot in IR_SLOTS]
    team = [p for p in roster if p.slot not in IR_SLOTS] + back
    best = None
    for _ in range(max(len(team) - ACTIVE_SPOTS, 0)):
        goalies = sum(p.is_goalie for p in team)
        options = sorted((p for p in team if p.id not in {b.id for b in back}
                          and not (p.is_goalie and goalies <= MIN_GOALIES)),
                         key=lambda p: season_value(p, ctx, lines))[:CUT_OPTIONS]
        weeks_cut = [(project("me", [q for q in team if q.id != p.id], ctx, future, lines, starters, True), p)
                     for p in options]
        best, cut = max(weeks_cut, key=lambda wc: wc[0].expected)
        team = [q for q in team if q.id != cut.id]
    return best or project("me", team, ctx, future, lines, starters, True)


def ir_returns(moves: list[Move], gains: dict, roster: list[RosterPlayer], ctx, future: dict, lines: dict,
               starters: dict, ahead: list[WeekAhead], weeks_after: int, later_weight: float) -> list[Move]:
    """The best moves' later gains with the players in IR slots coming back:
    each day, (1 - P(back)) x the gain as it is + P(back) x the gain with them
    back and the cheapest players cut (with_returns), for this roster and the
    move's. So an add that would be the one cut when they're back (Kelly when
    Celebrini returns) counts only until then, and one that takes an open
    spot holds it only that long. `gains`: id(move) -> (gain by day, the
    streaming hold's days or None)."""
    hurt = [p for p in roster if p.slot in IR_SLOTS]
    if not hurt or not future:
        return moves
    odds = ir_back(hurt, ctx, lines, future)
    base = with_returns(roster, ctx, future, lines, starters)
    names = " and ".join(p.name for p in hurt)
    out = []
    for m in sorted(moves, key=lambda m: m.value, reverse=True)[:IR_RETURN_MOVES]:
        gain, held = gains[id(m)]
        trial = with_returns(_swap(roster, m.add, m.drop), ctx, future, lines, starters)
        after = {d: trial.by_day.get(d, 0.0) - base.by_day.get(d, 0.0) for d in future}
        mixed = {d: (1 - odds[d]) * gain.get(d, 0.0) + odds[d] * after[d] for d in future}
        ahead_wins, ahead_pts, long_term = horizon(mixed, future, ahead, weeks_after, later_weight, held)
        changed = abs(sum(after.values()) - sum(gain.values())) > 0.5
        out.append(dataclasses.replace(m, ahead_wins=ahead_wins, ahead_pts=ahead_pts, long_term=long_term,
                                       next_weeks=sum(mixed[d] for d in sorted(future)[:7 * STREAM_WEEKS]),
                                       until_back=names if changed else ""))
    done = {id(m) for m in sorted(moves, key=lambda m: m.value, reverse=True)[:IR_RETURN_MOVES]}
    return out + [m for m in moves if id(m) not in done]


def horizon(gain: dict[dt.date, float], future: dict, ahead: list[WeekAhead], weeks_after: int,
            later_weight: float, held: set | None = None) -> tuple[tuple[float, ...], float, float]:
    """A move's gain by day after this week, valued: (P(win) change in each week
    ahead, the points counted there, long-run points after them). The long
    run: the gain per week over all of `future` (LONG_RUN_WEEKS: fewer weeks
    let one team's schedule swing it, 2026-10-01; the weeks ahead are in the
    average but not counted again), times the weeks left after the ones
    ahead, discounted (LONG_RUN_DISCOUNT). `held`: a streaming spot's days
    (hold_weeks): only those count, ahead or later, and no season."""
    ahead_wins, ahead_pts = ahead_value(gain, ahead, later_weight, held)
    if held is not None:
        near = set().union(*(w.days for w in ahead)) if ahead else set()
        return ahead_wins, ahead_pts, sum(gain.get(d, 0.0) for d in future if d in held and d not in near)
    per_week = sum(gain.get(d, 0.0) for d in future) / (len(future) / 7) if future else 0.0
    return ahead_wins, ahead_pts, LONG_RUN_DISCOUNT * per_week * max(weeks_after - len(ahead), 0)


def _stash_moves(roster, opponent, stashes, ctx, schedule, lines, starters, future, weeks_after, available_from,
                 so_far, current: TeamWeek, current_future: TeamWeek, later_weight, team_games,
                 ahead: list[WeekAhead] | None = None) -> list[Move]:
    """Injured free agents added straight into an empty IR or IR+ slot: no drop
    now; once he's back, the player cheapest to lose goes. Valued as an extra
    player whose games follow the return curves, less that drop's points on
    each day times the odds the stash is back by then. Slightly conservative:
    the drop's points are counted as they are without the stash competing for
    his slot."""
    free = ir.free_slots(roster)
    mine = active(roster)
    goalies = sum(p.is_goalie for p in mine)
    before = win_prob(current, opponent)
    soon = sorted(future)[:7 * STREAM_WEEKS]
    without = {}
    for d in drop_candidates(mine, ctx, lines):
        rest = [p for p in roster if p.id != d.id]
        without[d.id] = (d, project("me", rest, ctx, schedule, lines, starters, so_far=so_far),
                         project("me", rest, ctx, future, lines, starters, True))
    moves = []
    for add in stashes:
        slot = ir.slot_for(_ir_status(add, lines), free)
        if not slot:
            continue
        info = lines.get(add.team, {}).get(normalize_name(add.name))
        missed = ctx.games_missed(add.id, add.team)

        def back(day: dt.date) -> float:
            return availability.skater(info, True, (day - ctx.today).days, missed).prob

        def cost(base: TeamWeek, alt: TeamWeek, days) -> float:
            return sum(back(d) * (base.by_day.get(d, 0.0) - alt.by_day.get(d, 0.0)) for d in days)

        options = [(d, w, f) for d, w, f in without.values()
                   if not (d.is_goalie and not add.is_goalie and goalies <= MIN_GOALIES)]
        if not options:
            continue
        drop, week_without, future_without = min(options, key=lambda o: cost(current_future, o[2], future))
        trial = roster + [RosterPlayer(add.id, add.name, add.team, add.positions, BENCH)]
        plays_from = (available_from or {}).get(add.id)
        week = project("me", trial, ctx, schedule, lines, starters, joins={add.id: plays_from} if plays_from else None,
                       so_far=so_far)
        later = project("me", trial, ctx, future, lines, starters, True)
        week = dataclasses.replace(week, expected=week.expected - cost(current, week_without, current.by_day))
        gain = {d: later.by_day.get(d, 0.0) - current_future.by_day.get(d, 0.0)
                - back(d) * (current_future.by_day.get(d, 0.0) - future_without.by_day.get(d, 0.0)) for d in future}
        ahead_wins, ahead_pts, long_term = horizon(gain, future, ahead or [], weeks_after, later_weight)
        moves.append(Move(
            add=add, drop=None, week_gain=week.expected - current.expected,
            long_term=long_term, next_weeks=sum(gain[d] for d in soon),
            games=team_games.get(add.team, 0), win_before=before, win_after=win_after(current, week, opponent),
            later_weight=later_weight, plays_from=plays_from, ir_slot=slot, later_drop=drop,
            ahead_wins=ahead_wins, ahead_pts=ahead_pts,
        ))
    return moves


def rejection(move: Move, price: AddPrice) -> str | None:
    """Why a move isn't worth an add, or None if it is: it must buy at least
    the add's price in win probability (engine/addprice.py)."""
    if move.value < price.lam:
        return f"worth {100 * move.value:.1f} win-pts, under the {100 * price.lam:.1f} an add costs"
    return None


def _swap(roster: list[RosterPlayer], add: RosterPlayer, drop: RosterPlayer | None,
          slot: str = BENCH) -> list[RosterPlayer]:
    return [p for p in roster if drop is None or p.id != drop.id] + [
        RosterPlayer(add.id, add.name, add.team, add.positions, slot)]


def _deferred(roster: list[RosterPlayer], add: RosterPlayer, drop: RosterPlayer | None,
              plays_from: dt.date | None) -> tuple[list[RosterPlayer], dict | None, dict | None]:
    """The trial roster with its (joins, leaves) for project: an add who can't
    play before `plays_from` (a waiver claim, or this week's adds spent) is
    made then, so his drop keeps playing until that day."""
    if plays_from is None:
        return _swap(roster, add, drop), None, None
    return (_swap(roster, add, None), {add.id: plays_from},
            {drop.id: plays_from} if drop else None)


def waits(move: Move) -> bool:
    """A keeper that does nothing this week: it can be made next week."""
    return move.week_gain < KEEPER_WAITS_BELOW and move.next_weeks > 0


def holds_keepers(date: dt.date, week: int, p_win: float) -> bool:
    """Before the mid-week plan, a keeper that does nothing this week waits
    for it, so the add stays free to chase with if the week turns; it costs
    nothing this week (Nico, 2026-10-03). Not in a decided week: nothing to
    chase or protect. The risk: someone claims him meanwhile."""
    return date < weeks.midweek(week) and decided(p_win) is None


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
    available_from: dict[int, dt.date] | None = None,
) -> list[dict]:
    """Per skater position, the free agent who adds the most points to my
    lineup over this week and next (games on nights the slot is full add
    nothing, and so do games before he can join: `available_from`), with his
    best drop: {"position", "move", "next_gain", "this_week", "next_week"}
    (the trial lineups' weeks). Positions where nobody helps are left out."""
    base_next = project("me", roster, ctx, next_schedule, lines, starters, True).expected if next_schedule else 0.0
    out, used = [], set()
    for position in STREAMER_POSITIONS:
        best: dict[int, Move] = {}
        for m in ranked:
            if m.add.positions[0] != position or m.add.id in used or m.ir_slot:  # a stash is no streamer
                continue
            if m.add.id not in best or m.week_gain + m.next_weeks / 2 > best[m.add.id].week_gain + best[m.add.id].next_weeks / 2:
                best[m.add.id] = m
        shortlisted = sorted(best.values(), key=lambda m: m.week_gain + m.next_weeks / 2, reverse=True)
        top = None
        for m in shortlisted[:STREAMER_SHORTLIST]:
            trial, joins, leaves = _deferred(roster, m.add, m.drop, (available_from or {}).get(m.add.id))
            nxt = (project("me", trial, ctx, next_schedule, lines, starters, True, joins, leaves=leaves)
                   if next_schedule else None)
            gain = (nxt.expected - base_next) if nxt else 0.0
            if m.week_gain + gain > 0 and (top is None or m.week_gain + gain > top["move"].week_gain + top["next_gain"]):
                top = {"position": position, "move": m, "next_gain": gain, "next_week": nxt,
                       "this_week": project("me", trial, ctx, schedule, lines, starters, joins=joins, so_far=so_far,
                                            leaves=leaves)}
        if top:
            used.add(top["move"].add.id)
            out.append(top)
    return out


def why_not(move: Move, price: AddPrice | None, chosen: list[Move] = (), held: bool = False) -> str:
    """Why a move isn't a recommended add, in words Nico can weigh. `held`:
    keepers that do nothing this week wait for the mid-week plan."""
    if price is None:
        return "no adds left"
    reason = rejection(move, price)
    if reason is None and held and waits(move):
        return "worth an add, but it does nothing this week: Wednesday's plan, if the week holds"
    if reason is None:
        names = " and ".join(m.add.name for m in chosen)
        return f"worth an add, but this week's go to {names}" if names else "worth an add, but you have none left this week"
    if move.drop and move.long_term < 0 and reason.startswith("worth"):
        return (f"dropping {move.drop.name} costs about {-move.long_term:.0f} pts later, more than this week's "
                f"{100 * (move.win_after - move.win_before):+.0f} win-pts make up for")
    return reason


def adds_used(adds: list[dict], week_days: list[dt.date]) -> tuple[int, int]:
    """(this season, this week) adds made, from the ledger (state["adds"]):
    each counts in the week it was made."""
    first, last = week_days[0].isoformat(), week_days[-1].isoformat()
    return len(adds), sum(1 for a in adds if first <= a["date"] <= last)


def max_moves(season_used: int, week_used: int) -> int:
    return max(0, min(MAX_ADDS_PER_WEEK - week_used, MAX_ADDS_PER_SEASON - season_used))


def _games(n: int) -> str:
    return f"{n} game{'' if n == 1 else 's'}"


def _pct(p: float) -> str:
    return f"{min(max(p, 0.01), 0.99):.0%}"


def move_text(move: Move, opened_by: str | None = None) -> str:
    """The add as a message. `opened_by`: whose IR move opens the spot it fills."""
    p = move.add
    if move.ir_slot:
        return stash_text(move)
    head = f"Add {p.name} ({p.team}, {'/'.join(p.positions)}, {_games(move.games)} left this week)"
    if move.plays_from:
        head += (f". He's on waivers: claim him, he plays from {move.plays_from:%a %d %b}, "
                 "and a claim puts you last in waiver priority")
    drop = (f"drop {move.drop.name}" if move.drop else
            f"into the spot moving {opened_by} to IR opens" if opened_by else "into your open roster spot")
    detail = [f"{move.week_gain:+.1f} pts this week, win {_pct(move.win_before)} -> {_pct(move.win_after)}",
              f"{move.next_weeks:+.1f} over the next two weeks"]
    # The same long-run view that ranked the move, not just its next two weeks.
    if move.long_term >= KEEPER_LONG_RUN:
        detail.append(f"a keeper: ahead of {move.drop.name if move.drop else 'your roster'} over the coming weeks "
                      f"(long run {move.long_term:+.0f})")
    elif move.long_term < 0 or move.next_weeks < -1:
        detail.append("a streamer: drop him again when his games are done")
    return f"{head}, {drop}.\n" + "; ".join(detail) + (
        ".\nTap Done once it's made in Yahoo (Other drop if you dropped someone else), or Taken if someone has him.")


def stash_text(move: Move) -> str:
    """An IR stash as a message: into the empty slot now, who goes once he's back."""
    p = move.add
    tag = "IR" if move.ir_slot == "IR" else "IR or O"
    head = (f"Stash {p.name} ({p.team}, {'/'.join(p.positions)}, injured): add him straight into your empty "
            f"{move.ir_slot} slot, no drop now (Yahoo must tag him {tag}; check before you add)")
    if move.plays_from:
        head += f". He's on waivers: a claim, and it puts you last in waiver priority"
    later = (f"once he's back, he needs an active spot: drop {move.later_drop.name} then"
             if move.later_drop else "once he's back, he needs an active spot")
    return (f"{head}.\n{later}; long run {move.long_term:+.0f} pts, net of that drop.\n"
            "Tap Done once it's made in Yahoo, or Taken if someone has him.")
