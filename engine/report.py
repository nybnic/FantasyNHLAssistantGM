"""What the charts show, as plain data: numbers only, no drawing.

notify/charts.py draws these for Telegram, and a dashboard can read the same
dicts, so every view of a number agrees with the weekly plan's.
"""
from __future__ import annotations

import datetime as dt

from config.league import MAX_ADDS_PER_SEASON, REGULAR_SEASON_WEEKS, STARTERS
from engine import matchup
from league import weeks
from league.roster import BENCH, RosterPlayer, active

POSITION_ORDER = ["C", "LW", "RW", "D", "G"]


def _short(name: str) -> str:
    first, _, last = name.partition(" ")
    return f"{first[0]}. {last}" if last else name


def _last(name: str) -> str:
    return name.partition(" ")[2] or name


def move_key(m: matchup.Move) -> str:
    """A move's id across views: "add id:drop id" (0 for an open spot; ":IR" for an IR stash)."""
    return f"{m.add.id}:{m.drop.id if m.drop else 0}" + (":IR" if m.ir_slot else "")


def _slots(day: dict) -> tuple[int, dict[str, int], int]:
    """(lineup games, open starting slots by position, games lost to a full lineup) of one day's lineup."""
    starting = [slot for slot, _ in day.values() if slot != BENCH]
    benched = sum(slot == BENCH for slot, _ in day.values())
    return len(starting), {pos: n - starting.count(pos) for pos, n in STARTERS.items()}, benched


def _add_row(entry: dict, days: list[dict], plan: bool = False) -> dict:
    """An added player's row: where he'd slot in on each day, over the trial
    lineups of this week and next (entry: {"move", "this_week", "next_week"},
    and for a planned move its "when")."""
    m = entry["move"]
    merged, starts = {}, 0.0
    for wk in (entry["this_week"], entry["next_week"]):
        for d, day in (wk.lineups if wk else {}).items():
            if m.add.id in day:
                slot, prob = day[m.add.id]
                merged[d.isoformat()] = "start" if slot != BENCH else "bench"
                starts += prob if slot != BENCH else 0.0
    cells = [merged.get(d["date"]) for d in days]
    return {"key": move_key(m), "name": _short(m.add.name), "positions": "/".join(m.add.positions),
            "team": m.add.team, "drop": _short(m.drop.name) if m.drop else None, "cells": cells,
            "slot_games": cells.count("start"), "slot_starts": round(starts, 2) if m.add.is_goalie else None,
            "gain": [m.week_gain, entry.get("next_gain", 0.0)], "plan": plan,
            "when": entry["when"].isoformat() if entry.get("when") else None}


def schedule_view(roster: list[RosterPlayer], spans: list[tuple[int, str, matchup.TeamWeek, matchup.TeamWeek]],
                  streamers: list[dict] | None = None, recommended: list[matchup.Move] = (),
                  plan: list[dict] = (), after: list[matchup.TeamWeek | None] = ()) -> dict:
    """My active players by day over the given weeks ((week, opponent, my week,
    their week)): "start" (in the best lineup), "bench" (plays, but no slot is
    free), or None (no game); goalies carry their start odds. Then the plan's
    adds (`plan`: as streamers, with "when") and the best streamer per position
    (matchup.streamers) on the same days, as they'd slot in, and below: open
    starting slots, games lost to a full lineup, and both teams' lineup games
    per day; with `after` (my weeks with the plan made, one per span), the
    open slots and games with the plan too."""
    days, my_games, their_games, open_slots, benched, cells = [], [], [], [], [], {}
    for week, _, mine, theirs in spans:
        for d in sorted(mine.lineups):
            day = mine.lineups[d]
            days.append({"date": d.isoformat(), "label": f"{d:%a}", "day": d.day, "week": week})
            games, open_now, lost = _slots(day)
            my_games.append(games)
            their_games.append(sum(slot != BENCH for slot, _ in theirs.lineups.get(d, {}).values()))
            open_slots.append(open_now)
            benched.append(lost)
            for pid, (slot, prob) in day.items():
                cells.setdefault(pid, {})[d.isoformat()] = ("start" if slot != BENCH else "bench", prob)
    with_plan = None
    if plan and after:
        lineups = {d.isoformat(): day for wk in after if wk for d, day in wk.lineups.items()}
        with_plan = {"open": [], "my_games": [], "benched": []}
        for d, games, open_now, lost in zip(days, my_games, open_slots, benched):
            if d["date"] in lineups:
                games, open_now, lost = _slots(lineups[d["date"]])
            with_plan["open"].append(open_now)
            with_plan["my_games"].append(games)
            with_plan["benched"].append(lost)
    players = sorted(active(roster), key=lambda p: (POSITION_ORDER.index(p.positions[0]), p.name))
    rows = []
    for p in players:
        mine = cells.get(p.id, {})
        rows.append({"name": _short(p.name), "positions": "/".join(p.positions),
                     "cells": [mine.get(d["date"], (None, None))[0] for d in days],
                     "probs": [mine[d["date"]][1] if p.is_goalie and d["date"] in mine else None for d in days]})
    chosen = {(m.add.id, m.drop.id if m.drop else None) for m in recommended}
    planned = {entry["move"].add.id for entry in plan}
    plan_rows = [_add_row(entry, days, plan=True) for entry in plan]
    stream_rows = []
    for st in streamers or []:
        if st["move"].add.id in planned:
            continue  # shown once, as the plan's
        row = _add_row(st, days)
        row["recommended"] = (st["move"].add.id, st["move"].drop.id if st["move"].drop else None) in chosen
        stream_rows.append(row)
    return {"days": days, "rows": rows, "plan": plan_rows, "streamers": stream_rows, "open": open_slots,
            "benched": benched, "my_games": my_games, "their_games": their_games, "with_plan": with_plan,
            "slots": dict(STARTERS), "weeks": [{"week": w, "opponent": opp} for w, opp, _, _ in spans]}


def budget_view(adds: list[dict], week: int) -> dict:
    """Adds used (the ledger, state["adds"]) by the end of each week so far,
    against an even pace to the regular season's share, then the playoff reserve."""
    used_by_week: dict[int, int] = {}
    for a in adds:
        w = weeks.week_of(dt.date.fromisoformat(a["date"]))
        if w:
            used_by_week[w] = used_by_week.get(w, 0) + 1
    regular = MAX_ADDS_PER_SEASON - matchup.PLAYOFF_RESERVE
    pace, used, total = [], [], 0
    for w in range(1, weeks.LAST_WEEK + 1):
        if w <= REGULAR_SEASON_WEEKS:
            pace.append(regular * w / REGULAR_SEASON_WEEKS)
        else:
            pace.append(regular + matchup.PLAYOFF_RESERVE * (w - REGULAR_SEASON_WEEKS)
                        / (weeks.LAST_WEEK - REGULAR_SEASON_WEEKS))
        if w <= week:
            total += used_by_week.get(w, 0)
            used.append(total)
    return {"week": week, "used": used, "pace": pace, "cap": MAX_ADDS_PER_SEASON,
            "reserve": matchup.PLAYOFF_RESERVE, "playoffs_from": REGULAR_SEASON_WEEKS + 1}


def result_view(week: int, opponent: str, days: list[dt.date], mine: list[float], theirs: list[float],
                goalie_min: list[bool], plan: dict | None, adds: list[dict], skipped: int, budget: dict,
                finished: bool = True, scorecard: str | None = None) -> dict:
    """The finished week: each day's points for both teams (box scores, each
    day's roster, goalie points zeroed if the minimum was missed), against
    the week's first plan; the adds made and skipped; the budget."""
    final = [sum(mine), sum(theirs)]
    view = {"week": week, "opponent": opponent, "days": [d.isoformat() for d in days], "finished": finished,
            "mine": mine, "theirs": theirs, "final": final, "goalie_min": goalie_min, "plan": plan,
            "adds": len(adds), "added": [a["name"] for a in adds if a["name"]], "skipped": skipped,
            "budget": budget, "scorecard": scorecard}
    if plan:
        view["vs_plan"] = [final[i] - plan["expected"][i] for i in (0, 1)]
        view["z"] = [(final[i] - plan["expected"][i]) / plan["sd"][i] if plan["sd"][i] else None for i in (0, 1)]
    return view


def result_text(view: dict) -> str:
    """The week's result in a few lines, for a phone."""
    me, them = view["final"]
    outcome = "won" if me > them else "lost" if me < them else "tied"
    result = f"you {outcome}" if view["finished"] else "so far"
    lines = [f"Week {view['week']} vs {view['opponent']}: {result} {me:.0f} - {them:.0f} "
             "(box scores with the best lineup each day; Yahoo's can differ by bench choices)."]
    for name, made in zip(("You", "They"), view["goalie_min"]):
        if not made:
            lines.append(f"{name} missed the goalie minimum: goalie points count zero.")
    plan = view["plan"]
    if plan and view["finished"]:
        when = dt.datetime.fromisoformat(plan["at"]).strftime("%a %d %b")
        lines.append(f"The plan ({when}) said {plan['expected'][0]:.0f} - {plan['expected'][1]:.0f}, "
                     f"{plan['win']:.0%} to win. Against it you scored {view['vs_plan'][0]:+.0f}, "
                     f"they {view['vs_plan'][1]:+.0f}.")
    b = view["budget"]
    used = b["used"][-1] if b["used"] else 0
    pace = b["pace"][view["week"] - 1]
    made = f"{view['adds']} made" + (f" ({', '.join(view['added'])})" if view["added"] else "")
    lines.append(f"Adds: {made}, {view['skipped']} suggestion{'' if view['skipped'] == 1 else 's'} skipped. "
                 f"{b['cap'] - used} left; {used} used vs {pace:.1f} at an even pace.")
    if view.get("scorecard"):
        lines.append(view["scorecard"])
    return "\n".join(lines)
