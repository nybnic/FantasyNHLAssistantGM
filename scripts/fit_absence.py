"""How fast do absent regulars come back? Fits engine/availability.py's
return curves from game logs.

The bot sees tonight's status (DailyFaceoff: out, IR) but plans weeks ahead.
Here: a regular (played 14+ of his team's 20 games before) misses team game
D. How often does he play a team game k days later, by how many team games
in a row he has missed (counting D)? These are availability.RETURN_CURVES.
Played anchors give the healthy baseline over the same days.

    python -m scripts.fit_absence                      # fit 2024-25, check 2025-26
    python -m scripts.fit_absence --seasons 20252026
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from clients import nhl_stats
from engine import availability

REGULAR_GAMES = 20
REGULAR_SHARE = 0.7
HORIZON_DAYS = 42


def _label(missed: int) -> str:
    """The RETURN_CURVES group of an absence of `missed` games: "missed 3-5"."""
    low = 1
    for most, _ in availability.RETURN_CURVES:
        if most is None or missed <= most:
            return f"missed {low}-{most}" if most else f"missed {low}+"
        low = most + 1
    raise AssertionError


def curves(season: int) -> dict[str, dict[tuple[int, int], list[int]]]:
    """group -> bucket (first, last day ahead) -> [games played, team games]."""
    games = nhl_stats.skater_games(season)
    team_dates: dict[str, list[dt.date]] = defaultdict(set)
    played: dict[int, set[dt.date]] = defaultdict(set)
    team_of: dict[int, list[tuple[dt.date, str]]] = defaultdict(list)
    for g in games:
        team_dates[g.team].add(g.date)
        played[g.player_id].add(g.date)
        team_of[g.player_id].append((g.date, g.team))
    team_dates = {t: sorted(ds) for t, ds in team_dates.items()}
    groups = ["healthy"] + [_label(most or 999) for most, _ in availability.RETURN_CURVES]
    out = {group: {b: [0, 0] for b in availability.RETURN_BUCKETS} for group in groups}
    for pid, stints in team_of.items():
        mine = played[pid]
        last_game = max(mine)
        # Each team the player was with, from his first game there to his last,
        # or to the season's end if he never played for another team after
        # (a season-ending injury is an absence; a trade isn't).
        by_team: dict[str, list[dt.date]] = defaultdict(list)
        for d, t in stints:
            by_team[t].append(d)
        for team, ds in by_team.items():
            stint_end = ds[-1] if ds[-1] < last_game else team_dates[team][-1]
            sched = [d for d in team_dates[team] if ds[0] <= d <= stint_end]
            for i, anchor in enumerate(sched):
                if i < REGULAR_GAMES:
                    continue
                streak = 0  # team games missed in a row, up to and including the anchor
                for d in reversed(sched[:i + 1]):
                    if d in mine:
                        break
                    streak += 1
                start = i - streak + 1 if streak else i  # first game of the absence (or the anchor)
                if start < REGULAR_GAMES:
                    continue
                before = sched[start - REGULAR_GAMES:start]
                if sum(d in mine for d in before) < REGULAR_SHARE * REGULAR_GAMES:
                    continue
                group = _label(streak) if streak else "healthy"
                for d in sched[i + 1:]:
                    k = (d - anchor).days
                    bucket = next((b for b in availability.RETURN_BUCKETS if b[0] <= k <= b[1]), None)
                    if bucket is None:
                        break
                    cell = out[group][bucket]
                    cell[0] += d in mine
                    cell[1] += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", nargs="+", type=int, default=[20242025, 20252026])
    parser.add_argument("--goalies", action="store_true", help="goalies' starts k days on, vs their share then")
    args = parser.parse_args()
    if args.goalies:
        header = "".join(f"{f'{a}-{b}d':>9}" for a, b in availability.RETURN_BUCKETS)
        for season in args.seasons:
            cells = goalie_curves(season)
            print(f"{season}: a goalie's starts k days on / his share of the last 10 then")
            print(f"{'':14}{header}")
            ratios = [made / exp for made, exp in cells.values()]
            print(f"{'goalies':14}" + "".join(f"{r:9.2f}" for r in ratios))
            print(f"{'  / 3-6 days':14}" + "".join(f"{min(r / ratios[1], 1.0):9.2f}" for r in ratios))
            print(f"{'  model':14}" + "".join(f"{v:9.2f}" for v in availability.GOALIE_KEEP))
        return
    model = {_label(most or 999): curve for most, curve in availability.RETURN_CURVES}
    for season in args.seasons:
        print(f"\n{season}: P(plays a team game k days after the anchor game)")
        table = curves(season)
        header = "".join(f"{f'{a}-{b}d':>9}" for a, b in availability.RETURN_BUCKETS)
        print(f"{'':14}{header}   (anchors' team games)")
        for group, cells in table.items():
            row = "".join(f"{(p / n if n else float('nan')):9.2f}" for p, n in cells.values())
            print(f"{group:14}{row}   ({sum(n for _, n in cells.values())})")
            if group in model:
                print(f"{'  model':14}" + "".join(f"{p:9.2f}" for p in model[group]))




GOALIE_SHARE_MIN = 0.3  # a goalie with a role: this share of his team's last 10 starts
GOALIE_RECENT = 5  # ...and a start in its last 5 games (not already hurt)


def goalie_curves(season: int) -> dict[tuple[int, int], list[float]]:
    """bucket -> [starts made, starts expected at the anchor's share]: how a
    goalie's starts hold up k days after a game (injuries, lost jobs)."""
    starts = [g for g in nhl_stats.goalie_games(season) if g.started]
    by_team: dict[str, list[tuple[dt.date, int]]] = defaultdict(list)
    for g in sorted(starts, key=lambda g: g.date):
        by_team[g.team].append((g.date, g.player_id))
    out = {b: [0.0, 0.0] for b in availability.RETURN_BUCKETS}
    for team, seq in by_team.items():
        for i in range(10, len(seq)):
            anchor = seq[i - 1][0]  # the team's latest game before seq[i]
            last10 = [pid for _, pid in seq[i - 10:i]]
            for goalie in set(last10):
                share = last10.count(goalie) / 10
                if share < GOALIE_SHARE_MIN or goalie not in last10[-GOALIE_RECENT:]:
                    continue
                for d, pid in seq[i:]:
                    k = (d - anchor).days
                    bucket = next((b for b in availability.RETURN_BUCKETS if b[0] <= k <= b[1]), None)
                    if bucket is None:
                        break
                    out[bucket][0] += pid == goalie
                    out[bucket][1] += share
    return out


if __name__ == "__main__":
    main()
