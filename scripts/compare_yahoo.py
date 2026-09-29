"""Compare Yahoo's projections with ours, player by player and stat by stat.

Copy a skater list from Yahoo (Players, Stats: projected season, any
filter/sort) as text into a file, then:

    python -m scripts.compare_yahoo yahoo_paste.txt

Prints both per-game values, the stats behind each gap, rank agreement, and
Yahoo's games played next to DailyFaceoff's. Skaters only. Each row needs
Yahoo's columns GP*, Fan Pts, Pre-Season, Current, % Ros, then G A +/- PIM
PPG PPA SHG SHA GWG SOG FW HIT BLK - the default projected-stats view.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from clients import dfo_projections, nhl_client
from config.league import SKATER_WEIGHTS, fantasy_points
from league import parse
from model import context

STATS = ("g", "a", "pm", "pim", "ppg", "ppa", "shg", "sha", "gwg", "sog", "fow", "hit", "blk")
MIN_GP = 10  # per-game numbers over fewer projected games are noise
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?%?")


def parse_row(line: str) -> dict | None:
    """GP, Yahoo's season points and per-game stats from one pasted row, or
    None if it isn't a player row. Anchored on the "% Ros" column: the four
    numbers before it are GP*, Fan Pts, Pre-Season, Current; the 13 after it
    are the stats. (Earlier columns hold stray numbers: game times, waiver dates.)"""
    tokens = _NUMBER.findall(line)
    pct = next((i for i, t in enumerate(tokens) if t.endswith("%")), None)
    if pct is None or pct < 4 or len(tokens) < pct + 1 + len(STATS):
        return None
    gp, fpts = float(tokens[pct - 4]), float(tokens[pct - 3])
    stats = [float(t) for t in tokens[pct + 1:pct + 1 + len(STATS)]]
    if gp < MIN_GP:
        return None
    return {"gp": gp, "fpts": fpts, "per_game": {s: v / gp for s, v in zip(STATS, stats)}}


def _ranks(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0] * len(values)
    for rank, i in enumerate(order):
        ranks[i] = rank
    return ranks


def main(path: str) -> None:
    registry = parse.registry()
    ctx = context.build(dt.datetime.now(dt.timezone.utc).date())
    dfo = dfo_projections.match_to_nhl_ids(dfo_projections.fetch()["skaters"], nhl_client.current_rosters())
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        row = parse_row(line)
        found = parse.find_players(line, registry).players if row else []
        if len(found) != 1 or found[0].is_goalie:
            continue
        p = found[0]
        ours = ctx.skater(p.id, "D" if p.positions == ["D"] else "C").per_game
        yahoo = row["per_game"]
        gaps = {s: (ours[s] - yahoo[s]) * SKATER_WEIGHTS[s] for s in STATS}
        rows.append((p, row["gp"], fantasy_points(yahoo, SKATER_WEIGHTS), fantasy_points(ours, SKATER_WEIGHTS),
                     gaps, dfo.get(p.id, {}).get("gp")))
    if not rows:
        raise SystemExit("No player rows found - see the docstring for the expected columns.")

    print(f"{'player':24} {'Yahoo/g':>7} {'ours/g':>7} {'gap':>6}  biggest stat gaps (ours - Yahoo, pts/game)")
    for p, _, y, o, gaps, _ in rows:
        top = sorted(gaps.items(), key=lambda kv: -abs(kv[1]))[:3]
        print(f"{p.name[:24]:24} {y:7.2f} {o:7.2f} {o - y:+6.2f}  " + ", ".join(f"{s} {v:+.2f}" for s, v in top))
    n = len(rows)
    ry, ro = _ranks([r[2] for r in rows]), _ranks([r[3] for r in rows])
    rho = 1 - 6 * sum((a - b) ** 2 for a, b in zip(ry, ro)) / (n * (n * n - 1)) if n > 2 else float("nan")
    print(f"\n{n} players. Rank agreement per game (Spearman): {rho:.2f}. "
          f"Mean gap {sum(r[3] - r[2] for r in rows) / n:+.2f} pts/game.")
    print("Mean gap per stat: " + ", ".join(
        f"{s} {sum(r[4][s] for r in rows) / n:+.2f}" for s in STATS if abs(sum(r[4][s] for r in rows) / n) >= 0.02))
    print("\nGames played, biggest disagreements (Yahoo vs DailyFaceoff):")
    for p, gp, *_, dfo_gp in sorted((r for r in rows if r[5]), key=lambda r: -abs(r[1] - r[5]))[:8]:
        print(f"  {p.name[:24]:24} Yahoo {gp:5.1f}  DFO {dfo_gp:5.1f}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
