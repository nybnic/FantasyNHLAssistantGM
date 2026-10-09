"""The plan: which add/drops to make and when, as one coherent set, and when
to change it (docs/plan-2026-10-09.md, step 3).

Composing. Moves are taken best value first, each judged on the roster with
the ones before it made (no two moves share an add or a drop). Every move
must clear the add price. When it's made:
- A move that pays this week takes one of this week's adds, today.
- A keeper that does nothing this week (matchup.waits) loses nothing by
  waiting for Monday's adds, so it waits when another move can use this
  week's add (decision log 2026-10-01: McBain now, Silovs Monday). If nothing
  else can, this week's add would expire unused: the keeper takes it today
  (2026-10-09: Kantserov took the add and Kelly, worth twice as much, was
  sent to Monday dropping the same player). Before Wednesday's plan it's held
  for that plan instead, so the add stays free to chase with (Nico, 2026-10-03).

Committing. A plan Nico has seen stays unless one of its moves can no longer
be made (the add taken, the drop gone, skipped, or no longer worth an add) or
a new plan is better by CHANGE_MARGIN. Day-to-day changes in the projections
otherwise flip near-ties (week 2: 13 cards, 4 different adds).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

from engine import matchup
from engine.addprice import AddPrice

# A new plan replaces the one Nico has seen only if it's this much better, in
# wins (2 win-pts: about 2 points this week in a close week). A judgment call
# (Nico, 2026-10-09) until the plans' day-to-day jitter is measured.
CHANGE_MARGIN = 0.02


@dataclass
class Planned:
    move: matchup.Move
    when: dt.date
    why: str  # "now": pays this week; "spare": a keeper on this week's otherwise unused add;
    #           "monday": a keeper that waits for next week's adds; "held": kept for Wednesday's plan

    @property
    def key(self) -> str:
        return key(self.move)


def key(m: matchup.Move) -> str:
    """As report.move_key: "add id:drop id" (0 for an open spot; ":IR" for an IR stash)."""
    return f"{m.add.id}:{m.drop.id if m.drop else 0}" + (":IR" if m.ir_slot else "")


def _clash(m: matchup.Move, chosen: list[matchup.Move]) -> bool:
    adds = {c.add.id for c in chosen}
    drops = {c.drop.id for c in chosen if c.drop}
    return m.add.id in adds | drops or bool(m.drop and m.drop.id in adds | drops)


def compose(ranked: list[matchup.Move], rerank: Callable[[list[matchup.Move]], list[matchup.Move]],
            price: AddPrice | None, now_slots: int, today: dt.date, monday: dt.date, midweek: dt.date,
            hold_keepers: bool = False, monday_slots: int = 2, exclude: set[str] = frozenset()) -> list[Planned]:
    """The plan, best first. `ranked`: every move on today's roster;
    `rerank(moves)`: the moves again with those made; `now_slots`: this week's
    adds left; `exclude`: move keys Nico skipped."""
    if price is None:
        return []
    plan: list[Planned] = []
    passed: set[str] = set(exclude)
    current = ranked
    now_left, monday_left = now_slots, monday_slots
    while True:
        chosen = [p.move for p in plan]
        passing = [m for m in current if key(m) not in passed and not matchup.rejection(m, price)
                   and not _clash(m, chosen)]
        if not passing:
            break
        best = passing[0]
        passed.add(key(best))
        if not matchup.waits(best):
            if not now_left:
                continue  # it pays this week or not at all
            plan.append(Planned(best, best.plays_from or today, "now"))
            now_left -= 1
        else:
            others = any(not matchup.waits(m) and not _clash(m, chosen + [best]) for m in passing[1:])
            if now_left and not others:
                when = max(midweek, best.plays_from or midweek) if hold_keepers else best.plays_from or today
                plan.append(Planned(best, when, "held" if hold_keepers else "spare"))
                now_left -= 1
            elif monday_left:
                plan.append(Planned(best, max(monday, best.plays_from or monday), "monday"))
                monday_left -= 1
            else:
                continue
        if not now_left and not monday_left:
            break
        current = rerank([p.move for p in plan])
    # A keeper sent to Monday while this week's add went unused after all takes it.
    for i, p in enumerate(plan):
        if p.why == "monday" and now_left:
            plan[i] = Planned(p.move, max(midweek, p.move.plays_from or midweek) if hold_keepers
                              else p.move.plays_from or today, "held" if hold_keepers else "spare")
            now_left -= 1
    return sorted(plan, key=lambda p: p.when)


def search(roster, opponent: matchup.TeamWeek, candidates: list, ctx, schedule: dict, lines: dict, starters: dict,
           future: dict, weeks_after: int, price: AddPrice | None, now_slots: int, today: dt.date,
           monday: dt.date, midweek: dt.date, hold_keepers: bool = False, available_from: dict | None = None,
           so_far: tuple | None = None, hold_days: int = 7 * matchup.STREAM_WEEKS, ahead: list | None = None,
           monday_slots: int = 2, exclude: set[str] = frozenset(), ranked: list | None = None) -> list[Planned]:
    """compose() over matchup.candidate_moves: each move judged on the roster
    with the plan's earlier moves made. `ranked` (today's moves) saves a search."""
    later_weight = price.later_weight if price else 0.0
    so_far = so_far or matchup._so_far(matchup.active(roster), ctx, sorted(schedule))  # banked before any move

    def rank(chosen: list[matchup.Move]) -> list[matchup.Move]:
        trial = roster
        for m in chosen:
            trial = matchup._swap(trial, m.add, m.drop, m.ir_slot or matchup.BENCH)
        taken = {m.add.id for m in chosen}
        return matchup.candidate_moves(trial, opponent, [c for c in candidates if c.id not in taken], ctx, schedule,
                                       lines, starters, future, weeks_after, available_from, so_far, hold_days,
                                       later_weight, ahead)

    return compose(ranked if ranked is not None else rank([]), rank, price, now_slots, today, monday, midweek,
                   hold_keepers, monday_slots, exclude)


def total(plan: list[Planned]) -> float:
    return sum(p.move.value for p in plan)


@dataclass
class Verdict:
    keep: bool
    reason: str  # why it changed, in words ("" when kept or when there was no plan)


def decide(old: list[dict] | None, new: list[Planned], current: dict[str, matchup.Move],
           problems: dict[str, str], price: AddPrice | None) -> Verdict:
    """Keep the plan Nico has seen (`old`: its moves as stored, {key, add, drop})
    or take `new`. `current`: every move's value now (by key); `problems`: why
    an old move can't be made any more (taken, drop gone, skipped, done), by key."""
    if not old:
        return Verdict(False, "")
    if {p.key for p in new} == {o["key"] for o in old}:
        return Verdict(True, "")
    for o in old:
        label = _label(o)
        if o["key"] in problems:
            return Verdict(False, f"{label}: {problems[o['key']]}")
        m = current.get(o["key"])
        if m is None:
            return Verdict(False, f"{label} no longer comes up as a move")
        if price is None or matchup.rejection(m, price):
            worth = f" (worth {100 * m.value:.1f} win-pts, under the {100 * price.lam:.1f} an add costs)" if price else ""
            return Verdict(False, f"{label} is no longer worth an add{worth}")
    old_total = sum(current[o["key"]].value for o in old)
    gain = total(new) - old_total
    if gain < CHANGE_MARGIN:
        return Verdict(True, "")
    return Verdict(False, f"the new plan is better by {100 * gain:.0f} win-pts")


def _label(o: dict) -> str:
    return o["add"]["name"] + (f" for {o['drop']['name']}" if o.get("drop") else "")
