"""Every team's depth, position by position: where it is strong, where it is
shallow, and who it could spare. For trade hunting (Nico, 2026-10-20): a team
with excess D and thin forwards is the partner for one with the opposite.

Offline, from what the bot already holds: the rosters (state/league.json,
state/roster.json) and the latest weekly projection snapshot
(state/xfp_log.csv: a skater's points per game, with durability as the weekly
plan counts it, `matchup.durability`; a goalie's start share x points per
start). Nights are simulated (each NHL team plays on NIGHT_PLAY of them, on
the same random nights for every team) and each night's best lineup is solved
(C2 LW2 RW2 D4 G2, Yahoo eligibility), so a bench player counts on the nights
he fills an empty slot, and a sixth D counts little.

Per team and slot, in points a week:
- strength: the points the slot's starters score, against the league average;
- need: what a solid starter at that slot (the league's median starter there,
  eligible only there) would add in place of the team's least useful player
  (two goalies kept). High = shallow there; near zero or below = deep;
- spare: the player the team loses least by trading, against what he'd give a
  team that starts him every game (his edge over waiver level): "chip" vs "keep".
Then for my team, the 1-for-1 swaps where both sides gain, by team.

A static long-run view: no real schedule, no goalie minimum, no injuries
beyond durability, rosters as last known. Judge a real offer with /trade (the
full engine). Read-only.

    python -m scripts.depth
    python -m scripts.depth --team "HC Bulju"   # their swaps instead of mine
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from config.league import BENCH_SLOTS, MY_TEAM, STARTERS

LOG_FILE = Path("state/xfp_log.csv")
LEAGUE_FILE = Path("state/league.json")
ROSTER_FILE = Path("state/roster.json")
SLOTS = tuple(STARTERS)  # C LW RW D G
FORWARD_SLOTS = ("C", "LW", "RW")
NIGHT_PLAY = 82 / 190  # 82 games over ~190 nights of the NHL regular season
NIGHTS = 3000
WAIVER_TOP = 3  # waiver level: the best 3 free agents by group, as ctx.replacement_xfp
ACTIVE_SPOTS = sum(STARTERS.values()) + BENCH_SLOTS  # 14; IR and IR+ hold the rest
MIN_GOALIES = 2  # below it a week's goalie points are likely zeroed (the 3-game minimum)
OFF_SCHEDULE = "_any"  # a stand-in player's own NHL team: random nights of its own


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    nhl: str
    positions: tuple[str, ...]
    value: float  # expected fantasy points per team game

    @property
    def group(self) -> str:
        return "G" if "G" in self.positions else "D" if self.positions == ("D",) else "F"


def _group_of(pos: str) -> str:
    return "G" if pos == "G" else "D" if pos == "D" else "F"


def latest_rows(path: Path = LOG_FILE) -> dict[int, dict]:
    """Player id -> his row in the latest week of the snapshot that has him."""
    rows: dict[int, dict] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = int(r["id"])
            if pid not in rows or int(r["week"]) >= int(rows[pid]["week"]):
                rows[pid] = r
    return rows


def raw_value(row: dict) -> float:
    if row["pos"] == "G":
        return float(row["start_share"] or 0) * float(row["xfp_start"] or 0)
    return float(row["xfp"] or 0)


def waiver_level(rows: dict[int, dict], rostered: set[int]) -> dict[str, float]:
    """Group -> the average of the best WAIVER_TOP free agents' points per team game."""
    by_group: dict[str, list[float]] = {"F": [], "D": [], "G": []}
    for pid, r in rows.items():
        if pid not in rostered:
            by_group[_group_of(r["pos"])].append(raw_value(r))
    return {g: statistics.mean(sorted(v, reverse=True)[:WAIVER_TOP]) if v else 0.0 for g, v in by_group.items()}


def value(row: dict, waiver: dict[str, float]) -> float:
    """Points per team game; a skater's projected missed games cost only his
    edge over waiver level (matchup.durability)."""
    x = raw_value(row)
    if row["pos"] == "G" or not row["durability"]:
        return x
    dur, repl = float(row["durability"]), waiver[_group_of(row["pos"])]
    return x if x <= repl else x - (1 - dur) * (x - repl)


def load(log: Path = LOG_FILE, league: Path = LEAGUE_FILE, mine: Path = ROSTER_FILE
         ) -> tuple[dict[str, list[Player]], dict[str, float], list[str]]:
    """(team -> players, waiver level by group, players missing from the snapshot)."""
    rosters = {t: e["players"] for t, e in json.loads(league.read_text(encoding="utf-8"))["teams"].items()}
    rosters[MY_TEAM] = json.loads(mine.read_text(encoding="utf-8"))["players"]
    rows = latest_rows(log)
    waiver = waiver_level(rows, {p["id"] for ps in rosters.values() for p in ps})
    missing = []
    teams = {}
    for team, players in rosters.items():
        teams[team] = []
        for p in players:
            if p["id"] not in rows:
                missing.append(f"{p['name']} ({team})")
            v = value(rows[p["id"]], waiver) if p["id"] in rows else 0.0
            teams[team].append(Player(p["id"], p["name"], p["team"], tuple(p["positions"]), v))
    return teams, waiver, missing


@lru_cache(maxsize=None)
def assign(playing: tuple[tuple[float, tuple[str, ...]], ...]) -> tuple[str | None, ...]:
    """The best lineup of one night: each player's slot (None = bench), exact."""
    forwards = [i for i, (_, pos) in enumerate(playing) if not {"D", "G"} & set(pos)]

    @lru_cache(maxsize=None)
    def best(k: int, left: tuple[int, int, int]) -> tuple[float, tuple[str | None, ...]]:
        if k == len(forwards):
            return 0.0, ()
        v, pos = playing[forwards[k]]
        score, rest = best(k + 1, left)
        out = (score, (None, *rest))
        for j, slot in enumerate(FORWARD_SLOTS):
            if left[j] and slot in pos:
                score, rest = best(k + 1, left[:j] + (left[j] - 1,) + left[j + 1:])
                if score + v > out[0]:
                    out = (score + v, (slot, *rest))
        return out

    slots: list[str | None] = [None] * len(playing)
    for i, slot in zip(forwards, best(0, tuple(STARTERS[s] for s in FORWARD_SLOTS))[1]):
        slots[i] = slot
    for slot in ("D", "G"):
        group = sorted((i for i, (_, pos) in enumerate(playing) if slot in pos), key=lambda i: -playing[i][0])
        for i in group[:STARTERS[slot]]:
            slots[i] = slot
    return tuple(slots)


class Nights:
    """The same random nights for every team: who plays on each."""

    def __init__(self, nhl_teams: set[str], n: int = NIGHTS, seed: int = 2026):
        rng = np.random.default_rng(seed)
        names = sorted(nhl_teams | {OFF_SCHEDULE})
        self.index = {t: i for i, t in enumerate(names)}
        self.plays = rng.random((n, len(names))) < NIGHT_PLAY

    def week(self, players: list[Player]) -> dict[str, float]:
        """Expected points a week by slot (C LW RW D G) from each night's best lineup.
        D and G take their best on the night; forwards are solved once per set
        of forwards playing (a few hundred at most)."""
        col = [self.index.get(p.nhl, self.index[OFF_SCHEDULE]) for p in players]
        n = len(self.plays)
        out = dict.fromkeys(SLOTS, 0.0)
        for slot in ("D", "G"):
            group = [i for i, p in enumerate(players) if slot in p.positions]
            if group:
                v = np.where(self.plays[:, [col[i] for i in group]], [players[i].value for i in group], 0.0)
                out[slot] = np.sort(v, axis=1)[:, ::-1][:, :STARTERS[slot]].sum() / n * 7
        forwards = [p for p in players if p.group == "F"]
        if forwards:
            patterns, counts = np.unique(self.plays[:, [self.index.get(p.nhl, self.index[OFF_SCHEDULE])
                                                         for p in forwards]], axis=0, return_counts=True)
            for pattern, count in zip(patterns, counts):
                playing = tuple(sorted((p.value, p.positions) for p, on in zip(forwards, pattern) if on))
                for (v, _), slot in zip(playing, assign(playing)):
                    if slot:
                        out[slot] += v * count / n * 7
        return out


def total(by_slot: dict[str, float]) -> float:
    return sum(by_slot.values())


def stand_in(name: str, positions: tuple[str, ...], v: float) -> Player:
    return Player(-1, name, OFF_SCHEDULE, positions, v)


def median_starters(teams: dict[str, list[Player]]) -> dict[str, float]:
    """Slot -> the league's median starter there, per team game (everyone playing)."""
    by_slot: dict[str, list[float]] = {s: [] for s in SLOTS}
    for players in teams.values():
        playing = tuple(sorted((p.value, p.positions) for p in players))
        for (v, _), slot in zip(playing, assign(playing)):
            if slot:
                by_slot[slot].append(v)
    return {s: statistics.median(v) for s, v in by_slot.items()}


@dataclass
class Spare:
    player: Player
    chip: float  # his edge over waiver level, a week, as an every-game starter
    keep: float  # what his team loses a week without him (a waiver-level player in his spot)


@dataclass
class Depth:
    team: str
    active: list[Player]  # the best 14
    extra: list[Player]  # the rest (on IR, or a roster that's out of date): not in the lineup here
    week: dict[str, float]  # points a week by slot
    need: dict[str, float]  # slot -> gain a week from a median starter there
    spares: list[Spare]  # every player, the cheapest to trade first
    cut: Player | None = None  # who a new starter would replace: the least useful (goalies kept at 2)


def _goalies(players: list[Player]) -> int:
    return sum(p.group == "G" for p in players)


def _without(players: list[Player], p: Player) -> list[Player]:
    return [q for q in players if q.id != p.id]


def cap(players: list[Player], nights: Nights) -> tuple[list[Player], list[Player]]:
    """(the best ACTIVE_SPOTS, the rest): the least useful go first, two goalies kept."""
    active, extra = list(players), []
    while len(active) > ACTIVE_SPOTS:
        can_go = [p for p in active if p.group != "G" or _goalies(active) > MIN_GOALIES]
        out = max(can_go, key=lambda p: total(nights.week(_without(active, p))))
        active, extra = _without(active, out), extra + [out]
    return active, extra


def analyse(team: str, players: list[Player], nights: Nights, waiver: dict[str, float],
            starter: dict[str, float]) -> Depth:
    active, extra = cap(players, nights)
    base = nights.week(active)
    spares = [Spare(p, max(p.value - waiver[p.group], 0.0) * NIGHT_PLAY * 7, 0.0) for p in extra]
    for p in active:
        filler = stand_in("waiver", p.positions, waiver[p.group])
        keep = total(base) - total(nights.week(_without(active, p) + [filler]))
        spares.append(Spare(p, max(p.value - waiver[p.group], 0.0) * NIGHT_PLAY * 7, keep))
    spares.sort(key=lambda s: -(s.chip - s.keep))
    cut = min((s for s in spares if s.player in active and (s.player.group != "G" or _goalies(active) > MIN_GOALIES)),
              key=lambda s: s.keep).player
    rest = _without(active, cut)
    need = {s: total(nights.week(rest + [stand_in("starter", (s,), starter[s])])) - total(base) for s in SLOTS}
    return Depth(team, active, extra, base, need, spares, cut)


def slot_of(p: Player) -> str:
    return "G" if p.group == "G" else "D" if p.group == "D" else "/".join(p.positions)


@dataclass
class Swap:
    give: Player
    get: Player
    mine: float  # points a week, after minus before
    theirs: float


def _after(side: Depth, out: Player, incoming: Player, nights: Nights) -> list[Player] | None:
    """The side's best 14 after the swap, or None if it falls under two goalies."""
    roster = _without(side.active + side.extra, out) + [incoming]
    if _goalies(roster) < MIN_GOALIES:
        return None
    return _without(side.active, out) + [incoming] if out in side.active else cap(roster, nights)[0]


def swaps(me: Depth, them: Depth, nights: Nights) -> list[Swap]:
    """Every 1-for-1 where both sides gain and both keep two goalies (the
    goalie minimum), best for the side that gains less first."""
    out = []
    for give in me.active + me.extra:
        for get in them.active + them.extra:
            mine_after = _after(me, give, get, nights)
            theirs_after = _after(them, get, give, nights)
            if mine_after is None or theirs_after is None:
                continue
            mine = total(nights.week(mine_after)) - total(me.week)
            if mine <= 0:
                continue
            theirs = total(nights.week(theirs_after)) - total(them.week)
            if theirs > 0:
                out.append(Swap(give, get, mine, theirs))
    return sorted(out, key=lambda s: -min(s.mine, s.theirs))


def _rank(values: dict[str, float], team: str) -> int:
    return sorted(values, key=lambda t: -values[t]).index(team) + 1


def report(depths: dict[str, Depth], me: str, best_swaps: dict[str, list[Swap]], waiver: dict[str, float],
           missing: list[str]) -> str:
    teams = sorted(depths, key=lambda t: -total(depths[t].week))
    avg = {s: statistics.mean(d.week[s] for d in depths.values()) for s in SLOTS}
    f_of = {t: sum(depths[t].week[s] for s in FORWARD_SLOTS) for t in depths}
    f_avg = statistics.mean(f_of.values())
    out = ["## Strength by slot (points a week vs the league average; rank of 16)", "",
           "| Team | Total | F | C | LW | RW | D | G |", "|---|---|---|---|---|---|---|---|"]
    for t in teams:
        d = depths[t]
        cells = [f"{f_of[t] - f_avg:+.1f} ({_rank(f_of, t)})"]
        cells += [f"{d.week[s] - avg[s]:+.1f} ({_rank({u: depths[u].week[s] for u in depths}, t)})" for s in SLOTS]
        name = f"**{t}**" if t == me else t
        out.append(f"| {name} | {total(d.week):.0f} | " + " | ".join(cells) + " |")
    out += ["", "## Needs and spares (points a week)",
            "Need: what a median starter at the slot would add in place of the team's least useful player "
            "(Cut). Spare: the player cheapest to trade "
            "(keep = his team's loss without him, chip = his edge over waiver level as an every-game starter).", "",
            "| Team | Needs most | Needs least | Cut | Most spare (keep / chip) | Beyond 14 |",
            "|---|---|---|---|---|---|"]
    for t in teams:
        d = depths[t]
        order = sorted(SLOTS, key=lambda s: -d.need[s])
        spare = [s for s in d.spares if s.chip > 0][:2]
        name = f"**{t}**" if t == me else t
        out.append(f"| {name} | " + ", ".join(f"{s} {d.need[s]:.1f}" for s in order[:2])
                   + " | " + ", ".join(f"{s} {d.need[s]:.1f}" for s in order[-2:])
                   + f" | {d.cut.name if d.cut else '-'}"
                   + " | " + "; ".join(f"{s.player.name} {slot_of(s.player)} ({s.keep:.1f} / {s.chip:.1f})"
                                       for s in spare)
                   + " | " + (", ".join(p.name for p in d.extra) or "-") + " |")
    out += ["", f"## Swaps for {me} where both sides gain (points a week, you / them)", ""]
    for t, found in sorted(best_swaps.items(), key=lambda kv: -min(kv[1][0].mine, kv[1][0].theirs) if kv[1] else 0):
        if found:
            out.append(f"- **{t}**: " + "; ".join(f"{s.give.name} ({slot_of(s.give)}) for {s.get.name} "
                                                  f"({slot_of(s.get)}) {s.mine:+.1f} / {s.theirs:+.1f}"
                                                  for s in found[:3]))
    quiet = [t for t, found in best_swaps.items() if not found]
    if quiet:
        out.append("- No 1-for-1 where both gain: " + ", ".join(quiet))
    out += ["", f"Waiver level a team game: F {waiver['F']:.2f}, D {waiver['D']:.2f}, G {waiver['G']:.2f}."]
    if missing:
        out.append("Not in the projection snapshot (counted as 0): " + ", ".join(missing) + ".")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--team", default=MY_TEAM, help="whose swaps to list (default: mine)")
    args = parser.parse_args()
    teams, waiver, missing = load()
    nights = Nights({p.nhl for ps in teams.values() for p in ps})
    starter = median_starters(teams)
    depths = {t: analyse(t, ps, nights, waiver, starter) for t, ps in teams.items()}
    found = {t: swaps(depths[args.team], depths[t], nights)
             for t in teams if t != args.team}
    print(report(depths, args.team, found, waiver, missing))
    print("Median starter a team game: " + ", ".join(f"{s} {v:.2f}" for s, v in starter.items()) + ".")


if __name__ == "__main__":
    main()
