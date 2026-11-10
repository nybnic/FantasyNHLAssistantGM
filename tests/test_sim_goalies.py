"""The goalie-policy replay's pieces that run offline."""
from engine import matchup
from scripts import sim_goalies


def test_at_least_counts_the_odds_of_reaching_the_minimum():
    assert sim_goalies.at_least(0, []) == 1.0
    assert sim_goalies.at_least(-1, [0.5]) == 1.0
    assert sim_goalies.at_least(1, []) == 0.0
    assert abs(sim_goalies.at_least(1, [0.5, 0.5]) - 0.75) < 1e-12
    assert abs(sim_goalies.at_least(2, [0.5, 0.5]) - 0.25) < 1e-12
    probs = [0.7, 0.66, 0.48, 0.62, 0.4]
    for need in range(6):  # the same as the engine's goalie-minimum odds
        assert abs(sim_goalies.at_least(need, probs) - matchup._at_least(probs, 0, need)) < 1e-12


def test_stream_variants_are_replayed_at_each_threshold():
    assert all(0 < b < 1 for b in sim_goalies.STREAM_BELOW)
    assert len(set(sim_goalies.STREAM_BELOW)) == len(sim_goalies.STREAM_BELOW)
