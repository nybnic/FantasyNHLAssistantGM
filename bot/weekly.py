"""The weekly plan and everything built on its numbers: the add/drop search, the evening
news check, the dashboard, the results log and Monday's report of last week.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path


from clients import dfo_lines, goalie_client, nhl_client
from config.league import (MAX_ADDS_PER_SEASON, MAX_ADDS_PER_WEEK, MIN_GOALIE_GAMES_PER_WEEK, MY_TEAM, POST_DRAFT_WAIVERS_CLEAR,
                           SEASON_END)
from engine import addprice, briefing, ir, matchup, report, scorecard, season
from engine import plan as plan_mod
from league import teams, weeks
from league import roster as roster_mod
from model import context
from notify import charts, snapshot
from bot import board, messages
from bot.common import NHL_TIME, CHART_DIR, Outbox, _safe, current_opponent, free_agents, _weakest

logger = logging.getLogger(__name__)

SITE_DIR = Path("site")  # the dashboard: index.html (ours) and data.json (written by each plan run)
DASHBOARD_URL = "https://nybnic.github.io/FantasyNHLAssistantGM/"  # GitHub Pages, published from site/




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
    strengths: dict | None = None  # team -> (mean, sd) of a typical week's points (team_strengths)
    ahead: list = field(default_factory=list)  # matchup.WeekAhead: the next weeks vs their opponents (plan_moves)


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
    ctx.replacement_xfp = _safe(replacement_xfp, pool, ctx, default={})
    max_moves = matchup.max_moves(season_used, week_used)
    available_from = waiver_days(pool, league, date)
    if not max_moves:
        available_from = after_this_week(available_from, pool, days)
    strengths = team_strengths(players, league, ctx, future, lines, starters)
    tau = league_tau(strengths)
    sigma_week = math.sqrt((me.variance + them.variance) * 7 / max(remaining, 1))
    return WeekInputs(
        ctx=ctx, days=days, schedule=schedule, future=future, lines=lines, starters=starters,
        me=me, them=them, tau=tau, strengths=strengths, later_weight=addprice.later_weight(sigma_week, tau),
        pace=addprice.pace(MAX_ADDS_PER_SEASON - season_used, week),
        so_far=mine, live=is_live, yahoo_projected=projected,
        live_check={"through": date.isoformat(), "yahoo": list(live["score"]), "box": box,
                    "yahoo_projected": list(live["projected"] or [])} if is_live else None,
        hold_days=max(0, round(7 * matchup.hold_weeks(MAX_ADDS_PER_SEASON - season_used, week))
                      - sum(d >= date for d in days)),
        pool=pool,
        season_used=season_used, week_used=week_used,
        max_moves=max_moves,
        weeks_after=weeks.LAST_WEEK - week,
        available_from=available_from,
    )


def replacement_xfp(pool: list, ctx) -> dict[str, float]:
    """A replacement-level player's points per game by group ("C" for
    forwards, "D"): the best REPLACEMENT_SAMPLE free agents' average. What
    a missed game by one of mine is streamed at (matchup.durability)."""
    out = {}
    for group, is_d in (("C", False), ("D", True)):
        xs = sorted((ctx.skater(p.id, group).xfp for p in pool
                     if not p.is_goalie and (p.positions == ["D"]) == is_d), reverse=True)[:matchup.REPLACEMENT_SAMPLE]
        if xs:
            out[group] = sum(xs) / len(xs)
    return out


def after_this_week(available_from: dict, pool: list, days: list[dt.date]) -> dict[int, dt.date]:
    """With this week's adds spent, nobody joins before Monday: every free
    agent's first day moves to next week's first (or his waiver day, if later)."""
    monday = days[-1] + dt.timedelta(days=1)
    return {p.id: max(available_from.get(p.id, monday), monday) for p in pool}


def team_strengths(players: list, league: dict, ctx, future: dict, lines: dict, starters: dict
                   ) -> dict[str, tuple[float, float]]:
    """Team -> (mean, sd) of its points in a typical week: its best lineup over
    the next full week (long run: durability in), for every team whose roster
    is known (MIN_KNOWN_ROSTER players), mine included."""
    week = {d: future[d] for d in sorted(future)[:7]}
    if len(week) < 7:
        return {}
    rosters = {MY_TEAM: players} | {t: teams.players(league, t) for t in league["teams"]}
    out = {}
    for team, roster in rosters.items():
        if len(roster_mod.active(roster)) >= MIN_KNOWN_ROSTER:
            projected = matchup.project(team, roster, ctx, week, lines, starters, True)
            out[team] = (projected.expected, math.sqrt(projected.variance))
    return out


def league_tau(strengths: dict[str, tuple[float, float]]) -> float:
    """The spread of matchup margins: how far apart two of the league's teams
    usually project in a week (the margin's spread is sqrt(2) times the teams'),
    at the size projected margins realize (matchup.MARGIN_REALIZES: a typical
    week's margin is known as well as this week's is on its Monday)."""
    totals = [mean for mean, _ in strengths.values()]
    if len(totals) < 4:
        return addprice.DEFAULT_TAU
    mean = sum(totals) / len(totals)
    return matchup.MARGIN_REALIZES * math.sqrt(2 * sum((t - mean) ** 2 for t in totals) / (len(totals) - 1))


def season_odds(state: dict, wk: WeekInputs, week: int) -> tuple[season.SeasonOdds | None, str]:
    """Playoff and title odds and this week's leverage (engine/season.py),
    from the latest standings (state["standings"], League > Standings
    screenshots), else from an even start, with this week's pairings when an
    All Matchups screenshot gave them; and a note on the standings' age."""
    odds = season.simulate(wk.strengths, MY_TEAM, week, matchup.win_prob(wk.me, wk.them),
                           (state.get("standings") or {}).get("teams"),
                           pairs=state.get("league_weeks", {}).get(str(week), {}).get("pairs"))
    through = (state.get("standings") or {}).get("week")
    note = (" (No standings yet: everyone starts even.)" if through is None
            else f" (Standings through week {through}: send a new screenshot.)" if through < week - 1 else "")
    return odds, note


def add_price(state: dict, week: int, wk: WeekInputs, ranked: list, date: dt.date) -> addprice.AddPrice | None:
    """The add's price, solved over this week's candidates and the ones logged
    in earlier weeks (state["add_pools"]). With this week's adds spent, its
    candidates can't join until Monday, so the logged pools stay as they are
    (the price then judges Monday's moves)."""
    if wk.pace is None:
        return None
    if wk.max_moves:
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


def weeks_ahead(state: dict, league: dict, week: int, roster: list, wk: WeekInputs) -> list[matchup.WeekAhead]:
    """The next matchup.AHEAD_WEEKS weeks as my roster would play them against
    each week's opponent (the schedule in config/league.py; playoff weeks'
    only once named with /opp)."""
    out = []
    for w in range(week + 1, min(week + matchup.AHEAD_WEEKS, weeks.LAST_WEEK) + 1):
        sched = {d: games for d, games in wk.future.items() if weeks.week_of(d) == w}
        if not sched:
            break
        opponent = current_opponent(state, w)
        mine = matchup.project(MY_TEAM, roster, wk.ctx, sched, wk.lines, wk.starters, True)
        theirs = (matchup.project(opponent, teams.players(league, opponent), wk.ctx, sched, wk.lines, wk.starters,
                                  True) if opponent and teams.players(league, opponent) else None)
        out.append(matchup.week_ahead(w, sched, mine, theirs, opponent, w - week))
    return out


def add_candidates(wk: WeekInputs, nxt: NextWeek) -> list:
    """Free agents to judge, including streamers whose games fall on nights my
    lineup has their slot open, this week and next (also scripts/explain_week.py)."""
    open_days = matchup.open_positions(wk.me) | (matchup.open_positions(nxt.mine) if nxt.mine else {})
    return matchup.shortlist(wk.pool, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.available_from,
                             open_days, wk.schedule | nxt.schedule)


def week_views(state: dict, players: list, league: dict, week: int, wk: WeekInputs, nxt: NextWeek,
               ranked: list, moves: list, plan: list = ()) -> dict:
    """The dashboard's schedule grid (this week and next, with the best
    streamer per position) and the add budget."""
    spans = [(week, current_opponent(state, week), wk.me, wk.them)]
    if nxt.week:
        opp = current_opponent(state, nxt.week)
        theirs = matchup.project(opp or "?", teams.players(league, opp) if opp else [], wk.ctx, nxt.schedule,
                                 wk.lines, wk.starters, True)
        spans.append((nxt.week, opp or "?", nxt.mine, theirs))
    streams = matchup.streamers(players, ranked, wk.ctx, wk.schedule, nxt.schedule, wk.lines, wk.starters, wk.so_far,
                                wk.available_from)
    plan_entries, after = plan_weeks(players, plan, wk, nxt)
    return {"schedule": report.schedule_view(players, spans, streams, moves, plan_entries, after),
            "budget": report.budget_view(state["adds"], week)}


def plan_weeks(players: list, plan: list, wk: WeekInputs, nxt: NextWeek) -> tuple[list[dict], list]:
    """My roster with the whole plan made, each move from its day (an add
    counts from then, his drop until then), projected over this week and next:
    each planned add's grid row, and the weeks for the grid's "with the plan"."""
    if not plan:
        return [], []
    roster, joins, leaves = players, {}, {}
    for q in plan:
        m = q.move
        roster = matchup._swap(roster, m.add, None, m.ir_slot or roster_mod.BENCH)
        joins[m.add.id] = max(q.when, m.plays_from or q.when)
        if m.drop:
            leaves[m.drop.id] = joins[m.add.id]
    this_week = matchup.project(MY_TEAM, roster, wk.ctx, wk.schedule, wk.lines, wk.starters, joins=joins,
                                so_far=wk.so_far, leaves=leaves)
    next_week_ = (matchup.project(MY_TEAM, roster, wk.ctx, nxt.schedule, wk.lines, wk.starters, True, joins,
                                  leaves=leaves) if nxt.week else None)
    entries = [{"move": q.move, "this_week": this_week, "next_week": next_week_, "when": q.when} for q in plan]
    return entries, [this_week, next_week_]


DASHBOARD_MOVES = 60  # the move table's rows (the Board keeps every one)


def dashboard_data(b: dict, p: PlanMoves, views: dict | None, date: dt.date, changed: str) -> dict:
    """site/data.json: the Board's numbers plus the plan in the message's own
    words (bot/messages.py), so the dashboard, its Telegram card and the plan
    message can't disagree."""
    wk = p.wk
    h = b["header"]
    weeks_shown = [{"week": b["week"], "opponent": b["opponent"], "win": h["win"],
                    "margin": round(matchup.margin(wk.me, wk.them), 1)}]
    weeks_shown += [{"week": w["week"], "opponent": w["opponent"], "win": w["win"],
                     "margin": w["margin"]} for w in h["ahead"]]
    return {
        "generated": b["at"], "date": b["date"], "week": b["week"], "opponent": b["opponent"], "days": b["days"],
        "header": h,
        "plan": messages.plan_items(p.plan, date),
        "plan_empty": messages.empty_text(wk.max_moves),
        "changed": changed,
        "this_week_line": messages.this_week_line(matchup.biggest_swing(p.ranked), p.plan,
                                                  wk.price if wk.max_moves else None, wk.max_moves),
        "weeks": weeks_shown,
        "moves": sorted(b["moves"], key=lambda r: -r["value"])[:DASHBOARD_MOVES],
        **(views or {}),
    }


def write_dashboard(data: dict, dry_run: bool) -> Path:
    """site/data.json for the dashboard (site/index.html), published to GitHub
    Pages by the workflow. A dry run writes it next to its charts instead,
    with a copy of the page, to preview locally."""
    folder = CHART_DIR if dry_run else SITE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    if dry_run:
        (folder / "index.html").write_bytes((SITE_DIR / "index.html").read_bytes())
    path = folder / "data.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def save_board(p: PlanMoves, week: int, opponent: str, now: dt.datetime, date: dt.date,
               odds_note: tuple | None, dry_run: bool, changed: str = "") -> dict:
    """The plan's Board (bot/board.py) to state/board.json (a dry run's next
    to its charts); returns it."""
    odds, note = odds_note or (None, "")
    b = board.build(p, week, opponent, now, date, odds, note, changed)
    board.save(b, CHART_DIR / "board.json" if dry_run else board.BOARD_FILE)
    return b


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


def record_league(state: dict, league: dict, week: int, opponent: str, wk: WeekInputs, now: dt.datetime) -> None:
    """Our projection of all 16 teams' weeks at the week's first plan, kept
    next to Yahoo's from All Matchups screenshots (league_weeks), so both can
    be checked against the results: 16 team-weeks a week, not 2."""
    entry = state["league_weeks"].setdefault(str(week), {"pairs": [], "scores": {}})
    if "ours" in entry:
        return
    projected = {MY_TEAM: wk.me, opponent: wk.them}
    for team in league["teams"]:
        if team not in projected:
            projected[team] = matchup.project(team, teams.players(league, team), wk.ctx, wk.schedule, wk.lines,
                                              wk.starters)
    through = teams.moves_through(league)
    entry["ours"] = {
        "at": now.isoformat(timespec="minutes"), "moves_through": through.isoformat() if through else None,
        "teams": {t: {"expected": round(w.expected, 2), "sd": round(math.sqrt(w.variance), 2),
                      "so_far": round(w.so_far, 2)} for t, w in projected.items()},
    }


def _plan_sent(state: dict, key: str, now: dt.datetime, midweek: bool) -> None:
    stamp = now.isoformat(timespec="minutes")
    record = state["weeks"].setdefault(key, {"sent": stamp})
    if midweek:
        record["midweek"] = stamp
    state["week_requested"] = False
    state["plan_check"] = False


def weekly_step(state: dict, players: list, league: dict, now: dt.datetime, force: bool, outbox: Outbox,
                build_context=context.build) -> None:
    """The plan message: from noon local on the week's first day, again from
    noon on its Wednesday, and whenever you send /week."""
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
    run_plan(state, players, league, now, outbox, build_context, "full")
    # Only now: a run that fails before this (an NHL or DailyFaceoff outage) leaves
    # the plan due, so the next run sends it.
    _plan_sent(state, key, now, is_midweek)


def plan_check_step(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox,
                    build_context=context.build) -> None:
    """The plan again between plan messages: right after a screenshot or a
    Taken or Skip tap (a reply either way), and once an evening before the
    first puck (a message only if the plan changed: an injury, a confirmed
    goalie, a player taken)."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    if week is None or not roster_mod.active(players) or not current_opponent(state, week):
        state["plan_check"] = False
        return
    if state.get("plan_check"):
        run_plan(state, players, league, now, outbox, build_context, "check")
        state["plan_check"] = False
        return
    games = nhl_client.games_on(date)
    if not games or state["news_checked"] == date.isoformat():
        return
    if now < briefing.briefing_due(date, games[0].start) or briefing.quiet(now) or now >= games[0].start:
        return
    record = state["weeks"].get(str(week), {})
    if any(record.get(k, "")[:10] == now.date().isoformat() for k in ("sent", "midweek")):
        return  # the plan went out today
    state["news_checked"] = date.isoformat()
    run_plan(state, players, league, now, outbox, build_context, "quiet")


@dataclass
class PlanMoves:
    """A week's add/drop search and its plan (engine/plan.py)."""
    wk: WeekInputs
    nxt: NextWeek
    ranked: list  # every move weighed, on today's roster
    plan: list  # plan.Planned: the moves to make and when, best first
    ir_moves: list  # IR moves the adds assume (each frees a spot)
    planned: list  # the roster with them made
    open_spots: int  # spots open before any IR move
    held: bool = False  # keepers that do nothing this week wait for the mid-week plan

    @property
    def moves(self) -> list:
        return [p.move for p in self.plan]


def plan_moves(state: dict, players: list, league: dict, date: dt.date, week: int, opponent: str,
               build_context, extra_ids: set | frozenset = frozenset(), exclude: set | frozenset = frozenset()
               ) -> PlanMoves:
    """`extra_ids`: free agents to judge besides the shortlist (the plan's adds,
    explain_week --add); `exclude`: move keys not to plan (skipped)."""
    wk = week_inputs(date, week, players, league, state, build_context, opponent)
    ir_moves = ir.moves(players, wk.lines)
    planned = ir.after(players, ir_moves)  # the adds assume the IR moves are made: a free spot each
    nxt = next_week(week, players, wk)
    candidates = add_candidates(wk, nxt)
    candidates += [p for p in wk.pool if p.id in extra_ids and p not in candidates]
    wk.ahead = weeks_ahead(state, league, week, planned, wk)
    ranked = matchup.candidate_moves(planned, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters,
                                     wk.future, wk.weeks_after, wk.available_from, wk.so_far, wk.hold_days,
                                     wk.later_weight, wk.ahead)
    wk.price = add_price(state, week, wk, ranked, date)
    held = matchup.holds_keepers(date, week, matchup.win_prob(wk.me, wk.them))
    season_left = MAX_ADDS_PER_SEASON - wk.season_used - wk.max_moves
    monday_slots = min(MAX_ADDS_PER_WEEK, max(season_left, 0)) if week < weeks.LAST_WEEK else 0
    composed = plan_mod.search(planned, wk.them, candidates, wk.ctx, wk.schedule, wk.lines, wk.starters, wk.future,
                               wk.weeks_after, wk.price, wk.max_moves, date, wk.days[-1] + dt.timedelta(days=1),
                               weeks.midweek(week), held, wk.available_from, wk.so_far, wk.hold_days, wk.ahead,
                               monday_slots, exclude, ranked)
    composed = plan_mod.time_moves(composed, planned, wk.ctx, wk.schedule, nxt.schedule, wk.lines, wk.starters,
                                   wk.so_far, wk.days[-1] + dt.timedelta(days=1))
    return PlanMoves(wk, nxt, ranked, composed, ir_moves, planned,
                     max(0, matchup.ACTIVE_SPOTS - len(roster_mod.active(players))), held)


def skipped(state: dict, date: dt.date) -> set[str]:
    """Moves Nico skipped in the last week: not planned again."""
    since = (date - dt.timedelta(days=7)).isoformat()
    return {f"{d['add']}:{d.get('drop') or 0}" for d in state["decisions"]
            if d["type"] == "add" and d["decision"] == "skip" and d.get("add") and d["date"] >= since}


def _stored(p: plan_mod.Planned, old: dict | None) -> dict:
    m = p.move
    return {"key": p.key, "add": {"id": m.add.id, "name": m.add.name, "team": m.add.team,
                                  "positions": m.add.positions},
            "drop": {"id": m.drop.id, "name": m.drop.name} if m.drop else None,
            "when": p.when.isoformat(), "why": p.why, "value": round(m.value, 4),
            "rec_id": (old or {}).get("rec_id"), "message_id": (old or {}).get("message_id")}


def run_plan(state: dict, players: list, league: dict, now: dt.datetime, outbox: Outbox, build_context,
             mode: str) -> PlanMoves:
    """Compose the plan, keep the one Nico has seen unless it must change
    (engine/plan.decide), and say what's needed. `mode`: "full" (the plan
    message), "check" (a screenshot or tap: the score, or the change),
    "quiet" (the evening: only a change)."""
    date = now.astimezone(NHL_TIME).date()
    week = weeks.week_of(date)
    opponent = current_opponent(state, week)
    old = state.get("plan") or {}
    mine = {p.id for p in players}
    old_open = [o for o in old.get("moves", []) if o["add"]["id"] not in mine]  # the rest were made
    skips = skipped(state, date)
    p = plan_moves(state, players, league, date, week, opponent, build_context,
                   {o["add"]["id"] for o in old_open}, skips)
    wk = p.wk
    current = {plan_mod.key(m): m for m in p.ranked}
    pool = {x.id for x in wk.pool}
    problems = {}
    for o in old_open:
        if o["key"] in skips:
            problems[o["key"]] = "you skipped it"
        elif o["add"]["id"] not in pool:
            problems[o["key"]] = "someone took him"
        elif o.get("drop") and o["drop"]["id"] not in mine:
            problems[o["key"]] = f"{o['drop']['name']} is no longer on your roster"
    verdict = plan_mod.decide(old_open, p.plan, current, problems, wk.price)
    if verdict.keep and old_open:
        dated = {q.key: q for q in p.plan}
        p.plan = sorted((dated.get(o["key"]) or plan_mod.Planned(current[o["key"]],
                                                                  max(date, dt.date.fromisoformat(o["when"])),
                                                                  o.get("why", "now"))
                         for o in old_open), key=lambda q: q.when)
    by_key = {o["key"]: o for o in old_open}
    changed = verdict.reason if not verdict.keep and old_open else ""
    odds_note = _safe(season_odds, state, wk, week)
    b = _safe(save_board, p, week, opponent, now, date, odds_note, outbox.settings.dry_run, changed)
    views = _safe(week_views, state, p.planned, league, week, wk, p.nxt, p.ranked, p.moves, p.plan)
    data = _safe(dashboard_data, b, p, views, date, changed) if b else None
    if data:
        _safe(write_dashboard, data, outbox.settings.dry_run)

    if mode == "full":
        ir_text = ir.text(p.ir_moves, ir.returning(players, wk.lines), _weakest(players, wk.ctx, wk.lines))
        odds = odds_note[0] if odds_note else None
        outbox.send(messages.plan_text(
            week, opponent, wk.me, wk.them, p.plan, date, wk.max_moves, wk.season_used, wk.ahead,
            matchup.biggest_swing(p.ranked), wk.price if wk.max_moves else None, odds, wk.yahoo_projected,
            [f"Changed since the last plan: {changed}." if changed else "",
             messages.league_warning(teams.moves_through(league), teams.updated(league, opponent), date),
             ir_text]))
        record_plan(state, week, opponent, wk.me, wk.them, now)
        _safe(record_league, state, league, week, opponent, wk, now)
        png = _safe(snapshot.card_png, data) if data else None
        if png:
            outbox.send_photo(png, f"Everything weighed: {DASHBOARD_URL}")
        elif views and (png := _safe(charts.schedule_chart, views["schedule"])):  # no browser: the old chart
            outbox.send_photo(png, f"Schedule, this week and next. Everything weighed: {DASHBOARD_URL}")
    elif changed:
        outbox.send(messages.change_text(old_open, p.plan, changed, date, wk.max_moves))
    elif p.plan and not old_open:  # moves where there were none: say so before any card
        outbox.send(messages.new_plan_text(p.plan, date, wk.max_moves, *((wk.me, wk.them) if mode == "check" else ())))
    elif mode == "check":
        outbox.send(messages.score_text(wk.me, wk.them, p.plan, date))
    if wk.live_check:  # one per screenshot day (a later plan the same day replaces it)
        entry = state["results"].setdefault(str(week), {"opponent": opponent})
        entry["live"] = [c for c in entry.get("live", []) if c["through"] != wk.live_check["through"]] + [wk.live_check]

    keys = {q.key for q in p.plan}
    label = ("Replaced: " + ("; ".join(messages.swap(q.move) for q in p.plan) or "no add"))[:60]
    for o in old_open:  # replaced cards lose their buttons (pending keeps them for the scorecard)
        if o["key"] not in keys and o.get("message_id"):
            outbox.mark(o["message_id"], label)
    if state.get("plan") is None:  # the first plan: the earlier system's cards this week are replaced
        for rec in state["pending"].values():
            if (rec["type"] == "add" and rec.get("message_id") and rec["date"] >= wk.days[0].isoformat()
                    and rec["add"]["id"] not in mine):  # a card whose add was made isn't replaced
                outbox.mark(rec["message_id"], label)
    stored = [_stored(q, by_key.get(q.key)) for q in p.plan]
    send_cards(state, p, stored, date, now, outbox)
    state["plan"] = {"week": week, "at": now.isoformat(timespec="minutes"), "moves": stored}
    return p


def send_cards(state: dict, p: PlanMoves, stored: list[dict], date: dt.date, now: dt.datetime,
               outbox: Outbox) -> None:
    """Each planned move due today as its own message with Done / Taken /
    Skip, once (`stored`: the plan as kept, its card's ids written back)."""
    due = [(q, s) for q, s in zip(p.plan, stored) if q.when <= date]
    for i, (q, s) in enumerate(due):
        if s["rec_id"]:
            continue  # its card is out
        move = q.move
        rec_id = f"add-{date.isoformat()}-{now:%H%M}-{move.add.id}"  # unique even for two runs in a minute
        buttons = [("Done", f"done:{rec_id}"), ("Other drop", f"other:{rec_id}"), ("Taken", f"taken:{rec_id}"),
                   ("Skip", f"skip:{rec_id}")]
        if move.ir_slot:  # a stash drops nobody now
            buttons = [b for b in buttons if b[0] != "Other drop"]
        # An add without a drop fills a spot already open, else the next IR move's
        # (an IR stash fills his IR slot instead).
        k = sum(m.drop is None and not m.ir_slot for m in [d[0].move for d in due[:i]]) - p.open_spots
        opens = (p.ir_moves[k] if move.drop is None and not move.ir_slot and 0 <= k < len(p.ir_moves)
                 else None)
        text = messages.make_it(q, date) + "\n" + matchup.move_text(move, opens.player.name if opens else None)
        message_id = outbox.send(text, buttons)
        s["rec_id"], s["message_id"] = rec_id, message_id
        state["pending"][rec_id] = {"type": "add", "date": date.isoformat(), "add": asdict(move.add),
                                    "drop": move.drop.id if move.drop else None,
                                    "drop_name": move.drop.name if move.drop else None, "message_id": message_id,
                                    "ir": {str(opens.player.id): opens.slot} if opens else {},
                                    "add_slot": move.ir_slot}
