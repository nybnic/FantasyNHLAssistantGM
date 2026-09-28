"""Current expected fantasy points per game for every rostered NHL skater.

Priors: the last three seasons blended with DailyFaceoff/5v5hockey's
preseason projections; then this season's games so far. Writes data/xfp.csv
and prints the top of the table.

    python -m scripts.xfp_table
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from clients import dfo_projections, nhl_client
from model import context
from model.projections import SKATER_STATS

OUT = Path("data/xfp.csv")


def main() -> None:
    ctx = context.build(dt.date.today())
    rows = []
    for p in nhl_client.current_rosters():
        if p["position"] == "G":
            continue
        proj = ctx.skater(p["id"], p["position"])
        rows.append({
            "id": p["id"],
            "name": p["name"],
            "team": p["team"],
            "position": p["position"],
            "games": proj.games,
            "toi_min": round(proj.toi / 60, 1),
            "pp_toi_min": round(proj.pp_toi / 60, 1),
            "xfp": round(proj.xfp, 2),
            **{s: round(proj.per_game[s], 3) for s in SKATER_STATS},
        })
    rows.sort(key=lambda r: r["xfp"], reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Season {ctx.season}: {len(rows)} skaters, {sum(r['games'] for r in rows)} games this season, "
          f"DFO projections updated {dfo_projections.fetch()['updated']} -> {OUT}")
    for group, n in (("F", 20), ("D", 10)):
        print(f"\nTop {n} {'forwards' if group == 'F' else 'defense'} (xFP/game):")
        top = [r for r in rows if (r["position"] == "D") == (group == "D")][:n]
        for r in top:
            print(f"  {r['xfp']:5.2f}  {r['name']:<24} {r['team']}  {r['toi_min']:4.1f} min")


if __name__ == "__main__":
    main()
