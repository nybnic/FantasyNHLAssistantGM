"""The season: playoff and title odds, and how much this week's matchup
matters next to a typical one.

H2H decides the title in three playoff weeks (8 of 16 teams, reseeded each
round, a tied week to the higher seed), reached by the regular season's wins
(ties in the standings broken by points for: Yahoo's usual rule, not yet
confirmed for this league). So a week's win is worth what it does to the
title odds, and that varies: a bubble team's late weeks matter more than a
locked-in team's.

`simulate` plays out the rest of the season many times:
- each team scores a normal week around its projected weekly mean
  (`team_strengths`: the best lineup over a typical week), with its spread;
- my opponents are the real schedule (config.league.SCHEDULE), the other 14
  teams are paired at random each week (the league's full schedule isn't
  recorded: a judgment call that blurs who-plays-whom, not team strength);
- this week's result is mine with the plan's P(win);
- standings start from the latest recorded ones (`state["standings"]`), else
  even, and rank by wins then points for;
- the top 8 play reseeded rounds: 1 v 8, 2 v 7, ...

A week's leverage is P(playoffs | win it) - P(playoffs | lose it): what a
regular-season win buys, mostly. Measured on the title instead, it drowned in
the simulated brackets' noise (the same setup read 0.76x to 1.12x a typical
week across seeds); what it leaves out is seeding, which only changes who you
meet. Untested: there is no standings history to backtest it on, and the
numbers are only as good as the team projections, which run a little high
(model/CLAUDE.md). Known bias: this week's result is forced without its
points, so its leverage leaves out the points-for tiebreak and reads ~10% low
next to later weeks' (even teams: 0.91x where 1x is due).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from config.league import LEAGUE_TEAMS, PLAYOFF_TEAMS, PLAYOFF_WEEKS, REGULAR_SEASON_WEEKS, SCHEDULE

SIMS = 10000


@dataclass
class SeasonOdds:
    playoffs: float  # P(make the playoffs)
    title: float  # P(win the title)
    leverage: float  # this week: P(playoffs | win it) - P(playoffs | lose it)
    typical: float  # the average leverage of the regular-season weeks left after this one
    seed: float  # expected seed when making the playoffs
    by_week: dict[int, float] = field(default_factory=dict)  # regular-season week -> leverage

    @property
    def weight(self) -> float:
        """This week's win next to a typical remaining week's (1 = typical)."""
        return self.leverage / self.typical if self.typical > 0 else 1.0


def simulate(strengths: dict[str, tuple[float, float]], me: str, week: int, p_win: float,
             standings: dict[str, dict] | None = None, sims: int = SIMS, seed: int = 7,
             schedule: tuple[str, ...] = SCHEDULE, pairs: list | None = None) -> SeasonOdds | None:
    """`strengths`: team -> (mean, sd) of its weekly points, every team
    including `me`. `week`: this week, still being played (its result is mine
    with `p_win`). `standings`: team -> {"w", "pf"} before this week. `pairs`:
    this week's other matchups (All Matchups screenshots), played instead of
    random ones when they cover every other team. None outside the regular
    season, or without every team's strength.
    This week's leverage compares the same simulated seasons with the week
    won and lost (paired: they differ only where the result flips a playoff
    spot); a later week's, seasons that won it against seasons that lost it."""
    weeks_left = list(range(week, REGULAR_SEASON_WEEKS + 1))
    if (not 1 <= week <= REGULAR_SEASON_WEEKS or len(strengths) < LEAGUE_TEAMS or me not in strengths
            or any(schedule[w - 1] not in strengths for w in weeks_left)):
        return None
    names = [me] + sorted(t for t in strengths if t != me)

    index = {t: i for i, t in enumerate(names)}
    others = {index[schedule[week - 1]], 0}
    fixed = [(index[a], index[b]) for a, b in (pairs or []) if a in index and b in index
             and not {index[a], index[b]} & others]
    covered = {i for pair in fixed for i in pair}
    fixed = fixed if len(covered) == len(names) - 2 and len(fixed) * 2 == len(covered) else None

    def run(p: float):
        return _season(names, strengths, standings or {}, weeks_left, schedule, p, sims, seed, fixed)

    made, title, my_seed, my_won = run(p_win)
    by_week = {}
    for k, w in enumerate(weeks_left[1:], 1):
        won = my_won[:, k]
        if won.any() and (~won).any():
            by_week[w] = float(made[won].mean() - made[~won].mean())
    leverage = float(run(1.0)[0].mean() - run(0.0)[0].mean())
    return SeasonOdds(
        playoffs=float(made.mean()), title=float(title.mean()), leverage=leverage,
        typical=float(np.mean(list(by_week.values()))) if by_week else leverage,
        seed=float(my_seed[made].mean()) if made.any() else 0.0, by_week={week: leverage, **by_week},
    )


def _season(names, strengths, standings, weeks_left, schedule, p_win, sims, seed, fixed=None):
    """(made the playoffs, won the title, my seed, my result in each week left)
    per simulated season. The same seed gives the same seasons whatever `p_win`.
    `fixed`: this week's other pairings (team indexes), when known."""
    rng = np.random.default_rng(seed)
    index = {t: i for i, t in enumerate(names)}
    mean = np.array([strengths[t][0] for t in names])
    sd = np.array([strengths[t][1] for t in names])
    wins = np.tile(np.array([float(standings.get(t, {}).get("w", 0)) for t in names]), (sims, 1))
    pf = np.tile(np.array([float(standings.get(t, {}).get("pf", 0.0)) for t in names]), (sims, 1))
    my_won = np.zeros((sims, len(weeks_left)), dtype=bool)
    rows = np.arange(sims)
    for k, w in enumerate(weeks_left):
        score = rng.normal(mean, sd, size=(sims, len(names)))
        pf += score
        opponent = index[schedule[w - 1]]
        others = np.array([i for i in range(1, len(names)) if i != opponent])
        order = others[rng.random((sims, len(others))).argsort(axis=1)]
        if k == 0 and fixed:
            order = np.tile(np.array([i for pair in fixed for i in pair]), (sims, 1))
        for a, b in zip(order[:, 0::2].T, order[:, 1::2].T):
            a_won = score[rows, a] > score[rows, b]
            wins[rows, a] += a_won
            wins[rows, b] += ~a_won
        draw = rng.random(sims)
        won = draw < p_win if k == 0 else score[:, 0] > score[:, opponent]
        my_won[:, k] = won
        wins[:, 0] += won
        wins[:, opponent] += ~won
    seeds = np.lexsort((-pf, -wins))[:, :PLAYOFF_TEAMS]  # by wins, then points for
    made = (seeds == 0).any(axis=1)
    return made, _playoffs(seeds, mean, sd, rng), (seeds == 0).argmax(axis=1) + 1, my_won


def _playoffs(seeds: np.ndarray, mean: np.ndarray, sd: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Whether team 0 (me) wins the title in each simulated bracket (`seeds`:
    team indexes, best seed first). Reseeded: each round the best remaining
    seed plays the worst; a tie goes to the higher seed."""
    alive = seeds.copy()  # stays in seed order: the higher seed is always on the left
    for _ in PLAYOFF_WEEKS:
        half = alive.shape[1] // 2
        high, low = alive[:, :half], alive[:, ::-1][:, :half]
        high_won = rng.normal(mean[high], sd[high]) >= rng.normal(mean[low], sd[low])
        winners = np.where(high_won, high, low)
        alive = _in_seed_order(seeds, winners)
    return alive[:, 0] == 0


def _in_seed_order(seeds: np.ndarray, winners: np.ndarray) -> np.ndarray:
    """`winners` (team indexes per row) reordered by their seed in `seeds`."""
    rank = np.argmax(seeds[:, :, None] == winners[:, None, :], axis=1)  # each winner's seed position
    return np.take_along_axis(winners, np.argsort(rank, axis=1), axis=1)


# Below this, a win barely moves the playoff odds (locked in, or out): said
# so instead of a ratio of two near-zero numbers (wording only).
MIN_LEVERAGE = 0.01


def text(odds: SeasonOdds) -> str:
    """One line for the weekly plan."""
    head = f"Season: playoffs {odds.playoffs:.0%}" + (f" (seed ~{odds.seed:.0f})" if odds.playoffs >= 0.05 else "")
    head += f", title {odds.title:.0%}."
    if odds.leverage < MIN_LEVERAGE:
        return head + " This week's result barely moves your playoff odds."
    weight = odds.weight
    how = ("about like a typical week left" if 0.8 <= weight <= 1.25
           else f"{weight:.1f}x a typical week left")
    return head + f" A win this week: playoffs +{100 * odds.leverage:.0f} pts, {how}."
