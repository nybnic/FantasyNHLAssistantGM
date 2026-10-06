from league.roster import RosterPlayer, active
from scripts.explain_week import mark_ir


def test_mark_ir_moves_a_player_off_the_active_roster():
    players = [RosterPlayer(id=1, name="Macklin Celebrini", team="SJS", positions=["C"], slot="C"),
               RosterPlayer(id=2, name="Mark Scheifele", team="WPG", positions=["C"], slot="C")]
    assert mark_ir(players, ["macklin celebrini", "Nobody Here"]) == ["Nobody Here"]
    assert [p.name for p in active(players)] == ["Mark Scheifele"]
