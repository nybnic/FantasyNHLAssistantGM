import pytest

from draft import consensus
from draft.projections import SKATER_STATS, Projection


def _skater(**stats) -> Projection:
    line = {k: 0.0 for k in SKATER_STATS} | stats
    return Projection(player_id=1, name="Skater", team="TST", pos="C", age=27, games=80,
                      stats=line, fpts=0.0)


def _goalie(starts: float, **stats) -> Projection:
    return Projection(player_id=2, name="Goalie", team="TST", pos="G", age=28, games=starts,
                      stats=stats, fpts=0.0)


def test_skater_blend_averages_rates_and_pulls_points_toward_nhl_com():
    p = _skater(g=30, a=40, ppg=10, ppa=15, sog=200, fow=800, hit=50, blk=40, pim=20, gwg=5)
    kodo = {"GP": 80, "G": 40, "A": 50, "+/-": 0, "PPP": 25, "SHP": 0,
            "SOG": 240, "FOW": 600, "HIT": 70, "BLK": 40}
    view = consensus.blend_skater(p, kodo, nhl_points=100)

    assert p.games == 80
    assert p.stats["sog"] == pytest.approx(220)
    assert p.stats["fow"] == pytest.approx(700)
    # Model 70 pts + Kodo 90 -> 80 blended; NHL.com's 100 is a third opinion -> 86.7.
    assert p.stats["g"] + p.stats["a"] == pytest.approx((80 * 2 + 100) / 3)
    # PIM isn't in Kodo, so it stays the model's number.
    assert p.stats["pim"] == pytest.approx(20)
    assert view.kodo_fpts is not None and p.fpts > 0


def test_goalie_starts_average_model_kodo_and_nhl_implied_starts():
    p = _goalie(60, gs=60, w=30, sv=1500, ga=150, so=3, sv_pct=1500 / 1650)
    kodo = {"GS": 50, "W": 25, "SHO": 2, "SV%": 0.9, "SV": 1350}
    consensus.blend_goalie(p, kodo, nhl_wins=20)
    # Both sources win half their starts -> NHL.com's 20 W imply 40 starts.
    assert p.games == pytest.approx((60 + 50 + 40) / 3)
    assert p.stats["w"] == pytest.approx(0.5 * p.games)


def test_team_starts_are_capped_at_a_season():
    a = _goalie(70, gs=70, w=35, sv=1800, ga=180, so=4, sv_pct=0.909)
    b = _goalie(40, gs=40, w=20, sv=1000, ga=100, so=2, sv_pct=0.909)
    b.player_id = 3
    consensus.cap_team_starts([a, b], team_games=88)
    assert a.games + b.games == pytest.approx(88)
