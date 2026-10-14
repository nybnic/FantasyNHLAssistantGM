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


def test_games_missed_counts_his_teams_games_since_he_last_played():
    import datetime as dt
    from clients.nhl_stats import SkaterGame
    from model.context import ModelContext
    days = [dt.date(2026, 10, d) for d in (7, 9, 11, 13)]
    ctx = ModelContext(today=dt.date(2026, 10, 14), season=20262027, skater_priors={}, skater_fallback={},
                       skater_games={5: [SkaterGame(5, "A", "C", "BOS", "TOR", days[1], 1)]}, goalie_history={},
                       goalie_games={}, team_starts={"BOS": [(d, 30) for d in days]}, team_ratings={}, league=None)
    assert ctx.games_missed(5, "BOS") == 2  # played the 9th, missed the 11th and 13th
    assert ctx.games_missed(6, "BOS") == 4  # never played


def test_a_newcomers_pp_time_is_implied_by_his_projected_pp_points():
    # The fallback: a fringe forward's 10 PP seconds a game, the average PP rate 0.0008 points a second.
    fallback = SkaterPrior(position="F", toi=12 * 60, pp_toi=10.0,
                           per_toi={s: (0.001 if s not in PP_STATS else 0.0004) for s in SKATER_STATS})
    row = {"gp": 80.0, "toi": 17.0, "stats": {"g": 20.0, "a": 24.0, "ppg": 6.0, "ppa": 10.0}}
    rookie = with_projection(fallback, row, 1.0, implied_pp=True)
    assert rookie.pp_toi == pytest.approx(0.2 / 0.0008)  # 250 s: where 0.2 PP points a game come at the average rate
    assert rookie.per_toi["ppg"] + rookie.per_toi["ppa"] == pytest.approx(0.0008)
    assert sum(project_skater(rookie, []).per_game[s] for s in PP_STATS) == pytest.approx(0.2)  # DFO's, before games
    # On PP1 (3 min a game) for 7 games with one PP point: near the average rate times his PP time,
    # not DFO's points over the fringe player's 10 seconds times it, as before.
    games = [SkaterGame(1, "Rookie", "RW", "CHI", "BOS", dt.date(2026, 10, d), d,
                        stats={s: (1.0 if s == "ppa" and d == 1 else 0.0) for s in SKATER_STATS},
                        toi=18 * 60, pp_toi=180.0) for d in range(1, 8)]
    pp = sum(project_skater(rookie, games).per_game[s] for s in PP_STATS)
    assert 0.12 < pp < 0.2  # the average rate x 3 min = 0.14, his PP time still between 250 s and 180 s
    old = with_projection(fallback, row, 1.0)
    assert sum(project_skater(old, games).per_game[s] for s in PP_STATS) > 0.6  # as Kantserov: 0.64
    # A projection with few PP points keeps the fallback's PP time as its floor.
    assert with_projection(fallback, {**row, "stats": {"ppg": 0.0}}, 1.0, implied_pp=True).pp_toi == 10.0
