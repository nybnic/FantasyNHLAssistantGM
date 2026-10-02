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


def test_trading_a_goalie_away_below_two_makes_room_for_another():
    mine, theirs = _full("BOS", 100, 2), _full("NYR", 300, 2) + [RosterPlayer(11, "Star", "NYR", ["C"])]
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


def test_short_counts_starting_slots_a_roster_cant_fill_itself():
    # Three centers can fill C twice but no wing slot: 4 forward slots short, plus D and G.
    roster = [RosterPlayer(i, f"C{i}", "BOS", ["C"]) for i in range(3)] + [
        RosterPlayer(10 + i, f"D{i}", "BOS", ["D"]) for i in range(3)] + [RosterPlayer(20, "G", "BOS", ["G"])]
    assert sorted(trade.short(roster)) == ["D", "F", "F", "F", "F", "G"]
    assert trade.balance(roster) == "3F 3D 1G"
    assert trade.short(_full("BOS", 100, 2)[:0] + [
        RosterPlayer(i, "W", "BOS", ["C", "LW", "RW"]) for i in range(6)] + [
        RosterPlayer(10 + i, "D", "BOS", ["D"]) for i in range(4)] + [
        RosterPlayer(20 + i, "G", "BOS", ["G"]) for i in range(2)]) == []


def test_a_trade_that_leaves_them_short_of_starters_is_flagged():
    mine = _full("BOS", 100, 3)
    theirs = [RosterPlayer(300 + i, f"F{i}", "NYR", ["C", "LW", "RW"]) for i in range(8)] + [
        RosterPlayer(400 + i, f"D{i}", "NYR", ["D"]) for i in range(4)] + [
        RosterPlayer(500 + i, f"G{i}", "NYR", ["G"]) for i in range(2)]
    schedule = {MON: [_game(MON, "BOS", "NYR")]}
    result = trade.evaluate(mine, theirs, "Them", [mine[0]], [theirs[8]], [], FakeContext(), schedule, {}, {})
    assert result.them.short == ["D"]
    assert result.them.balance == ("8F 4D 2G", "9F 3D 2G")
    assert "short of starters (1 D)" in trade.text(result)


def test_screen_keeps_trades_they_would_not_see_as_a_loss():
    mine = [RosterPlayer(1, "My Star", "BOS", ["C"]), RosterPlayer(2, "My Depth", "BOS", ["C"])]
    theirs = {"Them": [RosterPlayer(11, "Their Star", "NYR", ["C"]), RosterPlayer(12, "Their Depth", "NYR", ["C"])]}
    value = {1: 5.0, 2: 3.0, 11: 6.0, 12: 2.0}
    # Their star went in round 1: my round-5 depth for him (a gain for me) doesn't
    # "feel" like enough to them; my own first-rounder does.
    rounds = {"my star": 1, "my depth": 5, "their star": 1, "their depth": 9}
    found = trade.screen(mine, theirs, [], value, rounds)
    assert [([p.id for p in give], get.id) for _, _, give, get in found] == [([1], 11)]
    assert found[0][0] == pytest.approx(1.0)
    assert [([p.id for p in give], get.id) for _, _, give, get in trade.screen(mine, theirs, [], value, {})] == [
        ([2], 11), ([1], 11)]  # without draft data every trade "feels" even


def test_screen_skips_trades_that_leave_them_short_of_starters():
    mine = [RosterPlayer(1, "My C", "BOS", ["C"])]
    their_d = [RosterPlayer(10 + i, f"D{i}", "NYR", ["D"]) for i in range(4)]
    value = {1: 3.0, **{p.id: 6.0 for p in their_d}}
    assert trade.screen(mine, {"Them": their_d}, [], value, {}) == []


def test_suggestions_text_without_any():
    assert "No trade" in trade.suggestions_text([])


def test_balance_counts_the_injured():
    from clients.dfo_lines import LineInfo
    roster = [RosterPlayer(1, "Hurt Guy", "BOS", ["C"]), RosterPlayer(2, "Fine Guy", "BOS", ["D"])]
    lines = {"BOS": {"hurt guy": LineInfo(groups={"ir"}, injury="ir"), "fine guy": LineInfo(groups={"d1"})}}
    assert trade.balance(roster, lines) == "1F 1D 0G (1 hurt)"
