"""The replay harness: the 2025-26 season played under rule variants, scored in wins.

Leagues of 16 teams snake-drafted on preseason projections (scripts/check_sigma.py:
3 C, 2 LW, 2 RW, 4 D, 3 G, NHL positions). Every team in turn is managed by
the bot's add rule while the other 15 are managed lazily (a goalie who lost
his role, or a skater out 10+ team games, is replaced by the best free agent
at his position). Each Monday the managed team weighs every skater swap
(free agents shortlisted per position by this week and by season value,
times its 2 weakest forwards and D) and every goalie move (a free agent goalie
for one of those skaters or for my weakest goalie, a skater for my weakest
goalie when I have more than two; goalies valued per week with the goalie
minimum, expected points x P(3+ starts), as the bot does) the way
engine/matchup.py does:
- this week: the change in P(win) against that week's real opponent
  (margin at MARGIN_REALIZES, the gain in full);
- the next 2 weeks against their opponents (matchup.ahead_value: margins at
  MARGIN_REALIZES_AHEAD, gains at GAP_REALIZES);
- later points (matchup.horizon: the 6-week rate x the weeks left x
  LONG_RUN_DISCOUNT, a streaming swap only over the hold, the keep share in
  between) x later_weight (a typical week's sigma and the league's tau);
and makes the best one (then the next, at most 2 a week) when it beats the
add price, within the budget's pace (30 regular-season adds; 6 kept for the
playoffs). Projections: priors aged as the bot's, this season's games before
that Monday; a skater who missed games returns along availability's curves.
Scored on what happened: each day's best projected lineup among the players
who played, the goalie minimum. Wins against the week's opponent (random
pairings, the same for every variant), paired by team.

Variants (--variants, default all): the keep share (`KEEP_RAMP`, the on/off
switch it replaced, all hold, all season), the streaming spots
(`STREAMING_SPOTS`), `LONG_RUN_DISCOUNT`, the price, the price rule (PRICE_RULES:
the live bot's price solved each Monday over the weeks' logged candidates, or a
fixed bar times a pacing factor; these pace by the price alone, while the
fixed-price variants also stop at an even pace), and planned exits (EXITS:
a streamer's hold may end at a week boundary, so a bad week after it doesn't
count against him, and that Monday he is offered as a drop). One variant is the
reference; the others are reported as wins a season vs it (+/- SE, paired).

The audit (--audit; step 0 of replacing the keep share with tenure, Nico 2026-10-23):
each move a variant makes, with its predicted parts on its Monday (points this week,
in each week ahead, later; the keep share, the 6-week rate), against what it
brought week by week while it stood (the real score against the same roster with
that one swap undone, until the add leaves), and how long the adds stayed: by
keep share, by the swap's kind (a forward for a D...), by the 6-week rate, and the
values against the wins they added.

Simplifications, labeled: single NHL positions (Yahoo gives many two); goalie
starts at each goalie's share of his team's last 10 (no form, no confirmed
starters); decisions on Mondays only; each team manages alone (the free-agent pool
ignores the other teams' pickups); no IR slots; lineups set knowing who plays.

    python -m scripts.replay                       # 4 leagues, all variants
    python -m scripts.replay --leagues 8 --workers 4
    python -m scripts.replay --variants "ramp 1.0" "switch" "spots 2"
    python -m scripts.replay --variants "live price" "pace 0.08" --reference "live price"
    python -m scripts.replay --variants "pace 0.08 k2" --reference "pace 0.08 k2" --audit
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from statistics import mean

import numpy as np

from clients import nhl_stats
from config.league import GOALIE_WEIGHTS, MIN_GOALIE_GAMES_PER_WEEK, SKATER_WEIGHTS, fantasy_points
from engine import addprice, availability, matchup, tenure
from league.roster import RosterPlayer
from model.projections import aged, project_goalie, project_skater
from scripts.backtest import history_of
from scripts.check_sigma import POS, TARGET, draft, load

SLOTS = {"C": 2, "LW": 2, "RW": 2, "D": 4}
REGULAR_ADDS = 30  # 36 less the 6 kept for the playoffs
PRICE = 0.09  # win-pts an add must buy: about the bot's (8.9-9.1, Oct 10-11)
LAZY_OUT_GAMES = 10  # a lazy manager replaces a skater out this many team games
LOST_ROLE = 0.15  # a goalie under this share of his team's last 10 starts has lost his role
REFERENCE = "keeper add"  # the live rule since 2026-10-20 (before: "ramp 1.0")
# name -> (keep rule, streaming spots, long-run discount, price). keep: "add"
# (the live matchup.keep_share since 2026-10-20: judged on the add, so a keeper
# added onto a streaming spot counts the season; Nico, after Merilainen for
# Larsson, keeping Pinto, got only the hold's 4 later points to Merilainen for
# Pinto's 43), a ramp width in points a game (the rule to 2026-10-20: a
# streaming spot's drop holds, goalie swaps the season, else the ramp on the
# lower of the two gaps), "switch" (the rule before 2026-10-11: a streaming
# spot's drop holds, any other the season), 0.0 (every swap only while held)
# or "season" (every swap the season).
VARIANTS = {
    "ramp 1.0": (1.0, 3, 0.5, PRICE),
    "ramp 0.5": (0.5, 3, 0.5, PRICE),
    "ramp 2.0": (2.0, 3, 0.5, PRICE),
    "switch": ("switch", 3, 0.5, PRICE),
    "all hold": (0.0, 3, 0.5, PRICE),
    "all season": ("season", 3, 0.5, PRICE),
    "spots 2": (1.0, 2, 0.5, PRICE),
    "spots 4": (1.0, 4, 0.5, PRICE),
    "spots 5": (1.0, 5, 0.5, PRICE),
    "discount 0.35": (1.0, 3, 0.35, PRICE),
    "no swaps": (1.0, 3, 0.5, math.inf),
    "exit": (1.0, 3, 0.5, PRICE),
    "exit charged": (1.0, 3, 0.5, PRICE),
    "keeper add": ("add", 3, 0.5, PRICE),
    "skaters only": ("add", 3, 0.5, PRICE),
    # The price rule (Nico, 2026-10-21: "pacing to leave 6 for the playoffs; the
    # better the move the better to spend an add"; the candidate pool shouldn't
    # move the price). Moves valued as "keeper add"; no even-pace cap.
    "live price": ("add", 3, 0.5, ("live",)),
    **{f"pace {b:.2f}": ("add", 3, 0.5, ("pace", b, 1.0)) for b in (0.04, 0.06, 0.08, 0.10, 0.12)},
    **{f"pace {b:.2f} k2": ("add", 3, 0.5, ("pace", b, 2.0)) for b in (0.06, 0.08, 0.10)},
    # Tenure (Nico, 2026-10-23, roadmap item 8), at the live-like pacing price: step 1, the long
    # run from the whole season left instead of the 6-week rate x the weeks left (keep share and
    # hold as live); steps 1-2, each later day's gain x P(the add still stands then) by where he
    # sits on my roster (engine/tenure.py, fit on this harness's audit) x how far-off gaps realize,
    # no keep share, no hold, no LONG_RUN_DISCOUNT.
    "whole schedule": ("add", 3, 0.5, ("pace", 0.08, 2.0)),
    "tenure": ("tenure", 3, 0.5, ("pace", 0.08, 2.0)),
}
# Variants whose long run covers the whole season left (the rest: the next LONG_RUN_WEEKS).
WHOLE_SCHEDULE = {"whole schedule", "tenure"}
# The price rules, by the variants' price: a number is a fixed price (with the
# even-pace cap, as the harness always had); ("live",) is addprice.solve over the
# Monday pools logged so far (the newest POOL_WEEKS), at the pace the budget
# left needs; ("pace", bar, k) is bar x (the even pace / the pace the budget
# left needs) ** k: lower when adds were saved, higher when spent ahead of pace.
POOL_WEEKS = 12  # as gm_state.KEEP_POOL_WEEKS
# Variants that make no goalie moves (the harness before 2026-10-20): goalies
# change only when one loses his role.
SKATERS_ONLY = {"skaters only"}
# Planned exits (Nico, 2026-10-13: add Landeskog for week 4's games, stream him
# out before his weak week 5). A streaming swap's counted hold may end after
# this week or a week ahead instead of running the full hold; the variant takes
# whichever is worth most, and that Monday the add is offered as a drop (the
# exit happens only if a move then clears the price). "free": cutting the hold
# short costs nothing (the replacement add is a move of its own); "charged": it
# costs the unused share of the hold's add (price x days cut / days held).
EXITS = {"exit": "free", "exit charged": "charged"}


class Ctx:
    """What matchup's streaming-spot and keep-share helpers ask of a context."""

    def __init__(self, xfp: dict[int, float]):
        self._xfp = xfp

    def skater(self, pid, position, before=None):
        return type("P", (), {"xfp": self._xfp.get(pid, 0.0)})()


class Season:
    """2025-26 as the replay sees it: games, projections by Monday, availability."""

    def __init__(self, season: int = TARGET):
        s_priors, s_fallback, g_priors, g_fallback, skaters, goalies = load(season)
        born = {}
        for season_id in (season, *history_of(season)):
            born.update(nhl_stats.skater_birth_dates(season_id))
        start = dt.date(season // 10_000, 10, 1)
        self.s_priors = {pid: aged(p, (start - born[pid]).days / 365.25 if pid in born else None)
                         for pid, p in s_priors.items()}
        self.s_fallback, self.g_priors, self.g_fallback = s_fallback, g_priors, g_fallback
        self.by_skater, self.by_goalie = defaultdict(list), defaultdict(list)
        for g in skaters:
            self.by_skater[g.player_id].append(g)
        for g in goalies:
            self.by_goalie[g.player_id].append(g)
        self.goalie_ids = {pid for pid in self.by_goalie if pid not in self.by_skater}
        self.position = {pid: POS[gs[0].position] for pid, gs in self.by_skater.items()}
        self.points = {(g.player_id, g.date): fantasy_points(g.stats, SKATER_WEIGHTS) for g in skaters}
        self.g_points = {(g.player_id, g.date): fantasy_points(g.stats, GOALIE_WEIGHTS) for g in goalies if g.started}
        self.team_days = defaultdict(set)  # team -> its game dates
        self.starts = defaultdict(list)  # team -> [(date, goalie id)]
        for g in goalies:
            self.team_days[g.team].add(g.date)
            if g.started:
                self.starts[g.team].append((g.date, g.player_id))
        for seq in self.starts.values():
            seq.sort()
        self.sorted_days = {t: sorted(ds) for t, ds in self.team_days.items()}
        first, last = skaters[0].date, skaters[-1].date
        monday = first + dt.timedelta(days=14 + (7 - (first + dt.timedelta(days=14)).weekday()) % 7)
        self.mondays = []
        while monday + dt.timedelta(days=7) <= last:
            self.mondays.append(monday)
            monday += dt.timedelta(days=7)
        self._proj, self._team, self._missed = {}, {}, {}

    def eligible(self, m: dt.date) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """Skaters with 3+ games before `m`, per position: (by this week's
        expected points, by points a game), best first."""
        if ("eligible", m) not in self._proj:
            xfp = self.proj(m)[0]
            this = [m + dt.timedelta(days=i) for i in range(7)]
            by_week, by_xfp = {pos: [] for pos in SLOTS}, {pos: [] for pos in SLOTS}
            for pid, gs in self.by_skater.items():
                if sum(1 for g in gs if g.date < m) < 3:
                    continue
                team = self.team(pid, m)
                week = sum(xfp.get(pid, 0.0) * self.play(pid, m, d) for d in this if d in self.team_days[team])
                by_week[self.position[pid]].append((week, pid))
                by_xfp[self.position[pid]].append((xfp.get(pid, 0.0), pid))
            self._proj[("eligible", m)] = ({p: [q for _, q in sorted(v, reverse=True)] for p, v in by_week.items()},
                                           {p: [q for _, q in sorted(v, reverse=True)] for p, v in by_xfp.items()})
        return self._proj[("eligible", m)]

    def s_prior(self, pid):
        return self.s_priors.get(pid) or self.s_fallback["D" if self.position[pid] == "D" else "F"]

    def draft_pool(self) -> dict[str, list[tuple[float, int]]]:
        pool = defaultdict(list)
        for pid, gs in self.by_skater.items():
            if len(gs) >= 20:
                pool[self.position[pid]].append((project_skater(self.s_prior(pid), []).xfp, pid))
        for pid in self.goalie_ids:
            if sum(g.started for g in self.by_goalie[pid]) >= 15:
                pool["G"].append((project_goalie(self.g_priors.get(pid, self.g_fallback), []).xfp * 0.55, pid))
        return pool

    def proj(self, m: dt.date) -> tuple[dict[int, float], dict[int, float]]:
        """Skaters' points a game and goalies' a start, from the games before `m`."""
        if m not in self._proj:
            s = {pid: project_skater(self.s_prior(pid), [g for g in gs if g.date < m]).xfp
                 for pid, gs in self.by_skater.items()}
            g = {pid: project_goalie(self.g_priors.get(pid, self.g_fallback), [x for x in self.by_goalie[pid]
                                                                                if x.date < m]).xfp
                 for pid in self.goalie_ids}
            self._proj[m] = (s, g)
        return self._proj[m]

    def team(self, pid: int, m: dt.date) -> str:
        """His team as of `m` (his last game before it; else his first)."""
        key = (pid, m)
        if key not in self._team:
            gs = self.by_skater.get(pid) or self.by_goalie.get(pid)
            before = [g for g in gs if g.date < m]
            self._team[key] = (before[-1] if before else gs[0]).team
        return self._team[key]

    def missed(self, pid: int, m: dt.date) -> int:
        """His team's games in a row he missed before `m`."""
        key = (pid, m)
        if key not in self._missed:
            played = {g.date for g in self.by_skater[pid] if g.date < m}
            n = 0
            for d in reversed([d for d in self.sorted_days.get(self.team(pid, m), []) if d < m]):
                if d in played:
                    break
                n += 1
            self._missed[key] = n
        return self._missed[key]

    def play(self, pid: int, m: dt.date, d: dt.date) -> float:
        n = self.missed(pid, m)
        return availability.HEALTHY_PLAY if n == 0 else availability.skater(None, True, (d - m).days, n).prob

    def share(self, pid: int, m: dt.date) -> float:
        recent = [p for d, p in self.starts[self.team(pid, m)] if d < m][-10:]
        return recent.count(pid) / len(recent) if recent else 0.0

    def played_games(self, pid: int, m: dt.date) -> int:
        return sum(1 for g in (self.by_skater.get(pid) or self.by_goalie.get(pid)) if g.date < m)


def _top(values: list[float], k: int) -> float:
    return sum(sorted(values, reverse=True)[:k])


def at_least(probs: list[float], k: int) -> float:
    """P(at least k of these independent starts happen)."""
    dist = [1.0] + [0.0] * k  # dist[j]: P(j starts so far), the last bucket k or more
    for p in probs:
        for j in range(k, -1, -1):
            dist[j] = dist[j] * (1 - p) + (dist[j - 1] * p if j else 0.0) + (dist[j] * p if j == k else 0.0)
    return dist[k]


class Week:
    """One roster's projected days, by position, from a Monday: what a swap gains each day.
    Goalies count by the week: the two best expected starts each day, all of the
    week's goalie points times P(reaching the goalie minimum), as matchup does."""

    def __init__(self, s: Season, roster: list[int], m: dt.date, days: list[dt.date]):
        self.s, self.m = s, m
        xfp, self.gx = s.proj(m)
        self.vals: dict[dt.date, dict[str, list[float]]] = {}
        self.goalies = [pid for pid in roster if pid in s.goalie_ids]
        for d in days:
            by_pos = {pos: [] for pos in SLOTS}
            for pid in roster:
                if pid in s.goalie_ids or d not in s.team_days[s.team(pid, m)]:
                    continue
                by_pos[s.position[pid]].append(xfp.get(pid, 0.0) * s.play(pid, m, d))
            self.vals[d] = by_pos
        self._g = {}

    def goalie_weeks(self, goalies: list[int], days) -> dict[int, tuple[list[dt.date], float, float]]:
        """By week from the Monday: (its days among `days`, expected goalie points
        with the minimum, without it)."""
        key = (tuple(sorted(goalies)), tuple(days))
        if key not in self._g:
            s, m = self.s, self.m
            weeks: dict[int, list[dt.date]] = defaultdict(list)
            for d in days:
                weeks[(d - m).days // 7].append(d)
            out = {}
            for k, ds in weeks.items():
                starts = []
                for d in ds:
                    playing = sorted(((s.share(g, m) * self.gx.get(g, 0.0), s.share(g, m)) for g in goalies
                                      if d in s.team_days[s.team(g, m)]), reverse=True)[:2]
                    starts += [(prob, pts) for pts, prob in playing]
                plain = sum(pts for _, pts in starts)
                out[k] = (ds, plain * at_least([p for p, _ in starts], MIN_GOALIE_GAMES_PER_WEEK), plain)
            self._g[key] = out
        return self._g[key]

    def total(self, days) -> tuple[float, float]:
        """(expected points, variance) over `days`."""
        sk = sum(_top(self.vals[d][pos], k) for d in days for pos, k in SLOTS.items())
        weeks = self.goalie_weeks(self.goalies, days).values()
        g, plain = sum(w[1] for w in weeks), sum(w[2] for w in weeks)
        return (sk + g, matchup.SKATER_VARIANCE_PER_XFP * sk
                + matchup.GOALIE_START_VARIANCE * plain / matchup.TYPICAL_START_XFP)

    def gain(self, add: int, drop: int, days) -> dict[dt.date, float]:
        """Points gained each day by the swap; a goalie's change by the week,
        spread over its days."""
        s, m = self.s, self.m
        xfp = s.proj(m)[0]
        add_g, drop_g = add in s.goalie_ids, drop in s.goalie_ids
        out: dict[dt.date, float] = {}
        if not (add_g and drop_g):  # a skater comes or goes
            pos_add = None if add_g else s.position[add]
            pos_drop = None if drop_g else s.position[drop]
            ta = set() if add_g else s.team_days[s.team(add, m)]
            td = set() if drop_g else s.team_days[s.team(drop, m)]
            for d in days:
                a_plays, d_plays = d in ta, d in td
                if not a_plays and not d_plays:
                    continue
                g = 0.0
                for pos in {pos_add, pos_drop} - {None}:
                    vals = list(self.vals[d][pos])
                    before = _top(vals, SLOTS[pos])
                    if d_plays and pos == pos_drop:
                        vals.remove(xfp.get(drop, 0.0) * s.play(drop, m, d))
                    if a_plays and pos == pos_add:
                        vals.append(xfp.get(add, 0.0) * s.play(add, m, d))
                    g += _top(vals, SLOTS[pos]) - before
                if g:
                    out[d] = g
        if add_g or drop_g:  # the goalies change
            after = [g for g in self.goalies if g != drop] + ([add] if add_g else [])
            now, then = self.goalie_weeks(self.goalies, days), self.goalie_weeks(after, days)
            for k, (ds, value, _) in now.items():
                change = then[k][1] - value
                for d in ds:
                    out[d] = out.get(d, 0.0) + change / len(ds)
        return out


def actual(s: Season, roster: list[int], m: dt.date) -> float:
    """The week's real score: each day the best projected lineup among those who played."""
    xfp, gx = s.proj(m)
    sk = gp = 0.0
    starts = 0
    for i in range(7):
        d = m + dt.timedelta(days=i)
        for pos, k in SLOTS.items():
            played = sorted(((xfp.get(pid, 0.0), pid) for pid in roster if pid not in s.goalie_ids
                             and s.position[pid] == pos and (pid, d) in s.points), reverse=True)[:k]
            sk += sum(s.points[(pid, d)] for _, pid in played)
        started = sorted(((gx.get(pid, 0.0), pid) for pid in roster if (pid, d) in s.g_points), reverse=True)[:2]
        gp += sum(s.g_points[(pid, d)] for _, pid in started)
        starts += len(started)
    return sk + (gp if starts >= MIN_GOALIE_GAMES_PER_WEEK else 0.0)


def lazy(s: Season, roster: list[int], m: dt.date, taken: set[int]) -> list[int]:
    """A manager who only replaces a goalie who lost his role or a skater out LAZY_OUT_GAMES."""
    roster = list(roster)
    xfp, gx = s.proj(m)
    for i, pid in enumerate(list(roster)):
        if pid in s.goalie_ids:
            if not (s.share(pid, m) < LOST_ROLE and [x for x in s.starts[s.team(pid, m)] if x[0] < m]):
                continue
            new = best_goalie(s, m, taken)
        elif s.missed(pid, m) >= LAZY_OUT_GAMES:
            new = next((q for q in s.eligible(m)[1][s.position[pid]] if q not in taken and s.missed(q, m) == 0), None)
        else:
            continue
        if new:
            roster[i] = new
            taken.add(new)
    return roster


def best_goalie(s: Season, m: dt.date, taken: set[int]) -> int | None:
    gx = s.proj(m)[1]
    return max(((s.share(q, m) * gx.get(q, 0.0), q) for q in s.goalie_ids
                if q not in taken and s.share(q, m) >= 0.3), default=(0, None))[1]


def run_league(args: tuple) -> dict:
    """One league: every team managed under every variant. Returns wins, points and adds per (team,
    variant), and with `audit` each move's predicted parts and what it brought (realized)."""
    league_no, seed, names, managed, price_all, *rest = args
    audit = bool(rest and rest[0])
    s = Season()
    OPP_WEEKS.clear()
    rng = random.Random(seed * 1000 + league_no)
    rosters = draft(s.draft_pool(), 16, rng)
    drafted = {pid for r in rosters for ids in r.values() for pid in ids}
    mondays = s.mondays
    n_weeks = len(mondays)
    order = list(range(16))
    pairs = []
    for _ in mondays:
        rng.shuffle(order)
        pairs.append({a: b for a, b in zip(order[::2], order[1::2])} | {b: a for a, b in zip(order[::2], order[1::2])})

    # The lazy league: every team's roster and real score each week.
    lazy_rosters = [[pid for ids in r.values() for pid in ids] for r in rosters]
    lazy_by_week, lazy_score = [], []
    taken = set(drafted)
    for m in mondays:
        lazy_rosters = [lazy(s, r, m, taken) for r in lazy_rosters]
        lazy_by_week.append([list(r) for r in lazy_rosters])
        lazy_score.append([actual(s, r, m) for r in lazy_rosters])

    out = {}
    for t in range(managed):
        for name in names:
            keep_rule, spots, discount, rule = VARIANTS[name]
            fixed = not isinstance(rule, tuple)
            if fixed and price_all is not None and rule != math.inf:
                rule = price_all
            roster = list(lazy_by_week[0][t])
            used, wins, pts, planned, done = 0, 0, 0.0, 0, 0
            picks, prices, tops = [], [], []  # the live price's simulated Monday pools; each Monday's price; best two
            exits = {}  # add -> the Monday his planned exit is due
            mine_taken = set(drafted)
            made, by_week, scores = [], [], []  # the audit's moves, rosters and scores by week
            for w, m in enumerate(mondays):
                if w:  # forced like the lazy managers: a goalie who lost his role
                    for i, pid in enumerate(list(roster)):
                        if pid in s.goalie_ids and s.share(pid, m) < LOST_ROLE:
                            new = best_goalie(s, m, mine_taken | set(roster))
                            if new:
                                roster[i] = new
                                used += 1
                cap = math.ceil(REGULAR_ADDS * (w + 1) / n_weeks) if fixed else REGULAR_ADDS
                price = rule if fixed else None
                for _ in range(2):
                    if used >= cap or price == math.inf:
                        break
                    leaving = {pid for pid, due in exits.items() if due <= m}
                    collect = {} if price is None else None
                    parts = {} if audit else None
                    move = best_move(s, roster, m, w, t, pairs, lazy_by_week, mine_taken, keep_rule, spots,
                                     discount, n_weeks, used, EXITS.get(name), price if price is not None else PRICE,
                                     leaving, name not in SKATERS_ONLY, collect, parts, name in WHOLE_SCHEDULE)
                    if price is None:  # the Monday's price, before its first move
                        tops.append((w, *collect.get("top", (None, None))))
                        price = monday_price(rule, used, w, n_weeks, picks, collect if rule[0] == "live" else None)
                        prices.append(price)
                    if move is None or move[0] < price:
                        break
                    _, add, drop, due = move
                    roster[roster.index(drop)] = add
                    mine_taken.add(add)
                    used += 1
                    if audit:
                        made.append((w, add, drop, parts))
                    done += drop in leaving
                    exits.pop(drop, None)
                    if due:
                        exits[add] = due
                        planned += 1
                score = actual(s, roster, m)
                wins += score > lazy_score[w][pairs[w][t]]
                pts += score
                by_week.append(list(roster))
                scores.append(score)
            moves = realized(s, made, by_week, mondays, scores, [lazy_score[w][pairs[w][t]] for w in range(n_weeks)]) \
                if audit else None
            out[(t, name)] = (wins, pts / n_weeks, used, planned, done, prices, tops, moves)
        print(f"league {league_no + 1}: team {t + 1}/{managed} done", flush=True)
    return out


OPP_WEEKS: dict = {}  # (week index, team) -> its lazy roster's Week, this league's


def monday_price(rule: tuple, used: int, w: int, n_weeks: int, picks: list, collect: dict | None) -> float:
    """A price rule's price this Monday (PRICE_RULES), with `used` of the
    REGULAR_ADDS spent before week index `w`. "live" logs this Monday's pool
    (`collect`, from best_move) into `picks` first, simulated once."""
    spare, left = REGULAR_ADDS - used, n_weeks - w
    if spare <= 0:
        return math.inf
    need = spare / left  # the pace the budget left allows
    if rule[0] == "pace":
        _, bar, k = rule
        return bar * ((REGULAR_ADDS / n_weeks) / need) ** k
    if collect and collect["pool"]["moves"]:
        rng = np.random.default_rng(len(picks) + 1)
        picks.append(addprice._picks(collect["pool"], collect["weight"], addprice.SIMS_PER_POOL, rng))
        del picks[:-POOL_WEEKS]
    return addprice.price_from_picks(picks, need)


def best_exit(gain: dict, future: list[dt.date], ahead: list, weeks_after: int, later_weight: float,
              hold_days: int, keep: float, rule: str | None, price: float) -> tuple[float, int | None]:
    """A move's value after this week (the weeks ahead and later points) and its
    planned exit: None to hold the full `hold_days`, else the number of weeks
    after this one it is held (0: out next Monday). Without an exit `rule`, or
    for a move kept the season (keep 1), the full hold. See EXITS."""
    future_d = {d: None for d in future}

    def worth(days: list[dt.date]) -> float:
        ahead_wins, _, long_term = matchup.horizon(gain, future_d, ahead, weeks_after, later_weight, set(days), keep)
        return sum(ahead_wins) + later_weight * long_term

    held = future[:hold_days]
    best = (worth(held), None)
    if rule is None or keep >= 1.0 or not held:
        return best
    for k in range(len(ahead) + 1):
        cut = future[:min(7 * k, hold_days)]
        if len(cut) >= len(held):
            break
        charge = price * (len(held) - len(cut)) / len(held) if rule == "charged" else 0.0
        value = worth(cut) - charge
        if value > best[0]:
            best = (value, k)
    return best


def keep_of(keep_rule, add, drop, spot_ids: set[int], line: float, levels: dict, ctx) -> float:
    """The season's share of a swap's later gain under `keep_rule` (VARIANTS)."""
    if keep_rule == "switch":
        return 0.0 if drop.id in spot_ids else 1.0
    if keep_rule == "season":
        return 1.0
    if keep_rule == 0.0:
        return 0.0
    if keep_rule == "add":
        return matchup.keep_share(add, drop, spot_ids, line, levels, ctx, {})
    # A ramp width: the rule to 2026-10-20.
    if drop.id in spot_ids:
        return 0.0
    if add.is_goalie or drop.is_goalie:
        return 1.0
    gap = min(matchup.waiver_gap(p, levels, ctx, {}) for p in (add, drop))
    return min(max((gap - line) / keep_rule, 0.0), 1.0)


class TenureBins:
    """Where an add would sit on my roster after the swap (engine.tenure's bins): a skater by
    how many of my skaters are closer to waiver level at their position, a goalie by whether
    he'd be the weakest of three or more."""

    def __init__(self, s: Season, m: dt.date, roster: list[int], mine_rp: list, levels: dict, ctx):
        self.s, self.m, self.levels, self.ctx = s, m, levels, ctx
        self.gaps = {p.id: matchup.waiver_gap(p, levels, ctx, {}) for p in mine_rp}
        self.goalies = [pid for pid in roster if pid in s.goalie_ids]
        self.gx = s.proj(m)[1]

    def of(self, add, drop) -> str:
        s, m = self.s, self.m
        if add.is_goalie:
            after = [g for g in self.goalies if g != drop.id] + [add.id]
            weakest = min(after, key=lambda g: s.share(g, m) * self.gx.get(g, 0.0))
            return tenure.GOALIE_BINS[0] if len(after) > matchup.MIN_GOALIES and weakest == add.id \
                else tenure.GOALIE_BINS[1]
        mine = matchup.waiver_gap(add, self.levels, self.ctx, {})
        return tenure.skater_bin(sum(1 for pid, g in self.gaps.items() if pid != drop.id and g < mine))


def tenure_after(gain: dict, m: dt.date, future: list[dt.date], ahead: list, later_weight: float, bin_: str,
                 table: dict | None = None) -> tuple[tuple[float, ...], tuple[float, ...], float]:
    """Keep rule "tenure": the weeks ahead and later points with each day's gain x P(the add still
    stands that week) (engine.tenure.survival); the weeks ahead at GAP_REALIZES (matchup.ahead_value),
    later days at how far-off gaps realize (tenure.realizes). No keep share, hold or discount."""
    k = lambda d: (d - m).days // 7  # 1: next week
    scaled = {d: g * tenure.survival(bin_, k(d), table) for d, g in gain.items()}
    ahead_wins, ahead_pts = matchup.ahead_value(scaled, ahead, later_weight)
    near = set().union(*(w.days for w in ahead)) if ahead else set()
    later = sum(v * tenure.realizes(k(d)) for d, v in scaled.items() if d not in near)
    return ahead_wins, ahead_pts, later


def swap_kind(s: Season, add: int, drop: int) -> str:
    """What a swap does to the roster's shape: a goalie in it, the same position, or another."""
    if add in s.goalie_ids or drop in s.goalie_ids:
        return "goalie"
    pa, pd = s.position[add], s.position[drop]
    if pa == pd:
        return "same position"
    if "D" in (pa, pd):
        return "D for a forward" if pa == "D" else "forward for a D"
    return "forward, other position"


def predicted_parts(s: Season, m: dt.date, add, drop, keep: float | None, gain: dict, week_gain: float,
                    now: float, future: list[dt.date], ahead: list, weeks_after: int, later_weight: float,
                    hold_days: int, value: float, bin_: str = "", table: dict | None = None) -> dict:
    """The audit's record of a move as the valuation saw it on its Monday (the full hold:
    planned exits aside): points this week, in each week ahead as counted, and later
    (keep share, hold and LONG_RUN_DISCOUNT applied), the 6-week rate behind them, the
    weeks the rate is multiplied over, and the value in wins; where the add sits (tenure's bin).
    With a tenure `table`, the parts as keep rule "tenure" values them."""
    if table is not None:
        ahead_wins, ahead_pts, later = tenure_after(gain, m, future, ahead, later_weight, bin_, table)
    else:
        ahead_wins, ahead_pts, later = matchup.horizon(gain, {d: None for d in future}, ahead, weeks_after,
                                                       later_weight, set(future[:hold_days]), keep)
    games = lambda pid: sum(1 for d in future if d in s.team_days[s.team(pid, m)])
    return {"value": value, "now": now, "week_pts": week_gain, "ahead_wins": list(ahead_wins),
            "ahead_pts": list(ahead_pts), "later_pts": later, "later_weight": later_weight,
            "rate": sum(gain.get(d, 0.0) for d in future) / (len(future) / 7) if future else 0.0,
            "weeks_later": max(weeks_after - len(ahead), 0), "keep": keep, "hold_weeks": hold_days / 7,
            "bin": bin_,
            "kind": swap_kind(s, add.id, drop.id),
            "edge": (games(add.id) - games(drop.id)) / (len(future) / 7) if future else 0.0}


def realized(s: Season, made: list, rosters: list[list[int]], mondays: list[dt.date], scores: list[float],
             opponents: list[float]) -> list[dict]:
    """What each move brought, week by week from its Monday for as long as it stood (the
    add still on the roster, the drop not back): the week's real score against the same
    roster with that one swap undone, in points and in wins against the week's opponent
    (as the scoreboard judges the live moves: each against the team just before it)."""
    out = []
    for w, add, drop, parts in made:
        by_week, wins = [], 0
        for j in range(w, len(mondays)):
            if add not in rosters[j] or drop in rosters[j]:
                break
            alt = actual(s, [drop if p == add else p for p in rosters[j]], mondays[j])
            by_week.append(scores[j] - alt)
            wins += (scores[j] > opponents[j]) - (alt > opponents[j])
        out.append({**parts, "week": w, "weeks_left": len(mondays) - w, "held": len(by_week),
                    "real": by_week, "real_wins": wins})
    return out


def _mean_se(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    mu = mean(xs)
    return mu, ((sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 / math.sqrt(len(xs)) if len(xs) > 1 else 0.0)


def _ratio(real: float, pred: float) -> str:
    return f"{real / pred:5.2f}" if abs(pred) > 1e-9 else "    -"


def audit_report(name: str, records: list[dict]) -> str:
    """Step 0 of the tenure plan (Nico, 2026-10-23: "not an arbitrary value deciding whether
    value counts short term or the full season"): the moves the policy made, what the
    valuation predicted for each part (this week, the weeks ahead, later) against what
    they brought while they stood, how long the adds stayed by keep share and by the
    swap's kind, whether the 6-week rate carries over to the weeks after, and whether the
    values come true in wins. Points per move; later = weeks 3 on."""
    if not records:
        return f"\n## Audit: {name}\n(no moves)"
    out = [f"\n## Audit: {name}, {len(records)} moves", "",
           "Points per move, predicted (as valued) vs realized (+/- SE) while the swap stood"]
    parts = (("this week", lambda r: r["week_pts"], lambda r: r["real"][0] if r["real"] else 0.0),
             ("week +1", lambda r: (r["ahead_pts"] + [0, 0])[0], lambda r: (r["real"] + [0] * 3)[1]),
             ("week +2", lambda r: (r["ahead_pts"] + [0, 0])[1], lambda r: (r["real"] + [0] * 3)[2]),
             ("later", lambda r: r["later_pts"], lambda r: sum(r["real"][3:])))
    for label, pred, real in parts:
        p, (x, se) = mean(pred(r) for r in records), _mean_se([real(r) for r in records])
        out.append(f"  {label:10} predicted {p:+7.2f}   realized {x:+7.2f} ({se:.2f})   ratio {_ratio(x, p)}")
    full = mean(r["rate"] * r["weeks_later"] for r in records)
    out.append(f"  later, the 6-week rate x the weeks after the two ahead (no keep share, no discount): {full:+.2f}")

    def table(title: str, key, order=None) -> None:
        groups = defaultdict(list)
        for r in records:
            groups[key(r)].append(r)
        out.extend(["", title, f"  {'':24}{'n':>6}{'later pred':>11}{'real':>8}{'ratio':>7}{'rate':>7}"
                               f"{'real/wk':>8}{'held wks':>9}{'3+':>6}{'6+':>6}{'12+':>6}"])
        for g in order or sorted(groups):
            rs = groups.get(g)
            if not rs:
                continue
            p, x = mean(r["later_pts"] for r in rs), mean(sum(r["real"][3:]) for r in rs)
            long = [r for r in rs if r["held"] > 3]
            per_week = mean(sum(r["real"][3:]) / (r["held"] - 3) for r in long) if long else 0.0
            stay = []
            for k in (3, 6, 12):
                could = [r for r in rs if r["weeks_left"] >= k]
                stay.append(f"{sum(r['held'] >= k for r in could) / len(could):6.0%}" if could else "     -")
            out.append(f"  {g:24}{len(rs):6}{p:+11.2f}{x:+8.2f}{_ratio(x, p):>7}{mean(r['rate'] for r in rs):+7.2f}"
                       f"{per_week:+8.2f}{mean(r['held'] for r in rs):9.1f}" + "".join(stay))

    keep_bucket = lambda r: ("keep 0 (streamer)" if r["keep"] <= 0 else "keep 1 (keeper)" if r["keep"] >= 1
                             else "keep 0-0.5" if r["keep"] < 0.5 else "keep 0.5-1")
    table("By keep share (rate: the predicted 6-week rate, pts a week; real/wk: realized a week in weeks 3+, "
          "adds held past week 3; 3+/6+/12+: share still held, of those with the weeks left)", keep_bucket,
          ["keep 0 (streamer)", "keep 0-0.5", "keep 0.5-1", "keep 1 (keeper)"])
    table("By the swap's kind", lambda r: r["kind"])
    rates = sorted(r["rate"] for r in records)
    cuts = [rates[len(rates) * i // 4] for i in range(1, 4)]
    table("By the predicted 6-week rate (quartiles)",
          lambda r: f"rate q{1 + sum(r['rate'] >= c for c in cuts)}", [f"rate q{i}" for i in range(1, 5)])
    long = [r for r in records if r["held"] > 3]
    if len(long) > 2:
        xs = [r["rate"] for r in long]
        ys = [sum(r["real"][3:]) / (r["held"] - 3) for r in long]
        slope, _ = np.polyfit(xs, ys, 1)
        resid = np.array(ys) - np.polyval(np.polyfit(xs, ys, 1), xs)
        se = math.sqrt(resid.var(ddof=2) / (np.var(xs) * len(xs)))
        out.append(f"  realized a week in weeks 3+ on the predicted 6-week rate: slope {slope:.2f} ({se:.2f}), "
                   f"{len(long)} adds held past week 3")

    curves = tenure.fit([(r["bin"], r["held"], r["weeks_left"]) for r in records if r.get("bin")])
    if curves:
        out.extend(["", "Tenure by where the add sits (Kaplan-Meier, censored at the season's end): "
                        "P(still standing k weeks after)", f"  {'':28}{'n':>6}" + "".join(f"{f'k={k}':>7}" for k in (1, 2, 3, 6, 12, 20))])
        order = [b for b in tenure.SKATER_BINS + tenure.GOALIE_BINS if b in curves]
        n = defaultdict(int)
        for r in records:
            n[r.get("bin")] += 1
        for b in order:
            out.append(f"  {b:28}{n[b]:6}" + "".join(f"{tenure.survival(b, k, curves):7.2f}" for k in (1, 2, 3, 6, 12, 20)))
        out.append("  as engine.tenure.SURVIVAL:")
        out.append("  {" + ",\n   ".join(f"{b!r}: {curves[b]!r}" for b in order) + "}")
    out.extend(["", "In wins (value: predicted; real: wins added while it stood, +/- SE; at typical: realized "
                    "points x later_weight, less noisy)", f"  {'':16}{'n':>6}{'value':>8}{'real':>8}{'SE':>7}{'at typical':>11}"])
    for lo, hi in ((-9, 0.1), (0.1, 0.2), (0.2, 0.4), (0.4, 9)):
        rs = [r for r in records if lo <= r["value"] < hi]
        if not rs:
            continue
        x, se = _mean_se([r["real_wins"] for r in rs])
        typical = mean(sum(r["real"]) * r["later_weight"] for r in rs)
        label = f"value {max(lo, 0):.1f}-{hi:.1f}" if hi < 9 else f"value {lo:.1f}+"
        out.append(f"  {label:16}{len(rs):6}{mean(r['value'] for r in rs):8.3f}{x:+8.3f}{se:7.3f}{typical:+11.3f}")
    return "\n".join(out)


GOALIE_SHORTLIST = (4, 3)  # free agent goalies tried: the best by this week's points, by points a start x share


def best_move(s, roster, m, w, t, pairs, lazy_by_week, taken, keep_rule, spots, discount, n_weeks, used,
              exit_rule=None, price=PRICE, leaving=(), goalie_moves=True, collect=None, parts=None,
              whole=False, table=None):
    """(value, add, drop, exit Monday or None) of the best swap this Monday, valued as
    matchup.candidate_moves does. `exit_rule`: EXITS; `leaving`: players whose planned exit is due,
    offered as drops too; `goalie_moves`: goalies added and dropped too, never below MIN_GOALIES.
    `collect`: a dict given the week's candidates as the bot logs them (addprice.pool_entry:
    "pool") and the week's later_weight ("weight"), for the live price, and the best two
    moves' values with no player shared ("top"), to compare with the live bot's. `parts`: a dict
    given the best move's predicted parts (predicted_parts), for the audit. `whole`: the long run
    over the whole season left (WHOLE_SCHEDULE); `table`: the tenure curves for keep rule
    "tenure" (default engine.tenure.SURVIVAL)."""
    xfp = s.proj(m)[0]
    this = [m + dt.timedelta(days=i) for i in range(7)]
    last = s.mondays[-1] + dt.timedelta(days=6)
    future = [m + dt.timedelta(days=7 + i)
              for i in range(max((last - m).days - 6, 0) if whole else 7 * matchup.LONG_RUN_WEEKS)]
    mine = Week(s, roster, m, this + future)
    def opp(k):
        key = (w, pairs[min(w + k, n_weeks - 1)][t])
        if key not in OPP_WEEKS:
            OPP_WEEKS[key] = Week(s, lazy_by_week[w][key[1]], m, this + future)
        return OPP_WEEKS[key]

    me_now, var_now = mine.total(this)
    them_now, them_var = opp(0).total(this)
    sd_now = math.sqrt(var_now + them_var) or 1.0  # a week with no games (the Olympic break)
    margin_now = matchup.MARGIN_REALIZES * (me_now - them_now)
    ahead = []
    for k in range(1, matchup.AHEAD_WEEKS + 1):
        days = future[7 * (k - 1):7 * k]
        if w + k >= n_weeks:
            break
        me_k, v_k = mine.total(days)
        them_k, tv_k = opp(k).total(days)
        ahead.append(matchup.WeekAhead(w + k, frozenset(days), matchup.MARGIN_REALIZES_AHEAD[k] * (me_k - them_k),
                                       math.sqrt(v_k + tv_k) or 1.0))
    # A point later: a typical week's sigma (mine and the opponent's) and the league's tau.
    tau = 18.5  # the league's, Oct 2026 (bot/weekly.league_tau); replay leagues are drafted alike
    later_weight = addprice.later_weight(math.sqrt(var_now + them_var), tau)
    spare = REGULAR_ADDS - used
    rate = min(max(spare / max(n_weeks - w, 1), 0.5), 2.0)
    hold_days = max(0, round(7 * (spots + 1) / rate) - 7)
    weeks_after = n_weeks - w - 1

    skaters = [pid for pid in roster if pid not in s.goalie_ids]
    mine_rp = [RosterPlayer(pid, str(pid), s.team(pid, m), [s.position[pid]]) for pid in skaters]
    by_week, by_xfp = s.eligible(m)
    shortlist = set()
    for pos in SLOTS:
        shortlist |= set([q for q in by_week[pos] if q not in taken and q not in roster][:5])
        shortlist |= set([q for q in by_xfp[pos] if q not in taken and q not in roster][:3])
    fa_rp = [RosterPlayer(q, str(q), s.team(q, m), [s.position[q]]) for q in shortlist]
    ctx = Ctx(xfp)
    saved = matchup.STREAMING_SPOTS, matchup.KEEP_RAMP
    matchup.STREAMING_SPOTS = spots
    try:
        spot_ids = matchup.streaming_spots(mine_rp, fa_rp, ctx, {})
        levels = matchup.waiver_levels(fa_rp, ctx, {})
        line = max((matchup.waiver_gap(p, levels, ctx, {}) for p in mine_rp if p.id in spot_ids), default=-math.inf)
        drops = [p for p in matchup.drop_candidates(mine_rp, ctx, {})]
        drops += [p for p in mine_rp if p.id in leaving and p not in drops]
        pairs_tried = [(a, d) for a in fa_rp for d in drops]
        goalies = [pid for pid in roster if pid in s.goalie_ids]
        if goalie_moves and goalies:
            gx = s.proj(m)[1]
            weakest = min(goalies, key=lambda g: s.share(g, m) * gx.get(g, 0.0))
            g_rp = RosterPlayer(weakest, str(weakest), s.team(weakest, m), ["G"])
            if len(goalies) > matchup.MIN_GOALIES:
                spot_ids = spot_ids | {weakest}  # the spare goalie streams (GOALIE_STREAMING_SPOTS)
                pairs_tried += [(a, g_rp) for a in fa_rp]
            free = [q for q in s.goalie_ids if q not in taken and q not in roster and s.share(q, m) > 0]
            week = {q: s.share(q, m) * gx.get(q, 0.0) * len([d for d in this if d in s.team_days[s.team(q, m)]])
                    for q in free}
            picked = set(sorted(free, key=lambda q: week[q], reverse=True)[:GOALIE_SHORTLIST[0]])
            picked |= set(sorted(free, key=lambda q: s.share(q, m) * gx.get(q, 0.0), reverse=True)[:GOALIE_SHORTLIST[1]])
            for q in picked:
                a = RosterPlayer(q, str(q), s.team(q, m), ["G"])
                pairs_tried += [(a, d) for d in drops + [g_rp]]
        best, pool, detail = None, [], None
        bins = TenureBins(s, m, roster, mine_rp, levels, ctx)
        for a, d in pairs_tried:
            week_gain = sum(mine.gain(a.id, d.id, this).values())
            if week_gain < -matchup.MAX_WEEK_COST:
                continue
            gain = mine.gain(a.id, d.id, future)
            bin_ = bins.of(a, d)
            if keep_rule == "tenure":
                keep, exit_k = None, None
                ahead_wins, _, later = tenure_after(gain, m, future, ahead, later_weight, bin_, table)
                after = sum(ahead_wins) + later_weight * later
            else:
                keep = keep_of(keep_rule, a, d, spot_ids, line, levels, ctx)
                saved_discount = matchup.LONG_RUN_DISCOUNT
                matchup.LONG_RUN_DISCOUNT = discount
                try:
                    after, exit_k = best_exit(gain, future, ahead, weeks_after, later_weight, hold_days, keep,
                                              exit_rule, price)
                finally:
                    matchup.LONG_RUN_DISCOUNT = saved_discount
            now = matchup._phi((margin_now + week_gain) / sd_now) - matchup._phi(margin_now / sd_now)
            value = now + after
            pool.append([round(week_gain, 2), round(after / later_weight, 2), a.id, d.id, value])
            if best is None or value > best[0]:
                best = (value, a.id, d.id, None if exit_k is None else m + dt.timedelta(days=7 * (exit_k + 1)))
                detail = (a, d, keep, gain, week_gain, now, bin_)
        if parts is not None and detail:
            saved_discount = matchup.LONG_RUN_DISCOUNT
            matchup.LONG_RUN_DISCOUNT = discount
            try:
                parts.update(predicted_parts(s, m, *detail[:6], future, ahead, weeks_after, later_weight,
                                             hold_days, best[0], bin_=detail[6],
                                             table=(table if table is not None else tenure.SURVIVAL)
                                             if keep_rule == "tenure" else None))
            finally:
                matchup.LONG_RUN_DISCOUNT = saved_discount
    finally:
        matchup.STREAMING_SPOTS, matchup.KEEP_RAMP = saved
    if collect is not None:
        by_value = sorted(pool, key=lambda x: -x[4])
        first = by_value[0] if by_value else None
        second = next((x for x in by_value[1:] if x[2] != first[2] and x[3] != first[3]), None) if first else None
        pool.sort(key=lambda x: x[0] + x[1], reverse=True)
        collect.update(pool={"moves": [x[:4] for x in pool[:addprice.POOL_SIZE]], "sigma": sd_now, "tau": tau},
                       weight=later_weight, top=(first[4] if first else None, second[4] if second else None))
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leagues", type=int, default=4)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS))
    parser.add_argument("--teams", type=int, default=16, help="teams managed per league (all 16 by default)")
    parser.add_argument("--price", type=float, help="every fixed-price variant's add price (0: the budget's pace decides)")
    parser.add_argument("--reference", default=REFERENCE, help="the variant the others are compared with")
    parser.add_argument("--audit", action="store_true",
                        help="each variant's moves: predicted parts vs what they brought, tenure (audit_report)")
    args = parser.parse_args()
    reference = args.reference
    names = [reference] + [v for v in args.variants if v != reference]
    jobs = [(k, args.seed, names, args.teams, args.price, args.audit) for k in range(args.leagues)]
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for k, out in enumerate(pool.map(run_league, jobs)):
            results.update({(k, t, name): v for (t, name), v in out.items()})
    teams = sorted({(k, t) for k, t, _ in results})
    n = len(teams)
    print(f"\n{args.leagues} leagues x 16 teams, 2025-26 replayed; each team managed alone, the others lazily; "
          f"price {PRICE if args.price is None else args.price}")
    print(f"{'variant':16}{'wins':>7}{'vs ' + reference:>16}{'+/- SE':>9}{'pts/wk':>9}{'adds':>7}"
          f"{'exits planned':>15}{'made':>6}")
    ref = [results[(k, t, reference)] for k, t in teams]
    for name in names:
        rows = [results[(k, t, name)] for k, t in teams]
        diff = [a[0] - b[0] for a, b in zip(rows, ref)]
        se = (sum((x - mean(diff)) ** 2 for x in diff) / (n - 1)) ** 0.5 / math.sqrt(n) if n > 1 else 0.0
        pdiff = mean(a[1] - b[1] for a, b in zip(rows, ref))
        print(f"{name:16}{mean(r[0] for r in rows):7.2f}{mean(diff):+16.2f}{se:9.2f}"
              f"{mean(r[1] for r in rows):9.1f}{mean(r[2] for r in rows):7.1f}"
              f"{mean(r[3] for r in rows):15.1f}{mean(r[4] for r in rows):6.1f}   pts/wk vs ref {pdiff:+.2f}")
    # The price rules' prices: how much each moves, Monday to Monday, and by the season's stage.
    for name in names:
        series = [r[5] for r in (results[(k, t, name)] for k, t in teams) if r[5]]
        if not series:
            continue
        finite = [[p for p in s if p != math.inf] for s in series]
        jumps = [abs(b - a) for s in finite for a, b in zip(s, s[1:])]
        thirds = [mean(p for s in finite for p in s[len(s) * i // 3:len(s) * (i + 1) // 3] or [0]) for i in range(3)]
        print(f"  {name}: price {mean(p for s in finite for p in s):.3f} on average (by thirds of the season "
              + " / ".join(f"{x:.3f}" for x in thirds) + f"), Monday to Monday it moves {mean(jumps or [0]):.3f}"
              f" on average, {max(jumps or [0]):.3f} at most")
    # The move values a Monday offers (the first price-rule variant's, its roster as it went), by
    # the season's stage, to set a replayed bar on the live bot's scale (its Boards' values).
    rule_names = [nm for nm in names if isinstance(VARIANTS[nm][3], tuple)]
    if rule_names:
        tops = [x for k, t in teams for x in results[(k, t, rule_names[0])][6]]
        print(f"  move values on Mondays ({rule_names[0]}): best / second best with no player shared,"
              " median and 75th percentile; share of Mondays whose second clears 0.08 / 0.12")
        for lo, hi in ((0, 2), (2, 5), (5, 12), (12, 30)):
            rows = [(b, s2) for w, b, s2 in tops if lo <= w < hi and b is not None]
            if not rows:
                continue
            best = sorted(b for b, _ in rows)
            sec = sorted(s2 if s2 is not None else 0.0 for _, s2 in rows)
            q = lambda v, f: v[min(int(f * len(v)), len(v) - 1)]
            print(f"    weeks {lo + 1}-{hi}: best {q(best, .5):.3f} / {q(best, .75):.3f}, second {q(sec, .5):.3f} /"
                  f" {q(sec, .75):.3f}; second over 0.08 {sum(x >= 0.08 for x in sec) / len(sec):.0%},"
                  f" over 0.12 {sum(x >= 0.12 for x in sec) / len(sec):.0%}")
    if args.audit:
        for name in names:
            print(audit_report(name, [x for k, t in teams for x in results[(k, t, name)][7] or []]))


if __name__ == "__main__":
    main()
