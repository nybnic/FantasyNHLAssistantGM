"""/trade: what a proposed trade does to your team and to theirs.

Judged like the weekly plan judges a long-run add: the whole-lineup
projection over the 6 weeks after the trade clears (with durability), per
week, before and after. Both teams play from the same free-agent pool, so:
- a team left with an open roster spot fills it with its best free agent
  (that costs an add), and one left over the limit drops its lowest-value
  players;
- you keep at least two goalies (matchup.MIN_GOALIES), so trading one away
  below that means adding one; above it, the projection (each week's goalie
  minimum) prices whether the third is worth his spot;
- the result is close to zero-sum: what you gain they mostly lose. A trade
  gets accepted because they value players differently (names, "starting
  goalie"), which the numbers here don't try to model.

To give the gain context, it is also shown as the change in a typical
week's win odds against a team as good as yours is now (50% before the
trade, by definition). This week's matchup plays no part.

Positional balance is in the numbers already (each day's best lineup, by
Yahoo eligibility). It is also shown (F/D/G counts), and a trade that leaves
a team unable to fill its starting slots from its own roster is flagged:
they'd need an add, so expect a no.

`/trade` alone suggests trades:
1. Screen every 1-for-1 and 2-for-1 with a quick per-player lineup value
   (long-run points per game, empty slots at free-agent level). A shortcut,
   only good for ranking candidates.
2. Keep the ones they could plausibly accept: they don't lose on how the
   players feel to a manager (this league's draft rounds, `league/draft.py`,
   blended toward this season's fantasy-point rank as games pile up), and it
   doesn't leave them short of starters.
3. Judge the best dozen properly (`evaluate`) and reply with the top few,
   one per team.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import re
from dataclasses import dataclass, field
from functools import lru_cache

from clients.names import normalize_name
from config.league import GOALIE_WEIGHTS, LEAGUE_TEAMS, SKATER_WEIGHTS, STARTERS, fantasy_points
from engine import matchup
from league.roster import BENCH, RosterPlayer, active

# The commissioner can reject a trade for two days (league settings). A trade
# is for good, so it's judged like an add's long run: over 6 weeks
# (matchup.LONG_RUN_WEEKS), since a 2-week window turned one week's schedule
# into the verdict (decision log, 2026-10-01).
REVIEW_DAYS = 2
HORIZON_DAYS = 7 * matchup.LONG_RUN_WEEKS
# Below this, a trade is noise next to the model's error (a judgment call).
MIN_GAIN_PER_WEEK = 1.0
FORWARD_SLOTS = ("C", "LW", "RW")

# Suggestions (judgment calls, untested). How much a pick "feels" worth to a
# manager by round: 0.8 a round, so a first-rounder outweighs a 4th + 6th
# (0.84) but not a 2nd + 3rd (1.44). Was 0.85, which offered Knight (R4) +
# Dobson (R6) for MacKinnon (R1); Nico (2026-10-03): stars feel worth more.
# Undrafted players count as a round past the last.
PICK_DECAY = 0.8
UNDRAFTED_ROUND = 16
# Once the season runs, managers judge a player more by his fantasy points so
# far (Yahoo ranks players by them) than by where he went in the draft: his
# "feel" round blends the two, this season's rank as a round (top 16 = round
# 1) weighing as much as the draft after PERCEPTION_GAMES games (a judgment
# call, untested: there's no data on which trades get accepted).
PERCEPTION_GAMES = 20
SCREEN_KEEP = 12  # candidates judged in full (a few seconds each)
SCREEN_PER_TEAM = 3
MAX_SUGGESTIONS = 4


@dataclass
class Side:
    team: str
    per_week: float  # expected points per week, after minus before
    adds: list[RosterPlayer]  # free agents filling open spots after the trade...
    adds_before: list[RosterPlayer]  # ...and without it
    drops: list[RosterPlayer]  # to get back under the roster limit
    balance: tuple[str, str] = ("", "")  # "7F 4D 2G" before and after, before any adds
    short: list[str] = field(default_factory=list)  # starting slots it can no longer fill itself


@dataclass
class Result:
    partner: str
    give: list[RosterPlayer]
    get: list[RosterPlayer]
    me: Side
    them: Side
    win_even: float  # P(win) after the trade vs a team as good as yours before it
    give_rounds: list[int] = field(default_factory=list)  # draft rounds, UNDRAFTED_ROUND if none
    get_rounds: list[int] = field(default_factory=list)


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


def _group(p: RosterPlayer) -> str:
    return "G" if p.is_goalie else "D" if p.positions == ["D"] else "F"


def balance(roster: list[RosterPlayer], lines: dict | None = None) -> str:
    """"9F 3D 2G", plus how many are hurt (DailyFaceoff injury list): they
    count here but not in the projection."""
    players = active(roster)
    out = " ".join(f"{sum(_group(p) == g for p in players)}{g}" for g in ("F", "D", "G"))
    hurt = sum(1 for p in players
               if getattr((lines or {}).get(p.team, {}).get(normalize_name(p.name)), "injury", None))
    return out + (f" ({hurt} hurt)" if hurt else "")


def _best_forwards(forwards: tuple[tuple[float, tuple[str, ...]], ...]) -> float:
    """Most value the forwards can put in C2 LW2 RW2 (exact, small DP)."""
    @lru_cache(maxsize=None)
    def best(i: int, c: int, lw: int, rw: int) -> float:
        if i == len(forwards):
            return 0.0
        value, positions = forwards[i]
        out = best(i + 1, c, lw, rw)
        if "C" in positions and c:
            out = max(out, value + best(i + 1, c - 1, lw, rw))
        if "LW" in positions and lw:
            out = max(out, value + best(i + 1, c, lw - 1, rw))
        if "RW" in positions and rw:
            out = max(out, value + best(i + 1, c, lw, rw - 1))
        return out
    return best(0, STARTERS["C"], STARTERS["LW"], STARTERS["RW"])


def short(roster: list[RosterPlayer]) -> list[str]:
    """Starting slots the roster can't fill from its own players ("F", "D", "G")."""
    players = active(roster)
    forwards = tuple((1.0, tuple(p.positions)) for p in players if _group(p) == "F")
    missing = {
        "F": sum(STARTERS[s] for s in FORWARD_SLOTS) - round(_best_forwards(forwards)),
        "D": STARTERS["D"] - sum(_group(p) == "D" for p in players),
        "G": STARTERS["G"] - sum(_group(p) == "G" for p in players),
    }
    return [g for g, n in missing.items() for _ in range(max(n, 0))]


def _new_short(before: list[RosterPlayer], after: list[RosterPlayer]) -> list[str]:
    was = short(before)
    out = []
    for g in short(after):
        if g in was:
            was.remove(g)
        else:
            out.append(g)
    return out


def _static(roster: list[RosterPlayer], value: dict[int, float], replacement: dict[str, float]) -> float:
    """Quick lineup value per game: the best starters by long-run value,
    empty slots at free-agent level. Only for screening candidates."""
    players = active(roster)

    def top(group: str) -> float:
        values = [value[p.id] for p in players if _group(p) == group] + [replacement[group]] * STARTERS[group]
        return sum(sorted(values, reverse=True)[:STARTERS[group]])

    forwards = [(value[p.id], tuple(p.positions)) for p in players if _group(p) == "F"]
    forwards += [(replacement[s], (s,)) for s in FORWARD_SLOTS for _ in range(STARTERS[s])]
    return top("D") + top("G") + _best_forwards(tuple(forwards))


def draft_round(p: RosterPlayer, rounds: dict[str, int]) -> int:
    return rounds.get(normalize_name(p.name), UNDRAFTED_ROUND)


def season_points(p: RosterPlayer, ctx) -> tuple[float, int]:
    """(fantasy points, games) this season before today."""
    logs = ctx.goalie_games if p.is_goalie else ctx.skater_games
    games = [g for g in logs.get(p.id, []) if g.date < ctx.today]
    weights = GOALIE_WEIGHTS if p.is_goalie else SKATER_WEIGHTS
    return sum(fantasy_points(g.stats, weights) for g in games), len(games)


def feel_rounds(players: list[RosterPlayer], rounds: dict[str, int], ctx) -> dict[int, float]:
    """Player id -> the round he "feels" like to a manager now: his draft
    round, blended toward his season rank as a round (PERCEPTION_GAMES)."""
    season = {p.id: season_points(p, ctx) for p in players}
    order = sorted(players, key=lambda p: -season[p.id][0])
    out = {}
    for rank, p in enumerate(order):
        games = season[p.id][1]
        now = min(rank // LEAGUE_TEAMS + 1, UNDRAFTED_ROUND)
        weight = games / (games + PERCEPTION_GAMES)
        out[p.id] = (1 - weight) * draft_round(p, rounds) + weight * now
    return out


def perceived(players: list[RosterPlayer], rounds: dict[str, int], feel: dict[int, float] | None = None) -> float:
    """What the players "feel" worth to a manager: by `feel` round (feel_rounds)
    when given, else by draft round."""
    return sum(PICK_DECAY ** ((feel or {}).get(p.id, draft_round(p, rounds)) - 1) for p in players)


def evaluate(mine: list[RosterPlayer], theirs: list[RosterPlayer], partner: str, give: list[RosterPlayer],
             get: list[RosterPlayer], pool: list[RosterPlayer], ctx, schedule, lines, starters,
             rounds: dict[str, int] | None = None) -> Result:
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
    my_traded, their_traded = _after(mine, give, get), _after(theirs, get, give)
    rounds = rounds or {}
    return Result(
        partner=partner, give=give, get=get,
        me=Side("you", (my_after.expected - my_before.expected) / weeks, my_adds, my_usual, my_drops,
                (balance(mine, lines), balance(my_traded, lines)), _new_short(mine, my_traded)),
        them=Side(partner, (their_after.expected - their_before.expected) / weeks, their_adds, their_usual,
                  their_drops, (balance(theirs, lines), balance(their_traded, lines)), _new_short(theirs, their_traded)),
        win_even=matchup.win_prob(_per_week(my_after, weeks), _per_week(my_before, weeks)),
        give_rounds=[draft_round(p, rounds) for p in give],
        get_rounds=[draft_round(p, rounds) for p in get],
    )


def screen(mine: list[RosterPlayer], others: dict[str, list[RosterPlayer]], pool: list[RosterPlayer],
           value: dict[int, float], rounds: dict[str, int], feel: dict[int, float] | None = None
           ) -> list[tuple[float, str, list[RosterPlayer], RosterPlayer]]:
    """(quick gain per game, team, give, get) for trades worth a full look,
    best first: they gain you something on the quick value, the other team
    doesn't lose on how the players feel to a manager (`feel`, else draft
    rounds), and isn't left short of starters.
    `value` has every rostered and free-agent player's long-run points per game."""
    replacement = {g: max((value[p.id] for p in pool if _group(p) == g), default=0.0) for g in ("D", "G")}
    replacement |= {s: max((value[p.id] for p in pool if s in p.positions), default=0.0) for s in FORWARD_SLOTS}
    my_base = _static(mine, value, replacement)
    gives = [[p] for p in active(mine)] + [list(pair) for pair in itertools.combinations(active(mine), 2)]
    found = []
    for team, theirs in others.items():
        per_team = []
        for get in active(theirs):
            for give in gives:
                if perceived(give, rounds, feel) < perceived([get], rounds, feel):
                    continue
                gain = _static(_after(mine, give, [get]), value, replacement) - my_base
                if gain > 0 and not _new_short(theirs, _after(theirs, [get], give)):
                    per_team.append((gain, team, give, get))
        found += sorted(per_team, key=lambda t: -t[0])[:SCREEN_PER_TEAM]
    return sorted(found, key=lambda t: -t[0])


def suggest(mine: list[RosterPlayer], others: dict[str, list[RosterPlayer]], pool: list[RosterPlayer], ctx,
            schedule, lines, starters, rounds: dict[str, int]) -> list[Result]:
    """The best few trades they could plausibly accept, one per team."""
    value = {}
    everyone = [*mine, *itertools.chain.from_iterable(others.values()), *pool]
    feel = feel_rounds(everyone, rounds, ctx)
    for p in everyone:
        v = matchup.season_value(p, ctx, lines)
        value[p.id] = v if p.is_goalie else v * ctx.durability(p.id)
    results = [evaluate(mine, others[team], team, give, [get], pool, ctx, schedule, lines, starters, rounds)
               for _, team, give, get in screen(mine, others, pool, value, rounds, feel)[:SCREEN_KEEP]]
    best: dict[str, Result] = {}
    for r in sorted(results, key=lambda r: -r.me.per_week):
        if r.me.per_week >= MIN_GAIN_PER_WEEK and r.partner not in best:
            best[r.partner] = r
    return list(best.values())[:MAX_SUGGESTIONS]


def _odds(r: Result) -> str:
    return f"typical week's win odds {(r.win_even - 0.5) * 100:+.0f}%"


def _names(players: list[RosterPlayer]) -> str:
    return ", ".join(p.name for p in players)


def _rounds(rounds: list[int]) -> str:
    return "/".join("undrafted" if r >= UNDRAFTED_ROUND else f"R{r}" for r in rounds)


def _adds_text(side: Side, who: str) -> list[str]:
    lines = []
    new = [p for p in side.adds if p not in side.adds_before]
    if new:
        usual = [p for p in side.adds_before if p not in side.adds]
        extra = len(side.adds) - len(side.adds_before)
        lines.append(f"{who} then add {_names(new)}"
                     + (f" instead of {_names(usual)}" if usual else "")
                     + (f" ({extra} more add{'s' if extra > 1 else ''})" if extra > 0 else ""))
    if side.drops:
        lines.append(f"{who} then drop {_names(side.drops)}")
    return lines


def _short_text(side: Side) -> str:
    what = {"F": "forward", "D": "D", "G": "goalie"}
    return ", ".join(f"{side.short.count(g)} {what[g]}" for g in dict.fromkeys(side.short))


def suggestions_text(results: list[Result]) -> str:
    if not results:
        return ("No trade I'd expect them to accept gains you 1+ pts/week. "
                "Try one yourself: /trade Your Player for Their Player")
    lines = [f"{len(results)} trade{'s' if len(results) > 1 else ''} to propose "
             f"(pts/week over the next {HORIZON_DAYS // 7} weeks, you / them):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {_names(r.give)} for {_names(r.get)} ({r.partner}): "
                     f"{r.me.per_week:+.1f} / {r.them.per_week:+.1f}, {_odds(r)}")
        lines.append(f"   Drafted {_rounds(r.give_rounds)} for {_rounds(r.get_rounds)}; "
                     f"they go {r.them.balance[0]} -> {r.them.balance[1]}")
        lines += ["   " + line for line in _adds_text(r.me, "You")]
        if r.them.drops:
            lines.append(f"   They drop {_names(r.them.drops)}")
    lines.append("Details: /trade " + _names(results[0].give) + " for " + _names(results[0].get))
    return "\n".join(lines)


def text(r: Result) -> str:
    if r.me.per_week >= MIN_GAIN_PER_WEEK:
        verdict = "Worth proposing"
    elif r.me.per_week > -MIN_GAIN_PER_WEEK:
        verdict = "About even: not worth the hassle"
    else:
        verdict = "Don't: it makes you worse"
    lines = [
        f"{verdict}. {_names(r.give)} for {_names(r.get)} ({r.partner})",
        f"You: {r.me.per_week:+.1f} pts/week, {_odds(r)} (vs a team as good as yours now; "
        "not this week's matchup)",
        f"Them: {r.them.per_week:+.1f} pts/week",
    ]
    lines.append(f"Rosters: you {r.me.balance[0]} -> {r.me.balance[1]}, "
                 f"them {r.them.balance[0]} -> {r.them.balance[1]}")
    if r.them.short:
        lines.append(f"It leaves them short of starters ({_short_text(r.them)}): expect a no.")
    if min(r.give_rounds + r.get_rounds, default=UNDRAFTED_ROUND) < UNDRAFTED_ROUND:
        lines.append(f"Drafted: you give {_rounds(r.give_rounds)}, get {_rounds(r.get_rounds)}")
    for side, who in ((r.me, "You"), (r.them, "They")):
        lines += _adds_text(side, who)
    lines.append(f"Per week over the {HORIZON_DAYS // 7} weeks after the 2-day review; open spots filled from "
                 "free agents either way.")
    return "\n".join(lines)
