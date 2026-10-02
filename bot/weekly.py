"""The weekly plan and everything built on its numbers: the add/drop search, the evening
news check, the dashboard, the results log and Monday's report of last week.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import logging
from dataclasses import asdict, dataclass
from pathlib import Path


from clients import dfo_lines, goalie_client, nhl_client
from config.league import (MAX_ADDS_PER_SEASON, MIN_GOALIE_GAMES_PER_WEEK, MY_TEAM, POST_DRAFT_WAIVERS_CLEAR,
                           SEASON_END)
from engine import addprice, briefing, ir, matchup, report, scorecard
from league import teams, weeks
from league import roster as roster_mod
from model import context
from notify import charts
from bot.common import NHL_TIME, CHART_DIR, Outbox, _safe, current_opponent, free_agents, _weakest

logger = logging.getLogger(__name__)

SITE_DIR = Path("site")  # the dashboard: index.html (ours) and data.json (written by each weekly plan)


CHART_WEEKS = 5  # an add's chart shows this many weeks after the current one


MIN_KNOWN_ROSTER = 10  # a league team's roster counts toward the matchup spread from this many players


WEEKLY_PLAN_TIME = dt.time(12, 0)  # local, on the week's first day: before any NHL game


def waiver_days(pool: list, league: dict, date: dt.date) -> dict[int, dt.date]:
    """Free agent id -> the first day he can play for me, for those on waivers:
    dropped in the last day or so, or everyone until the post-draft waivers cleared."""
    days = {pid: day for pid, day in teams.on_waivers(league, date).items()}
    if date < POST_DRAFT_WAIVERS_CLEAR:
        days.update({p.id: max(days.get(p.id, POST_DRAFT_WAIVERS_CLEAR), POST_DRAFT_WAIVERS_CLEAR) for p in pool})
    return days


@dataclass
class WeekInputs:
    """Everything the weekly plan is computed from (also used by
    scripts/explain_week.py)."""
    ctx: object
    days: list[dt.date]
    schedule: dict
    future: dict  # the weeks after this one (matchup.LONG_RUN_WEEKS), for long-run value
    lines: dict
    starters: dict
    me: matchup.TeamWeek
    them: matchup.TeamWeek
    pool: list
    season_used: int
    week_used: int
    max_moves: int
    pace: float | None  # adds a week the budget allows; None when the regular season's share is spent
    later_weight: float  # win probability per later point (addprice.later_weight)
    tau: float  # spread of the league's matchup margins
    weeks_after: int
    available_from: dict  # player id -> first day he can play for me: waivers, or Monday with no adds left
    so_far: tuple | None = None  # my banked points: a matchup screenshot's, else box scores by day's roster
    live: bool = False  # so_far comes from a matchup screenshot taken today
    # Then Yahoo's score and the box scores' best-lineup score for the same days
    # (how far each manager's real lineups fall short: opponent profiles).
    live_check: dict | None = None
    hold_days: int = 14  # days after this week a streamer is kept (matchup.hold_weeks)
    yahoo_projected: list | None = None  # Yahoo's projected finals, from the same screenshot
    price: addprice.AddPrice | None = None  # set by add_price once the week's moves are known


def week_inputs(date: dt.date, week: int, players: list, league: dict, state: dict,
                build_context, opponent: str) -> WeekInputs:
    ctx = build_context(date)
    days = weeks.days(week)
    schedule = {d: nhl_client.games_on(d) for d in days}
    future_days = [days[-1] + dt.timedelta(days=i) for i in range(1, 7 * matchup.LONG_RUN_WEEKS + 1)]
    future = {d: nhl_client.games_on(d) for d in future_days if weeks.week_of(d)}
    lines = {t: _safe(dfo_lines.team_lines, t, default={}) for t in nhl_client.current_teams()}
    starters = _safe(goalie_client.get_starters, date, default={})
    season_used, week_used = matchup.adds_used(state["adds"], days)
    them_roster = teams.players(league, opponent)
    added_on = {a["id"]: dt.date.fromisoformat(a["date"]) for a in state["adds"] if a["id"] is not None}
    my_days = rosters_by_day(state, "me", roster_mod.active(players), days, date, added_on)
    their_days = rosters_by_day(state, opponent, them_roster, days, date)
    projected = None
    live = state["live_score"]
    is_live = bool(live and live["week"] == week and live["opponent"] == opponent
                   and live["through"] == date.isoformat())
    box = None
    if is_live:
        mine = _banked(live["score"][0], live["goalies"][0], players, ctx, days, my_days)
        theirs = _banked(live["score"][1], live["goalies"][1], them_roster, ctx, days, their_days)
        box = [sum(matchup._so_far(roster_mod.active(players), ctx, days, my_days)[:2]),
               sum(matchup._so_far(them_roster, ctx, days, their_days)[:2])]
        projected = live["projected"] or None
    else:
        mine = matchup._so_far(roster_mod.active(players), ctx, days, my_days)
        theirs = matchup._so_far(them_roster, ctx, days, their_days)
    me = matchup.project(MY_TEAM, players, ctx, schedule, lines, starters, so_far=mine)
    them = matchup.project(opponent, them_roster, ctx, schedule, lines, starters, so_far=theirs)
    remaining = sum(d >= date for d in days)
    pool = free_agents(players, league)
    max_moves = matchup.max_moves(season_used, week_used)
    available_from = waiver_days(pool, league, date)
    if not max_moves:
        available_from = after_this_week(available_from, pool, days)
    tau = league_tau(players, league, ctx, future, lines, starters)
    sigma_week = math.sqrt((me.variance + them.variance) * 7 / max(remaining, 1))
    return WeekInputs(
        ctx=ctx, days=days, schedule=schedule, future=future, lines=lines, starters=starters,
        me=me, them=them, tau=tau, later_weight=addprice.later_weight(sigma_week, tau),
        pace=addprice.pace(MAX_ADDS_PER_SEASON - season_used, week),
        so_far=mine, live=is_live, yahoo_projected=projected,
        live_check={"through": date.isoformat(), "yahoo": list(live["score"]), "box": box} if is_live else None,
        hold_days=max(0, round(7 * matchup.hold_weeks(MAX_ADDS_PER_SEASON - season_used, week))
                      - sum(d >= date for d in days)),
        pool=pool,
        season_used=season_used, week_used=week_used,
        max_moves=max_moves,
        weeks_after=weeks.LAST_WEEK - week,
        available_from=available_from,
    )


def after_this_week(available_from: dict, pool: list, days: list[dt.date]) -> dict[int, dt.date]:
    """With this week's adds spent, nobody joins before Monday: every free
    agent's first day moves to next week's first (or his waiver day, if later)."""
    monday = days[-1] + dt.timedelta(days=1)
    return {p.id: max(available_from.get(p.id, monday), monday) for p in pool}


def league_tau(players: list, league: dict, ctx, future: dict, lines: dict, starters: dict) -> float:
    """The spread of matchup margins: how far apart two of the league's teams
    usually project in a week (each team's best lineup over the next full
    week; the margin's spread is sqrt(2) times the teams')."""
    week = {d: future[d] for d in sorted(future)[:7]}
    rosters = [players] + [teams.players(league, t) for t in league["teams"]]
    totals = [matchup.project("t", r, ctx, week, lines, starters, True).expected
              for r in rosters if len(roster_mod.active(r)) >= MIN_KNOWN_ROSTER]
    if len(totals) < 4 or len(week) < 7:
        return addprice.DEFAULT_TAU
    mean = sum(totals) / len(totals)
    return math.sqrt(2 * sum((t - mean) ** 2 for t in totals) / (len(totals) - 1))


def add_price(state: dict, week: int, wk: WeekInputs, ranked: list, date: dt.date) -> addprice.AddPrice | None:
    """The add's price this week, solved over this week's candidates and the
    ones logged in earlier weeks (state["add_pools"])."""
    if wk.pace is None or not wk.max_moves:
        return None  # with this week's adds spent, its candidates can't join: the logged pool stays
    sigma_now = math.sqrt(wk.me.variance + wk.them.variance)
    remaining = sum(d >= date for d in wk.days)
    state["add_pools"][str(week)] = addprice.pool_entry(ranked, remaining, sigma_now, wk.tau)
    lam = addprice.solve(list(state["add_pools"].values()), wk.pace, wk.later_weight)
    return addprice.AddPrice(lam, wk.later_weight, wk.pace)


def _banked(total: float, goalie: float | None, roster: list, ctx, days: list[dt.date],
            history: dict | None = None) -> tuple[float, float, int]:
    """(skater points, goalie points, goalie games) so far: Yahoo's score,
    split by the G-slot rows (else by box scores); goalie games from box scores."""
    skater_bs, goalie_bs, goalie_games = matchup._so_far(roster_mod.active(roster), ctx, days, history)
    goalie = goalie_bs if goalie is None else goalie
    return total - goalie, goalie, goalie_games


def snapshot_rosters(state: dict, players: list, league: dict, now: dt.datetime) -> None:
    """Keep today's rosters, mine and my opponent's, as they are until the
    day's first puck: points banked later this week are scored with the
    players each team had that day, so a mid-week add or drop doesn't move them."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None or not players:
        return
    games = nhl_client.games_on(date)
    if games and now >= games[0].start:
        return  # lineups are locking: today's snapshot stays as it was
    opponent = current_opponent(state, week)
    state["day_rosters"][date.isoformat()] = {
        "me": [asdict(p) for p in roster_mod.active(players)],
        "them": {"team": opponent, "players": [asdict(p) for p in teams.players(league, opponent)]}
        if opponent else None,
    }


def rosters_by_day(state: dict, side: str, current: list, days: list[dt.date], today: dt.date,
                   added_on: dict[int, dt.date] | None = None) -> dict[dt.date, list]:
    """Each finished day's roster for `side` ("me" or the opponent's name):
    that day's snapshot plus today's players (a correction since may have
    shown someone the snapshot missed), less anyone added after that day
    (`added_on`: my adds ledger; the opponent's adds are unknown)."""
    out = {}
    for d in (d for d in days if d < today):
        snap = state["day_rosters"].get(d.isoformat()) or {}
        if side == "me":
            saved = snap.get("me") or []
        else:
            them = snap.get("them") or {}
            saved = them.get("players", []) if them.get("team") == side else []
        players = {p["id"]: roster_mod.RosterPlayer(**p) for p in saved}
        for p in current:
            if not (added_on and added_on.get(p.id, d) > d):
                players.setdefault(p.id, p)
        out[d] = list(players.values())
    return out


def week_result(state: dict, week: int, players: list, league: dict, ctx) -> dict | None:
    """The week's result as report.result_view has it, scored from box scores
    with each day's roster (days before ctx.today only). None without an opponent."""
    opponent = current_opponent(state, week)
    if not opponent:
        return None
    days = [d for d in weeks.days(week) if d < ctx.today]
    finished = days == weeks.days(week)  # else the goalie minimum isn't settled yet
    added_on = {a["id"]: dt.date.fromisoformat(a["date"]) for a in state["adds"] if a["id"] is not None}

    def daily(side: str, current: list, added: dict | None) -> tuple[list[float], bool]:
        history = rosters_by_day(state, side, current, days, ctx.today, added)
        rows = [matchup._so_far(current, ctx, [d], history) for d in days]
        made = sum(r[2] for r in rows) >= MIN_GOALIE_GAMES_PER_WEEK or not finished
        return [round(sk + (g if made else 0.0), 2) for sk, g, _ in rows], made

    mine, my_min = daily("me", roster_mod.active(players), added_on)
    theirs, their_min = daily(opponent, teams.players(league, opponent), None)
    first, last = weeks.days(week)[0].isoformat(), weeks.days(week)[-1].isoformat()
    adds = [a for a in state["adds"] if first <= a["date"] <= last]
    skipped = sum(1 for d in state["decisions"]
                  if d["type"] == "add" and d["decision"] == "skip" and first <= d["date"] <= last)
    plan = state["results"].get(str(week), {}).get("first")
    return report.result_view(week, opponent, days, mine, theirs, [my_min, their_min], plan, adds, skipped,
                              report.budget_view(state["adds"], week), finished,
                              scorecard.line(scorecard.score(state["decisions"], ctx, ctx.today)))


def report_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
                build_context=context.build, week: int | None = None) -> None:
    """Last week's result, from noon on the new week's first day (before its
    plan): only then are all its games in the box scores. `week` reports that
    week now (a dry run's --report), finished or not."""
    date = now.astimezone(NHL_TIME).date()
    if week is None:
        this = weeks.week_of(date)
        week = this - 1 if this else (weeks.LAST_WEEK if date > SEASON_END else None)
        if not week or "final" in state["results"].get(str(week), {}) or not roster_mod.active(players):
            return
        if briefing.quiet(now) or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    view = week_result(state, week, players, league, build_context(date))
    if view is None:
        logger.info("No opponent known for week %s; no result to report", week)
        return
    outbox.send(report.result_text(view))
    png = _safe(charts.result_chart, view)
    if png:
        outbox.send_photo(png)
    if not view["finished"]:
        return  # a dry run's look at a week still in progress
    entry = state["results"].setdefault(str(week), {"opponent": view["opponent"]})
    entry["final"] = {"score": [round(x, 2) for x in view["final"]], "goalie_min": view["goalie_min"],
                      "at": now.isoformat(timespec="minutes"), "source": "box scores"}


def _week_schedule(week: int) -> dict[dt.date, list]:
    return {d: nhl_client.games_on(d) for d in weeks.days(week)}


@dataclass
class NextWeek:
    """The week after this one: the streamer horizon and the grid's second half."""
    week: int | None
    schedule: dict
    mine: matchup.TeamWeek | None


def next_week(week: int, players: list, wk: WeekInputs) -> NextWeek:
    if week >= weeks.LAST_WEEK:
        return NextWeek(None, {}, None)
    sched = _week_schedule(week + 1)
    return NextWeek(week + 1, sched, matchup.project(MY_TEAM, players, wk.ctx, sched, wk.lines, wk.starters, True))


def add_candidates(wk: WeekInputs, nxt: NextWeek) -> list:
    """Free agents to judge, including streamers whose games fall on nights my
    lineup has their slot open, this week and next (also scripts/explain_week.py)."""
    open_days = matchup.open_positions(wk.me) | (matchup.open_positions(nxt.mine) if nxt.mine else {})
    return matchup.shortlist(wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.available_from,
                             open_days, wk.schedule | nxt.schedule)


def streamer_text(view: dict, streams: list[dict], price, chosen: list = (), adds_left: int = 1) -> str:
    """The schedule chart's caption. With this week's adds spent, a pickup
    plays from Monday, so only next week's games count."""
    if not adds_left:
        if not streams:
            return "This week's adds are used. No free agent adds points in your open slots next week."
        lines = ["This week's adds are used; best streamer per position from Monday:"]
        for row, st in zip(view["streamers"], streams):
            m = st["move"]
            lines.append(f"{st['position']}: {m.add.name} ({m.add.team})" + (f" for {m.drop.name}" if m.drop else "")
                         + f": {row['slot_games']} games in open slots, net of the drop {st['next_gain']:+.1f} pts.")
        return "\n".join(lines)
    if not streams:
        return "No free agent adds points in your open slots this week or next."
    lines = ["Best streamer per position, this week + next:"]
    for row, st in zip(view["streamers"], streams):
        m = st["move"]
        verdict = ("recommended, see below" if row["recommended"]
                   else f"not recommended: {matchup.why_not(m, price, chosen)}")
        lines.append(f"{st['position']}: {m.add.name} ({m.add.team})" + (f" for {m.drop.name}" if m.drop else "")
                     + f": {row['slot_games']} games in open slots; net of the drop {m.week_gain:+.1f} pts this "
                     f"week, {st['next_gain']:+.1f} next ({verdict}).")
    return "\n".join(lines)


def week_views(state: dict, players: list, league: dict, week: int, wk: WeekInputs, nxt: NextWeek,
               ranked: list, moves: list) -> dict:
    """Every number the charts and the dashboard show, computed once: the
    decision map, the schedule grid with streamers, the add budget, and each
    shown add's week-by-week gain (keyed by report.move_key)."""
    price = wk.price if wk.max_moves else None
    decision = report.decision_view(week, ranked, moves, price)
    spans = [(week, current_opponent(state, week), wk.me, wk.them)]
    if nxt.week:
        opp = current_opponent(state, nxt.week)
        theirs = matchup.project(opp or "?", teams.players(league, opp) if opp else [], wk.ctx, nxt.schedule,
                                 wk.lines, wk.starters, True)
        spans.append((nxt.week, opp or "?", nxt.mine, theirs))
    streams = matchup.streamers(players, ranked, wk.ctx, wk.schedule, nxt.schedule, wk.lines, wk.starters, wk.so_far,
                                wk.available_from)
    schedule = report.schedule_view(players, spans, streams, moves)
    budget = report.budget_view(state["adds"], week)
    by_key = {report.move_key(m): m for m in ranked}
    by_key.update({report.move_key(m): m for m in moves + [st["move"] for st in streams]})
    chosen = {report.move_key(m) for m in moves}
    keys = [pt["key"] for pt in decision["points"]] + [r["key"] for r in schedule["streamers"]] + list(chosen)
    later = {w: _week_schedule(w) for w in range(week + 1, min(week + CHART_WEEKS, weeks.LAST_WEEK) + 1)}
    adds = {}
    for key in dict.fromkeys(keys):
        m = by_key[key]
        verdict = "Recommended" if key in chosen else "Not recommended: " + matchup.why_not(m, price, moves)
        gains = report.weekly_gains(players, m, wk.ctx, later, wk.lines, wk.starters)
        adds[key] = report.add_view(m, week, gains, budget, verdict)
    return {"decision": decision, "schedule": schedule, "budget": budget, "adds": adds,
            "streamer_text": streamer_text(schedule, streams, price, moves, wk.max_moves)}


def write_dashboard(views: dict, wk: WeekInputs, week: int, opponent: str, stance_text: str | None,
                    now: dt.datetime, dry_run: bool) -> Path:
    """site/data.json for the dashboard (site/index.html), published to GitHub
    Pages by the workflow. A dry run writes it next to its charts instead,
    with a copy of the page, to preview locally."""
    p_win = matchup.win_prob(wk.me, wk.them)
    data = {
        "generated": now.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
        "week": week, "opponent": opponent, "days": [wk.days[0].isoformat(), wk.days[-1].isoformat()],
        "summary": {
            "so_far": [wk.me.so_far, wk.them.so_far], "expected": [wk.me.expected, wk.them.expected],
            "yahoo": wk.yahoo_projected, "win": p_win, "stance": matchup.stance(p_win), "stance_text": stance_text,
            "adds_left": {"season": MAX_ADDS_PER_SEASON - wk.season_used, "week": wk.max_moves},
        },
        **{k: v for k, v in views.items() if k != "streamer_text"},
    }
    folder = CHART_DIR if dry_run else SITE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    if dry_run:
        (folder / "index.html").write_bytes((SITE_DIR / "index.html").read_bytes())
    path = folder / "data.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def _wait_text(keeper) -> str:
    if not keeper:
        return ""
    swap = f"{keeper.add.name} for {keeper.drop.name}" if keeper.drop else keeper.add.name
    return (f"\n\nCan wait until Monday, when your adds reset: {swap} ({keeper.week_gain:+.1f} pts this week, "
            f"{keeper.next_weeks:+.1f} over the next two). The risk: someone claims him first.")


def plan_due(record: dict | None, date: dt.date, week: int) -> bool:
    """The plan goes out once at the start of a week and once from mid-week."""
    if record is None:
        return True
    return date >= weeks.midweek(week) and "midweek" not in record


def record_plan(state: dict, week: int, opponent: str, me: matchup.TeamWeek, them: matchup.TeamWeek,
                now: dt.datetime) -> None:
    """Log the plan's numbers for the week's result to be checked against:
    the first plan of the week, and the latest."""
    plan = {"at": now.isoformat(timespec="minutes"),
            "expected": [round(me.expected, 2), round(them.expected, 2)],
            "so_far": [round(me.so_far, 2), round(them.so_far, 2)],
            "sd": [round(math.sqrt(me.variance), 2), round(math.sqrt(them.variance), 2)],
            "win": round(matchup.win_prob(me, them), 4)}
    entry = state["results"].setdefault(str(week), {"opponent": opponent})
    entry.setdefault("first", plan)
    entry["last"] = plan


def _plan_sent(state: dict, key: str, now: dt.datetime, midweek: bool) -> None:
    stamp = now.isoformat(timespec="minutes")
    record = state["weeks"].setdefault(key, {"sent": stamp})
    if midweek:
        record["midweek"] = stamp
    state["week_requested"] = False


def weekly_step(state: dict, players: list, league: dict, now: dt.datetime, force: bool, outbox: Outbox,
                build_context=context.build) -> None:
    """The matchup plan: from noon local on the week's first day, again from
    noon on its Wednesday (with the mid-week stance), and whenever you send
    /week or a matchup screenshot."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    requested = state["week_requested"]
    if week is None or not roster_mod.active(players):
        if requested:
            outbox.send("No roster yet." if not players else "No fantasy week in progress.")
        state["week_requested"] = False
        return
    key = str(week)
    if not (force or requested):
        if not plan_due(state["weeks"].get(key), date, week) or briefing.quiet(now) \
                or now.astimezone(briefing.LOCAL).time() < WEEKLY_PLAN_TIME:
            return
    is_midweek = date >= weeks.midweek(week)
    opponent = current_opponent(state, week)
    if not opponent:
        outbox.send(f"Week {week} is a playoff week: who are you playing? Send /opp Team Name.")
        _plan_sent(state, key, now, is_midweek)  # asked once; /opp then /week brings the plan
        return

    p = plan_moves(state, players, league, date, week, opponent, build_context)
    wk, nxt, ranked, moves = p.wk, p.nxt, p.ranked, p.moves
    ir_text = ir.text(p.ir_moves, ir.returning(players, wk.lines), _weakest(players, wk.ctx, wk.lines))
    midweek = None
    if is_midweek or wk.live:
        chase = None
        if matchup.stance(matchup.win_prob(wk.me, wk.them)) == "chase":
            chase = matchup.biggest_swing(ranked)
        recommended = chase is not None and report.move_key(chase) in {report.move_key(m) for m in moves}
        midweek = matchup.midweek_text(wk.me, wk.them, chase, wk.price if wk.max_moves else None, recommended,
                                       moves)
    outbox.send(_action_line(moves, ir_text, wk.max_moves) + "\n\n"
                + matchup.text(week, wk.days, wk.me, wk.them, teams.updated(league, opponent), wk.season_used,
                               wk.week_used, date, wk.yahoo_projected)
                + "\n" + goalie_line(players, ranked, wk.price if wk.max_moves else None)
                + (f"\n\n{midweek}" if midweek else "")
                + _wait_text(matchup.can_wait(ranked, moves, wk.price if wk.max_moves else None))
                + (f"\n\n{ir_text}" if ir_text else ""))
    # Only now: a run that fails before this (an NHL or DailyFaceoff outage) leaves
    # the plan due, so the next run sends it.
    _plan_sent(state, key, now, is_midweek)
    record_plan(state, week, opponent, wk.me, wk.them, now)
    if wk.live_check:  # one per screenshot day (a later plan the same day replaces it)
        entry = state["results"][str(week)]
        entry["live"] = [c for c in entry.get("live", []) if c["through"] != wk.live_check["through"]] + [wk.live_check]
    views = _safe(week_views, state, p.planned, league, week, wk, nxt, ranked, moves)
    if views:
        png = _safe(charts.decision_chart, views["decision"])
        if png:
            outbox.send_photo(png)
        png = _safe(charts.schedule_chart, views["schedule"])
        if png:
            outbox.send_photo(png, views["streamer_text"])
        decided = matchup.decided(matchup.win_prob(wk.me, wk.them))
        stance_text = midweek or (f"This week looks {decided}: save your adds for players worth keeping."
                                  if decided else None)
        _safe(write_dashboard, views, wk, week, opponent, stance_text, now, outbox.settings.dry_run)
    send_adds(state, p, moves, views, date, now, outbox)


@dataclass
class PlanMoves:
    """A week's add/drop search (the weekly plan's, and the evening's news check)."""
    wk: WeekInputs
    nxt: NextWeek
    ranked: list  # every move weighed
    moves: list  # the adds worth making now, best first
    ir_moves: list  # IR moves the adds assume (each frees a spot)
    planned: list  # the roster with them made
    open_spots: int  # spots open before any IR move


def plan_moves(state: dict, players: list, league: dict, date: dt.date, week: int, opponent: str,
               build_context) -> PlanMoves:
    wk = week_inputs(date, week, players, league, state, build_context, opponent)
    ir_moves = ir.moves(players, wk.lines)
    planned = ir.after(players, ir_moves)  # the adds assume the IR moves are made: a free spot each
    nxt = next_week(week, players, wk)
    candidates = add_candidates(wk, nxt)
    ranked = matchup.candidate_moves(planned, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters,
                                     wk.future, wk.weeks_after, wk.available_from, wk.so_far, wk.hold_days,
                                     wk.later_weight)
    wk.price = add_price(state, week, wk, ranked, date)
    moves = []
    if wk.max_moves and wk.price is not None:
        moves = matchup.best_moves(planned, wk.them, wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future,
                                   wk.weeks_after, wk.max_moves, wk.price, wk.available_from, wk.so_far,
                                   candidates, ranked, wk.hold_days)
    return PlanMoves(wk, nxt, ranked, moves, ir_moves, planned,
                     max(0, matchup.ACTIVE_SPOTS - len(roster_mod.active(players))))


def goalie_line(players: list, ranked: list, price) -> str:
    """Two goalies or three, by value: the best move that changes the count,
    against what an add costs (the same moves the plan weighed)."""
    goalies = sum(p.is_goalie for p in roster_mod.active(players))
    if goalies <= matchup.MIN_GOALIES:
        options = [m for m in ranked if m.add.is_goalie and not (m.drop and m.drop.is_goalie)]
        what, keep = "a third", "two are enough for now"
    else:
        options = [m for m in ranked if m.drop and m.drop.is_goalie and not m.add.is_goalie]
        what, keep = "two (a skater for your weakest goalie)", "keep three"
    best = max(options, key=lambda m: m.value, default=None)
    head = f"Goalies: {goalies}, by value."
    if best is None:
        return f"{head} No move to {what} came up."
    swap = f"{best.add.name}" + (f" for {best.drop.name}" if best.drop else "")
    if price is None:
        return f"{head} Best move to {what}: {swap}, {100 * best.value:+.1f} win-pts (no adds left this week)."
    verdict = "worth an add" if best.value >= price.lam else keep
    return (f"{head} Best move to {what}: {swap}, {100 * best.value:+.1f} win-pts vs the "
            f"{100 * price.lam:.1f} an add costs: {verdict}.")


def _action_line(moves: list, ir_text: str, adds_left: int = 1) -> str:
    """The plan's first line: what to do now."""
    if not moves:
        head = ("No adds left this week (they reset Monday)." if not adds_left
                else "No add is worth one of yours right now.")
        return head + (" See the IR note below." if ir_text else "")
    adds = "; ".join(f"add {m.add.name}" + (f" for {m.drop.name}" if m.drop else "")
                     + f" (win {matchup._pct(m.win_before)} -> {matchup._pct(m.win_after)})" for m in moves)
    return f"Do now: {adds}. Details below."


def send_adds(state: dict, p: PlanMoves, moves: list, views: dict | None, date: dt.date, now: dt.datetime,
              outbox: Outbox) -> None:
    """Each add as its own message with Done / Taken / Skip, and its chart."""
    for i, move in enumerate(moves):
        rec_id = f"add-{date.isoformat()}-{now:%H%M}-{i}"
        buttons = [("Done", f"done:{rec_id}"), ("Other drop", f"other:{rec_id}"), ("Taken", f"taken:{rec_id}"),
                   ("Skip", f"skip:{rec_id}")]
        view = views and views["adds"].get(report.move_key(move))
        png = _safe(charts.add_chart, view) if view else None
        # An add without a drop fills a spot already open, else the next IR move's.
        k = sum(m.drop is None for m in moves[:i]) - p.open_spots
        opens = p.ir_moves[k] if move.drop is None and 0 <= k < len(p.ir_moves) else None
        text = matchup.move_text(move, opens.player.name if opens else None)
        message_id = (outbox.send_photo(png, text, buttons) if png else outbox.send(text, buttons))
        state["pending"][rec_id] = {"type": "add", "date": date.isoformat(), "add": asdict(move.add),
                                    "drop": move.drop.id if move.drop else None,
                                    "drop_name": move.drop.name if move.drop else None, "message_id": message_id,
                                    "ir": {str(opens.player.id): opens.slot} if opens else {}}


def news_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
              build_context=context.build) -> None:
    """Between plans, once an evening: an add that now clears the price and
    hasn't been suggested this week (an injury, a confirmed goalie, a hot
    free agent since the plan). Not on a plan's day; only with adds left."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    games = nhl_client.games_on(date) if week else []
    if not games or not roster_mod.active(players) or state["news_checked"] == date.isoformat():
        return
    if now < briefing.briefing_due(date, games[0].start) or briefing.quiet(now) or now >= games[0].start:
        return
    record = state["weeks"].get(str(week), {})
    if any(record.get(k, "")[:10] == now.date().isoformat() for k in ("sent", "midweek")):
        return  # the plan went out today
    days = weeks.days(week)
    if not matchup.max_moves(*matchup.adds_used(state["adds"], days)) or not current_opponent(state, week):
        return
    state["news_checked"] = date.isoformat()
    first, last = days[0].isoformat(), days[-1].isoformat()
    offered = {(r["add"]["id"], r["drop"]) for r in state["pending"].values() if r["type"] == "add"}
    offered |= {(d.get("add"), d.get("drop")) for d in state["decisions"]
                if d["type"] == "add" and first <= d["date"] <= last}
    p = plan_moves(state, players, league, date, week, current_opponent(state, week), build_context)
    new = [m for m in p.moves if (m.add.id, m.drop.id if m.drop else None) not in offered]
    if not new:
        logger.info("News check %s: no new add clears the price", date)
        return
    outbox.send(f"News since the plan: {'an add now clears' if len(new) == 1 else f'{len(new)} adds now clear'} "
                f"the price (you're at {matchup._pct(matchup.win_prob(p.wk.me, p.wk.them))} to win).")
    send_adds(state, p, new, None, date, now, outbox)
