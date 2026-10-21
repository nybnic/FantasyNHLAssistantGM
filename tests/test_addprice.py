import math
from types import SimpleNamespace

import numpy as np
import pytest

from engine import addprice


def _pool(moves, sigma=35.0, tau=20.0):
    return {"moves": moves, "sigma": sigma, "tau": tau}


STREAMERS = [[8.0, 0.0, 1, 10], [6.0, 0.0, 2, 11], [4.0, 0.0, 3, 12], [2.0, 0.0, 4, 13]]


def test_a_later_point_is_worth_what_a_point_does_in_a_typical_matchup():
    # Margin spread tau and result spread sigma combine: 1 / sqrt(2 pi (sigma^2 + tau^2)).
    assert addprice.later_weight(35.0, 20.0) == pytest.approx(1 / math.sqrt(2 * math.pi * (35 ** 2 + 20 ** 2)))
    assert addprice.later_weight(35.0, 5.0) > addprice.later_weight(35.0, 30.0)  # a tight league: points matter more


def test_the_pace_keeps_the_playoff_reserve_until_the_playoffs():
    assert addprice.pace(35, 1) == pytest.approx(29 / 23)
    assert addprice.pace(6, 10) is None  # only the reserve is left
    assert addprice.pace(6, 24) == pytest.approx(2.0)  # playoffs: spend it, 3 weeks


def test_the_price_spends_adds_at_the_budgets_pace():
    pools = [_pool(STREAMERS)]
    weight = addprice.later_weight(35.0, 20.0)
    lam = addprice.solve(pools, 1.3, weight, seed=3)
    v1, v2 = addprice._picks(pools[0], weight, addprice.SIMS_PER_POOL, np.random.default_rng(3))
    spent = np.mean((v1 >= lam).astype(int) + (np.minimum(v2, v1) >= lam).astype(int))
    assert spent == pytest.approx(1.3, abs=0.02)
    assert addprice.solve(pools, 2.0, weight) == 0.0  # the weekly cap is the pace: spend whenever it helps
    assert addprice.solve([_pool([])], 1.3, weight) == 0.0


def test_close_weeks_get_the_second_add_and_lopsided_ones_none():
    # The same four streamers (+8, +6, +4, +2 pts), weeks at different expected margins.
    weight = addprice.later_weight(35.0, 20.0)
    lam = addprice.solve([_pool(STREAMERS)], 1.3, weight)
    phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def adds(margin):
        first = [phi((margin + g) / 35) - phi(margin / 35) for g, *_ in STREAMERS]
        j = max(range(len(first)), key=first.__getitem__)
        after = margin + STREAMERS[j][0]
        second = max(phi((after + g) / 35) - phi(after / 35) for i, (g, *_) in enumerate(STREAMERS) if i != j)
        return int(first[j] >= lam) + int(first[j] >= lam and second >= lam)

    assert [adds(m) for m in (0, 10, 20, 40, -40)] == [2, 1, 1, 0, 0]


def _move(add, drop, gain, later=0.0):
    return SimpleNamespace(add=SimpleNamespace(id=add), drop=SimpleNamespace(id=drop) if drop else None,
                           week_gain=gain, long_term=later, ahead_pts=0.0)


def test_the_pool_keeps_each_adds_other_drops_so_a_second_add_is_seen():
    # Every add is best with drop 10, so best-drop-only pools never held a second
    # add; each add's next drop makes one (week 4, 2026-10-21: the price 8.7 -> ~14).
    moves = [_move(1, 10, 8.0), _move(1, 11, 7.0), _move(2, 10, 6.0), _move(2, 12, 5.0), _move(3, None, 1.0)]
    pool = addprice.pool_entry(moves, 7, 35.0, 20.0)
    assert [m[2:] for m in pool["moves"]] == [[1, 10], [1, 11], [2, 10], [2, 12], [3, None]]
    weight = addprice.later_weight(35.0, 20.0)
    _, v2 = addprice._picks(pool, weight, 200, np.random.default_rng(1))
    assert np.isfinite(v2).all()  # 1 for 10, then 2 for 12
    same_drop = _pool([[8.0, 0.0, 1, 10], [6.0, 0.0, 2, 10]])
    _, v2 = addprice._picks(same_drop, weight, 200, np.random.default_rng(1))
    assert not np.isfinite(v2).any()
