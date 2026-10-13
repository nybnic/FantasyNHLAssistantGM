"""scripts/fit_goalie_absence.py: which games start an absence, and the rows it scores."""
import datetime as dt

from scripts import fit_goalie_absence as fga

D0 = dt.date(2025, 10, 1)


def _games(starters, appeared=None):
    return [fga.TeamGame(D0 + dt.timedelta(days=2 * i), s, frozenset(appeared[i] if appeared else {s}))
            for i, s in enumerate(starters)]


def test_an_absence_starts_when_the_starter_misses_two_games_in_a_row():
    # Starter 1 starts 15 of 16, then misses games 16-17 (2 starts), then 2 starts the rest.
    starters = [1] * 8 + [2] + [1] * 7 + [2] * 14
    rows = fga.rows({"NYR": _games(starters)})
    assert rows and all(r["share"] < 0.5 for r in rows)  # 2's own share is small
    days = sorted({r["days"] for r in rows})
    assert days[0] == 0 and len(days) == fga.AHEAD_GAMES  # from the first game after the 2 missed
    assert all(r["y"] for r in rows)  # 2 started every one
    freed = [r["freed"] for r in sorted(rows, key=lambda r: r["days"])]
    assert freed[0] > 0.7 and freed == sorted(freed, reverse=True)  # less as his return nears
    assert all(r["rivals"] == 0 for r in rows)
    # Redistributed, 2's predicted share is far closer to what happened.
    assert fga.log_loss(rows, 1.0) < fga.log_loss(rows, 0.0)


def test_a_starter_resting_one_game_or_traded_away_is_no_absence():
    rest = [1] * 8 + [2] + [1] * 7 + [2] + [1] * 13
    assert fga.rows({"NYR": _games(rest)}) == []
    traded = [1] * 8 + [2] + [1] * 7 + [2] * 14
    games = {"NYR": _games(traded), "BOS": [fga.TeamGame(D0 + dt.timedelta(days=40), 1, frozenset({1}))]}
    assert fga.rows(games) == []
