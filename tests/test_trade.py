import pytest

from engine import matchup, trade
from league.roster import RosterPlayer
from tests.test_matchup import MON, WED, FakeContext, _game

ME = "Me"
KNIGHT = RosterPlayer(20, "Spencer Knight", "CHI", ["G"], "G")


def _rosters():
    return {
        ME: [KNIGHT, RosterPlayer(1, "Alex Tuch", "BUF", ["LW", "RW"], "LW")],
        "Bulju": [RosterPlayer(11, "Evan Bouchard", "EDM", ["D"]), RosterPlayer(12, "Quinn Hughes", "VAN", ["D"])],
        "Vantaa": [RosterPlayer(13, "Luke Hughes", "NJD", ["D"])],
    }


def test_resolve_matches_last_names_across_rosters():
    give, get, partner = trade.resolve("knight FOR Bouchard", ME, _rosters())
    assert ([p.id for p in give], [p.id for p in get], partner) == ([20], [11], "Bulju")
    give, get, _ = trade.resolve("Knight, Tuch for Quinn Hughes", ME, _rosters())
    assert [p.id for p in give] == [20, 1] and [p.id for p in get] == [12]


@pytest.mark.parametrize("text, problem", [
    ("Knight", "Send /trade"),
    ("Knight for Hughes", "could be"),
    ("Bouchard for Knight", "Not on your roster"),
    ("Knight for Bouchard, Luke Hughes", "different teams"),
    ("Knight for Nobody", "No rostered player"),
])
def test_resolve_explains_what_is_wrong(text, problem):
    assert problem in trade.resolve(text, ME, _rosters())


def _full(team, first_id, goalies):
    """A full roster of 1-point skaters plus `goalies` goalies, all on `team`."""
    skaters = matchup.ACTIVE_SPOTS - goalies
    return ([RosterPlayer(first_id + i, f"S{first_id + i}", team, ["C"], "BN") for i in range(skaters)]
            + [RosterPlayer(first_id + 50 + i, f"G{first_id + i}", team, ["G"], "BN") for i in range(goalies)])


def test_trading_a_goalie_away_makes_room_for_another_to_keep_three():
    mine, theirs = _full("BOS", 100, 3), _full("NYR", 300, 2) + [RosterPlayer(11, "Star", "NYR", ["C"])]
    theirs = [p for p in theirs if p.id != 300]  # keep them at the limit
    pool = [RosterPlayer(900, "Free Goalie", "PHI", ["G"]), RosterPlayer(9, "Free Skater", "PHI", ["C"])]
    schedule = {MON: [_game(MON, "BOS", "NYR")], WED: [_game(WED, "PHI", "BOS")]}
    result = trade.evaluate(mine, theirs, "Them", [mine[-1]], [theirs[-1]], pool, FakeContext(), schedule, {}, {})
    assert [p.id for p in result.me.adds] == [900]
    assert len(result.me.drops) == 1 and not result.me.drops[0].is_goalie


def test_a_hole_the_weekly_plan_would_fill_anyway_is_no_credit_to_the_trade():
    # My roster has an open spot either way; swapping equal players changes nothing.
    mine = _full("BOS", 100, 3)[1:]
    theirs = _full("NYR", 300, 3)
    pool = [RosterPlayer(9, "Free Skater", "PHI", ["C"])]
    schedule = {MON: [_game(MON, "BOS", "NYR")], WED: [_game(WED, "PHI", "BOS")]}
    result = trade.evaluate(mine, theirs, "Them", [mine[0]], [theirs[0]], pool, FakeContext(), schedule, {}, {})
    assert result.me.per_week == pytest.approx(0)
    assert result.win_even == pytest.approx(0.5)
    assert "About even" in trade.text(result)


def test_a_better_player_shows_as_a_gain_for_me_and_a_loss_for_them():
    mine, theirs = _full("BOS", 100, 3), _full("NYR", 300, 3)
    theirs[0] = RosterPlayer(11, "Star", "NYR", ["C"])  # 4 xFP against everyone else's 1
    schedule = {MON: [_game(MON, "BOS", "NYR")], WED: [_game(WED, "BOS", "NYR")]}
    result = trade.evaluate(mine, theirs, "Them", [mine[0]], [theirs[0]], [], FakeContext(), schedule, {}, {})
    assert result.me.per_week > 0 > result.them.per_week
    assert result.win_even > 0.5
    assert trade.text(result).startswith("Worth proposing")
