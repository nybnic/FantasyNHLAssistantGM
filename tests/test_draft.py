from league import draft


def test_rounds_follow_each_teams_pick_order(tmp_path):
    path = tmp_path / "draft.txt"
    path.write_text("# comment\n[Team A]\nNathan MacKinnon (COL - C)\nMoritz Seider (DET - D)\n\n"
                    "[Team B]\nTim Stützle (OTT - C,LW)\n", encoding="utf-8")
    assert draft.rounds(path) == {"nathan mackinnon": 1, "moritz seider": 2, "tim stutzle": 1}
    assert draft.rounds(tmp_path / "missing.txt") == {}


def test_the_real_draft_file_has_every_team():
    rounds = draft.rounds()
    assert rounds["macklin celebrini"] == 1 and rounds["brayden schenn"] == 14
    assert len(rounds) == 16 * 14
