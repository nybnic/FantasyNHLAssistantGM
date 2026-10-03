"""Expected fantasy points (xFP) per game for skaters and per start for goalies.

Skaters: projected stat per game = skill x role.
- Skill is each stat's rate per second of ice time (per second of power-play
  time for PPG/PPA). It is a Bayesian blend of a prior and this season's
  results: the prior counts as `k[stat]` games' worth of evidence, so stable
  stats (shots, hits, blocks) follow current play quickly while noisy ones
  (GWG, shorthanded points, plus/minus) lean on the prior.
- Role is ice time per game: an exponentially weighted average of this
  season's games (recent games count most) shrunk toward the prior. A line or
  power-play promotion shows up here within a few games, before it shows up
  in points.

Goalies: xFP per start = 1 + 4*P(win) - E[GA] + 0.35*E[saves] + 5*P(shutout),
each term a Bayesian blend of the goalie's history and this season's starts.
Relief appearances are left out: they can't be planned for.

Priors come from past seasons (`skater_priors` / `goalie_priors`), weighted
toward the most recent, and regressed toward the position average so small
samples don't produce extreme rates. Skater priors are then aged
(`age_factor`): history describes a player as he was, but young players
improve and veterans decline.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from clients.nhl_stats import GoalieGame, SkaterGame
from config.league import GOALIE_WEIGHTS, SKATER_WEIGHTS, fantasy_points

SKATER_STATS = tuple(SKATER_WEIGHTS)
PP_STATS = ("ppg", "ppa")
GOALIE_START_STATS = ("w", "ga", "sv", "so")

SEASON_WEIGHTS = (1.0, 0.6, 0.35)  # most recent past season first

# Prior strength, in games (skaters) or starts (goalies) of evidence.
# Skater values were doubled after the 2025-26 backtest (flat optimum at 2-3x).
SKATER_K: dict[str, float] = {
    "g": 60, "a": 50, "pm": 80, "pim": 50, "ppg": 50, "ppa": 40,
    "shg": 200, "sha": 200, "gwg": 200, "sog": 20, "fow": 10, "hit": 16, "blk": 16,
}
GOALIE_K: dict[str, float] = {"w": 30, "ga": 20, "sv": 15, "so": 60}
PRIOR_REGRESSION_GAMES = 20  # pulls thin histories toward the position average

# Aging, per position group: (peak age, change per year younger than the
# peak, change per year older), scaling every scoring rate. Age is on Oct 1
# of the projected season. Fit on 2024-25 + 2025-26 (history-only projection
# vs actual points per game, 20+ GP). Out of sample - fit on 2024-25, checked
# on 2025-26 - it cut per-game error 0.628 -> 0.580 and removed most of the
# bias (history had overrated D over 30 by ~0.4 pts/game, underrated F under
# 22 by ~0.65).
#
# The young side was refit on every skater (2026-10-03): the 20+ GP fit only
# saw young players who stuck, so applied to all it over-projected under-24s
# by +0.09 (2024-25) and +0.18 (2025-26) pts/game. No youth boost had the best
# MAE in both seasons, next week and next 4 weeks, every pool (all, projected
# top 450, under 24), pairwise equal or better; under-24 bias -0.09 / -0.01.
# The old side is kept: it removes most of the veterans' over-projection.
AGE_CURVES: dict[str, tuple[float, float, float]] = {"F": (26.0, 0.0, 0.009), "D": (23.5, 0.0, 0.0114)}
AGE_FACTOR_RANGE = (0.8, 1.25)
PRIOR_REGRESSION_STARTS = 15


@dataclass
class Params:
    k_scale: float = 1.0  # multiplies every SKATER_K / GOALIE_K
    toi_prior_games: float = 3.0
    # Kept after the role-blend grid (scripts/backtest.py --role-grid, 2026-10-03):
    # no half-life (2-9) or prior weight (0.5-3) beat 6 / 3 on both seasons.
    toi_halflife_games: float = 6.0


@dataclass
class SkaterPrior:
    position: str
    toi: float  # seconds per game
    pp_toi: float
    per_toi: dict[str, float]  # stat per second of TOI (PP stats: per second of PP TOI)


@dataclass
class GoaliePrior:
    per_start: dict[str, float]  # w, ga, sv, so per start


@dataclass
class SkaterProjection:
    player_id: int
    name: str
    position: str
    team: str
    games: int  # games played this season before the cutoff
    toi: float
    pp_toi: float
    per_game: dict[str, float] = field(default_factory=dict)

    @property
    def xfp(self) -> float:
        return fantasy_points(self.per_game, SKATER_WEIGHTS)


@dataclass
class GoalieProjection:
    player_id: int
    name: str
    team: str
    starts: int
    per_start: dict[str, float] = field(default_factory=dict)

    @property
    def xfp(self) -> float:
        return fantasy_points({"gs": 1.0, **self.per_start}, GOALIE_WEIGHTS)


def _position_group(position: str) -> str:
    return "D" if position == "D" else "F"


def skater_priors(past_seasons: list[list[SkaterGame]]) -> tuple[dict[int, SkaterPrior], dict[str, SkaterPrior]]:
    """Per-player priors from past seasons (most recent first), plus a
    fallback prior per position group ("F"/"D") for players with no history."""
    toi: dict[int, float] = defaultdict(float)
    pp_toi: dict[int, float] = defaultdict(float)
    games: dict[int, float] = defaultdict(float)
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    position: dict[int, str] = {}
    for season_weight, season in zip(SEASON_WEIGHTS, past_seasons):
        for g in season:
            pid = g.player_id
            position.setdefault(pid, g.position)
            games[pid] += season_weight
            toi[pid] += season_weight * g.toi
            pp_toi[pid] += season_weight * g.pp_toi
            for stat in SKATER_STATS:
                totals[pid][stat] += season_weight * g.stats.get(stat, 0.0)

    # Position-average rates (per TOI) and a fringe-player TOI for newcomers.
    group_rates: dict[str, dict[str, float]] = {}
    group_toi: dict[str, tuple[float, float]] = {}
    for group in ("F", "D"):
        members = [p for p in games if _position_group(position[p]) == group]
        sum_toi = sum(toi[p] for p in members)
        sum_pp = sum(pp_toi[p] for p in members)
        group_rates[group] = {
            s: sum(totals[p][s] for p in members) / ((sum_pp if s in PP_STATS else sum_toi) or 1.0)
            for s in SKATER_STATS
        }
        per_game = sorted(toi[p] / games[p] for p in members)
        per_game_pp = sorted(pp_toi[p] / games[p] for p in members)
        group_toi[group] = (per_game[len(per_game) // 4], per_game_pp[len(per_game_pp) // 4])

    fallback = {
        group: SkaterPrior(
            position=group,
            toi=group_toi[group][0],
            pp_toi=group_toi[group][1],
            per_toi=dict(group_rates[group]),
        )
        for group in ("F", "D")
    }

    priors = {}
    for pid, n in games.items():
        group = _position_group(position[pid])
        base = fallback[group]
        m = PRIOR_REGRESSION_GAMES
        per_toi = {}
        for s in SKATER_STATS:
            exposure = pp_toi[pid] if s in PP_STATS else toi[pid]
            base_exposure = m * (base.pp_toi if s in PP_STATS else base.toi)
            per_toi[s] = (totals[pid][s] + base_exposure * base.per_toi[s]) / ((exposure + base_exposure) or 1.0)
        priors[pid] = SkaterPrior(
            position=position[pid],
            toi=(toi[pid] + 5 * base.toi) / (n + 5),
            pp_toi=(pp_toi[pid] + 5 * base.pp_toi) / (n + 5),
            per_toi=per_toi,
        )
    return priors, fallback


def age_factor(position: str, age: float) -> float:
    peak, younger, older = AGE_CURVES[_position_group(position)]
    factor = 1 + younger * max(peak - age, 0.0) - older * max(age - peak, 0.0)
    return min(max(factor, AGE_FACTOR_RANGE[0]), AGE_FACTOR_RANGE[1])


def aged(prior: SkaterPrior, age: float | None) -> SkaterPrior:
    """The prior with its scoring rates scaled for age (ice time unchanged)."""
    if age is None:
        return prior
    f = age_factor(prior.position, age)
    return SkaterPrior(prior.position, prior.toi, prior.pp_toi, {s: r * f for s, r in prior.per_toi.items()})


def with_projection(prior: SkaterPrior, row: dict, weight: float) -> SkaterPrior:
    """Blend an outside full-season projection into a prior, per game.

    `row` has gp, toi (minutes per game) and season-total stats (the shape of
    clients.dfo_projections rows). Stats it doesn't cover (e.g. SHG, SHA, GWG)
    and power-play ice time keep the prior's values."""
    toi = weight * row["toi"] * 60 + (1 - weight) * prior.toi
    per_toi = dict(prior.per_toi)
    for s, total in row["stats"].items():
        pp = s in PP_STATS
        prior_exposure = prior.pp_toi if pp else prior.toi
        blended_per_game = weight * total / row["gp"] + (1 - weight) * prior.per_toi[s] * prior_exposure
        exposure = prior.pp_toi if pp else toi
        per_toi[s] = blended_per_game / exposure if exposure else 0.0
    return SkaterPrior(position=prior.position, toi=toi, pp_toi=prior.pp_toi, per_toi=per_toi)


# Weight of an outside preseason projection vs our history prior. It can't be
# backtested (no archived copies), so it starts at an even split and gets
# checked in-season. Players with no NHL history take the projection whole:
# the history prior treats them as fringe players and underrated relevant
# newcomers by ~1.3 pts/game early in 2025-26.
PROJECTION_WEIGHT = 0.5


def season_skater_priors(
    past_seasons: list[list[SkaterGame]], projections: dict[int, dict], weight: float = PROJECTION_WEIGHT,
    ages: dict[int, float] | None = None,
) -> tuple[dict[int, SkaterPrior], dict[str, SkaterPrior]]:
    """History priors, aged (`ages`: NHL id -> age at the season's start),
    with outside projections (NHL id -> row) blended in. Only the history
    part is aged: that's the part the age curve was fit and checked on."""
    priors, fallback = skater_priors(past_seasons)
    ages = ages or {}
    priors = {pid: aged(p, ages.get(pid)) for pid, p in priors.items()}
    for pid, row in projections.items():
        if row["position"] == "G":
            continue
        base = priors.get(pid)
        priors[pid] = with_projection(
            base or fallback[_position_group(row["position"])], row, weight if base else 1.0
        )
    return priors, fallback


def _weighted_recent(values: list[float], prior: float, prior_weight: float, halflife: float) -> float:
    decay = 0.5 ** (1.0 / halflife)
    num = prior_weight * prior
    den = prior_weight
    weight = 1.0
    for value in reversed(values):
        num += weight * value
        den += weight
        weight *= decay
    return num / den


def project_skater(
    prior: SkaterPrior, games: list[SkaterGame], params: Params | None = None
) -> SkaterProjection:
    """Projection from a prior and this season's games (chronological, all
    before the decision date). Name/team come from the latest game."""
    params = params or Params()
    last = games[-1] if games else None
    toi = _weighted_recent([g.toi for g in games], prior.toi, params.toi_prior_games, params.toi_halflife_games)
    pp_toi = _weighted_recent(
        [g.pp_toi for g in games], prior.pp_toi, params.toi_prior_games, params.toi_halflife_games
    )
    season_toi = sum(g.toi for g in games)
    season_pp_toi = sum(g.pp_toi for g in games)

    per_game = {}
    for s in SKATER_STATS:
        k = SKATER_K[s] * params.k_scale
        pp = s in PP_STATS
        prior_exposure = k * (prior.pp_toi if pp else prior.toi)
        exposure = season_pp_toi if pp else season_toi
        total = sum(g.stats.get(s, 0.0) for g in games)
        denom = prior_exposure + exposure
        rate = (prior_exposure * prior.per_toi[s] + total) / denom if denom else 0.0
        per_game[s] = rate * (pp_toi if pp else toi)

    return SkaterProjection(
        player_id=last.player_id if last else -1,
        name=last.name if last else "",
        position=last.position if last else prior.position,
        team=last.team if last else "",
        games=len(games),
        toi=toi,
        pp_toi=pp_toi,
        per_game=per_game,
    )


def goalie_priors(past_seasons: list[list[GoalieGame]]) -> tuple[dict[int, GoaliePrior], GoaliePrior]:
    """Per-goalie per-start priors from past seasons (most recent first), plus
    a fallback for goalies with no history (a below-average backup)."""
    starts: dict[int, float] = defaultdict(float)
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for season_weight, season in zip(SEASON_WEIGHTS, past_seasons):
        for g in season:
            if not g.started:
                continue
            starts[g.player_id] += season_weight
            for s in GOALIE_START_STATS:
                totals[g.player_id][s] += season_weight * g.stats.get(s, 0.0)

    league_starts = sum(starts.values()) or 1.0
    league = {s: sum(t[s] for t in totals.values()) / league_starts for s in GOALIE_START_STATS}
    # Goalies without history are usually call-ups: shade toward worse results.
    fallback = GoaliePrior(
        per_start={"w": league["w"] * 0.85, "ga": league["ga"] * 1.08, "sv": league["sv"] * 0.98,
                   "so": league["so"] * 0.7}
    )

    priors = {}
    m = PRIOR_REGRESSION_STARTS
    for pid, n in starts.items():
        priors[pid] = GoaliePrior(
            per_start={s: (totals[pid][s] + m * league[s]) / (n + m) for s in GOALIE_START_STATS}
        )
    return priors, fallback


def project_goalie(prior: GoaliePrior, games: list[GoalieGame], params: Params | None = None) -> GoalieProjection:
    params = params or Params()
    started = [g for g in games if g.started]
    last = games[-1] if games else None
    n = len(started)
    per_start = {}
    for s in GOALIE_START_STATS:
        k = GOALIE_K[s] * params.k_scale
        total = sum(g.stats.get(s, 0.0) for g in started)
        per_start[s] = (k * prior.per_start[s] + total) / (k + n)
    return GoalieProjection(
        player_id=last.player_id if last else -1,
        name=last.name if last else "",
        team=last.team if last else "",
        starts=n,
        per_start=per_start,
    )
