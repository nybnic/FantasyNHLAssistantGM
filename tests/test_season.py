import pytest

from config.league import MY_TEAM, SCHEDULE
from engine import season

TEAMS = sorted(set(SCHEDULE)) + [MY_TEAM]


def _even(points=200.0):
    return {t: (points, 30.0) for t in TEAMS}


def test_even_teams_make_the_playoffs_half_the_time_and_win_one_title_in_sixteen():
    odds = season.simulate(_even(), MY_TEAM, 2, 0.5, sims=6000)
    assert odds.playoffs == pytest.approx(0.5, abs=0.03)
    assert odds.title == pytest.approx(1 / 16, abs=0.015)
    assert odds.seed == pytest.approx(4.5, abs=0.3)
    assert 0.8 < odds.weight < 1.1  # an early week is about a typical one


def test_a_stronger_team_is_likelier_to_make_it_and_to_win():
    strengths = _even()
    strengths[MY_TEAM] = (230.0, 30.0)
    odds = season.simulate(strengths, MY_TEAM, 2, 0.5, sims=3000)
    assert odds.playoffs > 0.9 and odds.title > 0.25


def test_standings_decide_how_much_a_late_week_matters():
    bubble = {t: {"w": 10, "pf": 4000.0} for t in TEAMS}
    late = season.simulate(_even(), MY_TEAM, 21, 0.5, bubble, sims=3000)
    assert late.leverage > 0.3  # a win at 10-10 moves the playoff odds a lot
    locked = dict(bubble, **{MY_TEAM: {"w": 18, "pf": 4400.0}})
    odds = season.simulate(_even(), MY_TEAM, 21, 0.5, locked, sims=3000)
    assert odds.playoffs == 1.0 and odds.leverage < season.MIN_LEVERAGE
    assert "barely moves your playoff odds" in season.text(odds)


def test_this_weeks_leverage_is_paired_so_it_is_stable_across_seeds():
    weights = [season.simulate(_even(), MY_TEAM, 2, 0.5, sims=4000, seed=s).weight for s in (1, 2, 3)]
    assert max(weights) - min(weights) < 0.1


def test_no_odds_without_every_team_or_outside_the_regular_season():
    assert season.simulate({MY_TEAM: (200.0, 30.0)}, MY_TEAM, 2, 0.5) is None
    assert season.simulate(_even(), MY_TEAM, 24, 0.5) is None


def test_the_plan_line_reads_naturally():
    odds = season.SeasonOdds(playoffs=0.62, title=0.09, leverage=0.18, typical=0.12, seed=4.2)
    assert season.text(odds) == ("Season: playoffs 62% (seed ~4), title 9%. "
                                 "A win this week: playoffs +18 pts, 1.5x a typical week left.")
