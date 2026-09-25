from engine import roster_checks

ROSTER_SLOTS = {
    "C": {"position_type": "P", "count": 2},
    "LW": {"position_type": "P", "count": 2},
    "RW": {"position_type": "P", "count": 2},
    "D": {"position_type": "P", "count": 4},
    "G": {"position_type": "G", "count": 2},
    "BN": {"count": 2},
    "IR": {"count": 2},
}


def _player(player_id, name, selected_position, status=""):
    return {
        "player_id": player_id,
        "name": name,
        "selected_position": selected_position,
        "status": status,
    }


def test_ir_eligible_player_not_in_ir_slot_is_flagged():
    roster = [_player(1, "Hurt Guy", "BN", status="IR")]

    recs = roster_checks.check_ir_eligible(roster, ROSTER_SLOTS)

    assert len(recs) == 1
    assert "Hurt Guy" in recs[0].message


def test_ir_eligible_player_already_in_ir_slot_is_not_flagged():
    roster = [_player(1, "Hurt Guy", "IR", status="IR")]

    recs = roster_checks.check_ir_eligible(roster, ROSTER_SLOTS)

    assert recs == []


def test_no_ir_flag_when_ir_slots_full():
    roster = [
        _player(1, "Already IR 1", "IR", status="IR"),
        _player(2, "Already IR 2", "IR", status="IR"),
        _player(3, "New Injury", "BN", status="O"),
    ]

    recs = roster_checks.check_ir_eligible(roster, ROSTER_SLOTS)

    assert recs == []


def test_healthy_player_is_never_flagged():
    roster = [_player(1, "Healthy Guy", "BN", status="")]

    recs = roster_checks.check_ir_eligible(roster, ROSTER_SLOTS)

    assert recs == []


def test_empty_active_slot_is_flagged():
    roster = [
        _player(1, "Goalie One", "G"),
        _player(2, "Goalie Two", "G"),
    ]

    recs = roster_checks.check_empty_active_slots(roster, ROSTER_SLOTS)

    slots_flagged = {r.message.split()[1] for r in recs}
    assert "C" in slots_flagged  # 0 of 2 filled
    assert "G" not in slots_flagged  # both G slots filled


def test_full_active_slot_is_not_flagged():
    roster = [_player(i, f"Center {i}", "C") for i in range(1, 3)]

    recs = roster_checks.check_empty_active_slots(roster, ROSTER_SLOTS)

    slots_flagged = {r.message.split()[1] for r in recs}
    assert "C" not in slots_flagged
