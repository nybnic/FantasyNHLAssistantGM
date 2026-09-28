from engine.lineup import Candidate, optimize

SLOTS = {"C": 1, "LW": 1, "D": 1, "G": 1}


def test_starts_players_with_games_over_players_without():
    cands = [
        Candidate(1, ("C",), value=0.0, current_slot="C"),  # no game
        Candidate(2, ("C",), value=4.0, current_slot="BN"),
        Candidate(3, ("D",), value=3.0, current_slot="D"),
        Candidate(4, ("G",), value=8.0, current_slot="G"),
    ]
    assignment = optimize(cands, SLOTS)
    assert assignment == {1: "BN", 2: "C", 3: "D", 4: "G"}


def test_uses_multi_position_eligibility():
    # Only a C/LW dual can let both centers start.
    cands = [
        Candidate(1, ("C", "LW"), value=5.0, current_slot="C"),
        Candidate(2, ("C",), value=4.0, current_slot="BN"),
        Candidate(3, ("LW",), value=1.0, current_slot="LW"),
    ]
    assert optimize(cands, SLOTS) == {1: "LW", 2: "C", 3: "BN"}


def test_keeps_current_slots_when_nothing_changes():
    cands = [
        Candidate(1, ("C", "LW"), value=0.0, current_slot="LW"),
        Candidate(2, ("C", "LW"), value=0.0, current_slot="C"),
    ]
    assert optimize(cands, SLOTS) == {1: "LW", 2: "C"}


def test_season_value_breaks_ties_when_lineup_is_unknown():
    cands = [
        Candidate(1, ("D",), value=0.0, season_value=2.0),
        Candidate(2, ("D",), value=0.0, season_value=6.0),
    ]
    assert optimize(cands, SLOTS) == {1: "BN", 2: "D"}
