"""The plan's words, from one source: the composed plan (engine/plan.py) and
the week's numbers. Telegram is read on a phone, so the plan message leads
with what to do and stays a few lines (docs/plan-2026-10-09.md, step 3); the
full table lives on the Board (scripts/explain_week.py) and the dashboard.
"""
from __future__ import annotations

import datetime as dt

from config.league import MAX_ADDS_PER_SEASON, MIN_GOALIE_GAMES_PER_WEEK
from engine import matchup
from engine.plan import Planned

GOALIE_WARN_BELOW = 0.9  # the goalie minimum gets a line only when at risk


def _pct(p: float) -> str:
    return matchup._pct(p)


def swap(m: matchup.Move) -> str:
    if m.ir_slot:
        return f"{m.add.name} into your empty {m.ir_slot} slot"
    return m.add.name + (f" for {m.drop.name}" if m.drop else " into your open spot")


def _detail(p: Planned) -> str:
    """Why this move, in a few words: its win odds this week when it moves
    them, else what it's worth; then why it's made when it is."""
    m = p.move
    if m.win_after - m.win_before >= 0.01:
        head = f"win {_pct(m.win_before)} -> {_pct(m.win_after)}"
        if m.later_value + sum(m.ahead_wins) >= 0.01:
            head += f", +{100 * m.value:.0f} win-pts in all"
    else:
        head = f"+{100 * m.value:.0f} win-pts, a keeper"
    when = {"spare": "on this week's add, which would go unused",
            "monday": "it does nothing this week, so it takes next week's add",
            "held": "if the week holds: the add stays free to chase with until then"}.get(p.why)
    claim = f"; a claim, plays from {m.plays_from:%a %d}" if m.plays_from else ""
    return head + (f"; {when}" if when else "") + claim


def plan_lines(plan: list[Planned], today: dt.date, adds_left_week: int) -> list[str]:
    """What to do and when, one line per day."""
    if not plan:
        if not adds_left_week:
            return ["No adds left this week, and nothing worth one planned for Monday."]
        return ["No add is worth one of yours now."]
    lines = []
    for p in plan:
        day = "Today" if p.when <= today else f"{p.when:%a %d}"
        lines.append(f"{day}: {swap(p.move)} ({_detail(p)}).")
    if all(p.when > today for p in plan) and adds_left_week:
        lines.insert(0, "Nothing to add today.")
    return lines


def plan_text(week: int, opponent: str, me: matchup.TeamWeek, them: matchup.TeamWeek, plan: list[Planned],
              today: dt.date, adds_left_week: int, season_used: int, ahead: list[matchup.WeekAhead] = (),
              this_week_best: matchup.Move | None = None, price=None, odds=None,
              yahoo: list | None = None, warnings: list[str] = ()) -> str:
    """The plan message: the matchup, what to do, the weeks ahead, the budget."""
    yahoo_text = f" (Yahoo {yahoo[0]:.0f}-{yahoo[1]:.0f})" if yahoo else ""
    head = (f"Week {week} vs {opponent}: " + (f"{me.so_far:.0f}-{them.so_far:.0f} so far, " if me.so_far or them.so_far
                                               else "")
            + f"expect {me.expected:.0f}-{them.expected:.0f}{yahoo_text}. Win {_pct(matchup.win_prob(me, them))}.")
    lines = [head] + plan_lines(plan, today, adds_left_week)
    planned = {(p.move.add.id, p.move.drop.id if p.move.drop else None) for p in plan}
    if (this_week_best and price and adds_left_week
            and (this_week_best.add.id, this_week_best.drop.id if this_week_best.drop else None) not in planned):
        m = this_week_best
        head = f"For this week alone, the best is {swap(m)}: win {_pct(m.win_before)} -> {_pct(m.win_after)}"
        if m.value >= price.lam:
            lines.append(f"{head}. Worth an add too, but it clashes with the plan's.")
        else:
            later = m.value - (m.win_after - m.win_before)
            cost = (f", as dropping {m.drop.name} costs {100 * -later:.0f} later" if m.drop and later < 0 else "")
            lines.append(f"{head}. Not worth an add: {100 * m.value:.0f} win-pts in all{cost}, against the "
                         f"{100 * price.lam:.0f} an add costs.")
    if ahead:
        lines.append("Ahead: " + ", ".join(
            f"wk {w.week} vs {w.opponent} {_pct(matchup._phi(w.margin / w.sd))}" if w.margin is not None
            else f"wk {w.week} (opponent not known)" for w in ahead) + ".")
    budget = f"Adds: {adds_left_week} this week, {MAX_ADDS_PER_SEASON - season_used} this season."
    if odds:
        budget += f" Playoffs {odds.playoffs:.0%}, title {odds.title:.0%}."
    lines.append(budget)
    if me.goalie_min_prob < GOALIE_WARN_BELOW:
        games = me.goalie_games_so_far + me.goalie_starts_left
        lines.append(f"Goalie minimum at risk: ~{games:.1f} games of {MIN_GOALIE_GAMES_PER_WEEK}, "
                     f"{_pct(me.goalie_min_prob)} to make it. Pick up a goalie who plays this week.")
    return "\n".join(lines + [w for w in warnings if w])


def league_warning(league_through: dt.date | None, opponent_updated: str | None, today: dt.date) -> str:
    """Free agents and the opponent's roster go stale without Transactions screenshots."""
    if league_through and (today - league_through).days >= matchup.LEAGUE_MOVES_STALE_DAYS:
        return (f"League moves known through {league_through:%a %d %b}: send League > Transactions "
                "screenshots back to then, so free agents and their roster are current.")
    if opponent_updated and (today - dt.date.fromisoformat(opponent_updated)).days >= matchup.LEAGUE_MOVES_STALE_DAYS:
        return (f"Their roster is from {dt.date.fromisoformat(opponent_updated):%d %b}. If they've made moves, "
                "send League > Transactions screenshots (or /opp with their team page).")
    return ""


def change_text(old: list[dict], plan: list[Planned], reason: str, today: dt.date, adds_left_week: int) -> str:
    """The plan changed: from what, to what, and why."""
    before = "; ".join(o["add"]["name"] + (f" for {o['drop']['name']}" if o.get("drop") else "") for o in old)
    return "\n".join([f"Plan changed ({reason}). Was: {before or 'nothing'}. Now:"]
                     + plan_lines(plan, today, adds_left_week))


def _score(me: matchup.TeamWeek, them: matchup.TeamWeek) -> str:
    return (f"{me.so_far:.0f}-{them.so_far:.0f}, expect {me.expected:.0f}-{them.expected:.0f}: "
            f"win {_pct(matchup.win_prob(me, them))}.")


def score_text(me: matchup.TeamWeek, them: matchup.TeamWeek, plan: list[Planned], today: dt.date) -> str:
    """The reply to a screenshot when the plan holds."""
    now = [p for p in plan if p.when <= today]
    what = ("; ".join(swap(p.move) for p in now) + " today" if now
            else f"{swap(plan[0].move)} on {plan[0].when:%a %d}" if plan else "no add")
    return f"{_score(me, them)} Plan unchanged: {what}."


def new_plan_text(plan: list[Planned], today: dt.date, adds_left_week: int, me: matchup.TeamWeek | None = None,
                  them: matchup.TeamWeek | None = None) -> str:
    """Moves where there were none (the first plan, or the last one's moves
    made): what to do; with the score first when it answers a screenshot."""
    return "\n".join(([_score(me, them)] if me and them else []) + ["New plan:"]
                     + plan_lines(plan, today, adds_left_week))
