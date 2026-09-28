import pytest

from model import games
from model.games import League, TeamRating

LEAGUE = League(goals=3.0, shots=30.0, save_pct=0.90, home_edge=1.0)
AVERAGE = TeamRating(gf=3.0, ga=3.0, sf=30.0, sa=30.0)


def test_even_matchup_is_a_coin_flip():
    ratings = {"AAA": AVERAGE, "BBB": AVERAGE}
    line = games.goalie_start("AAA", "BBB", home=True, goalie_save_pct=0.90, team_ratings=ratings, league=LEAGUE)
    assert line["w"] / games.DECISION_SHARE == pytest.approx(0.5, abs=1e-6)
    # The starter's own line only covers his share of the shots.
    assert line["ga"] == pytest.approx(30.0 * games.STARTER_SHOT_SHARE * 0.10)


def test_stronger_team_and_better_goalie_score_more():
    strong = TeamRating(gf=3.6, ga=2.5, sf=31.0, sa=25.0)
    ratings = {"AAA": strong, "BBB": AVERAGE}
    fav = games.goalie_start("AAA", "BBB", True, 0.915, ratings, LEAGUE)
    dog = games.goalie_start("BBB", "AAA", False, 0.895, ratings, LEAGUE)
    assert fav["w"] > 0.55 > dog["w"]
    assert fav["so"] > dog["so"]
    assert fav["xfp"] > dog["xfp"]


def test_team_games_pairs_opponents():
    from clients.nhl_stats import GoalieGame
    import datetime as dt

    day = dt.date(2026, 10, 10)
    rows = [
        GoalieGame(1, "Home G", "AAA", "BBB", day, 99, True, True, {"ga": 2, "sv": 28}, 30),
        GoalieGame(2, "Away G", "BBB", "AAA", day, 99, True, False, {"ga": 4, "sv": 21}, 25),
    ]
    by_team = {tg.team: tg for tg in games.team_games(rows)}
    assert (by_team["AAA"].gf, by_team["AAA"].ga, by_team["AAA"].sf) == (4, 2, 25)
    assert by_team["BBB"].home is False
