import datetime as dt

import pytest

from clients.nhl_stats import SkaterGame
from model.projections import (
    PP_STATS,
    SKATER_STATS,
    SkaterPrior,
    project_skater,
    with_projection,
)

PRIOR = SkaterPrior(
    position="C",
    toi=18 * 60,
    pp_toi=2 * 60,
    per_toi={s: (0.001 if s not in PP_STATS else 0.002) for s in SKATER_STATS},
)


def _game(day: int, toi_min: float, **stats) -> SkaterGame:
    return SkaterGame(
        player_id=1, name="Test Skater", position="C", team="TOR", opponent="BOS",
        date=dt.date(2026, 10, day), game_id=day,
        stats={s: stats.get(s, 0.0) for s in SKATER_STATS},
        toi=toi_min * 60, pp_toi=2 * 60,
    )


def test_no_games_projects_the_prior():
    proj = project_skater(PRIOR, [])
    assert proj.per_game["sog"] == pytest.approx(0.001 * 18 * 60)
    assert proj.per_game["ppg"] == pytest.approx(0.002 * 2 * 60)


def test_recent_ice_time_moves_role_faster_than_skill():
    promoted = [_game(d, 22) for d in range(1, 6)]
    proj = project_skater(PRIOR, promoted)
    # Five games at 22 min pull TOI well over halfway from 18 toward 22.
    assert 20 * 60 < proj.toi < 22 * 60


def test_stable_stats_follow_current_play_sooner_than_noisy_ones():
    hot = [_game(d, 18, sog=6.0, gwg=1.0) for d in range(1, 21)]
    proj = project_skater(PRIOR, hot)
    prior_sog, prior_gwg = 0.001 * 18 * 60, 0.001 * 18 * 60
    sog_share = (proj.per_game["sog"] - prior_sog) / (6.0 - prior_sog)
    gwg_share = (proj.per_game["gwg"] - prior_gwg) / (1.0 - prior_gwg)
    assert sog_share > 0.4
    assert gwg_share < 0.15


def test_full_weight_projection_reproduces_its_per_game_rates():
    row = {"gp": 80.0, "toi": 20.0, "stats": {"g": 40.0, "sog": 240.0, "ppg": 16.0}}
    blended = with_projection(PRIOR, row, 1.0)
    proj = project_skater(blended, [])
    assert proj.toi == pytest.approx(20 * 60)
    assert proj.per_game["g"] == pytest.approx(0.5)
    assert proj.per_game["sog"] == pytest.approx(3.0)
    assert proj.per_game["ppg"] == pytest.approx(0.2)
    # Stats the projection doesn't cover keep the prior's per-TOI rate.
    assert blended.per_toi["gwg"] == PRIOR.per_toi["gwg"]


def test_half_weight_projection_averages_per_game():
    row = {"gp": 80.0, "toi": 18.0, "stats": {"hit": 160.0}}
    proj = project_skater(with_projection(PRIOR, row, 0.5), [])
    assert proj.per_game["hit"] == pytest.approx(0.5 * 2.0 + 0.5 * 0.001 * 18 * 60)
