import random

from scripts import check_forecasts


def _state(effect: float, weeks_=3, teams=16, seed=1):
    """league_weeks with our forecast and points for; teams that add beat the
    forecast by `effect` points per add (the whole week), plus noise and a level bias."""
    rng = random.Random(seed)
    lw, adds, pf = {}, {}, {f"T{t}": 0.0 for t in range(teams)}
    for w in range(1, weeks_ + 1):
        start = {1: "2026-09-29", 2: "2026-10-05", 3: "2026-10-12"}[w]
        ours = {}
        for t in pf:
            n = rng.choice([0, 1, 2])
            adds.setdefault(t, []).extend([start] * n)
            ours[t] = {"expected": 200.0, "sd": 30.0}
            pf[t] += 200.0 + 20.0 + effect * n + rng.gauss(0, 10)
        lw[str(w)] = {"ours": {"teams": ours}, "points_for": dict(pf)}
    return {"league_weeks": lw, "league_adds": adds, "adds": []}


def test_each_teams_week_is_its_points_for_less_the_week_before_against_our_forecast():
    rows = check_forecasts.team_weeks(_state(0.0))
    assert len(rows) == 48 and all(abs(r["miss"] - 20.0) < 40 for r in rows)
    assert {r["week"] for r in rows} == {1, 2, 3}


def test_a_planted_streaming_effect_is_found_and_none_when_there_is_none():
    b, se = check_forecasts.slope(check_forecasts.team_weeks(_state(15.0)), "adds")
    assert abs(b - 15.0) < 3 * se
    b, se = check_forecasts.slope(check_forecasts.team_weeks(_state(0.0)), "adds")
    assert abs(b) < 3 * se
    lines = check_forecasts.report(check_forecasts.team_weeks(_state(0.0)))
    assert lines[0] == "48 team-weeks (3 weeks):" and lines[1].startswith("  week 1: mean miss +2")
