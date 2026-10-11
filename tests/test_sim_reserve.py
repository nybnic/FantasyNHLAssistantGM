"""scripts/sim_reserve.py: the playoffs use only the adds that are left, at most 2 a week."""
import numpy as np

from scripts import sim_reserve
from scripts.sim_leverage import HOLD_WEEKS, WEEKS


class _League:
    def __init__(self, gap):
        self.means = np.array([[100.0] + [100.0 + gap] * 15])  # I'm `gap` points a week behind everyone
        self.playoff_noise = np.zeros((1, 3, 16))


def test_the_playoffs_use_only_the_adds_left_two_a_week():
    pools = [{"moves": [(6.0, 0.0, 1, None), (5.0, 0.0, 2, None), (4.0, 0.0, 3, None)]}]
    picks = np.zeros(WEEKS, dtype=int)
    bank = np.zeros(WEEKS + HOLD_WEEKS + 3)
    seeds = np.arange(8)
    added = np.zeros(WEEKS + 3)
    # 10 behind each round: 2 adds a week (11 pts) win it, 1 (6 pts) doesn't.
    assert sim_reserve._playoffs(_League(10.0), 0, seeds, bank, pools, picks, 6, added)
    assert list(added[WEEKS:]) == [11.0, 11.0, 11.0]
    assert not sim_reserve._playoffs(_League(10.0), 0, seeds, bank, pools, picks, 5)  # the final with 1 add
    assert not sim_reserve._playoffs(_League(10.0), 0, seeds, bank, pools, picks, 0)
