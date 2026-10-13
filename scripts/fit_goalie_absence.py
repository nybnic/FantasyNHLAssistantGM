"""When a team's starting goalie is out, who gets his starts? Fits
engine/availability.py's ABSENT_TO_TEAMMATES from goalie game logs.

Each start of an absence: a goalie who started 5+ of his team's last 10 games
misses two team games in a row (no start, no relief), as when DFO lists him
out; trades are left out. From the next game (the bot's view: he's out
tonight, back along the return curve for 3 games missed), each teammate who
started in the 40 games before gets, for each of the next 10 team games:
- now: his blended share (last 10, a prior of his share of the 40 before), as
  the bot has it: the absent goalie's starts go nowhere;
- redistributed: plus the absent goalie's share times his odds of still being
  out that day, split among the teammates by their shares, times a factor (the
  part that goes to known goalies, not a call-up).
Scored against who started: mean predicted vs actual, and log loss.

    python -m scripts.fit_goalie_absence       # fit 2023-24 + 2024-25, check 2025-26
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass

from clients import nhl_stats
from engine import availability

PRIOR_GAMES = 40
AHEAD_GAMES = 10
MISSED_TO_START = 2  # team games in a row a starter misses before the absence counts
TRAIN, TEST = (20232024, 20242025), 20252026


@dataclass
class TeamGame:
    date: dt.date
    starter: int | None
    appeared: frozenset


def team_games(season: int) -> dict[str, list[TeamGame]]:
    by_game = defaultdict(list)
    for g in nhl_stats.goalie_games(season, today=nhl_stats.season_window(season)[1]):
        by_game[(g.team, g.game_id)].append(g)
    out = defaultdict(list)
    for (team, _), gs in by_game.items():
        starters = [g.player_id for g in gs if g.started]
        out[team].append(TeamGame(gs[0].date, starters[0] if len(starters) == 1 else None,
                                  frozenset(g.player_id for g in gs)))
    return {t: sorted(gs, key=lambda g: g.date) for t, gs in out.items()}


def rows(games_by_team: dict[str, list[TeamGame]]) -> list[dict]:
    """One row per (absence, teammate, game ahead)."""
    last_team: dict[int, list[tuple[dt.date, str]]] = defaultdict(list)
    for team, games in games_by_team.items():
        for g in games:
            for pid in g.appeared:
                last_team[pid].append((g.date, team))
    out = []
    for team, games in games_by_team.items():
        for i in range(10 + MISSED_TO_START, len(games)):
            recent = [g.starter for g in games[i - 10:i]]
            starter = max(set(recent) - {None}, key=recent.count, default=None)
            if starter is None or recent.count(starter) < 5:
                continue
            missed = games[i - MISSED_TO_START:i]
            if any(starter in g.appeared for g in missed) or starter not in games[i - MISSED_TO_START - 1].appeared:
                continue
            later = sorted(d for d, t in last_team[starter] if d >= games[i].date)
            if later and dict(last_team[starter])[later[0]] != team:
                continue  # traded
            today = games[i].date
            starts = [(g.date, g.starter) for g in games[:i] if g.starter is not None]
            window = [pid for _, pid in starts[-PRIOR_GAMES:]]
            mates = {pid for pid in window if pid != starter}
            if not mates:
                continue

            def share(pid: int) -> float:
                return availability.start_share(pid, today, None, starts, window.count(pid) / len(window))

            absent_share = share(starter)
            shares = {pid: share(pid) for pid in mates}
            curve = availability.return_curve(MISSED_TO_START + 1)
            for g in games[i:i + AHEAD_GAMES]:
                if g.starter is None:
                    continue
                days = (g.date - today).days
                out_odds = 1 - availability.ahead(curve, days, 0.0)
                for pid, s in shares.items():
                    out.append({"share": s, "freed": absent_share * out_odds,
                                "rivals": sum(shares.values()) - s, "y": g.starter == pid, "days": days})
    return out


def predicted(r: dict, factor: float) -> float:
    if not factor:
        return r["share"]
    return availability.start_share(0, dt.date.min, None, [], r["share"], absent=factor * r["freed"],
                                    rivals=r["rivals"])


def log_loss(rs: list[dict], factor: float) -> float:
    def p(r):
        return min(max(predicted(r, factor), 1e-3), 1 - 1e-3)
    return -sum(math.log(p(r) if r["y"] else 1 - p(r)) for r in rs) / len(rs)


def fit(rs: list[dict]) -> float:
    return min((f / 20 for f in range(0, 31)), key=lambda f: log_loss(rs, f))


def report(name: str, rs: list[dict], factor: float) -> None:
    if not rs:
        print(f"  {name}: no rows")
        return
    actual = sum(r["y"] for r in rs) / len(rs)
    now = sum(predicted(r, 0) for r in rs) / len(rs)
    new = sum(predicted(r, factor) for r in rs) / len(rs)
    print(f"  {name}: n={len(rs)}  started {actual:.3f}  predicted now {now:.3f}, redistributed {new:.3f}  "
          f"log loss {log_loss(rs, 0):.4f} -> {log_loss(rs, factor):.4f}")


def main() -> None:
    data = {s: rows(team_games(s)) for s in (*TRAIN, TEST)}
    train = [r for s in TRAIN for r in data[s]]
    factor = fit(train)
    print(f"Absent starter's starts to his teammates (fit {TRAIN}): factor {factor:.2f} "
          f"(1 = all to known goalies, in proportion to their shares)")
    for s in (*TRAIN, TEST):
        print(f"{s}: fit alone {fit(data[s]):.2f}")
        report("all", data[s], factor)
        for lo, hi in ((0, 6), (7, 13), (14, 99)):
            report(f"{lo}-{hi} days on", [r for r in data[s] if lo <= r["days"] <= hi], factor)
    print(f"availability.ABSENT_TO_TEAMMATES = {availability.ABSENT_TO_TEAMMATES}")


if __name__ == "__main__":
    main()
