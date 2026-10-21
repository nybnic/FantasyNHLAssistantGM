"""Do our weekly forecasts of every team come true, and does streaming explain the misses?

Each week's first plan logs our projection of all 16 teams (league_weeks[w].ours,
rosters as known that Monday, frozen for the week), and each Standings screenshot
the teams' points for (league_weeks[w].points_for, cumulative), so every team's
week is a check: 16 a week. Per week: the league's mean miss (actual - ours; a
level bias cancels in a matchup) and the misses' spread against our sd. Then the
question the plan's frozen opponents raise (roadmap, opponent streaming): do teams
that add players that week beat our forecast by more? The miss is regressed on the
team's adds that week (league_adds; mine from the adds ledger), each weighted by
the share of the week left when he was added, with the week's mean taken out; and
whether a team's miss one week predicts its miss in the others (a team effect).

    python -m scripts.check_forecasts

Offline: reads the committed state/ files (`git pull` first).
"""
from __future__ import annotations

import datetime as dt
import math
import statistics as st

from config.league import MY_TEAM
from league import weeks
from state import gm_state


def team_weeks(state: dict) -> list[dict]:
    """Every team-week with our forecast and its points: {week, team, actual,
    ours, sd, miss, adds, add_weeks (each add x the share of the week left)}."""
    lw = state["league_weeks"]
    adds = {team: list(days) for team, days in state["league_adds"].items()}
    adds[MY_TEAM] = [a["date"] for a in state["adds"]]
    rows = []
    for key in sorted(lw, key=int):
        w = int(key)
        ours, pf, before = lw[key].get("ours"), lw[key].get("points_for"), lw.get(str(w - 1), {}).get("points_for")
        if not ours or not pf or (w > 1 and not before):
            continue
        days = weeks.days(w)
        first, last = days[0].isoformat(), days[-1].isoformat()
        for team, f in ours["teams"].items():
            if team not in pf or (w > 1 and team not in before):
                continue
            actual = pf[team] - (before[team] if w > 1 else 0.0)
            made = [d for d in adds.get(team, []) if first <= d <= last]
            rows.append({"week": w, "team": team, "actual": actual, "ours": f["expected"], "sd": f["sd"],
                         "miss": actual - f["expected"], "adds": len(made),
                         "add_weeks": sum(((days[-1] - dt.date.fromisoformat(d)).days + 1) / len(days)
                                          for d in made)})
    return rows


def _within_week(rows: list[dict], key: str) -> list[float]:
    """`key` less its week's mean: week-to-week swings (scoring, the level bias) out."""
    means = {w: st.mean(r[key] for r in rows if r["week"] == w) for w in {r["week"] for r in rows}}
    return [r[key] - means[r["week"]] for r in rows]


def slope(rows: list[dict], key: str = "add_weeks") -> tuple[float, float] | None:
    """The miss per unit of `key`, within weeks, and its standard error."""
    x, y = _within_week(rows, key), _within_week(rows, "miss")
    sxx = sum(v * v for v in x)
    dof = len(rows) - len({r["week"] for r in rows}) - 1
    if not sxx or dof < 1:
        return None
    b = sum(a * c for a, c in zip(x, y)) / sxx
    resid = sum((c - b * a) ** 2 for a, c in zip(x, y))
    return b, math.sqrt(resid / dof / sxx)


def persistence(rows: list[dict]) -> float | None:
    """Correlation of a team's within-week miss with its mean miss in the other
    weeks: > 0 if some teams beat (or fall short of) our forecast week after week."""
    miss = dict(zip(((r["team"], r["week"]) for r in rows), _within_week(rows, "miss")))
    pairs = []
    for (team, week), m in miss.items():
        other = [v for (t, w), v in miss.items() if t == team and w != week]
        if other:
            pairs.append((m, st.mean(other)))
    if len(pairs) < 3:
        return None
    return st.correlation(*zip(*pairs))


def report(rows: list[dict]) -> list[str]:
    if not rows:
        return ["No week has both our forecast and its points for yet."]
    out = [f"{len(rows)} team-weeks ({len({r['week'] for r in rows})} weeks):"]
    for w in sorted({r["week"] for r in rows}):
        rs = [r for r in rows if r["week"] == w]
        out.append(f"  week {w}: mean miss {st.mean(r['miss'] for r in rs):+.1f} pts (actual - ours), "
                   f"spread {st.stdev(r['miss'] for r in rs):.1f} vs our sd {st.mean(r['sd'] for r in rs):.1f}; "
                   f"{sum(r['adds'] for r in rs)} adds")
    if fit := slope(rows):
        out.append(f"  per add (x the week left), within weeks: {fit[0]:+.1f} +/- {fit[1]:.1f} pts")
    if fit := slope(rows, "adds"):
        out.append(f"  per add (count), within weeks: {fit[0]:+.1f} +/- {fit[1]:.1f} pts")
    if (r := persistence(rows)) is not None:
        out.append(f"  a team's miss vs its other weeks' (team effect): correlation {r:+.2f}")
    return out


def main_() -> None:
    print("\n".join(report(team_weeks(gm_state.load()))))


if __name__ == "__main__":
    main_()
