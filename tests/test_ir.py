from clients.dfo_lines import LineInfo
from engine import ir
from league.roster import RosterPlayer

LINES = {"BOS": {"long out": LineInfo(groups={"ir"}, injury="ir"), "short out": LineInfo(groups={"f2"}, injury="out"),
                 "doubtful": LineInfo(groups={"f3"}, injury="dtd"), "healed": LineInfo(groups={"d2"})}}


def _p(pid, name, slot):
    return RosterPlayer(pid, name, "BOS", ["C"], slot)


def test_injured_players_fit_the_free_ir_slots_ir_first():
    roster = [_p(1, "Short Out", "C"), _p(2, "Long Out", "BN"), _p(3, "Doubtful", "C"), _p(4, "Fine", "C")]
    assert [(m.player.id, m.slot, m.status) for m in ir.moves(roster, LINES)] == [(2, "IR", "IR"), (1, "IR+", "O")]
    # IR+ taken: an "O" has nowhere to go, an "IR" still fits the IR slot.
    roster.append(_p(5, "Already There", "IR+"))
    assert [(m.player.id, m.slot) for m in ir.moves(roster, LINES)] == [(2, "IR")]


def test_a_healed_player_in_an_ir_slot_needs_a_spot():
    roster = [_p(1, "Healed", "IR+"), _p(2, "Long Out", "IR"), _p(3, "Fine", "C")]
    assert [p.id for p in ir.returning(roster, LINES)] == [1]
    text = ir.text([], ir.returning(roster, LINES), roster[2])
    assert "Healed is back in BOS's lineup" in text and "the weakest to drop is Fine" in text


def test_the_plan_assumes_the_ir_moves_without_touching_the_roster():
    roster = [_p(1, "Short Out", "C"), _p(4, "Fine", "C")]
    moves = ir.moves(roster, LINES)
    assert [p.slot for p in ir.after(roster, moves)] == ["IR+", "C"] and roster[0].slot == "C"
    assert "move Short Out to IR+" in ir.text(moves, []) and "an add needs no drop" in ir.text(moves, [])


def test_yahoos_likely_tag_follows_dfos_status():
    assert ir.likely_status(LineInfo(groups={"ir"}, injury="dtd")) is None  # day-to-day: stays active
    assert ir.likely_status(LineInfo(groups={"ir"}, injury="out")) == "O"
    assert ir.likely_status(LineInfo(groups={"ir"})) == "IR"
    assert ir.likely_status(LineInfo(groups={"f1"}, injury="ir")) == "IR"
