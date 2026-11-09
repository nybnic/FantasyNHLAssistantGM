"""A skater's share of his team's power-play time, game by game, this season.

The team's PP time per game is estimated from the NHL stats game rows
(skater/timeonice): the sum of every teammate's PP time / 5 (exact for 5-on-4
and 5-on-3; a 4-on-3 in overtime reads low, a pulled goalie on the PP high).
The longest single teammate's PP time is printed too, a floor on the team's.
The NHL's own share (skater/powerplay, ppTimeOnIcePctPerGame) is shown when
the report has it.

    python -m scripts.pp_share --player "Timo Meier" [--season 20262027]
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from clients import nhl_stats
from clients.names import normalize_name

SKATERS_ON_PP = 5


def _nhl_pct(season: int, player_id: int, today: dt.date) -> dict[int, float]:
    """gameId -> the NHL's PP TOI % for the player, if the report carries it."""
    out = {}
    try:
        for w_start, w_end in nhl_stats._span(season, None, None, today):
            for row in nhl_stats._report_rows("skater/powerplay", season, w_start, w_end, today):
                if row.get("playerId") == player_id and row.get("ppTimeOnIcePctPerGame") is not None:
                    out[row["gameId"]] = float(row["ppTimeOnIcePctPerGame"])
    except Exception as exc:  # the estimate stands on its own
        print(f"(skater/powerplay not read: {type(exc).__name__}: {exc})")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", required=True)
    parser.add_argument("--season", type=int, default=20262027)
    args = parser.parse_args()
    today = dt.date.today()
    games = nhl_stats.skater_games(args.season, today=today)
    want = normalize_name(args.player)
    mine = [g for g in games if normalize_name(g.name) == want]
    if not mine:
        print(f"No games found for {args.player} in {args.season}")
        return
    team_pp: dict[tuple[int, str], list[float]] = defaultdict(list)
    for g in games:
        team_pp[(g.game_id, g.team)].append(g.pp_toi)
    nhl_pct = _nhl_pct(args.season, mine[0].player_id, today)

    print(f"{mine[0].name} ({mine[0].team}), {args.season}: PP time vs his team's, per game (min:sec)")
    print(f"{'date':<10} {'opp':<4} {'his PP':>7} {'team PP':>8} {'share':>6} {'max mate':>9} {'NHL %':>6}")
    tot_his = tot_team = 0.0
    shares = []
    for g in mine:
        team = sum(team_pp[(g.game_id, g.team)]) / SKATERS_ON_PP
        top = max(team_pp[(g.game_id, g.team)])
        share = g.pp_toi / team if team else None
        if team:
            shares.append(share)
        tot_his += g.pp_toi
        tot_team += team
        pct = nhl_pct.get(g.game_id)
        print(f"{g.date.isoformat():<10} {g.opponent:<4} {_mmss(g.pp_toi):>7} {_mmss(team):>8} "
              f"{(f'{share:.0%}' if share is not None else '-'):>6} {_mmss(top):>9} "
              f"{(f'{pct:.0%}' if pct is not None and pct <= 1 else f'{pct:.0f}%' if pct is not None else '-'):>6}")
    n = len(mine)
    print(f"\n{n} games: his PP {_mmss(tot_his / n)}/game, team PP {_mmss(tot_team / n)}/game, "
          f"share of the season's PP time {tot_his / tot_team:.0%}" if tot_team else f"\n{n} games")
    if shares:
        print(f"Mean of per-game shares {sum(shares) / len(shares):.0%} ({len(shares)} games with PP time)")
    for last in (5, 10):
        recent = mine[-last:]
        t = sum(sum(team_pp[(g.game_id, g.team)]) / SKATERS_ON_PP for g in recent)
        if t and len(mine) > last:
            print(f"Last {last}: {sum(g.pp_toi for g in recent) / t:.0%}")


def _mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


if __name__ == "__main__":
    main()
