from draft import scoring
from draft.projections import Projection, norm_name
from draft.value import assign_tiers, value_positions


def test_skater_points_uses_league_weights():
    line = {"g": 30, "a": 45, "sog": 250, "fow": 900, "hit": 50, "blk": 40}
    # 135 + 135 + 125 + 180 + 30 + 32
    assert scoring.skater_points(line) == 637


def test_goalie_start_is_worth_about_ten_points():
    start = {"gs": 1, "w": 0.55, "sv": 27.3, "ga": 2.7, "so": 0.0}
    assert 9.8 < scoring.goalie_points(start) < 10.2


def test_norm_name_handles_accents_nicknames_and_middle_names():
    assert norm_name("Tim Stützle") == "tim stutzle"
    assert norm_name("Nicholas Robertson") == norm_name("Nick Robertson")
    assert norm_name("Elias Nils Pettersson") == "elias pettersson"
    assert norm_name("J.T. Miller") == "jt miller"


def _proj(pid: int, pos: str, fpts: float, elig: set[str] | None = None) -> Projection:
    return Projection(player_id=pid, name=f"p{pid}", team="TST", pos=pos, age=25,
                      games=82, stats={}, fpts=fpts, elig=elig or {pos})


def test_dual_eligible_player_is_valued_at_the_weaker_position():
    # Deep center pool, shallow right wing: a C/RW should count as a winger.
    projs = [_proj(i, "C", 700 - i) for i in range(60)]
    projs += [_proj(100 + i, "RW", 500 - 3 * i) for i in range(60)]
    projs += [_proj(999, "C", 560, {"C", "RW"})]
    for pos in ("LW", "D", "G"):
        projs += [_proj(1000 + 100 * len(pos) + i, pos, 300) for i in range(80)]
    levels, assigned = value_positions(projs)
    assert levels["C"] > levels["RW"]
    assert assigned[999] == "RW"


def test_tiers_break_on_big_gaps_and_cap_width():
    values = [300, 298, 296, 250, 248, 246]
    assert assign_tiers(values) == [1, 1, 1, 2, 2, 2]
    flat = [200 - i for i in range(60)]  # evenly spaced: only the width cap splits it
    tiers = assign_tiers(flat)
    assert tiers[0] == 1 and tiers[-1] >= 3
