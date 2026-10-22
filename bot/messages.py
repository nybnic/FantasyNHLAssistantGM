"""The plan's words, from one source: the composed plan (engine/plan.py) and
the week's numbers. Telegram is read on a phone, so the plan message leads
with what to do and stays a few lines (docs/plan-2026-10-09.md, step 3); the
full table lives on the Board (scripts/explain_week.py) and the dashboard.
"""
from __future__ import annotations

import datetime as dt
from html import escape as html_escape
from zoneinfo import ZoneInfo

from clients.names import normalize_name
from config.league import MAX_ADDS_PER_SEASON, MIN_GOALIE_GAMES_PER_WEEK, TIMEZONE
from engine import addprice, matchup
from engine.plan import Planned

GOALIE_WARN_BELOW = 0.9  # the goalie minimum gets a line only when at risk
WHY_BUTTON = "Why?"  # a move card's link to its sheet on the dashboard


def _pct(p: float) -> str:
    return matchup._pct(p)


def swap(m: matchup.Move) -> str:
    if m.ir_slot:
        return f"{m.add.name} into your empty {m.ir_slot} slot"
    return m.add.name + (f" for {m.drop.name}" if m.drop else " into your open spot")


# Why a planned move is made on its day (engine/plan.compose's `why`; "now" needs no words).
WHEN_TEXT = {"spare": "uses this week's slot, so next week's two adds stay free",
             "monday": "it does nothing this week, so it takes next week's add",
             "held": "if the week holds: the add stays free to chase with until then"}


def _detail(p: Planned) -> str:
    """Why this move, in a few words: its win odds this week when it moves
    them, else what it's worth; then why it's made when it is."""
    m = p.move
    if m.win_after - m.win_before >= 0.01:
        head = f"win {_pct(m.win_before)} -> {_pct(m.win_after)}"
        if m.later_value + sum(m.ahead_wins) >= 0.01:
            head += f", +{100 * m.value:.0f} win-pts in all"
    else:
        # A streaming swap pays in the weeks ahead, not the season (matchup.keep_share).
        head = f"+{100 * m.value:.0f} win-pts, " + ("a keeper" if m.keep >= 0.5 else "for the weeks ahead")
    when = WHEN_TEXT.get(p.why)
    claim = f"; a claim, plays from {date_text(m.plays_from)}" if m.plays_from else ""
    claim += f"; holds the spot until {m.until_back} is back" if m.until_back else ""
    cond = f"; {condition_text(p.condition)}" if p.condition else ""
    return head + (f"; {when}" if when else "") + (f"; {p.note}" if p.note else "") + claim + cond


def instead_text(c) -> str:
    return f"{swap(c.alt)} instead" if c.alt else "no add"


def condition_text(c) -> str:
    """A goalie add worth it only if he starts tonight (plan.starter_conditions)."""
    return f"only once {c.team} confirms {c.goalie} starts tonight; if {c.other} starts, {instead_text(c)}"


def empty_text(adds_left_week: int) -> str:
    return ("No adds left this week, and nothing worth one planned for Monday." if not adds_left_week
            else "No add is worth one of yours now.")


def date_text(d: dt.date) -> str:
    """"Sat 10 Oct" (no leading zero, on Windows too)."""
    return f"{d:%a} {d.day} {d:%b}"


def day_label(when: dt.date, today: dt.date) -> str:
    """The exact day to make a move: its NHL date, i.e. during that day in
    Helsinki, before the evening's games."""
    when = max(when, today)
    return date_text(when) + (" (today)" if when == today else " (tomorrow)" if (when - today).days == 1 else "")


def confirmed_text(stored: dict, html: bool = False) -> str:
    """A move made only if its goalie starts tonight: he does (bot/weekly.run_plan)."""
    c = stored["if_starts"]
    move = stored["add"]["name"] + (f" for {stored['drop']['name']}" if stored.get("drop") else "")
    text = f"{c['goalie']} is confirmed in {c['team']}'s net tonight: make {move} now."
    return f"✅ <b>Go</b>: {_esc(text)}" if html else text


def make_it(p: Planned, today: dt.date, html: bool = False) -> str:
    """A card's first line: the day, and why not sooner."""
    if html:
        rest = make_it(p, today).removeprefix(f"Make it {day_label(p.when, today)}")
        return f"📅 <b>Make it {day_label(p.when, today)}</b>{_esc(rest)}"
    if c := p.condition:
        return (f"Make it {day_label(p.when, today)}, once {c.team} confirms {c.goalie} in net tonight "
                f"(DailyFaceoff, usually after the morning skate). If {c.other} starts: {instead_text(c)}.")
    return (f"Make it {day_label(p.when, today)}, before that evening's games"
            + (f": {p.note}" if p.note else "") + ".")


def card_text(p: Planned, today: dt.date, body: str) -> str:
    """A move's card in HTML: 📅 the day in bold, then the move as matchup.move_text words it."""
    return make_it(p, today, html=True) + "\n" + _esc(body)


def plan_items(plan: list[Planned], today: dt.date) -> list[dict]:
    """The plan as the dashboard lists it: the same words as the message."""
    return [{"day": day_label(p.when, today), "today": p.when <= today, "swap": swap(p.move), "detail": _detail(p),
             "key": p.key} for p in plan]


def activation_items(activations: list, today: dt.date) -> list[dict]:
    """Players back from IR, as the plan's first steps (bot/weekly.activate_returns):
    Yahoo allows no other move until each has an active spot."""
    return [{"day": day_label(today, today), "today": True,
             "swap": f"Activate {back.name}" + (f", drop {drop.name}" if drop else ""),
             "detail": f"back in {back.team}'s lineup; Yahoo allows no other move until he's active. "
                       + (f"{drop.name} costs least to lose, counting the nights he'd be in your lineup. "
                          if drop else "") + "Then send your team page", "key": None}
            for back, drop in activations]


def activation_lines(activations: list, today: dt.date) -> list[str]:
    return [f"{i['day']}: {i['swap']} ({i['detail']})." for i in activation_items(activations, today)]


def plan_lines(plan: list[Planned], today: dt.date, adds_left_week: int) -> list[str]:
    """What to do and when, one line per day."""
    if not plan:
        return [empty_text(adds_left_week)]
    lines = []
    for p in plan:
        lines.append(f"{day_label(p.when, today)}: {swap(p.move)} ({_detail(p)}).")
    if all(p.when > today for p in plan) and adds_left_week:
        lines.insert(0, "Nothing to add today.")
    return lines


def plan_text(week: int, opponent: str, me: matchup.TeamWeek, them: matchup.TeamWeek, plan: list[Planned],
              today: dt.date, adds_left_week: int, season_used: int, ahead: list[matchup.WeekAhead] = (),
              this_week_best: matchup.Move | None = None, price=None, odds=None,
              yahoo: list | None = None, warnings: list[str] = (), activations: list = (), html: bool = False) -> str:
    """The plan message: the matchup, what to do (players back from IR first),
    the weeks ahead, the budget. `html`: for Telegram's HTML mode, the moves
    grouped by day with bold, the score in fixed width and a marker a line."""
    yahoo_text = f" (Yahoo {yahoo[0]:.0f}-{yahoo[1]:.0f})" if yahoo else ""
    win = _pct(matchup.win_prob(me, them))
    tail = []
    if best := this_week_line(this_week_best, plan, price, adds_left_week):
        tail.append(("💡", best))
    if ahead:
        tail.append(("🔭", "Ahead: " + ", ".join(
            f"wk {w.week} vs {w.opponent} {_pct(matchup._phi(w.margin / w.sd))}" if w.margin is not None
            else f"wk {w.week} (opponent not known)" for w in ahead) + "."))
    budget = f"Adds: {adds_left_week} this week, {MAX_ADDS_PER_SEASON - season_used} this season."
    if price and price.lam != float("inf"):
        budget += f" An add needs {100 * price.lam:.0f} win-pts ({pace_text(price.pace, week)})."
    tail.append(("🎟️", budget))
    if odds:
        tail.append(("🏆", f"Playoffs {odds.playoffs:.0%}, title {odds.title:.0%}."))
    if me.goalie_min_prob < GOALIE_WARN_BELOW:
        games = me.goalie_games_so_far + me.goalie_starts_left
        tail.append(("⚠️", f"Goalie minimum at risk: ~{games:.1f} games of {MIN_GOALIE_GAMES_PER_WEEK}, "
                           f"{_pct(me.goalie_min_prob)} to make it. Pick up a goalie who plays this week."))
    tail += [("🔄" if w.startswith("Changed since") else "⚠️", w) for w in warnings if w]
    if html:
        so_far = f"<code>{me.so_far:.0f}-{them.so_far:.0f}</code> so far · " if me.so_far or them.so_far else ""
        head = [f"<b>Week {week} vs {_esc(opponent)}</b>",
                f"{so_far}expect <code>{me.expected:.0f}-{them.expected:.0f}</code>{yahoo_text}",
                f"🎯 Win <b>{win}</b>"]
        blocks = ["\n".join(head)] + activation_blocks(activations, today) + plan_blocks(plan, today, adds_left_week)
        return "\n\n".join(blocks + ["\n".join(f"{mark} {_esc(line)}" for mark, line in tail)])
    head = (f"Week {week} vs {opponent}: " + (f"{me.so_far:.0f}-{them.so_far:.0f} so far, " if me.so_far or them.so_far
                                               else "")
            + f"expect {me.expected:.0f}-{them.expected:.0f}{yahoo_text}. Win {win}.")
    lines = [head] + activation_lines(activations, today) + plan_lines(plan, today, adds_left_week)
    for mark, line in tail:  # the playoff odds share the budget's line
        if mark == "🏆":
            lines[-1] += " " + line
        else:
            lines.append(line)
    return "\n".join(lines)


def _esc(text: str) -> str:
    """Text for Telegram's HTML mode (names like "D'Astous" or "A & B" stay as they are), with real arrows."""
    return html_escape(str(text).replace(" -> ", " → "), quote=False)


def swap_html(m: matchup.Move) -> str:
    """➕ the add (bold), ➖ the drop; or where the add goes."""
    add = f"➕ <b>{_esc(m.add.name)}</b>"
    if m.ir_slot:
        return f"{add} into your empty {m.ir_slot} slot"
    return f"{add}  ➖ {_esc(m.drop.name)}" if m.drop else f"{add} into your open spot"


def plan_blocks(plan: list[Planned], today: dt.date, adds_left_week: int) -> list[str]:
    """The plan in HTML, one block a day: 📅 the day, then each move and why."""
    if not plan:
        return ["💤 " + _esc(empty_text(adds_left_week))]
    blocks, days = [], sorted({max(p.when, today) for p in plan})
    if days[0] > today and adds_left_week:
        blocks.append("⏳ Nothing to add today.")
    for day in days:
        lines = [f"📅 <b>{day_label(day, today)}</b>"]
        for p in (q for q in plan if max(q.when, today) == day):
            lines += [swap_html(p.move), _esc(_detail(p))]
        blocks.append("\n".join(lines))
    return blocks


def activation_blocks(activations: list, today: dt.date) -> list[str]:
    return [f"🩹 <b>{i['day']}</b>\n{_esc(i['swap'])}\n{_esc(i['detail'])}." for i in activation_items(activations, today)]


def pace_text(need: float, week: int) -> str:
    """How the budget stands, as the add price reads it (addprice.pacing_price)."""
    ratio = need / addprice.even_pace(week)
    if abs(ratio - 1) < 0.05:
        return "on pace"
    return "adds saved: a lower bar" if ratio > 1 else "ahead of pace: a higher bar"


def this_week_line(best: matchup.Move | None, plan: list[Planned], price, adds_left_week: int) -> str:
    """The move that lifts this week's odds most, when it isn't the plan's:
    what chasing would buy and why it isn't planned ("" otherwise)."""
    planned = {(p.move.add.id, p.move.drop.id if p.move.drop else None) for p in plan}
    if not (best and price and adds_left_week) or (best.add.id, best.drop.id if best.drop else None) in planned:
        return ""
    head = f"For this week alone, the best is {swap(best)}: win {_pct(best.win_before)} -> {_pct(best.win_after)}"
    if best.value >= price.lam:
        return f"{head}. Worth an add too, but it clashes with the plan's."
    later = best.value - (best.win_after - best.win_before)
    cost = f", as dropping {best.drop.name} costs {100 * -later:.0f} later" if best.drop and later < 0 else ""
    return (f"{head}. Not worth an add: {100 * best.value:.0f} win-pts in all{cost}, against the "
            f"{100 * price.lam:.0f} an add costs.")


def league_warning(league_through: dt.date | None, opponent_updated: str | None, today: dt.date) -> str:
    """Free agents and the opponent's roster go stale without Transactions screenshots."""
    if league_through and (today - league_through).days >= matchup.LEAGUE_MOVES_STALE_DAYS:
        return (f"League moves known through {league_through:%a %d %b}: send League > Transactions "
                "screenshots back to then, so free agents and their roster are current.")
    if opponent_updated and (today - dt.date.fromisoformat(opponent_updated)).days >= matchup.LEAGUE_MOVES_STALE_DAYS:
        return (f"Their roster is from {dt.date.fromisoformat(opponent_updated):%d %b}. If they've made moves, "
                "send League > Transactions screenshots (or /opp with their team page).")
    return ""


def change_text(old: list[dict], plan: list[Planned], reason: str, today: dt.date, adds_left_week: int,
                activations: list = (), html: bool = False) -> str:
    """The plan changed: from what, to what, and why."""
    before = "; ".join(o["add"]["name"] + (f" for {o['drop']['name']}" if o.get("drop") else "") for o in old)
    if html:
        return "\n\n".join([f"🔄 <b>Plan changed</b> ({_esc(reason)}). Was: {_esc(before or 'nothing')}. Now:"]
                           + activation_blocks(activations, today) + plan_blocks(plan, today, adds_left_week))
    return "\n".join([f"Plan changed ({reason}). Was: {before or 'nothing'}. Now:"]
                     + activation_lines(activations, today) + plan_lines(plan, today, adds_left_week))


def _score(me: matchup.TeamWeek, them: matchup.TeamWeek, html: bool = False) -> str:
    if html:
        return (f"📊 <code>{me.so_far:.0f}-{them.so_far:.0f}</code>, expect "
                f"<code>{me.expected:.0f}-{them.expected:.0f}</code>: win <b>{_pct(matchup.win_prob(me, them))}</b>.")
    return (f"{me.so_far:.0f}-{them.so_far:.0f}, expect {me.expected:.0f}-{them.expected:.0f}: "
            f"win {_pct(matchup.win_prob(me, them))}.")


def score_text(me: matchup.TeamWeek, them: matchup.TeamWeek, plan: list[Planned], today: dt.date,
               html: bool = False) -> str:
    """The reply to a screenshot when the plan holds."""
    now = [p for p in plan if p.when <= today]
    what = ("; ".join(swap(p.move) for p in now) + f" today ({date_text(today)})" if now
            else f"{swap(plan[0].move)} on {date_text(plan[0].when)}" if plan else "no add")
    return (f"{_score(me, them, True)}\nPlan unchanged: {_esc(what)}." if html
            else f"{_score(me, them)} Plan unchanged: {what}.")


def new_plan_text(plan: list[Planned], today: dt.date, adds_left_week: int, me: matchup.TeamWeek | None = None,
                  them: matchup.TeamWeek | None = None, html: bool = False) -> str:
    """Moves where there were none (the first plan, or the last one's moves
    made): what to do; with the score first when it answers a screenshot."""
    if html:
        return "\n\n".join(([_score(me, them, True)] if me and them else []) + ["🆕 <b>New plan</b>"]
                           + plan_blocks(plan, today, adds_left_week))
    return "\n".join(([_score(me, them)] if me and them else []) + ["New plan:"]
                     + plan_lines(plan, today, adds_left_week))


GROUP_TEXT = {"f1": "the first line", "f2": "the second line", "d1": "the top D pair", "pp1": "the first power play"}


def group_text(group: str) -> str:
    return GROUP_TEXT.get(group, group)


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def watch_value(value: tuple | None) -> str:
    """The plan's verdict on adding him: (best move's value, the add price, in the plan)."""
    if not value:
        return "No move with him passes the plan's checks this week (waivers, or he'd cost points now)."
    worth, price, planned = value
    head = f"{100 * worth:+.0f} win-pts against the {100 * price:.1f} an add costs"
    if planned:
        return head + ": in the plan now."
    return head + (": worth an add, but the plan's moves beat it." if worth >= price else ": not yet worth one.")


def watch_why(s) -> str:
    """Why one watch signal (bot/watch.Signal) was raised."""
    n = s.numbers
    if s.kind == "goalie":
        return (f"{s.reason[0].upper()}{s.reason[1:]}. Expected to start {_pct(n['expected'])} of the games ahead, "
                f"against {_pct(n['projected'])} projected before the season.")
    if s.kind == "promoted":
        return f"DFO {s.reason}" + (f", and it has held for {n['days']} days." if n.get("days") else ".")
    return (f"Ice time {_minutes(n['recent'])} a game in his last 3, up from {_minutes(n['usual'])}. "
            "The model follows ice time slowly: jumps like this run ~0.3 pts a game above it the next week.")


def watch_text(signals: list, values: dict) -> str:
    """The breakout watch (bot/watch.py): each free agent whose role just
    changed (all his signals in one block), why, and what the plan makes of him."""
    by_player: dict = {}
    for s in signals:
        by_player.setdefault(s.player.id, []).append(s)
    blocks = []
    for sigs in by_player.values():
        p = sigs[0].player
        head = f"Watch: {p.name} ({p.team} {'/'.join(p.positions)}), a free agent"
        blocks.append("\n".join([head, *(watch_why(s) for s in sigs), watch_value(values.get(p.id))]))
    return "\n\n".join(blocks)


# /why: one player or move, from the latest plan's Board (bot/board.py), in
# expected wins (a win-pt is 0.01 of one), the unit the plan itself decides in.

def _wins(x: float) -> str:
    return f"{round(x, 2) or 0.0:+.2f}"  # no "-0.00"


def _names(b: dict) -> dict[int, str]:
    """Everyone the Board names: id -> name."""
    names = {int(k): v["name"] for k, v in b.get("players", {}).items()}
    for r in b["moves"]:
        names.setdefault(r["add"]["id"], r["add"]["name"])
        if r["drop"]:
            names.setdefault(r["drop"]["id"], r["drop"]["name"])
    return names


def find_named(names: dict[int, str], query: str) -> list[int]:
    """Ids whose name is `query`, else whose last name(s) are ("Ek", "Eriksson Ek")."""
    q = normalize_name(query)
    if not q:
        return []
    exact = [i for i, n in names.items() if normalize_name(n) == q]
    return exact or [i for i, n in names.items() if normalize_name(n).endswith(" " + q)]


def _label(r: dict) -> str:
    if r["ir_slot"]:
        return f"{r['add']['name']} into your empty {r['ir_slot']} slot"
    return r["add"]["name"] + (f" for {r['drop']['name']}" if r["drop"] else " into your open spot")


def _verdict(r: dict, b: dict) -> str:
    today = dt.date.fromisoformat(b["date"])
    if r["status"] == "plan":
        return (f"in the plan, {day_label(dt.date.fromisoformat(r['when']), today)}" if r.get("when")
                else "in the plan") + (f" ({WHEN_TEXT[r['why']]})" if r["why"] in WHEN_TEXT else "")
    if r["status"] == "passes":
        return r["why"] if r["why"].startswith("worth an add") else f"worth an add, not in the plan: {r['why']}"
    price = b["header"]["price"]
    if price and r["value"] < price["lam"]:
        return "not worth an add" + ("" if r["why"].startswith("worth ") else f": {r['why']}")
    return f"not in the plan: {r['why']}"


def move_why(r: dict, b: dict) -> list[str]:
    """A move's verdict and its value, part by part."""
    price = b["header"]["price"]
    lines = [f"{_label(r)}: {_verdict(r, b)}.",
             f"Worth {_wins(r['value'])} expected wins; " + (f"an add needs {price['lam']:.2f}." if price
                                                             else "no adds left to spend.")]
    lines.append(f"This week {_wins(r['now_wins'])}: {r['week_pts']:+.1f} pts, win {_pct(r['win'][0])} -> "
                 f"{_pct(r['win'][1])}.")
    ahead = b["header"].get("ahead", [])
    if ahead and r.get("ahead_wins"):
        pts = r.get("ahead_week_pts") or []
        parts = [f"{pts[k]:+.1f} pts vs {w['opponent'] or 'a typical week'}" for k, w in enumerate(ahead) if k < len(pts)]
        weeks_ = f"Week {ahead[0]['week']}" if len(ahead) == 1 else f"Weeks {ahead[0]['week']}-{ahead[-1]['week']}"
        lines.append(f"{weeks_} {_wins(sum(r['ahead_wins']))}: " + ", ".join(parts) + ".")
    hold = b["header"].get("long_run", {}).get("hold_days")
    counted = (f"only while he'd be held, ~{hold} days" if r["held"] and hold else "only while he'd be held"
               if r["held"] else f"{r['keep']:.0%} of the season counted" if r["keep"] < 1 else "the season")
    lines.append(f"Later {_wins(r['later_wins'])}: {r['later_pts']:+.1f} pts ({counted}).")
    return lines


def _player_line(pid: int, b: dict) -> str:
    """His points a game and his week: lineup points and games left."""
    p = b.get("players", {}).get(str(pid))
    if not p:
        return ""
    unit = "start" if p["goalie"] else "game"
    line = f"{p['name']} ({p['team']} {p['positions']}): {p['xfp'] or 0:.1f} pts a {unit}"
    side = {"me": "me", "them": "them"}.get(p["owner"])
    row = next((r for r in b.get("teams", {}).get(side, {}).get("players", []) if r["id"] == pid), None) if side else None
    if row:
        line += f", {row['pts']:.0f} pts in {row['games']:g} lineup {unit}s left this week"
        if row["bench_pts"] and side == "me":
            line += f" ({row['bench_pts']:.0f} more on your bench)"
    return line + "."


def why_text(b: dict | None, query: str) -> str:
    """/why Name, or /why Add for Drop: what the latest plan makes of him or
    of that move, from its Board, so it says what the plan computed."""
    if not b:
        return "No plan yet: send /week first."
    names = _names(b)
    at = dt.datetime.fromisoformat(b["at"]).astimezone(ZoneInfo(TIMEZONE))
    foot = f"From the plan of {date_text(at.date())} {at:%H:%M}. The dashboard has every move."
    add_q, sep, drop_q = query.partition(" for ")
    found = find_named(names, add_q)
    if len(found) > 1:
        return "Which one? " + ", ".join(sorted(names[i] for i in found)) + "."
    if not found:
        return (f"{add_q.strip()} isn't on the latest plan's Board. It holds your players, those of "
                f"{b['opponent']}, and the free agents the plan weighed.")
    pid = found[0]
    if sep:
        drops = find_named(names, drop_q)
        r = next((r for r in b["moves"] if r["add"]["id"] == pid and r["drop"] and r["drop"]["id"] in drops), None)
        if not r:
            return (f"{names[pid]} for {drop_q.strip()} wasn't weighed. The plan tries each free agent for the "
                    "2 players per group (forwards, D, goalies) cheapest to lose, and your most-benched skater.")
        return "\n".join(move_why(r, b) + [foot])
    as_add = sorted((r for r in b["moves"] if r["add"]["id"] == pid), key=lambda r: -r["value"])
    as_drop = sorted((r for r in b["moves"] if r["drop"] and r["drop"]["id"] == pid), key=lambda r: -r["value"])
    owner = b.get("players", {}).get(str(pid), {}).get("owner") or ("fa" if as_add else "me")
    lines = [line] if (line := _player_line(pid, b)) else []
    if as_add:
        best = next((r for r in as_add if r["status"] == "plan"), as_add[0])
        lines += move_why(best, b)
        others = [r for r in as_add if r is not best]
        if others:
            lines.append(f"{len(others)} other move{'s' if len(others) > 1 else ''} with him weighed; next best: "
                         f"{_label(others[0])} {_wins(others[0]['value'])}.")
    elif owner == "me":
        if as_drop:
            best = next((r for r in as_drop if r["status"] == "plan"), as_drop[0])
            lines += [f"Dropping him: {len(as_drop)} moves weighed, the best:"] + move_why(best, b)
        else:
            lines.append("Not weighed as a drop: the plan tries the 2 players per group (forwards, D, goalies) "
                         "cheapest to lose, and your most-benched skater.")
    elif owner == "them":
        lines.append(f"On {b['opponent']}'s roster.")
    else:
        lines.append("A free agent, but no move with him reached the plan's shortlist.")
    return "\n".join(lines + [foot])
