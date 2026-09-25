import datetime as dt

from engine import goalie_checks

TODAY = dt.date(2026, 10, 10)


def _goalie(player_id, name, selected_position, team="TOR"):
    return {
        "player_id": player_id,
        "name": name,
        "position_type": "G",
        "eligible_positions": ["G"],
        "selected_position": selected_position,
        "editorial_team_abbr": team,
        "status": "",
    }


def test_bench_goalie_confirmed_starting_is_flagged(monkeypatch):
    roster = [_goalie(1, "Test Goalie", "BN")]

    monkeypatch.setattr(goalie_checks.nhl_client, "teams_playing_on", lambda date: {"TOR"})
    monkeypatch.setattr(
        goalie_checks.goalie_client,
        "get_starters",
        lambda date: {"TOR": {"goalie_name": "Test Goalie", "confirmed": True}},
    )

    recs = goalie_checks.check_goalies(roster, lookahead_days=0, today=TODAY)

    assert len(recs) == 1
    assert "start him" in recs[0].message
    assert recs[0].category == "goalie"


def test_active_goalie_not_starting_is_flagged(monkeypatch):
    roster = [_goalie(2, "Bench Me", "G")]

    monkeypatch.setattr(goalie_checks.nhl_client, "teams_playing_on", lambda date: {"TOR"})
    monkeypatch.setattr(
        goalie_checks.goalie_client,
        "get_starters",
        lambda date: {"TOR": {"goalie_name": "Someone Else", "confirmed": True}},
    )

    recs = goalie_checks.check_goalies(roster, lookahead_days=0, today=TODAY)

    assert len(recs) == 1
    assert "benching" in recs[0].message


def test_no_flag_when_starter_not_confirmed(monkeypatch):
    roster = [_goalie(3, "Uncertain Guy", "BN")]

    monkeypatch.setattr(goalie_checks.nhl_client, "teams_playing_on", lambda date: {"TOR"})
    monkeypatch.setattr(goalie_checks.goalie_client, "get_starters", lambda date: {})

    recs = goalie_checks.check_goalies(roster, lookahead_days=0, today=TODAY)

    assert recs == []


def test_no_flag_when_team_not_playing(monkeypatch):
    roster = [_goalie(4, "Off Day Guy", "BN")]

    monkeypatch.setattr(goalie_checks.nhl_client, "teams_playing_on", lambda date: set())
    monkeypatch.setattr(
        goalie_checks.goalie_client,
        "get_starters",
        lambda date: {"TOR": {"goalie_name": "Off Day Guy", "confirmed": True}},
    )

    recs = goalie_checks.check_goalies(roster, lookahead_days=0, today=TODAY)

    assert recs == []


def test_active_goalie_confirmed_starting_is_not_flagged(monkeypatch):
    roster = [_goalie(5, "Already Right", "G")]

    monkeypatch.setattr(goalie_checks.nhl_client, "teams_playing_on", lambda date: {"TOR"})
    monkeypatch.setattr(
        goalie_checks.goalie_client,
        "get_starters",
        lambda date: {"TOR": {"goalie_name": "Already Right", "confirmed": True}},
    )

    recs = goalie_checks.check_goalies(roster, lookahead_days=0, today=TODAY)

    assert recs == []
