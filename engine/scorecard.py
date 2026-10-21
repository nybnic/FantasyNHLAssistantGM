"""How the moves turned out.

`score`/`line`: each add suggestion, made or not, against the player he would
replace over the weeks after it. Raw fantasy points in NHL games, not
lineup-adjusted: a bench game counts too, so this is a rough check of the
picks, not of their exact worth in Yahoo. A suggestion is scored once
WINDOW_DAYS have passed. The same add and drop suggested again counts once,
with the last decision on it.

`made_moves`/`move_check`: the scoreboard's main check (Nico, 2026-10-21): each
move I made against the team just before it. The swap undone (the dropped
player kept, every other move as made), both rosters played through each week
the added player was held, from box scores with the best lineup each day and
the goalie minimum, and the difference read as wins at the odds the week's
first plan saw. Over a move's first three weeks (its week and the two ahead,
the plan's own horizon) it is set against what the plan forecast for it.

`week_value`/`value_line`: the season against doing nothing: my rosters vs the
drafted one, the same way. It flatters the moves as the season goes on (the
drafted roster never fills a hole), so it is one line of context.

Each week's points are scored the same way throughout (`lineup_points`).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

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
    """One line for the weekly result: what the suggestions not made would have
    gained (made ones are checked by `move_check`). None until one's window has closed."""
    passed = [s for s in scored if s.decision != "done"]
    if not passed:
        return None
    return (f"Suggestions not made ({WINDOW_DAYS} days after each, raw points): {len(passed)}, "
            f"would have been {sum(s.gain for s in passed):+.0f} vs their drops.")


def lineup_points(history: dict[dt.date, list], ctx, days: list[dt.date], finished: bool = True) -> float:
    """A week's points from box scores, each day's best lineup from that day's
    roster (`history`), goalie points zeroed if a finished week missed the minimum."""
    skater, goalie, games = matchup._so_far([], ctx, days, history)
    made = games >= MIN_GOALIE_GAMES_PER_WEEK or not finished
    return skater + (goalie if made else 0.0)


def wins_worth(first: dict, gain: float) -> float:
    """What `gain` points were worth in a week's P(win), at the odds its first
    plan saw (its margin as P(win) reads it, matchup.margin)."""
    banked = first["so_far"][0] - first["so_far"][1]
    mu = banked + matchup.MARGIN_REALIZES * (first["expected"][0] - first["expected"][1] - banked)
    sd = math.hypot(*first["sd"]) or 1.0
    return matchup._phi(mu / sd) - matchup._phi((mu - gain) / sd)


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
        out["wins"] = round(wins_worth(first, added), 4)
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
    """The season against doing nothing, one line (`season`: every week's
    value, this one included)."""
    if not v:
        return None
    wins = [w["wins"] for w in season if w.get("wins") is not None]
    return (f"Against keeping the drafted roster (doing nothing): {v['added']:+.0f} pts this week; season "
            f"{sum(w['added'] for w in season):+.0f} pts" + (f", {sum(wins):+.2f} wins" if wins else "") + ".")


# Each move against the team just before it.
CHECK_WEEKS = 1 + matchup.AHEAD_WEEKS  # the plan's horizon: the move's week and the two ahead


@dataclass
class MadeMove:
    add: int
    drop: int | None  # None: into an open spot
    day: dt.date  # his first day on my active roster
    until: dt.date | None  # his last, if he has gone
    # Into an open spot, while he held it: a player back from IR needed a spot and
    # this one was cut that day; without the add he'd have stayed.
    cut: int | None = None
    cut_from: dt.date | None = None

    @property
    def key(self) -> str:
        """As the Board keys moves (engine/report.move_key)."""
        return f"{self.add}:{self.drop or 0}"


def _goalie(pid: int, players: dict) -> bool:
    return "G" in players.get(str(pid), {}).get("positions", [])


def made_moves(adds: list[dict], my_days: dict[str, list[int]], players: dict, on_ir: set[int],
               known: dict[int, int]) -> list[MadeMove]:
    """Every add I made, paired with the player he replaced, from my active
    roster day by day (`my_days`). A player who leaves and comes back without
    being re-added, or is in an IR slot now, was on IR, not dropped; one who
    arrives without an add is back from IR. An add's drop: the one named
    (`known`: a Done tap's, the plan's), else the day's only drop when nobody
    came back from IR that day, else the day's drop in his group (goalie or
    skater), else none (an open spot). A drop that day left over was the cut for
    a player back from IR: it goes to the latest open-spot add still held, who
    took the spot that player would otherwise have kept."""
    days = sorted(my_days)
    roster = [set(my_days[d]) for d in days]
    add_days = {}
    for a in adds:
        if a["id"] is not None:
            add_days.setdefault(a["id"], []).append(dt.date.fromisoformat(a["date"]))

    def re_added(pid: int, after: str) -> bool:
        return any(d.isoformat() >= after for d in add_days.get(pid, []))

    def dropped(pid: int, i: int) -> bool:
        """He left on day i for good (else IR)."""
        later = next((j for j in range(i + 1, len(days)) if pid in roster[j]), None)
        if later is None:
            return pid not in on_ir
        return re_added(pid, days[i])

    out, cuts = [], []
    for i in range(1, len(days)):
        arrived, left = roster[i] - roster[i - 1], roster[i - 1] - roster[i]
        drops = [p for p in sorted(left) if dropped(p, i)]
        day = dt.date.fromisoformat(days[i])
        new = [p for p in sorted(arrived) if any(abs((d - day).days) <= 2 for d in add_days.get(p, []))]
        back = arrived - set(new)
        paired = {}
        for p in new:
            if known.get(p) in drops:
                paired[p] = known[p]
        free = [d for d in drops if d not in paired.values()]
        rest = [p for p in new if p not in paired]
        if len(rest) == 1 and len(free) == 1 and not back:
            paired[rest[0]] = free[0]
        else:
            for p in rest:
                same = next((d for d in free if d not in paired.values() and _goalie(d, players) == _goalie(p, players)),
                            None)
                if same is not None:
                    paired[p] = same
        for p in new:
            gone = next((j for j in range(i + 1, len(days)) if p not in roster[j] and dropped(p, j)), None)
            out.append(MadeMove(p, paired.get(p), day, dt.date.fromisoformat(days[gone - 1]) if gone else None))
        if back:
            cuts += [(day, d) for d in drops if d not in paired.values()]
    for day, pid in cuts:
        holding = [m for m in out if m.drop is None and m.cut is None and m.day <= day
                   and (m.until is None or day <= m.until)]
        if holding:
            m = max(holding, key=lambda m: m.day)
            m.cut, m.cut_from = pid, day
    return out


def move_weeks(m: MadeMove, history: dict[dt.date, list], people: dict, ctx, firsts: dict[int, dict],
               weeks_: list[tuple[int, list[dt.date]]]) -> list[dict]:
    """The move week by week ({week, pts, wins}), for each of `weeks_` (finished
    weeks with every day's roster in `history`): my real rosters against the same
    days with this one swap undone while he was held (his drop, and his cut from
    its day, back: `people` has them by id)."""
    out = []
    for week, days in weeks_:
        held = [d for d in days if d >= m.day and (m.until is None or d <= m.until)]
        if not held:
            out.append({"week": week, "pts": 0.0, "wins": 0.0})
            continue
        undone = dict(history)
        for d in held:
            roster = [p for p in history[d] if p.id != m.add]
            back = [m.drop] + ([m.cut] if m.cut and d >= m.cut_from else [])
            roster += [people[i] for i in back if i in people and all(p.id != i for p in roster)]
            undone[d] = roster
        gain = lineup_points(history, ctx, days) - lineup_points(undone, ctx, days)
        first = firsts.get(week)
        out.append({"week": week, "pts": round(gain, 2), "wins": round(wins_worth(first, gain), 4) if first else None})
    return out


def move_check(m: MadeMove, by_week: list[dict], forecast: dict | None, names: dict) -> dict:
    """A move's first CHECK_WEEKS weeks against the plan's forecast for them
    (its week's and the two ahead: `now_wins` + `ahead_wins`, points `week_pts` +
    `ahead_week_pts`); later points aren't checked here (a keeper's run the season)."""
    first = by_week[:CHECK_WEEKS]
    out = {"key": m.key, "day": m.day.isoformat(), "add": names.get(str(m.add), {}).get("name", str(m.add)),
           "drop": names.get(str(m.drop), {}).get("name") if m.drop else None,
           "cut": names.get(str(m.cut), {}).get("name") if m.cut else None,
           "pts": round(sum(w["pts"] for w in first), 2),
           "wins": round(sum(w["wins"] or 0.0 for w in first), 4)}
    if forecast:
        out["forecast"] = {"pts": round(forecast["week_pts"] + sum(forecast.get("ahead_week_pts") or []), 2),
                           "wins": round(forecast["now_wins"] + sum(forecast.get("ahead_wins") or []), 4),
                           "value": forecast["value"], "price": forecast.get("price"), "date": forecast["date"]}
    return out


def _day(d: str) -> str:
    x = dt.date.fromisoformat(d)
    return f"{x:%a} {x.day} {x:%b}"


def check_line(c: dict) -> str:
    """One checked move: what it did in its first weeks, against the forecast."""
    swap = c["add"] + (f" for {c['drop']}" if c["drop"] else " into an open spot"
                       + (f" (kept {c['cut']} from being cut)" if c.get("cut") else ""))
    head = f"{swap} ({_day(c['day'])}), its first {CHECK_WEEKS} weeks: {c['pts']:+.0f} pts, {c['wins']:+.2f} wins"
    f = c.get("forecast")
    if not f:
        return head + " (no forecast saved: from before the Board, or not the plan's move)."
    price = f" (all in {f['value']:+.2f} with later points; an add cost {f['price']:.2f})" if f.get("price") else ""
    return head + f"; the plan forecast {f['pts']:+.0f} pts, {f['wins']:+.2f}{price}."


def checks_line(checks: list[dict]) -> str | None:
    """Every move checked so far, in one line."""
    if not checks:
        return None
    fc = [c for c in checks if c.get("forecast")]
    head = (f"Moves checked so far: {len(checks)}, {sum(c['pts'] for c in checks):+.0f} pts, "
            f"{sum(c['wins'] for c in checks):+.2f} wins in their first {CHECK_WEEKS} weeks")
    if fc:
        head += (f"; the {len(fc)} with a forecast {sum(c['wins'] for c in fc):+.2f} wins against "
                 f"{sum(c['forecast']['wins'] for c in fc):+.2f} forecast")
    return head + "."
