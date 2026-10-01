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

CONFIDENT_WEEKS = 2  # weeks after this one the add rule judges (decision log); later ones are display only
POSITION_ORDER = ["C", "LW", "RW", "D", "G"]


def _short(name: str) -> str:
    first, _, last = name.partition(" ")
    return f"{first[0]}. {last}" if last else name


def _last(name: str) -> str:
    return name.partition(" ")[2] or name


def _move_label(m: matchup.Move) -> str:
    return f"{_last(m.add.name)} for {_last(m.drop.name)}" if m.drop else f"{_last(m.add.name)} (open spot)"


def week_view(week: int, me: matchup.TeamWeek, them: matchup.TeamWeek, ranked: list[matchup.Move],
              recommended: list[matchup.Move], top: int = 5) -> dict:
    """Win odds now and with each of the best `top` adds (each player once, his
    best drop) plus every recommended one, and both teams' projected running
    totals over the days left."""
    best: dict[int, matchup.Move] = {}
    for m in ranked:
        if m.add.id not in best or m.win_after > best[m.add.id].win_after:
            best[m.add.id] = m
    chosen = {(m.add.id, m.drop.id if m.drop else None) for m in recommended}
    adds = sorted(best.values(), key=lambda m: m.win_after, reverse=True)[:top]
    adds += [m for m in recommended if m not in adds]
    days = sorted(me.by_day)

    def running(team: matchup.TeamWeek) -> list[float]:
        totals = [team.so_far]
        for d in days:
            totals.append(totals[-1] + team.by_day.get(d, 0.0))
        return totals

    return {
        "week": week,
        "win_now": matchup.win_prob(me, them),
        "adds": [{"label": _move_label(m), "games": m.games, "win": m.win_after,
                  "recommended": (m.add.id, m.drop.id if m.drop else None) in chosen} for m in adds],
        "race": {"labels": ["So far"] + [f"{d:%a}" for d in days], "me": running(me), "them": running(them),
                 "expected": [me.expected, them.expected]},
    }


def schedule_view(roster: list[RosterPlayer], spans: list[tuple[int, str, matchup.TeamWeek, matchup.TeamWeek]]) -> dict:
    """My active players by day over the given weeks ((week, opponent, my week,
    their week)): "start" (in the best lineup), "bench" (plays, but no slot is
    free), or None (no game); goalies carry their start odds. Below: open
    starting slots, and both teams' lineup games per day."""
    days, my_games, their_games, open_slots, cells = [], [], [], [], {}
    for week, _, mine, theirs in spans:
        for d in sorted(mine.lineups):
            day = mine.lineups[d]
            days.append({"date": d.isoformat(), "label": f"{d:%a}", "day": d.day, "week": week})
            starting = [slot for slot, _ in day.values() if slot != BENCH]
            my_games.append(len(starting))
            their_games.append(sum(slot != BENCH for slot, _ in theirs.lineups.get(d, {}).values()))
            open_slots.append({pos: n - starting.count(pos) for pos, n in STARTERS.items()})
            for pid, (slot, prob) in day.items():
                cells.setdefault(pid, {})[d.isoformat()] = ("start" if slot != BENCH else "bench", prob)
    players = sorted(active(roster), key=lambda p: (POSITION_ORDER.index(p.positions[0]), p.name))
    rows = []
    for p in players:
        mine = cells.get(p.id, {})
        rows.append({"name": _short(p.name), "positions": "/".join(p.positions),
                     "cells": [mine.get(d["date"], (None, None))[0] for d in days],
                     "probs": [mine[d["date"]][1] if p.is_goalie and d["date"] in mine else None for d in days]})
    return {"days": days, "rows": rows, "open": open_slots, "my_games": my_games, "their_games": their_games,
            "weeks": [{"week": w, "opponent": opp} for w, opp, _, _ in spans]}


def weekly_gains(roster: list[RosterPlayer], move: matchup.Move, ctx, week_schedules: dict[int, dict],
                 lines: dict, starters: dict) -> list[tuple[int, float]]:
    """The add's points gain in each later week (`week_schedules`: week ->
    that week's schedule), judged like the long run: durability included."""
    trial = matchup._swap(roster, move.add, move.drop)
    gains = []
    for week, schedule in sorted(week_schedules.items()):
        before = matchup.project("me", roster, ctx, schedule, lines, starters, True).expected
        after = matchup.project("me", trial, ctx, schedule, lines, starters, True).expected
        gains.append((week, after - before))
    return gains


def add_view(move: matchup.Move, week: int, later: list[tuple[int, float]], budget: dict) -> dict:
    """This week's gain, then each later week's; weeks past the add rule's
    horizon are marked as less certain."""
    rows = [{"week": week, "gain": move.week_gain, "confident": True}]
    rows += [{"week": w, "gain": g, "confident": w - week <= CONFIDENT_WEEKS} for w, g in later]
    return {"label": f"Add {_short(move.add.name)}" + (f", drop {_short(move.drop.name)}" if move.drop else ""),
            "weeks": rows, "win": [move.win_before, move.win_after], "budget": budget}


def budget_view(decisions: list[dict], week: int) -> dict:
    """Adds used by the end of each week so far, against an even pace to the
    regular season's share, then the playoff reserve."""
    used_by_week: dict[int, int] = {}
    for d in decisions:
        if d["type"] == "add" and d["decision"] == "done":
            w = weeks.week_of(dt.date.fromisoformat(d["date"]))
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
