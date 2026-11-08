"""A skater's share of his team's power-play time, game by game, this season.

The team's power-play time in a game is its skaters' power-play time summed
and divided by 5 (five skaters on the ice on a power play; 4-on-3 and an
empty net with a man advantage are the rare exceptions). Also his rank among
his team's skaters by power-play time that game (1-5 is roughly PP1).

    python -m scripts.pp_share "Porter Martone" [--season 20262027]
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from bot.common import nhl_today
from clients import nhl_stats
from clients.names import normalize_name
from model.context import current_season_id

ON_ICE = 5


def shares(games: list[nhl_stats.SkaterGame], name: str) -> list[dict]:
    """One row per game the named skater played: his PP time, his team's, the
    share, and his rank among the team's skaters by PP time."""
    want = normalize_name(name)
    team_games: dict[tuple[int, str], list[nhl_stats.SkaterGame]] = defaultdict(list)
    for g in games:
        team_games[(g.game_id, g.team)].append(g)
    rows = []
    for g in games:
        if normalize_name(g.name) != want:
            continue
        mates = team_games[(g.game_id, g.team)]
        team_pp = sum(m.pp_toi for m in mates) / ON_ICE
        rank = 1 + sum(m.pp_toi > g.pp_toi for m in mates)
        rows.append({"date": g.date, "opp": g.opponent, "toi": g.toi, "pp": g.pp_toi, "team_pp": team_pp,
                     "share": g.pp_toi / team_pp if team_pp else None, "rank": rank if g.pp_toi else None})
    return rows


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="+")
    parser.add_argument("--season", type=int)
    args = parser.parse_args()
    today = nhl_today()
    games = nhl_stats.skater_games(args.season or current_season_id(today), today=today)
    for name in args.names:
        rows = shares(games, name)
        if not rows:
            print(f"{name}: no games found\n")
            continue
        print(f"{name}: share of his team's power-play time, by game")
        print(f"{'date':10}  {'opp':4} {'TOI':>6} {'PP':>5} {'team PP':>8} {'share':>6} {'PP rank':>8}")
        for r in rows:
            share = "" if r["share"] is None else f"{100 * r['share']:.0f}%"
            rank = "" if r["rank"] is None else str(r["rank"])
            print(f"{r['date']}  {r['opp']:4} {_mmss(r['toi']):>6} {_mmss(r['pp']):>5} {_mmss(r['team_pp']):>8} "
                  f"{share:>6} {rank:>8}")
        pp, team = sum(r["pp"] for r in rows), sum(r["team_pp"] for r in rows)
        print(f"Season: {_mmss(pp / len(rows))} PP a game of the team's {_mmss(team / len(rows))}, "
              f"{100 * pp / team:.0f}% of it" if team else "Season: no power-play time", "\n")


if __name__ == "__main__":
    main()
