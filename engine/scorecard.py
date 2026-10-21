"""How the moves turned out.

`score`/`line`: each add suggestion, made or not, against the player he would
replace over the weeks after it. Raw fantasy points in NHL games, not
lineup-adjusted: a bench game counts too, so this is a rough check of the
picks, not of their exact worth in Yahoo. A suggestion is scored once
WINDOW_DAYS have passed. The same add and drop suggested again counts once,
with the last decision on it.

`week_value`/`value_line`: the scoreboard (Nico, 2026-10-21). What every move
since the draft added, against keeping the drafted roster: both rosters'
best lineup each day from box scores (the same lineup rule as the week's
result), the goalie minimum applied, and what those points were worth in that
week's win odds as the week's first plan saw them. No pairing of adds with
drops is needed, so moves learned any way (taps, screenshots) all count.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import math

from config.league import GOALIE_WEIGHTS, MIN_GOALIE_GAMES_PER_WEEK, SKATER_WEIGHTS, fantasy_points
from engine import matchup

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


def lineup_points(history: dict[dt.date, list], ctx, days: list[dt.date], finished: bool = True) -> float:
    """A week's points from box scores, each day's best lineup from that day's
    roster (`history`), goalie points zeroed if a finished week missed the minimum."""
    skater, goalie, games = matchup._so_far([], ctx, days, history)
    made = games >= MIN_GOALIE_GAMES_PER_WEEK or not finished
    return skater + (goalie if made else 0.0)


def week_value(mine: dict[dt.date, list], drafted: list, theirs: float, ctx, days: list[dt.date],
               first: dict | None = None, finished: bool = True) -> dict:
    """The moves' week: my points with each day's real roster, the drafted
    roster's, the difference ("added"), my final margin; "wins": what the added
    points were worth at the odds the week's first plan saw (its margin as P(win)
    reads it, matchup.margin); "forecast": what that plan projected them to add."""
    actual = lineup_points(mine, ctx, days, finished)
    kept = lineup_points({d: drafted for d in days}, ctx, days, finished)
    added = actual - kept
    out = {"actual": round(actual, 2), "drafted": round(kept, 2), "added": round(added, 2),
           "margin": round(actual - theirs, 2)}
    if first:
        banked = first["so_far"][0] - first["so_far"][1]
        mu = banked + matchup.MARGIN_REALIZES * (first["expected"][0] - first["expected"][1] - banked)
        sd = math.hypot(*first["sd"]) or 1.0
        out["wins"] = round(matchup._phi(mu / sd) - matchup._phi((mu - added) / sd), 4)
        if first.get("drafted") is not None:
            out["forecast"] = round(first["expected"][0] - first["drafted"], 2)
    return out


def outcome(v: dict) -> str:
    """Whether the moves decided the week."""
    won, without = v["margin"] > 0, v["margin"] - v["added"] > 0
    if won != without:
        return "they turned a loss into a win" if won else "they turned a win into a loss"
    return "you'd have won without them too" if won else "you'd have lost without them too"


def value_line(v: dict | None, season: list[dict]) -> str | None:
    """The scoreboard's lines in the week's result: this week, then the season
    so far (`season`: every week's value, this one included)."""
    if not v:
        return None
    head = (f"Your moves since the draft: {v['added']:+.0f} pts this week ({v['actual']:.0f} vs the drafted "
            f"roster's {v['drafted']:.0f})")
    if v.get("wins") is not None:
        head += f", {v['wins']:+.2f} wins at this week's odds"
    head += f"; {outcome(v)}."
    if v.get("forecast") is not None:
        head += f" Forecast at the week's first plan: {v['forecast']:+.0f}."
    flipped = sum(1 for w in season if (w["margin"] > 0) != (w["margin"] - w["added"] > 0))
    wins = [w["wins"] for w in season if w.get("wins") is not None]
    tail = (f"Season, {len(season)} week{'s' if len(season) != 1 else ''}: {sum(w['added'] for w in season):+.0f} pts"
            + (f", {sum(wins):+.2f} wins" if wins else "")
            + f", {flipped} result{'s' if flipped != 1 else ''} decided by them.")
    return head + "\n" + tail
