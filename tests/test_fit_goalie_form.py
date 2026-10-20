"""scripts/fit_goalie_form.py: a planted rule in synthetic seasons is found."""
import datetime as dt
import random

from clients.nhl_stats import GoalieGame
from scripts import fit_goalie_form as fgf

D0 = dt.date(2025, 10, 1)


def _season(seed: int, teams: int = 12, games: int = 80) -> list[GoalieGame]:
    """Goalie 1 starts 70% of the time, but sits the 3 games after he's pulled
    (a 7-goal start, 1 in 5 of his starts); every game is 2 days apart."""
    rng = random.Random(seed)
    out, gid = [], 0
    for t in range(teams):
        a, b, sit = 100 * t + 1, 100 * t + 2, 0
        for i in range(games):
            gid += 1
            starter = b if sit or rng.random() > 0.7 else a
            sit = max(sit - 1, 0)
            bad = rng.random() < 0.2
            ga = 7 if bad else 2
            date = D0 + dt.timedelta(days=2 * i)
            out.append(GoalieGame(starter, "", f"T{t}", "X", date, gid, True, True,
                                  {"gs": 1, "w": 0 if bad else 1, "ga": ga, "sv": 28 - ga, "so": 0}, 28))
            if bad and starter == a:
                sit = 3
                out.append(GoalieGame(b, "", f"T{t}", "X", date, gid, False, True,
                                      {"gs": 0, "w": 0, "ga": 0, "sv": 10, "so": 0}, 10))
    return out


def test_a_pulled_goalie_sitting_his_next_games_is_found(monkeypatch):
    seasons = {s: _season(s) for s in (*fgf.TRAIN, fgf.TEST)}
    monkeypatch.setattr(fgf.nhl_stats, "goalie_games", lambda season, **_: seasons[season])
    data = {s: fgf.rows(s) for s in seasons}
    train = [r for s in fgf.TRAIN for r in data[s][(1, 1)]]
    test = data[fgf.TEST][(1, 1)]
    beta = fgf.fit(train)
    bad = fgf.FEATURES.index("L bad 1 ago")
    assert beta[bad] < -1  # a pulled goalie rarely starts the next game (the backup's bad starts change nothing)
    # Better than his share alone and than the bot now, which only sees the
    # team's last start (it does see this one: the team's last start was his).
    assert fgf.loss(test, lambda r: fgf.probs(r, beta)) < fgf.loss(test, fgf.shares) - 0.05
    assert fgf.loss(test, lambda r: fgf.probs(r, beta)) <= fgf.loss(test, fgf.now) + 0.01
    # Two games on he still sits: the bot's next-game rule can't see that.
    assert fgf.fit([r for s in fgf.TRAIN for r in data[s][(2, 3)]])[fgf.FEATURES.index("L bad 2 ago")] < -0.5
