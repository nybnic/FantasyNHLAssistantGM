"""Why the weekly plan says what it says: both teams' projections and every
add/drop the bot weighed, ranked, with the reason each one passed or failed.

    python -m scripts.explain_week                   # this week, top 25 moves
    python -m scripts.explain_week --top 60
    python -m scripts.explain_week --position D      # only adds who play D
    python -m scripts.explain_week --add "Colton Parayko" --add "Ben Chiarot"   # score these too
    python -m scripts.explain_week --now 2026-10-05T12:00:00+03:00
    python -m scripts.explain_week --ir "Macklin Celebrini"    # as if he sat on IR+ (moved, bot not told yet)
    python -m scripts.explain_week --player "Esa Lindell"     # his points per game by stat, availability, durability

Read-only: sends and saves nothing. Uses the committed state/ files, so
`git pull` first to see what the bot sees.
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot import board, common, weekly
from clients.names import normalize_name
from league import roster as roster_mod
from league import teams, weeks
from model import context
from state import gm_state


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--position", help="only adds eligible here (C, LW, RW, D, G)")
    parser.add_argument("--add", action="append", default=[], help="also score this free agent")
    parser.add_argument("--now", help="ISO time with offset (default: now)")
    parser.add_argument("--ir", action="append", default=[], help="treat this player of mine as on IR+ (this run only)")
    parser.add_argument("--player", action="append", default=[], help="this player's numbers (any on the Board)")
    args = parser.parse_args()

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    date = now.astimezone(common.NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None:
        raise SystemExit(f"{date} is outside the fantasy season")
    state, players, league = gm_state.load(), roster_mod.load(), teams.load()
    for name in mark_ir(players, args.ir):
        print(f"  ({name} isn't on my roster: --ir ignored)")
    opponent = common.current_opponent(state, week)
    if not opponent:
        raise SystemExit(f"Week {week} has no known opponent (playoffs: /opp Team Name)")

    pool = common.free_agents(players, league)
    by_name = {normalize_name(p.name): p for p in pool}
    extra = set()
    for name in args.add:
        p = by_name.get(normalize_name(name))
        if p:
            extra.add(p.id)
        else:
            print(f"  ({name} isn't a free agent here: on a roster, /taken, or not on an NHL roster)")

    # The plan's own computation and its Board: what the plan message is built from.
    plan = weekly.plan_moves(state, players, league, date, week, opponent, context.build, extra,
                             weekly.skipped(state, date))
    wk = plan.wk
    print(f"Week {week} ({wk.days[0]} - {wk.days[-1]}) vs {opponent}, as of {date}")
    for t in (wk.me, wk.them):
        print(f"  {t.name:24} so far {t.so_far:6.1f}  expected {t.expected:6.1f} +/- {t.variance ** 0.5:4.1f}  "
              f"lineup games {t.player_games:3}  goalie min {t.goalie_min_prob:.0%}")
    # The dashboard's breakdowns: each team's rest of week by player and by stat.
    b = board.build(plan, week, opponent, now, date,
                    details=weekly.player_details(plan, players, league, opponent))
    for side, name in (("me", wk.me.name), ("them", opponent)):
        print("\n".join(team_lines(b, side, name)))
    for name in args.player:
        found = [v for v in b["players"].values() if normalize_name(v["name"]) == normalize_name(name)]
        print("\n".join(player_lines(found[0]) if found else [f"\n  ({name} isn't on the Board)"]))
    copy = {"league_weeks": {}}  # what the week's first plan records (state["league_weeks"][week]["ours"])
    weekly.record_league(copy, league, week, opponent, wk, now)
    print("\n  Every team's week, as the first plan logs it (expected +/- sd):")
    for team, t in sorted(copy["league_weeks"][str(week)]["ours"]["teams"].items(), key=lambda kv: -kv[1]["expected"]):
        print(f"    {team[:24]:24} {t['expected']:6.1f} +/- {t['sd']:4.1f}")
    h = b["header"]
    print(f"\n  P(win) {h['win']:.0%}   adds used {wk.season_used} season / {wk.week_used} week, "
          f"{h['adds_left']['week']} allowed now; matchup spread tau {h['tau']:.1f} pts, a later point = "
          f"{100 * h['later_weight']:.2f} win-pts")
    print("  add price: " + (f"{100 * h['price']['lam']:.1f} win-pts (pace {h['price']['pace']:.2f} adds a week, "
                             f"{len(state['add_pools'])} week(s) of candidates logged)" if h["price"]
                             else "none (budget spent)")
          + ("; no adds left this week: everyone joins Monday" if not wk.max_moves else "")
          + (f"; {len(wk.available_from)} free agents on waivers" if wk.available_from and wk.max_moves else ""))
    for w in h["ahead"]:
        print(f"  week {w['week']} vs {w['opponent'] or '(unknown: a point at a typical week)'}: "
              + (f"margin {w['margin']:+.1f} +/- {w['sd']:.1f} pts, win {w['win']:.0%}" if w["win"] is not None
                 else "no matchup to play out"))
    if b["plan"]["ir"]:
        print("  the adds assume these IR moves: " + ", ".join(f"{m['name']} to {m['slot']}" for m in b["plan"]["ir"]))
    print("  plan (as composed now; the bot keeps the one already sent unless it must change): "
          + ("; ".join(f"{_label(b, q['key'])} {q['when']} ({q['why']})" for q in b["plan"]["moves"])
             or "no add"))
    print(f"\n{len(b['moves'])} moves (value = this week's win-pts + the next weeks' (+1/+2) + later win-pts; "
          "plan: in the plan, passes: worth an add but not in it, fails: under the price):")
    print("\n".join(board.table(b, args.top, args.position)))


def _label(b: dict, key: str) -> str:
    r = next(r for r in b["moves"] if r["key"] == key)
    return r["add"]["name"] + (f" for {r['drop']['name']}" if r["drop"] else "")


def mark_ir(players: list, names: list[str]) -> list[str]:
    """Moves the named players of mine to IR+ in memory (state is untouched);
    returns the names that matched nobody."""
    by_name = {normalize_name(p.name): p for p in players}
    missing = []
    for name in names:
        if p := by_name.get(normalize_name(name)):
            p.slot = "IR+"
        else:
            missing.append(name)
    return missing


def team_lines(b: dict, side: str, name: str) -> list[str]:
    """A team's rest of week player by player, and by stat group (the Board's
    "teams", as the dashboard shows them): adds up to the team's expected
    points with the points banked and the goalie minimum's cost."""
    t, people = b["teams"][side], b["players"]
    out = [f"\n  {name}: rest of week, expected pts in the lineup (games), lost on the bench"]
    for r in t["players"]:
        if r["pts"] or r["bench_pts"] or r["slot"]:
            p = people[str(r["id"])] if str(r["id"]) in people else people.get(r["id"], {})
            out.append(f"    {p.get('name', r['id'])[:22]:22} {p.get('positions', ''):8} {r['pts']:6.1f} "
                       f"({r['games']:3.1f})" + (f"  bench {r['bench_pts']:4.1f}" if r["bench_pts"] else "")
                       + (f"  [{r['slot']}]" if r["slot"] else ""))
    out.append(f"    goalie minimum costs {t['goalie_lost']:.1f}; rest of week {t['rest']:.1f}")
    out.append("    by stat: " + ", ".join(f"{k} {v:.1f}" for k, v in t["by_stat"].items()))
    return out


def player_lines(p: dict) -> list[str]:
    """One player's numbers (report.player_view): per game (goalies: per start) by stat group."""
    head = f"\n  {p['name']} ({p['team']}, {p['positions']}, {p['owner']}): {p['xfp'] or 0:.2f} pts per " + \
           ("start" if p["goalie"] else "game") + f", {p['gp']} games this season"
    out = [head, "    " + ", ".join(f"{k} {v:+.2f}" for k, v in (p["per_game"] or {}).items())]
    if p["goalie"]:
        out.append(f"    start share {p['share']:.0%}; starts left this week: " + (", ".join(
            f"{s['date'][5:]} {s['opp']} {s['prob']:.0%} x {s['xfp']:.1f}" for s in p["starts"]) or "none"))
    else:
        out.append(f"    TOI {p['toi']:.1f} (PP {p['pp_toi']:.1f}); plays tonight {p['play']:.0%}"
                   + (f" ({p['note']})" if p["note"] else "")
                   + f"; projected games {p['durability']:.0%}, long-run points kept {p['kept']:.0%}"
                   + (f" (a streamer scores {p['replacement']:.2f})" if p["replacement"] is not None else ""))
    return out


if __name__ == "__main__":
    main_()
