import numpy as np

from scripts import depth
from scripts.depth import Player

WAIVER = {"F": 3.0, "D": 2.0, "G": 2.0}
STARTER = {"C": 5.0, "LW": 5.0, "RW": 5.0, "D": 4.0, "G": 5.0}
NHL = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]


def _team(prefix: int, forwards: list[tuple[str, ...]], d: int = 4, g: int = 2, f_value: float = 5.0) -> list[Player]:
    """Players spread over NHL teams, so they play on different nights."""
    out = [Player(prefix + i, f"F{prefix + i}", NHL[i % 8], pos, f_value) for i, pos in enumerate(forwards)]
    out += [Player(prefix + 50 + i, f"D{prefix + i}", NHL[(i + 3) % 8], ("D",), 4.0) for i in range(d)]
    out += [Player(prefix + 80 + i, f"G{prefix + i}", NHL[(i + 5) % 8], ("G",), 5.0) for i in range(g)]
    return out


def _random_nights() -> depth.Nights:
    return depth.Nights(set(NHL), n=2000)


def _every_night(n: int = 4) -> depth.Nights:
    nights = depth.Nights({"AAA"}, n=n)
    nights.plays = np.ones_like(nights.plays)  # the stand-ins too
    return nights


def test_assign_solves_eligibility_exactly():
    # Three C-only and one C/LW: the C/LW must take LW so all four start.
    playing = tuple(sorted([(6.0, ("C",)), (5.0, ("C",)), (4.0, ("C",)), (3.0, ("C", "LW"))]))
    slots = dict(zip(playing, depth.assign(playing)))
    assert slots[(3.0, ("C", "LW"))] == "LW"
    assert slots[(4.0, ("C",))] is None
    d = tuple(sorted((float(v), ("D",)) for v in range(1, 7)))
    assert [s for s in depth.assign(d)] == [None, None, "D", "D", "D", "D"]  # the best four


def test_week_counts_starters_on_their_nights():
    nights = _every_night()
    team = _team(0, [("C",), ("C",), ("LW",), ("LW",), ("RW",), ("RW",)])
    nights.index = dict.fromkeys(nights.index, 0)  # one NHL team: everyone plays every night
    week = nights.week(team)
    assert week == {"C": 70.0, "LW": 70.0, "RW": 70.0, "D": 112.0, "G": 70.0}
    nights.plays[:, :] = False
    assert depth.total(nights.week(team)) == 0.0


def test_cap_sets_the_least_useful_aside_and_keeps_two_goalies():
    nights = _random_nights()
    team = _team(0, [("C",), ("C",), ("LW",), ("LW",), ("RW",), ("RW",), ("C",), ("C",)], g=2)
    team.append(Player(99, "Spare C", "AAA", ("C",), 1.0))
    team.append(Player(98, "Third G", "BBB", ("G",), 0.5))  # 16 players: both go
    active, extra = depth.cap(team, nights)
    assert len(active) == depth.ACTIVE_SPOTS and sorted(p.name for p in extra) == ["Spare C", "Third G"]
    assert depth._goalies(active) == 2


def test_need_is_highest_where_the_team_is_thin():
    nights = _random_nights()
    no_rw = _team(0, [("C",)] * 4 + [("LW",)] * 4, d=4, g=2)
    d = depth.analyse("A", no_rw, nights, WAIVER, STARTER)
    assert max(d.need, key=d.need.get) == "RW"
    assert d.need["C"] <= 0  # a fifth C wouldn't start


def test_complementary_teams_both_gain_from_a_swap():
    nights = _random_nights()
    c_heavy = depth.analyse("A", _team(0, [("C",)] * 4 + [("LW",)] * 2 + [("RW",)] * 2), nights, WAIVER, STARTER)
    no_c = depth.analyse("B", _team(100, [("LW",)] * 2 + [("RW",)] * 6), nights, WAIVER, STARTER)
    found = depth.swaps(c_heavy, no_c, nights)
    assert found and found[0].give.positions == ("C",) and found[0].get.positions == ("RW",)
    assert found[0].mine > 0 and found[0].theirs > 0


def test_no_swap_leaves_a_team_under_two_goalies():
    nights = _random_nights()
    me = depth.analyse("A", _team(0, [("C",)] * 8, g=2), nights, WAIVER, STARTER)
    them = depth.analyse("B", _team(100, [("RW",)] * 8, g=2), nights, WAIVER, STARTER)
    assert all(s.give.group != "G" and s.get.group != "G" for s in depth.swaps(me, them, nights))
