"""scripts.depth as JSON (teams, my players, swaps), for the League Depth Map
dashboard. Same computation as `python -m scripts.depth`; read-only.

    python -m scripts.depth_json out.json            # the real NHL schedule (network)
    python -m scripts.depth_json out.json --offline  # random nights
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics

from config.league import MY_TEAM
from scripts.depth import (FORWARD_SLOTS, SLOTS, Nights, analyse, free_agents, latest_rows, load, median_starters,
                           schedule, slot_of, swaps, total, try_moves)


def build(offline: bool, tries: list[str] = (), detail: str = "") -> dict:
    teams, waiver, missing = load()
    if offline:
        nights = Nights.random({p.nhl for ps in teams.values() for p in ps})
    else:
        from bot.common import NHL_TIME
        nights = Nights.from_schedule(schedule(None, dt.datetime.now(NHL_TIME).date()))
    starter = median_starters(teams)
    depths = {t: analyse(t, ps, nights, waiver, starter) for t, ps in teams.items()}
    avg = {s: statistics.mean(d.week[s] for d in depths.values()) for s in SLOTS}
    f_of = {t: sum(depths[t].week[s] for s in FORWARD_SLOTS) for t in depths}
    f_avg = statistics.mean(f_of.values())
    r = lambda v, n=2: round(float(v), n)  # noqa: E731

    team_rows = []
    for t, d in depths.items():
        row = {"team": t, "total": r(total(d.week), 1), "F": r(f_of[t] - f_avg)}
        row.update({s: r(d.week[s] - avg[s]) for s in SLOTS})
        row.update({f"need_{s}": r(d.need[s]) for s in SLOTS})
        row["cut"] = d.cut.name if d.cut else ""
        row.update({f"empty_{s}": r(d.empty[s]) for s in SLOTS})
        for g in ("F", "D", "G"):
            row[f"benched_{g}"] = r(sum(d.games[p.id][1] for p in d.active if p.group == g))
        row["pts_benched"] = r(sum(d.games[p.id][1] * p.value for p in d.active))
        spare = [s for s in d.spares if s.chip > 0][:2]
        row["spare"] = "; ".join(f"{s.player.name} {slot_of(s.player)}" for s in spare)
        row["beyond_14"] = ", ".join(p.name for p in d.extra)
        team_rows.append(row)

    mine = depths[MY_TEAM]
    players = []
    for p in sorted(mine.active, key=lambda p: (["F", "D", "G"].index(p.group), -p.value)):
        g, b = mine.games[p.id]
        players.append({"player": p.name, "pos": slot_of(p), "pts_game": r(p.value), "games": r(g),
                        "benched": r(b), "pts_benched": r(b * p.value)})

    swap_rows = []
    for t in teams:
        if t == MY_TEAM:
            continue
        for s in swaps(mine, depths[t], nights)[:3]:
            swap_rows.append({"swap": f"{s.give.name} for {s.get.name}", "partner": t,
                              "give": s.give.name, "give_pos": slot_of(s.give),
                              "get": s.get.name, "get_pos": slot_of(s.get),
                              "mine": r(s.mine), "theirs": r(s.theirs)})
    tried = []
    if tries:
        pool = free_agents(latest_rows(), {p.id for ps in teams.values() for p in ps}, waiver)
        for text in tries:
            t = try_moves(text, MY_TEAM, depths, pool, nights)
            for team, (before, after) in t.sides.items():
                week = nights.week(after)
                empty, games = nights.usage(after)
                tried.append({"scenario": text, "team": team, "delta": r(total(week) - total(before.week)),
                              **{s: r(week[s] - before.week[s]) for s in SLOTS},
                              "empty_D_before": r(before.empty["D"]), "empty_D_after": r(empty["D"]),
                              "empty_RW_before": r(before.empty["RW"]), "empty_RW_after": r(empty["RW"]),
                              "benched_pts_after": r(sum(games[p.id][1] * p.value for p in after))})
    roster = []
    if detail:
        d = depths[detail]
        for p in d.active + d.extra:
            g, b = d.games.get(p.id, (0.0, 0.0)) if p in d.active else (0.0, 0.0)
            roster.append({"player": p.name, "pos": slot_of(p), "pts_game": r(p.value), "games": r(g),
                           "benched": r(b), "active": p in d.active})
    return {"tried": tried, "detail_team": detail, "detail_roster": roster, "label": nights.label, "waiver": {k: r(v) for k, v in waiver.items()},
            "missing": missing, "teams": team_rows, "players": players, "swaps": swap_rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--try", dest="tries", default="")
    parser.add_argument("--detail", default="")
    args = parser.parse_args()
    data = build(args.offline, [t.strip() for t in args.tries.split("|") if t.strip()], args.detail)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{len(data['teams'])} teams, {len(data['players'])} players, {len(data['swaps'])} swaps; {data['label']}")


if __name__ == "__main__":
    main()
