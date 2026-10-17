"""A third goalie or the skater he'd replace? Every week left, on the real NHL
schedule, by the bot's own projection (engine/matchup.project, long run:
durability, the goalie minimum each week, a goalie lost for a week).

For each free-agent goalie tried in place of the skater (default: the plan's
drop), week by week: both teams' games, the nights all three goalies' teams
play (only then must one sit), the change in expected points and where it
comes from (skaters, goalies), the goalie's expected starts and how many land
in a G slot, the skater's lineup games, and P(reaching the goalie minimum).
Regular season and playoffs (weeks 24-26) summed apart.

    python -m scripts.third_goalie
    python -m scripts.third_goalie --drop "Shane Pinto" --goalie "Joseph Woll" --goalie "Anthony Stolarz"

Read-only: sends and saves nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt

from bot import common
from clients import dfo_lines, goalie_client, nhl_client
from clients.names import normalize_name
from config.league import PLAYOFF_WEEKS, REGULAR_SEASON_WEEKS
from engine import availability, matchup
from league import roster as roster_mod
from league import teams, weeks
from model import context

TOP_GOALIES = 6  # free-agent goalies tried when none are named: the highest start shares


def week_parts(roster, ctx, schedule, lines, starters):
    t = matchup.project("me", roster, ctx, schedule, lines, starters, long_run=True)
    skaters = sum(v[0] for pid, v in t.by_player.items() if not _goalie(roster, pid))
    return t, skaters, t.expected - skaters


def _goalie(roster, pid) -> bool:
    return any(p.id == pid and p.is_goalie for p in roster)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", default="Shane Pinto")
    parser.add_argument("--goalie", action="append", default=[])
    parser.add_argument("--from-week", type=int, help="first week (default: next week)")
    args = parser.parse_args()

    today = common.nhl_date(dt.datetime.now(dt.timezone.utc))
    players, league = roster_mod.load(), teams.load()
    mine = roster_mod.active(players)
    drop = next(p for p in mine if normalize_name(p.name) == normalize_name(args.drop))
    ctx = context.build(today)
    lines = {t: common._safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    starters = common._safe(goalie_client.get_starters, today, default={})
    pool = [p for p in common.free_agents(players, league) if p.is_goalie]
    def share(p) -> float:
        return availability.start_share(p.id, today, lines.get(p.team, {}).get(normalize_name(p.name)),
                                        ctx.team_starts.get(p.team, []), ctx.prior_start_share(p.id),
                                        *matchup.goalie_absences(p, ctx, lines.get(p.team, {}), today))

    if args.goalie:
        wanted = {normalize_name(n) for n in args.goalie}
        tried = [p for p in pool if normalize_name(p.name) in wanted]
    else:
        tried = sorted(pool, key=share, reverse=True)[:TOP_GOALIES]
    keepers = [p for p in mine if p.is_goalie]
    first = args.from_week or weeks.week_of(today) + 1
    schedules = {w: {d: nhl_client.games_on(d) for d in weeks.days(w)} for w in range(first, weeks.LAST_WEEK + 1)}

    def plays(team, games):
        return any(team in (g.home, g.away) for g in games)

    base = {w: week_parts(mine, ctx, s, lines, starters) for w, s in schedules.items()}
    print(f"As of {today}. My goalies: " + ", ".join(f"{g.name} ({g.team}, share {share(g):.0%})" for g in keepers)
          + f". Dropped: {drop.name} ({drop.team}, {drop.positions}).")
    print("Each week: games of my goalies' teams / the added goalie's / the dropped skater's; nights all three"
          " goalies' teams play; change in expected points (skaters, goalies); the goalie's expected starts and"
          " those in a G slot; the skater's lineup games and points in the current lineup; P(goalie minimum)"
          " now -> with him.\n")
    for g in tried:
        trial = [p for p in mine if p.id != drop.id] + [roster_mod.RosterPlayer(g.id, g.name, g.team, ["G"], "BN")]
        print(f"== {g.name} ({g.team}) for {drop.name}  (start share now {share(g):.0%})")
        sums = {"regular": [0.0] * 6, "playoffs": [0.0] * 6}
        for w, sched in schedules.items():
            t_base, s_base, g_base = base[w]
            t, s, gl = week_parts(trial, ctx, sched, lines, starters)
            days = sorted(sched)
            three = sum(all(plays(k.team, sched[d]) for k in keepers) and plays(g.team, sched[d]) for d in days)
            mine_games = "/".join(str(sum(plays(k.team, sched[d]) for d in days)) for k in keepers)
            used_starts = t.by_player.get(g.id, (0.0, 0.0, 0.0))[1]
            skater_pts, skater_games, _ = t_base.by_player.get(drop.id, (0.0, 0.0, 0.0))
            delta = t.expected - t_base.expected
            print(f"  wk {w:2} games {mine_games} / {sum(plays(g.team, sched[d]) for d in days)} / "
                  f"{sum(plays(drop.team, sched[d]) for d in days)}  3-way {three}  "
                  f"change {delta:+6.1f} (skaters {s - s_base:+6.1f}, goalies {gl - g_base:+6.1f})  "
                  f"his starts in slot {used_starts:4.2f}  {drop.name.split()[-1]} {skater_games:3.0f} g {skater_pts:5.1f} pts  "
                  f"min {t_base.goalie_min_prob:.0%} -> {t.goalie_min_prob:.0%}")
            part = sums["playoffs" if w in PLAYOFF_WEEKS else "regular"]
            for i, v in enumerate((delta, s - s_base, gl - g_base, used_starts, skater_pts, 1)):
                part[i] += v
        for name, (d, s, gl, st, sp, n) in sums.items():
            if n:
                print(f"  {name:8} {int(n):2} weeks: change {d:+6.1f} ({d / n:+.1f} a week; skaters {s:+.1f}, "
                      f"goalies {gl:+.1f}); his starts in slot {st:.1f} ({st / n:.2f} a week); "
                      f"{drop.name.split()[-1]}'s lineup points {sp:.1f}")
        print()
    print(f"Regular season ends with week {REGULAR_SEASON_WEEKS}; playoffs: weeks {PLAYOFF_WEEKS}.")


if __name__ == "__main__":
    main()
