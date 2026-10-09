"""The plan's words (bot/messages.py), read whole: a phone-sized message
that leads with the action and never contradicts itself."""
import datetime as dt

from bot import messages
from engine import matchup, plan
from engine.addprice import AddPrice
from engine.season import SeasonOdds
from league.roster import RosterPlayer

FRIDAY, MONDAY, WEDNESDAY = dt.date(2026, 10, 9), dt.date(2026, 10, 12), dt.date(2026, 10, 7)
SAMUELSSON = RosterPlayer(6, "Mattias Samuelsson", "BUF", ["D"])
LINDELL = RosterPlayer(7, "Esa Lindell", "DAL", ["D"])
PRICE = AddPrice(0.10, 0.009, 1.23)
ME = matchup.TeamWeek("me", 136.8, 223.0, 357.2, 17, 2, 1.6, 0.96)
THEM = matchup.TeamWeek("them", 162.8, 249.5, 324.7, 17, 2, 2.0, 1.0)


def _move(name, drop, win_after, long_term=0.0, week_gain=0.0, next_weeks=0.0):
    return matchup.Move(RosterPlayer(hash(name) % 1000, name, "NYR", ["LW"]), drop, week_gain, long_term, next_weeks,
                        2, 0.16, win_after, later_weight=0.009)


def _text(planned, adds_left=1, best=None, ahead=()):
    return messages.plan_text(2, "Retrot Chicken Wings", ME, THEM, planned, FRIDAY, adds_left, 3, ahead, best,
                              PRICE, SeasonOdds(0.21, 0.01, 0.13, 0.12, 6.4), [229.96, 257.38])


def test_the_plan_message_leads_with_the_matchup_then_what_to_do_and_stays_short():
    kelly = _move("Parker Kelly", SAMUELSSON, 0.16, long_term=34.2, next_weeks=11.1)
    olivier = _move("Mathieu Olivier", LINDELL, 0.22, week_gain=5.8)
    ahead = [matchup.WeekAhead(3, frozenset(), -12.0, 46.0, "Lallat"), matchup.WeekAhead(4, frozenset(), None, 45.0)]
    text = _text([plan.Planned(kelly, FRIDAY, "spare")], best=olivier, ahead=ahead)
    lines = text.split("\n")
    assert lines[0] == ("Week 2 vs Retrot Chicken Wings: 137-163 so far, expect 223-250 (Yahoo 230-257). Win 16%.")
    assert lines[1] == ("Fri 9 Oct (today): Parker Kelly for Mattias Samuelsson (+31 win-pts, a keeper; on this "
                        "week's add, which would go unused).")
    assert lines[2] == ("For this week alone, the best is Mathieu Olivier for Esa Lindell: win 16% -> 22%. "
                        "Not worth an add: 6 win-pts in all, against the 10 an add costs.")
    costly = _move("Mathieu Olivier", LINDELL, 0.22, long_term=-11.0, week_gain=5.8)  # -10 win-pts later
    assert "Not worth an add: -4 win-pts in all, as dropping Esa Lindell costs 10 later, against the 10" in _text(
        [plan.Planned(kelly, FRIDAY, "spare")], best=costly)
    assert lines[3] == "Ahead: wk 3 vs Lallat 40%, wk 4 (opponent not known)."
    assert lines[4] == "Adds: 1 this week, 33 this season. Playoffs 21%, title 1%."
    assert len(lines) == 5


def test_the_plan_message_never_contradicts_itself():
    kelly = _move("Parker Kelly", SAMUELSSON, 0.16, long_term=34.2, next_weeks=11.1)
    # The week's best stream is the planned move: no second line about it.
    stream = _move("Eeli Tolvanen", SAMUELSSON, 0.21, long_term=9.0, week_gain=4.9)
    text = _text([plan.Planned(stream, FRIDAY, "now")], best=stream)
    assert "For this week alone" not in text and "win 16% -> 21%" in text
    # No adds left this week: it doesn't point to one, and says so.
    none = _text([], adds_left=0, best=stream)
    assert "No adds left this week" in none and "For this week alone" not in none and "Today" not in none
    # Only a Monday move: nothing today, said first.
    later = _text([plan.Planned(kelly, MONDAY, "monday")])
    assert "Nothing to add today.\nMon 12 Oct: Parker Kelly for Mattias Samuelsson" in later
    # A move held for Wednesday says why it waits.
    held = messages.plan_lines([plan.Planned(kelly, WEDNESDAY + dt.timedelta(days=7), "held")], FRIDAY, 1)
    assert "if the week holds" in held[-1]


def test_the_goalie_minimum_and_other_warnings_appear_only_when_they_apply():
    risky = matchup.TeamWeek("me", 100.0, 200.0, 300.0, 17, 1, 0.8, 0.45)
    text = messages.plan_text(2, "R", risky, THEM, [], FRIDAY, 1, 3, warnings=["", "IR: move X to IR+."])
    assert "Goalie minimum at risk: ~1.8 games of 3, 45% to make it" in text
    assert text.endswith("IR: move X to IR+.") and "\n\n" not in text
    assert "Goalie" not in _text([])


def test_a_screenshot_gets_the_score_and_the_plan_in_one_line():
    kelly = _move("Parker Kelly", SAMUELSSON, 0.16, long_term=34.2, next_weeks=11.1)
    assert messages.score_text(ME, THEM, [plan.Planned(kelly, FRIDAY, "spare")], FRIDAY) == (
        "137-163, expect 223-250: win 16%. Plan unchanged: Parker Kelly for Mattias Samuelsson today (Fri 9 Oct).")
    assert messages.score_text(ME, THEM, [plan.Planned(kelly, MONDAY, "monday")], FRIDAY).endswith(
        "Plan unchanged: Parker Kelly for Mattias Samuelsson on Mon 12 Oct.")


def test_a_move_names_its_exact_day_and_why_it_waits():
    kelly = _move("Parker Kelly", SAMUELSSON, 0.16, long_term=34.2, next_weeks=11.1)
    waits = plan.Planned(kelly, dt.date(2026, 10, 10), "spare", "after Mattias Samuelsson's game on Fri 9")
    assert messages.plan_lines([waits], FRIDAY, 1)[1].startswith(
        "Sat 10 Oct (tomorrow): Parker Kelly for Mattias Samuelsson (+31 win-pts, a keeper; on this week's add, "
        "which would go unused; after Mattias Samuelsson's game on Fri 9)")
    assert messages.make_it(waits, FRIDAY) == ("Make it Sat 10 Oct (tomorrow), before that evening's games: "
                                               "after Mattias Samuelsson's game on Fri 9.")
    assert messages.make_it(plan.Planned(kelly, FRIDAY, "spare"), FRIDAY) == (
        "Make it Fri 9 Oct (today), before that evening's games.")
