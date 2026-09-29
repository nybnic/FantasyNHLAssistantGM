"""/trade: what a proposed trade does to your team and to theirs.

Judged like the weekly plan judges a long-run add: the whole-lineup
projection over the two weeks after the trade clears (with durability), per
week, before and after. Both teams play from the same free-agent pool, so:
- a team left with an open roster spot fills it with its best free agent
  (that costs an add), and one left over the limit drops its lowest-value
  players;
- you always keep three goalies (decision log), so trading one away means
  adding one;
- the result is close to zero-sum: what you gain they mostly lose. A trade
  gets accepted because they value players differently (names, "starting
  goalie"), which the numbers here don't try to model.

To give the gain context, it is also shown as win odds against a team as
good as yours is now (50% before the trade, by definition).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from dataclasses import dataclass

from clients.names import normalize_name
from engine import matchup
from league.roster import BENCH, RosterPlayer, active

# The commissioner can reject a trade for two days (league settings), and the
# model's value of a single player barely moves in two weeks.
REVIEW_DAYS = 2
HORIZON_DAYS = 14
# Below this, a trade is noise next to the model's error (a judgment call).
MIN_GAIN_PER_WEEK = 1.0


@dataclass
class Side:
    team: str
    per_week: float  # expected points per week, after minus before
    adds: list[RosterPlayer]  # free agents filling open spots after the trade...
    adds_before: list[RosterPlayer]  # ...and without it
    drops: list[RosterPlayer]  # to get back under the roster limit


@dataclass
class Result:
    partner: str
    give: list[RosterPlayer]
    get: list[RosterPlayer]
    me: Side
    them: Side
    win_even: float  # P(win) after the trade vs a team as good as yours before it


def horizon(today: dt.date) -> list[dt.date]:
    start = today + dt.timedelta(days=REVIEW_DAYS)
    return [start + dt.timedelta(days=i) for i in range(HORIZON_DAYS)]


def _split(side: str) -> list[str]:
    return [s.strip() for s in re.split(r",|\+|\band\b|\n", side) if s.strip()]


def _match(name: str, rosters: dict[str, list[RosterPlayer]]) -> list[tuple[str, RosterPlayer]]:
    """Rostered players called `name`: the full name, else the end of one
    ("Knight", "Hughes" - which can match several)."""
    want = normalize_name(name)
    everyone = [(team, p) for team, players in rosters.items() for p in players]
    exact = [(t, p) for t, p in everyone if normalize_name(p.name) == want]
    return exact or [(t, p) for t, p in everyone if normalize_name(p.name).endswith(" " + want)]


def resolve(text: str, my_team: str, rosters: dict[str, list[RosterPlayer]]
            ) -> tuple[list[RosterPlayer], list[RosterPlayer], str] | str:
    """(give, get, partner) from "Knight, Tuch for Makar", or what's wrong."""
    give_text, sep, get_text = re.sub(r"\s+for\s+", " for ", text.strip(), flags=re.I).partition(" for ")
    if not sep or not give_text.strip() or not get_text.strip():
        return "Send /trade Your Player for Their Player (several: Knight, Tuch for Makar)."
    sides: list[list[tuple[str, RosterPlayer]]] = []
    for part in (give_text, get_text):
        found = []
        for name in _split(part):
            hits = _match(name, rosters)
            if not hits:
                return f"No rostered player called {name!r}."
            if len(hits) > 1:
                return f"{name!r} could be " + " or ".join(f"{p.name} ({t})" for t, p in hits) + ". Use the full name."
            found.append(hits[0])
        sides.append(found)
    give, get = sides
    if any(t != my_team for t, _ in give):
        return "Not on your roster: " + ", ".join(f"{p.name} ({t})" for t, p in give if t != my_team)
    partners = {t for t, _ in get}
    if my_team in partners:
        return "Already yours: " + ", ".join(p.name for t, p in get if t == my_team)
    if len(partners) > 1:
        return "Those players are on different teams: " + ", ".join(f"{p.name} ({t})" for t, p in get)
    return [p for _, p in give], [p for _, p in get], partners.pop()


def _long_run(roster, ctx, schedule, lines, starters) -> matchup.TeamWeek:
    return matchup.project("", roster, ctx, schedule, lines, starters, long_run=True)


def _settle(roster: list[RosterPlayer], pool: list[RosterPlayer], min_goalies: int, ctx, schedule, lines,
            starters) -> tuple[list[RosterPlayer], list[RosterPlayer], list[RosterPlayer]]:
    """(roster, adds, drops) once it fits the roster limit again: extra
    players go, lowest long-run value first (keeping `min_goalies`, and
    making room for a goalie if it's short of them), and open spots take
    the free agent who adds the most."""
    drops = []

    def goalies() -> int:
        return sum(p.is_goalie for p in active(roster))

    shortlist = matchup.shortlist(pool, ctx, schedule, lines, starters)
    goalie_room = min(max(min_goalies - goalies(), 0), sum(p.is_goalie for p in shortlist))
    while len(active(roster)) > matchup.ACTIVE_SPOTS - goalie_room:
        can_go = [p for p in active(roster) if not (p.is_goalie and goalies() <= min_goalies)]
        drop = min(can_go, key=lambda p: matchup.season_value(p, ctx, lines))
        drops.append(drop)
        roster = [p for p in roster if p.id != drop.id]
    adds = []
    while len(active(roster)) < matchup.ACTIVE_SPOTS:
        need_goalie = goalies() < min_goalies
        options = [p for p in shortlist if p.is_goalie or not need_goalie]
        if not options:
            break
        add = max(options, key=lambda p: _long_run(matchup._swap(roster, p, None), ctx, schedule, lines,
                                                   starters).expected)
        adds.append(add)
        shortlist = [p for p in shortlist if p.id != add.id]
        roster = matchup._swap(roster, add, None)
    return roster, adds, drops


def _after(roster: list[RosterPlayer], out: list[RosterPlayer], incoming: list[RosterPlayer]) -> list[RosterPlayer]:
    ids = {p.id for p in out}
    return [p for p in roster if p.id not in ids] + [dataclasses.replace(p, slot=BENCH) for p in incoming]


def _per_week(week: matchup.TeamWeek, weeks: float) -> matchup.TeamWeek:
    return dataclasses.replace(week, expected=week.expected / weeks, variance=week.variance / weeks)


def evaluate(mine: list[RosterPlayer], theirs: list[RosterPlayer], partner: str, give: list[RosterPlayer],
             get: list[RosterPlayer], pool: list[RosterPlayer], ctx, schedule, lines, starters) -> Result:
    """`schedule` is the horizon's games (see `horizon`); `pool` the free agents.
    Open spots are filled before the trade too, so a trade gets no credit
    for a hole the weekly plan would fill anyway."""
    weeks = len(schedule) / 7

    def both(roster, pool, min_goalies):
        settled_before, adds_before, _ = _settle(roster(False), pool, min_goalies, ctx, schedule, lines, starters)
        settled_after, adds_after, drops = _settle(roster(True), pool, min_goalies, ctx, schedule, lines, starters)
        before = _long_run(settled_before, ctx, schedule, lines, starters)
        after = _long_run(settled_after, ctx, schedule, lines, starters)
        return before, after, adds_after, adds_before, drops, {p.id for p in adds_before + adds_after}

    my_before, my_after, my_adds, my_usual, my_drops, taken = both(
        lambda traded: _after(mine, give, get) if traded else mine, pool, matchup.MIN_GOALIES)
    their_before, their_after, their_adds, their_usual, their_drops, _ = both(
        lambda traded: _after(theirs, get, give) if traded else theirs, [p for p in pool if p.id not in taken], 0)
    return Result(
        partner=partner, give=give, get=get,
        me=Side("you", (my_after.expected - my_before.expected) / weeks, my_adds, my_usual, my_drops),
        them=Side(partner, (their_after.expected - their_before.expected) / weeks, their_adds, their_usual, their_drops),
        win_even=matchup.win_prob(_per_week(my_after, weeks), _per_week(my_before, weeks)),
    )


def _names(players: list[RosterPlayer]) -> str:
    return ", ".join(p.name for p in players)


def text(r: Result) -> str:
    if r.me.per_week >= MIN_GAIN_PER_WEEK:
        verdict = "Worth proposing"
    elif r.me.per_week > -MIN_GAIN_PER_WEEK:
        verdict = "About even: not worth the hassle"
    else:
        verdict = "Don't: it makes you worse"
    lines = [
        f"{verdict}. {_names(r.give)} for {_names(r.get)} ({r.partner})",
        f"You: {r.me.per_week:+.1f} pts/week, win {matchup._pct(0.5)} -> {matchup._pct(r.win_even)} "
        "vs a team as good as yours now",
        f"Them: {r.them.per_week:+.1f} pts/week",
    ]
    for side, who in ((r.me, "You"), (r.them, "They")):
        new = [p for p in side.adds if p not in side.adds_before]
        if new:
            usual = [p for p in side.adds_before if p not in side.adds]
            extra = len(side.adds) - len(side.adds_before)
            lines.append(f"{who} then add {_names(new)}"
                         + (f" instead of {_names(usual)}" if usual else "")
                         + (f" ({extra} more add{'s' if extra > 1 else ''})" if extra > 0 else ""))
        if side.drops:
            lines.append(f"{who} then drop {_names(side.drops)}")
    lines.append("Over the two weeks after the 2-day review; open spots filled from free agents either way.")
    return "\n".join(lines)
