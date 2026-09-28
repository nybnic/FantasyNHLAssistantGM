import pytest

from draft.yahoo_projections import GOALIES_FILE, SKATERS_FILE, _PLAYER_RE, load_yahoo_projections


def test_player_cell_parsing():
    name, status, team, pos = _PLAYER_RE.match("Jake OettingerOPlayer NoteDAL - G").groups()
    assert (name, status, team, pos) == ("Jake Oettinger", "O", "DAL", "G")
    name, status, team, pos = _PLAYER_RE.match("Leon DraisaitlNo new player NotesEDM - C,LW").groups()
    assert (name, status, team, pos) == ("Leon Draisaitl", None, "EDM", "C,LW")
    assert _PLAYER_RE.match("Thatcher DemkoIRNew Player NoteVAN - G").group(2) == "IR"


@pytest.mark.skipif(not (SKATERS_FILE.exists() and GOALIES_FILE.exists()),
                    reason="Yahoo projection exports are kept out of the repo")
def test_export_loads_with_yahoo_points_and_eligibility():
    # The loader raises if a player's stat line doesn't reproduce Yahoo's Fan Pts
    # under either the export-time or the current scoring.
    projs = {p.name: p for p in load_yahoo_projections()}
    assert projs["Leon Draisaitl"].elig == {"C", "LW"}
    assert projs["Andrei Vasilevskiy"].pos == "G"
    # McDavid: 454 FOW. Yahoo's export said 779.9 with faceoffs at 0.2; the league uses 0.1.
    assert round(projs["Connor McDavid"].fpts, 2) == round(779.9 - 454 * 0.1, 2)
