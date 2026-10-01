"""How the add suggestions turned out: what each added player scored against
the player he would replace, over the weeks after the suggestion, whether
Nico made the add or not.

Raw fantasy points in NHL games, not lineup-adjusted: a bench game counts
too, so this is a rough check of the picks, not of their exact worth in
Yahoo. A suggestion is scored once WINDOW_DAYS have passed. The same add and
drop suggested again counts once, with the last decision on it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from config.league import GOALIE_WEIGHTS, SKATER_WEIGHTS, fantasy_points

WINDOW_DAYS = 14  # about a streamer's hold (engine/matchup.hold_weeks); a judgment call


@dataclass
class Scored:
    date: dt.date
    decision: str  # "done", "skip", "taken" or "none" (no tap)
    add: str
    drop: str | None
    add_points: float
    drop_points: float

    @property
    def gain(self) -> float:
        return self.add_points - self.drop_points


def _points(ctx, player_id: int | None, first: dt.date, last: dt.date) -> float:
    if player_id is None:
        return 0.0  # an open roster spot
    total = 0.0
    for logs, weights in ((ctx.skater_games, SKATER_WEIGHTS), (ctx.goalie_games, GOALIE_WEIGHTS)):
        total += sum(fantasy_points(g.stats, weights) for g in logs.get(player_id, []) if first <= g.date <= last)
    return total


def score(decisions: list[dict], ctx, today: dt.date) -> list[Scored]:
    """Every add suggestion whose window has closed by `today`, oldest first."""
    last_word: dict[tuple, dict] = {}
    for d in decisions:
        if d["type"] == "add" and d.get("add") is not None:
            last_word[(d["add"], d.get("drop"))] = d
    out = []
    for d in sorted(last_word.values(), key=lambda d: d["date"]):
        first = dt.date.fromisoformat(d["date"])
        last = first + dt.timedelta(days=WINDOW_DAYS - 1)
        if last >= today:
            continue
        out.append(Scored(first, d["decision"], d.get("add_name") or str(d["add"]), d.get("drop_name"),
                          _points(ctx, d["add"], first, last), _points(ctx, d.get("drop"), first, last)))
    return out


def line(scored: list[Scored]) -> str | None:
    """One line for the weekly result: made adds' gain, and what skipped ones
    would have gained. None until a suggestion's window has closed."""
    if not scored:
        return None
    made = [s for s in scored if s.decision == "done"]
    passed = [s for s in scored if s.decision != "done"]
    parts = []
    if made:
        parts.append(f"{len(made)} made, {sum(s.gain for s in made):+.0f} pts vs their drops")
    if passed:
        parts.append(f"{len(passed)} not made, would have been {sum(s.gain for s in passed):+.0f}")
    return f"Suggestions so far ({WINDOW_DAYS} days after each, raw points): " + "; ".join(parts) + "."
