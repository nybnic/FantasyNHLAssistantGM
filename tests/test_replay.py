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


def test_a_planned_exit_drops_a_bad_week_ahead_only_when_it_pays():
    # Week +1 gains 6 points, week +2 loses 5, then 2 more held days gain 1.
    future = [MON + dt.timedelta(days=7 + i) for i in range(42)]
    gain = {future[0]: 6.0, future[7]: -5.0, future[14]: 1.0}
    ahead = [replay.matchup.WeekAhead(k, frozenset(future[7 * (k - 1):7 * k]), None, 40.0) for k in (1, 2)]
    w, g1, g2 = 0.01, replay.matchup.GAP_REALIZES[1], replay.matchup.GAP_REALIZES[2]
    full = w * (6.0 * g1 - 5.0 * g2 + 1.0)

    def exit_(rule, price=0.09, keep=0.0):
        return replay.best_exit(gain, future, ahead, 20, w, 16, keep, rule, price)

    assert exit_(None) == (pytest.approx(full), None)
    assert exit_("free") == (pytest.approx(w * 6.0 * g1), 1)  # out after week +1
    # Charged the unused 9 of 16 held days' share of an add: dearer than week +2's loss.
    assert exit_("charged") == (pytest.approx(full), None)
    assert exit_("charged", price=0.01)[1] == 1
    assert exit_("free", keep=1.0)[1] is None  # a keeper is never planned out
