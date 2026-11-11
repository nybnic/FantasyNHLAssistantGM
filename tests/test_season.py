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
    assert odds.playoffs > 0.85 and odds.title > 0.15
    assert odds.strengths[MY_TEAM] == (230.0, 30.0)  # kept as given, for the Board


def test_projected_gaps_count_at_the_size_they_come_true():
    strengths = _even()
    strengths[MY_TEAM] = (240.0, 30.0)
    played = season.realized(strengths)
    avg = (200.0 * 15 + 240.0) / 16
    assert played[MY_TEAM] == (pytest.approx(avg + season.GAPS_REALIZE * (240.0 - avg)), 30.0)
    assert sum(m for m, _ in played.values()) == pytest.approx(sum(m for m, _ in strengths.values()))
    full = season.simulate(strengths, MY_TEAM, 2, 0.5, sims=3000)
    shrunk = season.simulate(season.realized(strengths, 1 / season.GAPS_REALIZE), MY_TEAM, 2, 0.5, sims=3000)
    assert full.title < shrunk.title  # read at full size, a +40 team would win far more often


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


def test_this_weeks_known_pairings_are_played_when_they_cover_everyone():
    strengths = _even()
    others = [t for t in TEAMS if t not in (MY_TEAM, SCHEDULE[1])]
    pairs = [[others[i], others[i + 1]] for i in range(0, len(others), 2)]
    plain = season.simulate(strengths, MY_TEAM, 2, 0.5, sims=2000)
    partial = season.simulate(strengths, MY_TEAM, 2, 0.5, sims=2000, pairs=pairs[:3])
    assert partial == plain  # incomplete: random pairings as before
    known = season.simulate(strengths, MY_TEAM, 2, 0.5, sims=2000, pairs=pairs)
    assert known != plain and 0.4 < known.playoffs < 0.6


def test_a_projection_is_weighed_against_the_points_scored():
    import numpy as np
    rng = np.random.default_rng(3)
    strengths = {t: (200.0 + g, 30.0) for t, g in zip(TEAMS, rng.normal(0, 12, len(TEAMS)))}
    standings = {t: {"w": 3, "l": 3, "t": 0, "pf": 6 * (220.0 + g)}
                 for t, g in zip(TEAMS, rng.normal(0, 18, len(TEAMS)))}
    strengths[MY_TEAM] = (245.0, 30.0)  # projected far ahead...
    standings[MY_TEAM]["pf"] = 6 * 222.0  # ...scoring about average
    read = season.estimated(strengths, standings)
    avg = sum(m for m, _ in strengths.values()) / len(strengths)
    assert sum(m for m, _ in read.values()) / len(read) == pytest.approx(avg)
    assert 0 < read[MY_TEAM][0] - avg < 0.7 * (245.0 - avg)  # pulled toward what he scored
    assert read[MY_TEAM][1] == 30.0
    a, b = [t for t in TEAMS if t != MY_TEAM][:2]
    strengths[a] = strengths[b] = (205.0, 30.0)  # projected alike, one scoring more
    standings[a]["pf"], standings[b]["pf"] = 6 * 240.0, 6 * 200.0
    read = season.estimated(strengths, standings)
    assert read[a][0] > read[b][0]
    odds = season.simulate(strengths, MY_TEAM, 7, 0.5, standings, sims=2000)
    assert odds.read == read and odds.read[MY_TEAM][0] - avg < 0.7 * (245.0 - avg) and odds.strengths[MY_TEAM] == (245.0, 30.0)


def test_without_standings_for_everyone_the_projection_is_read_at_its_realized_size():
    strengths = _even()
    strengths[MY_TEAM] = (240.0, 30.0)
    assert season.estimated(strengths, {}) == season.realized(strengths)
    assert season.estimated(strengths, {MY_TEAM: {"w": 2, "l": 4, "pf": 1300.0}}) == season.realized(strengths)
    even = {t: {"w": 3, "l": 3, "pf": 1300.0} for t in TEAMS}  # points that vary no more than their error
    assert season.estimated(strengths, even) == season.realized(strengths)
