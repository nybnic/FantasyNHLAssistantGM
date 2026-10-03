"""Who starts next? Fits engine/availability.py's LAST_RESULT_ODDS and
BACK_TO_BACK_REPEAT from goalie game logs.

For each team game (after its first 10), the bot's start odds for the goalie
who started the team's previous game (his last-10 share blended with a prior:
here his share of the 40 before) against whether he started again:
- not a back-to-back: the odds multiplier that calibrates them, by how his
  last start went (a win, a loss, a loss with 5+ goals against or pulled),
  for the next game and the ones after;
- the second night of a back-to-back: his repeat rate over his share.

    python -m scripts.fit_goalie_starts       # fit 2023-24 + 2024-25, check 2025-26
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from clients import nhl_stats
from engine import availability

PRIOR_GAMES = 40
TRAIN, TEST = (20232024, 20242025), 20252026


def team_games(season: int) -> dict[str, list[tuple[dt.date, int, str]]]:
    """team -> [(date, starter id, his result)], oldest first."""
    by_game = defaultdict(list)
    for g in nhl_stats.goalie_games(season, today=nhl_stats.season_window(season)[1]):
        by_game[(g.team, g.game_id)].append(g)
    out = defaultdict(list)
    for (team, _), gs in by_game.items():
        starters = [g for g in gs if g.started]
        if len(starters) == 1:
            out[team].append((starters[0].date, starters[0].player_id, availability.start_result(starters[0], len(gs) > 1)))
    return {t: sorted(gs) for t, gs in out.items()}


def rows(season: int, lag: int = 1) -> tuple[list[dict], list[dict]]:
    """(non back-to-back rows `lag` games after the last start, back-to-back rows)."""
    plain, b2b = [], []
    for games in team_games(season).values():
        for i in range(10, len(games) - lag + 1):
            target = games[i + lag - 1]
            last_date, last, result = games[i - 1]
            before = [pid for _, pid, _ in games[max(0, i - PRIOR_GAMES):i]]
            share = availability.start_share(last, target[0], None, [(d, pid) for d, pid, _ in games[:i]],
                                             before.count(last) / len(before))
            row = {"share": share, "y": target[1] == last, "result": result}
            if target[0] - games[i + lag - 2][0] == dt.timedelta(days=1):
                if lag == 1:
                    b2b.append(row)
            else:
                plain.append(row)
    return plain, b2b


def _odds(p: float, m: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return availability.with_odds(p, m)


def fit(rs: list[dict]) -> dict[str, float]:
    """result -> the odds multiplier that calibrates the share (maximum likelihood)."""
    out = {}
    for result in ("W", "L", "L bad"):
        group = [r for r in rs if r["result"] == result]
        lo, hi = -4.0, 4.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if sum(r["y"] - _odds(r["share"], math.exp(mid)) for r in group) > 0:
                lo = mid
            else:
                hi = mid
        out[result] = math.exp(lo)
    return out


def log_loss(rs: list[dict], prob) -> float:
    return -sum(math.log(prob(r) if r["y"] else 1 - prob(r)) for r in rs) / len(rs)


def main() -> None:
    data = {s: rows(s) for s in (*TRAIN, TEST)}
    train = [r for s in TRAIN for r in data[s][0]]
    test = data[TEST][0]
    m = fit(train)
    print(f"Next game, not a back-to-back: odds x by the last start (fit {TRAIN}, n={len(train)})")
    print("  fit:     " + "  ".join(f"{k} {v:.2f}" for k, v in m.items()))
    print(f"  {TEST}: " + "  ".join(f"{k} {v:.2f}" for k, v in fit(test).items()) + f"  (n={len(test)})")
    print(f"  log loss on {TEST}: share alone {log_loss(test, lambda r: _odds(r['share'], 1)):.4f}, "
          f"with the fit {log_loss(test, lambda r: _odds(r['share'], m[r['result']])):.4f}, "
          f"with LAST_RESULT_ODDS {log_loss(test, lambda r: _odds(r['share'], availability.LAST_RESULT_ODDS[r['result']])):.4f}")
    for lag in (2, 3):
        later = [r for s in TRAIN for r in rows(s, lag)[0]]
        print(f"  {lag} games on: " + "  ".join(f"{k} {v:.2f}" for k, v in fit(later).items()))
    print("\nSecond night of a back-to-back: the last starter's repeat rate / his share")
    for s in (*TRAIN, TEST):
        b2b = data[s][1]
        print(f"  {s}: {sum(r['y'] for r in b2b) / sum(r['share'] for r in b2b):.3f} (n={len(b2b)})")
    print(f"  BACK_TO_BACK_REPEAT = {availability.BACK_TO_BACK_REPEAT}")


if __name__ == "__main__":
    main()
