import datetime as dt

from clients.nhl_stats import SkaterGame
from scripts.pp_share import shares


def _g(pid, name, game, pp, team="PHI"):
    return SkaterGame(pid, name, "R", team, "NYR", dt.date(2026, 11, 5), game, toi=900.0, pp_toi=pp)


def test_share_of_team_pp_time_and_rank():
    games = [_g(1, "Porter Martone", 10, 60.0)] + [_g(i, f"Mate {chr(64 + i)}", 10, 240.0) for i in range(2, 6)] \
        + [_g(9, "Other", 10, 300.0, team="NYR"), _g(1, "Porter Martone", 11, 0.0), _g(2, "Mate B", 11, 120.0)]
    rows = shares(games, "Porter Martone")
    assert len(rows) == 2
    first = rows[0]
    assert first["team_pp"] == (60 + 4 * 240) / 5  # the other team's PP time doesn't count
    assert round(first["share"], 3) == round(60 / 204, 3)
    assert first["rank"] == 5
    assert rows[1]["share"] == 0 and rows[1]["rank"] is None
