"""Why the weekly plan says what it says: both teams' projections and every
add/drop the bot weighed, ranked, with the reason each one passed or failed.

    python -m scripts.explain_week                   # this week, top 25 moves
    python -m scripts.explain_week --top 60
    python -m scripts.explain_week --position D      # only adds who play D
    python -m scripts.explain_week --add "Colton Parayko" --add "Ben Chiarot"   # score these too
    python -m scripts.explain_week --now 2026-10-05T12:00:00+03:00

Read-only: sends and saves nothing. Uses the committed state/ files, so
`git pull` first to see what the bot sees.
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot import common, weekly
from clients.names import normalize_name
from engine import matchup
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
    args = parser.parse_args()

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    date = now.astimezone(common.NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None:
        raise SystemExit(f"{date} is outside the fantasy season")
    state, players, league = gm_state.load(), roster_mod.load(), teams.load()
    opponent = common.current_opponent(state, week)
    if not opponent:
        raise SystemExit(f"Week {week} has no known opponent (playoffs: /opp Team Name)")

    wk = weekly.week_inputs(date, week, players, league, state, context.build, opponent)
    print(f"Week {week} ({wk.days[0]} - {wk.days[-1]}) vs {opponent}, as of {date}")
    for t in (wk.me, wk.them):
        print(f"  {t.name:24} so far {t.so_far:6.1f}  expected {t.expected:6.1f} +/- {t.variance ** 0.5:4.1f}  "
              f"lineup games {t.player_games:3}  goalie min {t.goalie_min_prob:.0%}")
    for name, roster, team in ((wk.me.name, players, wk.me), (opponent, teams.players(league, opponent), wk.them)):
        print_players(name, roster_mod.active(roster), team, wk)
    copy = {"league_weeks": {}}  # what the week's first plan records (state["league_weeks"][week]["ours"])
    weekly.record_league(copy, league, week, opponent, wk, now)
    print("\n  Every team's week, as the first plan logs it (expected +/- sd):")
    for team, t in sorted(copy["league_weeks"][str(week)]["ours"]["teams"].items(), key=lambda kv: -kv[1]["expected"]):
        print(f"    {team[:24]:24} {t['expected']:6.1f} +/- {t['sd']:4.1f}")
    candidates = weekly.add_candidates(wk, weekly.next_week(week, players, wk))
    by_name = {normalize_name(p.name): p for p in wk.pool}
    for name in args.add:
        p = by_name.get(normalize_name(name))
        if not p:
            print(f"  ({name} isn't a free agent here: on a roster, /taken, or not on an NHL roster)")
        elif p not in candidates:
            candidates.append(p)
    if args.position:
        candidates = [p for p in candidates if args.position.upper() in p.positions]

    ranked = matchup.candidate_moves(players, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters,
                                     wk.future, wk.weeks_after, wk.available_from, wk.so_far, wk.hold_days,
                                     wk.later_weight)
    price = weekly.add_price(state, week, wk, ranked, date)
    print(f"  P(win) {matchup.win_prob(wk.me, wk.them):.0%}   adds used {wk.season_used} season / {wk.week_used} week, "
          f"{wk.max_moves} allowed now; matchup spread tau {wk.tau:.1f} pts, a later point = "
          f"{100 * wk.later_weight:.2f} win-pts")
    print("  add price: " + (f"{100 * price.lam:.1f} win-pts (pace {price.pace:.2f} adds a week, "
                             f"{len(state['add_pools'])} week(s) of candidates logged)" if price
                             else "none (no adds left this week: everyone joins Monday)" if not wk.max_moves
                             else "none (budget spent)")
          + (f"; {len(wk.available_from)} free agents on waivers" if wk.available_from and wk.max_moves else ""))
    print(f"\n{len(ranked)} moves from {len(candidates)} free agents (value = this week's win-pts + later win-pts):")
    print(f"  {'add':22} {'pos':5} {'drop':18} {'wk gms':>6} {'week':>6} {'later':>6} {'now':>5} {'+later':>6} "
          f"{'value':>6}  win     verdict")
    for m in ranked[:args.top]:
        verdict = matchup.rejection(m, price) if price else "no adds left"
        print(f"  {m.add.name[:22]:22} {'/'.join(m.add.positions):5} {(m.drop.name if m.drop else f'({m.ir_slot} stash)' if m.ir_slot else '(open spot)')[:18]:18} "
              f"{m.games:6} {m.week_gain:+6.1f} {m.long_term:+6.1f} {100 * (m.win_after - m.win_before):+5.1f} "
              f"{100 * m.later_value:+6.1f} {100 * m.value:+6.1f}  "
              f"{m.win_before:.0%}->{m.win_after:.0%}  {verdict or 'WORTH AN ADD'}")


def print_players(name: str, roster: list, team: matchup.TeamWeek, wk) -> None:
    """Each player's week from today: games, expected points over all of them
    (what a per-player projection like Yahoo's shows) and over the games he
    starts in the best daily lineup (what the team total counts)."""
    print(f"\n  {name}: player, games, expected pts (all games / in the lineup)")
    rows = []
    first = matchup._first_games(wk.schedule, wk.ctx.today)
    for p in roster:
        games = all_pts = lineup_pts = 0.0
        for date in (d for d in sorted(wk.schedule) if d >= wk.ctx.today):
            game = matchup._game_of(wk.schedule[date]).get(p.team)
            if not game:
                continue
            yesterday = matchup._game_of(wk.schedule.get(date - dt.timedelta(days=1), []))
            mean, _, _ = matchup._player_day(p, wk.ctx, date, game, p.team in yesterday, wk.lines, wk.starters,
                                             next_game=first.get(p.team) == date)
            games += 1
            all_pts += mean
            slot = team.lineups.get(date, {}).get(p.id, ("G" if p.is_goalie else "BN", 1.0))[0]
            lineup_pts += mean if slot != roster_mod.BENCH else 0.0
        rows.append((p, games, all_pts, lineup_pts))
    for p, games, all_pts, lineup_pts in sorted(rows, key=lambda r: -r[2]):
        print(f"    {p.name[:22]:22} {'/'.join(p.positions):8} {games:2.0f}  {all_pts:6.1f} / {lineup_pts:6.1f}")
    print(f"    {'sum':22} {'':8} {sum(r[1] for r in rows):2.0f}  {sum(r[2] for r in rows):6.1f} / "
          f"{sum(r[3] for r in rows):6.1f}  (team expected {team.expected:.1f}: goalies x P(minimum))")


if __name__ == "__main__":
    main_()
