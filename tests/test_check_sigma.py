"""scripts/check_sigma.py's --from-day: a week projected from its Sunday on, on a
made-up season (offline), and its helpers."""
import datetime as dt
import random
import sys

import pytest

from clients.nhl_stats import GoalieGame, SkaterGame
from scripts import check_sigma

D = dt.date(2025, 10, 6)  # a Monday


def test_banked_starts_counts_two_goalie_slots_a_day():
    mon, tue, wed = D, D + dt.timedelta(days=1), D + dt.timedelta(days=2)
    starts = [{mon: 5.0, tue: 4.0}, {mon: 9.0}, {mon: 1.0, wed: 3.0}]
    # Monday's three starts fill two slots, the better two.
    assert check_sigma.banked_starts(starts, [mon, tue]) == (3, 5.0 + 9.0 + 4.0)
    assert check_sigma.banked_starts(starts, []) == (0, 0.0)


def test_tails_pool_both_sides_against_one_normal_tail():
    zs = [-2.0, -0.5, 0.0, 0.5, 2.0]
    (k, got, normal), = check_sigma.tails(zs, cuts=(1.0,))
    assert (k, got) == (1.0, 0.2)  # 2 of 5 beyond 1 sigma, either side: 1 in 5 for one side
    assert normal == pytest.approx(0.1587, abs=1e-4)


def _season(year: int, rng: random.Random):
    """32 teams of 4 C, 3 L, 3 R, 6 D and 2 goalies, every team playing every other day."""
    skaters, goalies = [], []
    start = dt.date(year, 10, 1)
    for t in range(32):
        team = f"T{t:02d}"
        roster = [(1000 * t + i, pos) for i, pos in enumerate("CCCCLLLRRRDDDDDD")]
        for day in range(0, 170, 2):
            date = start + dt.timedelta(days=day + t % 2)
            game_id = year * 1000 + day * 32 + t
            for pid, pos in roster:
                stats = {"g": rng.random() < 0.15, "a": rng.random() < 0.25, "sog": rng.randint(0, 5),
                         "hit": rng.randint(0, 3), "blk": rng.randint(0, 3)}
                skaters.append(SkaterGame(pid, f"P{pid}", pos, team, "X", date, game_id,
                                          {k: float(v) for k, v in stats.items()}, toi=1000.0))
            starter = 1000 * t + 900 + (rng.random() < 0.35)
            for gid in (1000 * t + 900, 1000 * t + 901):
                saves = rng.randint(18, 32)
                goalies.append(GoalieGame(gid, f"G{gid}", team, "X", date, game_id, gid == starter, True,
                                          {"gs": float(gid == starter), "w": float(rng.random() < 0.5),
                                           "ga": float(rng.randint(0, 5)), "sv": float(saves)},
                                          shots_against=saves + 3.0))
    return sorted(skaters, key=lambda g: g.date), sorted(goalies, key=lambda g: g.date)


def test_from_day_runs_on_a_made_up_season(monkeypatch, capsys):
    rng = random.Random(3)
    seasons = {s: _season(s // 10_000, rng) for s in (20252026, 20242025, 20232024)}
    monkeypatch.setattr(check_sigma.nhl_stats, "skater_games", lambda s: seasons[s][0])
    monkeypatch.setattr(check_sigma.nhl_stats, "goalie_games", lambda s: seasons[s][1])
    monkeypatch.setattr(check_sigma.nhl_stats, "skater_birth_dates", lambda s: {})
    monkeypatch.setattr(sys, "argv", ["check_sigma", "--leagues", "1", "--from-day", "6", "1", "0"])
    check_sigma.main_()
    out = capsys.readouterr().out
    assert "== from Sun on (1 day still to play) ==" in out
    assert "== from Mon on (7 days still to play) ==" in out
    assert "== from Tue on (6 days still to play) ==" in out
    assert out.count("k 1.44:") == 3
    assert out.count("both goalie minimums met by then") == 2  # not for Monday


def test_from_day_and_ahead_dont_mix(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_sigma", "--ahead", "1", "--from-day", "6"])
    with pytest.raises(SystemExit):
        check_sigma.main_()
