"""The replay harness's own arithmetic (scripts/replay.py): a swap's gain by
day against a full lineup, and the week's real score with the goalie minimum."""
import datetime as dt
from collections import defaultdict

import pytest

from scripts import replay

MON = dt.date(2025, 11, 3)


def _season(xfp, position, team_days, points=None, g_points=None, goalies=()):
    s = object.__new__(replay.Season)
    s.position, s.goalie_ids = position, set(goalies)
    s.team_days = defaultdict(set, team_days)
    s.points, s.g_points = points or {}, g_points or {}
    s._proj = {MON: (xfp, {g: 9.0 for g in goalies})}
    s._team, s._missed = {}, {}
    s.team = lambda pid, m: {1: "A", 2: "A", 3: "B", 9: "C", 10: "C", 11: "C"}[pid]
    s.missed = lambda pid, m: 0
    s.share = lambda pid, m: 0.5
    return s


def test_a_swap_gains_only_what_it_changes_in_the_best_lineup():
    # Two C slots: 1 (5.0) and 2 (3.0) start; 9 (4.0) would take 2's slot.
    tue, wed = MON + dt.timedelta(days=1), MON + dt.timedelta(days=2)
    s = _season({1: 5.0, 2: 3.0, 3: 2.0, 9: 4.0}, {1: "C", 2: "C", 3: "C", 9: "C"},
                {"A": {MON, tue}, "B": {MON}, "C": {tue, wed}})
    week = replay.Week(s, [1, 2, 3], MON, [MON, tue, wed])
    play = replay.availability.HEALTHY_PLAY
    gain = week.gain(9, 3, [MON, tue, wed])
    # Monday: dropping 3 (the third C, benched) costs nothing. Tuesday: 9 replaces
    # 2 in the lineup. Wednesday: 9 alone.
    assert MON not in gain and gain[tue] == pytest.approx((4.0 - 3.0) * play)
    assert gain[wed] == pytest.approx(4.0 * play)
    assert week.gain(9, 2, [tue])[tue] == pytest.approx((4.0 - 3.0) * play)  # same slot, same gain


def test_the_real_score_counts_who_played_and_zeroes_goalies_below_the_minimum():
    days = [MON + dt.timedelta(days=i) for i in range(7)]
    points = {(1, days[0]): 6.0, (2, days[0]): 2.0, (3, days[0]): 4.0}  # 3 Cs played Monday: 2 slots
    g_points = {(10, days[1]): 9.0, (11, days[2]): 7.0}
    s = _season({1: 5.0, 2: 3.0, 3: 4.0}, {1: "C", 2: "C", 3: "C"}, {}, points, g_points, goalies=(10, 11))
    assert replay.actual(s, [1, 2, 3, 10, 11], MON) == 10.0  # 6 + 4; two goalie starts: zeroed
    s.g_points[(10, days[3])] = 5.0
    assert replay.actual(s, [1, 2, 3, 10, 11], MON) == 10.0 + 21.0
