"""Two goalies or three? The 2025-26 season replayed under three policies.

Leagues of 16 teams snake-drafted on preseason projections (scripts/check_sigma.py:
3 C, 2 LW, 2 RW, 4 D, 3 G). Each team plays the season three times:
- always-3: keeps three goalies;
- always-2: in week 1 drops its weakest goalie for the best free-agent skater;
- by-value: every Monday, the bot's projection (engine/matchup.project, long
  run: the goalie minimum each week, a goalie lost for a week per
  availability.GOALIE_KEEP) compares its roster with one goalie more or one
  fewer over the next 4 weeks, and switches when the gain beats an add.
Controls with the same weekly like-for-like swaps (+ swaps: the weakest
skater or goalie for the best free agent at his position, when the gain beats
an add) separate the
goalie decision from simply using adds well: compare by-value + swaps with
always-3 + swaps. Moves are capped at 2 a week.
All replace a goalie who has lost his role (under 15% of his team's
last 10 starts) with the free agent with the most recent starts; every move
is an add. Each week a team's real score (box scores, the best projected
lineup each day, confirmed starters known, the goalie minimum) meets its
opponent's always-3 score: wins per season, paired by team.

Judgment calls, labeled: an add is worth SWITCH_COST points over 4 weeks
(about the add price, 7 win-pts, at ~0.75 win-pts a point); skaters are
assumed healthy in projections (no DFO history); starters are known (DFO
confirms most by briefing time).

    python -m scripts.sim_goalies               # 4 leagues
    python -m scripts.sim_goalies --leagues 8
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from collections import defaultdict
from statistics import mean

from clients.nhl_client import ScheduledGame
from config.league import GOALIE_WEIGHTS, MIN_GOALIE_GAMES_PER_WEEK, SKATER_WEIGHTS, fantasy_points
from engine import lineup, matchup
from league.roster import RosterPlayer
from model.projections import project_goalie, project_skater
from scripts.check_sigma import POS, draft, load

HORIZON_WEEKS = 4
SWITCH_COST = 9.0  # points over the horizon an add must buy (judgment call, see docstring)
LOST_SHARE = 0.15


class ReplayContext:
    """What engine/matchup.project needs, from 2025-26 data before `today`."""

    def __init__(self, today, s_proj, g_proj, team_starts, by_skater, by_goalie):
        self.today, self._s, self._g = today, s_proj, g_proj
        self.team_starts = {t: [(d, p) for d, p in seq if d < today] for t, seq in team_starts.items()}
        self.skater_games, self.goalie_games = by_skater, by_goalie

    def skater(self, pid, position):
        return type("P", (), {"xfp": self._s.get(pid, 0.0)})()

    def goalie_start(self, pid, team, opponent, home):
        return {"xfp": self._g.get(pid, 0.0)}

    def prior_start_share(self, pid):
        return None

    def durability(self, pid):
        return 1.0

    def games_missed(self, pid, team):
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leagues", type=int, default=4)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    s_priors, s_fallback, g_priors, g_fallback, skaters, goalies = load()

    by_skater, by_goalie = defaultdict(list), defaultdict(list)
    for g in skaters:
        by_skater[g.player_id].append(g)
    for g in goalies:
        by_goalie[g.player_id].append(g)
    position = {pid: POS[gs[0].position] for pid, gs in by_skater.items()}
    team_of = {pid: gs[-1].team for pid, gs in {**by_skater, **by_goalie}.items()}
    starts_by_team = defaultdict(list)
    games: dict[dt.date, dict[int, ScheduledGame]] = defaultdict(dict)
    for g in goalies:
        if g.started:
            starts_by_team[g.team].append((g.date, g.player_id))
        home, away = (g.team, g.opponent) if g.home else (g.opponent, g.team)
        games[g.date][g.game_id] = ScheduledGame(g.game_id, dt.datetime.combine(g.date, dt.time()), home, away)
    for seq in starts_by_team.values():
        seq.sort()
    points = {(g.player_id, g.date): fantasy_points(g.stats, SKATER_WEIGHTS) for g in skaters}
    g_points = {(g.player_id, g.date): fantasy_points(g.stats, GOALIE_WEIGHTS) for g in goalies if g.started}

    def s_prior(pid):
        return s_priors.get(pid) or s_fallback["D" if position[pid] == "D" else "F"]

    pool = defaultdict(list)
    for pid, gs in by_skater.items():
        if len(gs) >= 20:
            pool[position[pid]].append((project_skater(s_prior(pid), []).xfp, pid))
    for pid, gs in by_goalie.items():
        if sum(g.started for g in gs) >= 15:
            pool["G"].append((project_goalie(g_priors.get(pid, g_fallback), []).xfp * 0.55, pid))

    first, last = skaters[0].date, skaters[-1].date
    monday = first + dt.timedelta(days=14 + (7 - (first + dt.timedelta(days=14)).weekday()) % 7)
    mondays = []
    while monday + dt.timedelta(days=7) <= last:
        mondays.append(monday)
        monday += dt.timedelta(days=7)

    def week_schedule(m):
        return {m + dt.timedelta(days=i): list(games.get(m + dt.timedelta(days=i), {}).values()) for i in range(7)}

    proj_cache = {}

    def projections(m):
        if m not in proj_cache:
            s = {pid: project_skater(s_prior(pid), [g for g in gs if g.date < m]).xfp for pid, gs in by_skater.items()}
            g = {pid: project_goalie(g_priors.get(pid, g_fallback), [x for x in gs if x.date < m]).xfp
                 for pid, gs in by_goalie.items()}
            proj_cache[m] = (s, g)
        return proj_cache[m]

    def share(pid, m):
        recent = [p for d, p in starts_by_team[team_of[pid]] if d < m][-10:]
        return recent.count(pid) / len(recent) if recent else 0.0

    def player(pid):
        pos = ["G"] if pid in by_goalie and pid not in by_skater else [position[pid]]
        return RosterPlayer(pid, str(pid), team_of[pid], pos, "BN")

    def expected(roster_ids, m):
        ctx = ReplayContext(m, *projections(m), starts_by_team, by_skater, by_goalie)
        roster = [player(pid) for pid in roster_ids]
        total = 0.0
        for k in range(HORIZON_WEEKS):  # each week on its own: the minimum is weekly
            total += matchup.project("t", roster, ctx, week_schedule(m + dt.timedelta(weeks=k)), {}, {},
                                     long_run=True).expected
        return total

    def actual(roster_ids, m):
        """The week's real score: the best projected lineup each day, starters known."""
        s_proj, g_proj = projections(m)
        total_s = total_g = 0.0
        starts = 0
        for i in range(7):
            d = m + dt.timedelta(days=i)
            playing = {t for gm in games.get(d, {}).values() for t in (gm.home, gm.away)}
            cands = []
            for pid in roster_ids:
                if team_of[pid] not in playing:
                    continue
                if pid in by_goalie and pid not in by_skater:
                    cands.append(lineup.Candidate(pid, ("G",), g_proj.get(pid, 0) if (pid, d) in g_points else 0))
                else:
                    cands.append(lineup.Candidate(pid, (position[pid],), s_proj.get(pid, 0) * 0.97))
            for pid, slot in lineup.optimize(cands).items():
                if slot == "BN":
                    continue
                if slot == "G":
                    if (pid, d) in g_points:
                        total_g += g_points[(pid, d)]
                        starts += 1
                else:
                    total_s += points.get((pid, d), 0.0)
        return total_s + (total_g if starts >= MIN_GOALIE_GAMES_PER_WEEK else 0.0), starts >= MIN_GOALIE_GAMES_PER_WEEK

    rng = random.Random(args.seed)
    POLICIES = {"always-3": (3, False), "always-2": (2, False), "always-3 + swaps": (3, True),
                "always-2 + swaps": (2, True), "by-value + swaps": ("value", True)}
    results = {p: [] for p in POLICIES}
    adds = {p: [] for p in results}
    zeroed = {p: [] for p in results}
    mean_goalies = []
    counts = {}  # (league, team, policy) -> goalies carried each week
    split = {2: [], 3: []}  # by-value's weekly score minus always-3 + swaps', by its goalie count
    for league_no in range(args.leagues):
        rosters = draft(pool, 16, rng)
        drafted = {pid for r in rosters for ids in r.values() for pid in ids}

        def free(m, taken, goalie):
            if goalie:
                cands = [(share(pid, m) * projections(m)[1].get(pid, 0), pid) for pid in by_goalie
                         if pid not in taken and pid not in by_skater and share(pid, m) >= 0.3]
            else:
                cands = [(projections(m)[0].get(pid, 0), pid) for pid in by_skater
                         if pid not in taken and len([g for g in by_skater[pid] if g.date < m]) >= 3]
            return max(cands, default=(0, None))[1]

        trajectories = {}
        for t, r in enumerate(rosters):
            base = [pid for ids in r.values() for pid in ids]
            for policy, (goalie_rule, swaps) in POLICIES.items():
                roster, n_adds, week_scores, misses, g_count = list(base), 0, [], 0, []
                for w, m in enumerate(mondays):
                    moves = 0
                    taken = drafted | set(roster)
                    g_ids = [pid for pid in roster if pid in by_goalie and pid not in by_skater]
                    for pid in g_ids:  # a goalie who lost his role is replaced
                        if share(pid, m) < LOST_SHARE and [d for d, _ in starts_by_team[team_of[pid]] if d < m][-10:]:
                            new = free(m, taken, True)
                            if new and moves < 2:
                                roster[roster.index(pid)] = new
                                taken.add(new)
                                n_adds += 1
                                moves += 1
                    g_ids = [pid for pid in roster if pid in by_goalie and pid not in by_skater]
                    s_proj, g_proj = projections(m)
                    weakest_g = min(g_ids, key=lambda p: share(p, m) * g_proj.get(p, 0))
                    if goalie_rule == 2 and len(g_ids) > 2 and moves < 2:
                        sk = free(m, taken, False)
                        roster[roster.index(weakest_g)] = sk
                        taken.add(sk)
                        n_adds += 1
                        moves += 1
                    elif goalie_rule == 3 and len(g_ids) < 3 and moves < 2:  # back to three if one was lost
                        new = free(m, taken, True)
                        if new:
                            skaters_mine = [pid for pid in roster if pid not in g_ids]
                            roster[roster.index(min(skaters_mine, key=lambda p: s_proj.get(p, 0)))] = new
                            taken.add(new)
                            n_adds += 1
                            moves += 1
                    elif goalie_rule == "value" and moves < 2:
                        if len(g_ids) > 2:
                            alt = list(roster)
                            sk = free(m, taken, False)
                            alt[alt.index(weakest_g)] = sk
                        else:
                            skaters_mine = [pid for pid in roster if pid not in g_ids]
                            weakest_s = min(skaters_mine, key=lambda p: s_proj.get(p, 0))
                            alt = list(roster)
                            alt[alt.index(weakest_s)] = free(m, taken, True) or weakest_s
                        if alt != roster and expected(alt, m) - expected(roster, m) > SWITCH_COST:
                            roster = alt
                            taken |= set(roster)
                            n_adds += 1
                            moves += 1
                    if swaps and moves < 2:  # like for like: a skater for a skater, a goalie for a goalie
                        g_now = [pid for pid in roster if pid in by_goalie and pid not in by_skater]
                        skaters_mine = [pid for pid in roster if pid not in g_now]
                        options = []
                        for out, new in ((min(skaters_mine, key=lambda p: s_proj.get(p, 0)), free(m, taken, False)),
                                         (min(g_now, key=lambda p: share(p, m) * g_proj.get(p, 0)),
                                          free(m, taken, True))):
                            if new:
                                alt = list(roster)
                                alt[alt.index(out)] = new
                                options.append((expected(alt, m) - expected(roster, m), alt))
                        gain, alt = max(options, key=lambda o: o[0], default=(0, None))
                        if alt and gain > SWITCH_COST:
                            roster = alt
                            n_adds += 1
                    score, made = actual(roster, m)
                    week_scores.append(score)
                    misses += not made
                    g_count.append(sum(1 for pid in roster if pid in by_goalie and pid not in by_skater))
                trajectories[(t, policy)] = week_scores
                counts[(league_no, t, policy)] = g_count
                adds[policy].append(n_adds)
                zeroed[policy].append(misses)
                if goalie_rule == "value":
                    mean_goalies.append(mean(g_count))
        for t in range(16):
            for w, n_g in enumerate(counts[(league_no, t, "by-value + swaps")]):
                split[min(max(n_g, 2), 3)].append(trajectories[(t, "by-value + swaps")][w]
                                                  - trajectories[(t, "always-3 + swaps")][w])
        for w in range(len(mondays)):
            order = list(range(16))
            rng.shuffle(order)
            opponent = {a: b for a, b in zip(order[::2], order[1::2])} | {b: a for a, b in zip(order[::2], order[1::2])}
            for t in range(16):
                theirs = trajectories[(opponent[t], "always-3")][w]
                for policy in results:
                    if len(results[policy]) <= league_no * 16 + t:
                        results[policy].append(0)
                    results[policy][league_no * 16 + t] += trajectories[(t, policy)][w] > theirs
        print(f"league {league_no + 1}/{args.leagues} done")

    n = len(results["always-3"])
    print(f"\n{args.leagues} leagues x 16 teams, {len(mondays)} weeks of 2025-26; opponents play always-3")
    for policy in results:
        baseline = "always-3 + swaps" if POLICIES[policy][1] else "always-3"
        diff = [a - b for a, b in zip(results[policy], results[baseline])]
        se = (sum((d - mean(diff)) ** 2 for d in diff) / (n - 1)) ** 0.5 / math.sqrt(n)
        print(f"  {policy:16}: {mean(results[policy]):5.2f} wins a season, {mean(diff):+.2f} vs {baseline} "
              f"(+/- {se:.2f}), "
              f"{mean(adds[policy]):4.1f} adds, {mean(zeroed[policy]):4.2f} weeks with goalie points zeroed")
    print(f"  by-value carried {mean(mean_goalies):.2f} goalies on average")
    for n_g, diffs in split.items():
        if diffs:
            print(f"  by-value's weeks with {n_g} goalies: {len(diffs)}, score vs always-3 + swaps "
                  f"{mean(diffs):+.1f} pts a week (differs in {sum(1 for d in diffs if abs(d) > 0.01)})")


if __name__ == "__main__":
    main()
