"""The Board: everything one weekly plan computed, as one JSON record
(docs/plan-2026-10-09.md). It is what the plan knows: the matchup, the add
budget and price, every add/drop weighed with its verdict, and what to do now.
Built once per plan (the weekly plan, the evening news check), saved to
state/board.json (git history keeps each one), and read by
scripts/explain_week.py, so the explanation and the plan can't disagree.

Values are in wins (a probability: 0.05 = 5 win-pts); points are fantasy points.
A move's value = this week's change in P(win) + the change in each week
ahead's P(win) against its opponent (header "ahead") + later points' worth.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict
from pathlib import Path

from config.league import MAX_ADDS_PER_SEASON
from engine import matchup, report

BOARD_FILE = Path("state/board.json")


def _r(x: float | None, digits: int = 2) -> float | None:
    return None if x is None else round(x, digits)


def _team(t: matchup.TeamWeek) -> dict:
    return {"name": t.name, "so_far": _r(t.so_far), "expected": _r(t.expected), "sd": _r(math.sqrt(t.variance)),
            "player_games": t.player_games, "goalie_games": _r(t.goalie_games_so_far + t.goalie_starts_left, 1),
            "goalie_min": _r(t.goalie_min_prob, 3)}


def _ahead(w: matchup.WeekAhead) -> dict:
    """A week ahead as my current roster plays it against that week's opponent."""
    return {"week": w.week, "opponent": w.opponent, "margin": _r(w.margin), "sd": _r(w.sd),
            "win": _r(matchup._phi(w.margin / w.sd), 4) if w.margin is not None else None}


def status(move: matchup.Move, now: set[str], waits: str | None, price) -> str:
    """now: one of the plan's adds; waits: the keeper kept for later; passes:
    worth an add but the week's go elsewhere (or none are left); fails: under the price."""
    key = report.move_key(move)
    if key in now:
        return "now"
    if key == waits:
        return "waits"
    return "passes" if price is not None and not matchup.rejection(move, price) else "fails"


def when(move: matchup.Move, date: dt.date, monday: dt.date) -> dt.date:
    """The day to make the move: when it starts paying. A claim plays from its
    day; a keeper that does nothing this week can wait for Monday's adds
    (matchup.waits); anything else, today."""
    if matchup.waits(move):
        return max(monday, move.plays_from or monday)
    return move.plays_from or date


def move_row(move: matchup.Move, state: str, why: str, day: dt.date | None = None) -> dict:
    return {
        "key": report.move_key(move),
        "add": {"id": move.add.id, "name": move.add.name, "team": move.add.team, "positions": move.add.positions},
        "drop": {"id": move.drop.id, "name": move.drop.name} if move.drop else None,
        "ir_slot": move.ir_slot,
        "later_drop": move.later_drop.name if move.later_drop else None,
        "plays_from": move.plays_from.isoformat() if move.plays_from else None,
        "when": day.isoformat() if day else None,
        "games": move.games,
        "week_pts": _r(move.week_gain),
        "next_two_pts": _r(move.next_weeks),
        "later_pts": _r(move.long_term),
        "win": [_r(move.win_before, 4), _r(move.win_after, 4)],
        "now_wins": _r(move.win_after - move.win_before, 4),
        "ahead_wins": [_r(w, 4) for w in move.ahead_wins],
        "ahead_pts": _r(move.ahead_pts),
        "later_wins": _r(move.later_value, 4),
        "value": _r(move.value, 4),
        "status": state,
        "why": why,
    }


def build(p, week: int, opponent: str, now: dt.datetime, date: dt.date, keeper: matchup.Move | None,
          odds=None, odds_note: str = "") -> dict:
    """The Board of a plan: `p` is bot.weekly.PlanMoves, `keeper` the move
    that waits for later (matchup.can_wait), `odds` the season's (engine/season.py)."""
    wk = p.wk
    price = wk.price if wk.max_moves else None
    now_keys = [report.move_key(m) for m in p.moves]
    waits = report.move_key(keeper) if keeper else None
    monday = wk.days[-1] + dt.timedelta(days=1)
    rows = []
    for m in p.ranked:
        state = status(m, set(now_keys), waits, price)
        why = "" if state == "now" else matchup.why_not(m, price, p.moves, p.held)
        rows.append(move_row(m, state, why, when(m, date, monday)))
    known = {r["key"] for r in rows}
    # A plan's later adds are judged after its first is made, so they may not be
    # among the moves weighed on today's roster.
    rows += [move_row(m, "now", "", when(m, date, monday)) for m in p.moves if report.move_key(m) not in known]
    if keeper and waits not in known:
        rows.append(move_row(keeper, "waits", matchup.why_not(keeper, price, p.moves, p.held),
                             when(keeper, date, monday)))
    return {
        "at": now.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
        "date": date.isoformat(),
        "week": week,
        "days": [wk.days[0].isoformat(), wk.days[-1].isoformat()],
        "opponent": opponent,
        "header": {
            "me": _team(wk.me),
            "them": _team(wk.them),
            "win": _r(matchup.win_prob(wk.me, wk.them), 4),
            "live": wk.live,
            "yahoo_projected": wk.yahoo_projected,
            "adds_left": {"season": MAX_ADDS_PER_SEASON - wk.season_used, "week": wk.max_moves},
            "price": {"lam": _r(price.lam, 4), "later_weight": _r(price.later_weight, 5),
                      "pace": _r(price.pace, 3)} if price else None,
            "later_weight": _r(wk.later_weight, 5),
            "tau": _r(wk.tau),
            "ahead": [_ahead(w) for w in getattr(wk, "ahead", [])],
            "season": ({**{k: _r(v, 4) for k, v in asdict(odds).items() if k != "by_week"}, "note": odds_note}
                       if odds else None),
        },
        "plan": {
            "now": now_keys,
            "ir": [{"id": m.player.id, "name": m.player.name, "slot": m.slot} for m in p.ir_moves],
            "waits": waits,
            "held": p.held,
        },
        "moves": rows,
    }


def save(board: dict, path: Path = BOARD_FILE) -> Path:
    """One move per line: small, and a git diff shows which moves changed."""
    head = json.dumps({k: v for k, v in board.items() if k != "moves"}, indent=1, ensure_ascii=False)
    rows = ",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in board["moves"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head[:-2] + ',\n "moves": [\n' + rows + "\n ]\n}\n", encoding="utf-8")
    return path


def load(path: Path = BOARD_FILE) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def table(board: dict, top: int = 25, position: str | None = None) -> list[str]:
    """The move table as text lines (scripts/explain_week.py), best value first."""
    rows = [r for r in board["moves"] if not position or position.upper() in r["add"]["positions"]]
    rows = sorted(rows, key=lambda r: -r["value"])[:top]
    out = [f"  {'add':22} {'pos':5} {'drop':18} {'gms':>3} {'week':>6} {'next2':>6} {'later':>6} "
           f"{'now':>5} {'+1/+2':>6} {'+later':>6} {'value':>6}  {'win':9} {'when':6} status  why"]
    for r in rows:
        drop = r["drop"]["name"] if r["drop"] else f"({r['ir_slot']} stash)" if r["ir_slot"] else "(open spot)"
        win = f"{r['win'][0]:.0%}->{r['win'][1]:.0%}"
        day = dt.date.fromisoformat(r["when"]).strftime("%a %d") if r.get("when") else ""
        out.append(f"  {r['add']['name'][:22]:22} {'/'.join(r['add']['positions'])[:5]:5} {drop[:18]:18} "
                   f"{r['games']:3} {r['week_pts']:+6.1f} {r['next_two_pts']:+6.1f} {r['later_pts']:+6.1f} "
                   f"{100 * r['now_wins']:+5.1f} {100 * sum(r.get('ahead_wins', [])):+6.1f} "
                   f"{100 * r['later_wins']:+6.1f} {100 * r['value']:+6.1f}  "
                   f"{win:9} {day:6} {r['status']:6}  {r['why']}")
    return out
