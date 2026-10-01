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
    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
