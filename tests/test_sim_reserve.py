"""scripts/sim_reserve.py: the playoffs use only the adds that are left, at
most 2 a week, and end in a place (the semifinal losers play for third)."""
import numpy as np

from scripts import sim_reserve
from scripts.sim_leverage import HOLD_WEEKS, WEEKS


class _League:
    def __init__(self, gap):
        self.means = np.array([[100.0] + [100.0 + gap] * 15])  # I'm `gap` points a week behind everyone
        self.playoff_noise = np.zeros((1, 3, 16))


POOLS = [{"moves": [(6.0, 0.0, 1, None), (5.0, 0.0, 2, None), (4.0, 0.0, 3, None)]}]
PICKS = np.zeros(WEEKS, dtype=int)
BANK = np.zeros(WEEKS + HOLD_WEEKS + 3)
SEEDS = np.arange(8)


def test_the_playoffs_use_only_the_adds_left_two_a_week():
    added = np.zeros(WEEKS + 3)
    # 10 behind each round: 2 adds a week (11 pts) win it, 1 (6 pts) doesn't.
    assert sim_reserve._playoffs(_League(10.0), 0, SEEDS, BANK, POOLS, PICKS, 6, added) == 1
    assert list(added[WEEKS:]) == [11.0, 11.0, 11.0]
    assert sim_reserve._playoffs(_League(10.0), 0, SEEDS, BANK, POOLS, PICKS, 5) == 2  # the final with 1 add
    assert sim_reserve._playoffs(_League(10.0), 0, SEEDS, BANK, POOLS, PICKS, 0) == 0  # out in the quarterfinal


def test_a_semifinal_loser_plays_for_third():
    league = _League(8.0)
    # The quarterfinal won with 2 adds, the semifinal lost with 1 (6 < 8), then
    # the 3rd-place game against seed 3 (the other semifinal's loser, 1 v 2), who has a bad week.
    assert sim_reserve._playoffs(league, 0, SEEDS, BANK, POOLS, PICKS, 3) == 4
    league.playoff_noise[0, 2, 2] = -20.0
    assert sim_reserve._playoffs(league, 0, SEEDS, BANK, POOLS, PICKS, 3) == 3
