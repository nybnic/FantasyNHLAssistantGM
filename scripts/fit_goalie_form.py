"""Who starts, from each goalie's own record: does how a goalie's own last
start went (however many team games ago), and his recent form, tell who
starts the team's next games better than the bot does now?

The bot now: his share of the last 10 starts blended with a prior
(availability.start_share), moved for the team's next game only by how the
team's LAST start went (LAST_RESULT_ODDS: a loss sits the goalie who lost,
so his partner gains). It never looks at the partner's own last start: on
2026-10-20 it put Kuemper at 70% for LAK's next game because Forsberg had
lost on Saturday, though Kuemper had been pulled in his own last start.

Here, for each team game (not the second night of a back-to-back) and each
goalie with a start in the team's last 10, a conditional logit picks the
starter among them: log(share) as the offset, plus
- his own last start's result (W, L, L bad = 5+ GA or pulled) by how many
  team games ago it was (1 = he started the last game, 2, 3+), W 1 game ago
  the reference;
- his form: goals saved above average over his last FORM_STARTS starts
  (saves - the season's league save % x shots), per goal.
Fit on 2023-24 + 2024-25, checked on 2025-26 (log loss of the actual
starter, per game), for the next game and for games further ahead (the
features as of today, so: does it still say something a week or a month on?).

    python -m scripts.fit_goalie_form
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

import numpy as np

from clients import nhl_stats
from engine import availability

PRIOR_GAMES = 40
FORM_STARTS = 5
TRAIN, TEST = (20232024, 20242025), 20252026
RESULTS = ("W", "L", "L bad")
GAPS = (1, 2, 3)  # 3 = 3+ team games ago
CATS = [(r, g) for r in RESULTS for g in GAPS]
REFERENCE = ("W", 1)
FEATURES = [f"{r} {g}{'+' if g == 3 else ''} ago" for r, g in CATS if (r, g) != REFERENCE] + ["form (per goal)"]
HORIZONS = [(1, 1), (2, 3), (4, 7), (8, 15), (16, 30)]  # team games ahead (1 = the next game)


def season_games(season: int) -> tuple[dict[str, list[dict]], float]:
    """team -> its games, oldest first: {date, starter, result (the starter's),
    gsaa (the starter's goals saved above average that game)}; and the league
    save %."""
    apps = nhl_stats.goalie_games(season, today=nhl_stats.season_window(season)[1])
    shots = sum(g.shots_against for g in apps)
    league_sv = sum(g.stats["sv"] for g in apps) / shots if shots else 0.9
    by_game = defaultdict(list)
    for g in apps:
        by_game[(g.team, g.game_id)].append(g)
    out = defaultdict(list)
    for (team, _), gs in by_game.items():
        starters = [g for g in gs if g.started]
        if len(starters) != 1:
            continue
        s = starters[0]
        out[team].append({"date": s.date, "starter": s.player_id,
                          "result": availability.start_result(s, len(gs) > 1),
                          "gsaa": s.stats["sv"] - league_sv * s.shots_against})
    return {t: sorted(gs, key=lambda x: x["date"]) for t, gs in out.items()}, league_sv


def features(games: list[dict], i: int) -> dict[int, dict]:
    """Each candidate's (a start in the team's last 10 before game i) share,
    last result, team games since it, and form, as of game i's morning."""
    before = games[max(0, i - PRIOR_GAMES):i]
    starts = [(g["date"], g["starter"]) for g in games[:i]]
    out = {}
    for pid in {g["starter"] for g in games[max(0, i - availability.RECENT_STARTS):i]}:
        share = availability.start_share(pid, games[i]["date"], None, starts,
                                         sum(g["starter"] == pid for g in before) / len(before))
        own = [g for g in games[:i] if g["starter"] == pid]
        gap = i - max(j for j in range(i) if games[j]["starter"] == pid)
        out[pid] = {"share": share, "result": own[-1]["result"], "gap": min(gap, 3),
                    "form": sum(g["gsaa"] for g in own[-FORM_STARTS:])}
    return out


def x_of(f: dict) -> list[float]:
    cats = [1.0 if (f["result"], f["gap"]) == c else 0.0 for c in CATS if c != REFERENCE]
    return cats + [f["form"]]


def rows(season: int) -> dict[tuple[int, int], list[dict]]:
    """horizon -> games: {offset (log share), x, y (the starter's index), now
    (the bot's odds today, next game only)}."""
    teams, _ = season_games(season)
    out = defaultdict(list)
    for games in teams.values():
        for i in range(10, len(games)):
            cands = features(games, i)
            pids = sorted(cands)
            last = games[i - 1]
            for lo, hi in HORIZONS:
                for k in range(lo, hi + 1):
                    t = i + k - 1
                    if t >= len(games):
                        break
                    if (games[t]["date"] - games[t - 1]["date"]).days == 1:
                        continue  # back-to-backs have their own rule
                    if games[t]["starter"] not in cands:
                        continue  # a call-up or a goalie back from injury: not predictable here
                    row = {"offset": np.log([max(cands[p]["share"], 1e-3) for p in pids]),
                           "x": np.array([x_of(cands[p]) for p in pids]),
                           "y": pids.index(games[t]["starter"])}
                    if k == 1:
                        # The bot today: availability.goalie with the team's last result.
                        starts = [(g["date"], g["starter"]) for g in games[:i]]
                        prior = {p: sum(g["starter"] == p for g in games[max(0, i - PRIOR_GAMES):i])
                                 / len(games[max(0, i - PRIOR_GAMES):i]) for p in pids}
                        row["now"] = np.array([availability.goalie(p, "", games[i]["date"], None, None, starts,
                                                                   prior[p], last_result=last["result"]).prob
                                               for p in pids])
                    out[(lo, hi)].append(row)
    return out


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


def probs(row: dict, beta: np.ndarray) -> np.ndarray:
    return _softmax(row["offset"] + row["x"] @ beta)


def fit(rs: list[dict], cols: list[int] | None = None, iters: int = 30) -> np.ndarray:
    """Newton's method on the conditional logit's likelihood (a little ridge
    so empty categories stay at 1), on the feature columns `cols` (all)."""
    cols = list(range(len(FEATURES))) if cols is None else cols
    k = len(cols)
    beta = np.zeros(k)
    for _ in range(iters):
        grad, hess = -1e-2 * beta, -1e-2 * np.eye(k)
        for r in rs:
            x = r["x"][:, cols]
            p = _softmax(r["offset"] + x @ beta)
            xbar = p @ x
            grad += x[r["y"]] - xbar
            d = x - xbar
            hess -= (d * p[:, None]).T @ d
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.abs(step).max() < 1e-6:
            break
    full = np.zeros(len(FEATURES))
    full[cols] = beta
    return full


def loss(rs: list[dict], prob) -> float:
    return -sum(math.log(max(prob(r)[r["y"]], 1e-9)) for r in rs) / len(rs)


def shares(r: dict) -> np.ndarray:
    s = np.exp(r["offset"])
    return s / s.sum()


def now(r: dict) -> np.ndarray:
    return r["now"] / r["now"].sum()


def counts(rs: list[dict]) -> dict[str, int]:
    out = defaultdict(int)
    for r in rs:
        for j, name in enumerate(FEATURES[:-1]):
            out[name] += int(r["x"][:, j].sum())
    return out


def main() -> None:
    data = {s: rows(s) for s in (*TRAIN, TEST)}
    for h in HORIZONS:
        train = [r for s in TRAIN for r in data[s][h]]
        test = data[TEST][h]
        beta = fit(train)
        alone = {s: fit(data[s][h]) for s in (*TRAIN, TEST)}
        span = f"game {h[0]}" if h[0] == h[1] else f"games {h[0]}-{h[1]}"
        print(f"\n== {span} ahead: fit {TRAIN} (n={len(train)} games), check {TEST} (n={len(test)}) ==")
        n = counts(train)
        print(f"  {'feature':16s} {'fit':>6s}   " + "  ".join(f"{f'{s // 10000 % 100}-{s % 100:02d}':>6s}" for s in alone) + "   (odds x; form: per goal)")
        for j, name in enumerate(FEATURES):
            d = 3 if name.startswith("form") else 2  # a goal's effect is small: 3 decimals
            print(f"  {name:16s} {math.exp(beta[j]):6.{d}f}   " + "  ".join(f"{math.exp(alone[s][j]):6.{d}f}" for s in alone)
                  + (f"   n={n[name]}" if name in n else ""))
        line = f"  log loss on {TEST}: shares {loss(test, shares):.4f}"
        if h == (1, 1):
            line += f", the bot now {loss(test, now):.4f}"
        line += f", the fit {loss(test, lambda r: probs(r, beta)):.4f}"
        results = fit(train, list(range(len(FEATURES) - 1)))
        form = fit(train, [len(FEATURES) - 1])
        line += (f" (results alone {loss(test, lambda r: probs(r, results)):.4f},"
                 f" form alone {loss(test, lambda r: probs(r, form)):.4f}: x{math.exp(form[-1]):.3f} a goal)")
        print(line)


if __name__ == "__main__":
    main()
