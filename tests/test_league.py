import datetime as dt

import pytest

from league import parse, roster, teams, weeks
from league.roster import RosterPlayer
from scripts.seed_league import sections

REGISTRY = [
    {"id": 1, "name": "Nathan MacKinnon", "team": "COL", "position": "C"},
    {"id": 2, "name": "Sebastian Aho", "team": "CAR", "position": "C"},
    {"id": 3, "name": "Sebastian Aho", "team": "NYI", "position": "D"},
    {"id": 4, "name": "Tim Stützle", "team": "OTT", "position": "C"},
    {"id": 5, "name": "Kiefer Sherwood", "team": "SJS", "position": "L"},
    {"id": 6, "name": "J.T. Miller", "team": "NYR", "position": "C"},
]


def test_week_one_is_short_and_week_19_spans_the_february_break():
    assert weeks.week_span(1) == (dt.date(2026, 9, 29), dt.date(2026, 10, 4))
    assert weeks.week_span(2) == (dt.date(2026, 10, 5), dt.date(2026, 10, 11))
    assert weeks.week_span(19) == (dt.date(2027, 2, 1), dt.date(2027, 2, 14))
    assert weeks.week_span(26) == (dt.date(2027, 3, 29), dt.date(2027, 4, 4))
    assert weeks.week_of(dt.date(2026, 10, 4)) == 1
    assert weeks.week_of(dt.date(2027, 2, 10)) == 19
    assert weeks.week_of(dt.date(2027, 4, 5)) is None
    assert weeks.opponent(1) == "Bahelin Boys" and weeks.opponent(24) is None


def test_finds_players_in_yahoo_draft_results_and_browser_copies():
    text = "Nathan MacKinnon (COL - C)\nTim Stutzle (OTT - C,LW)\nKiefer SherwoodPlayer NoteSJ - LW,RW\nJT Miller"
    found = parse.find_players(text, REGISTRY)
    assert [(p.id, p.positions) for p in found.players] == [
        (1, ["C"]), (4, ["C", "LW"]), (5, ["LW", "RW"]), (6, ["C"])]
    assert found.problems == []


def test_same_name_players_are_told_apart_by_yahoo_position_or_team():
    assert [p.id for p in parse.find_players("Sebastian Aho (NYI - D)", REGISTRY).players] == [3]
    assert [p.id for p in parse.find_players("Sebastian Aho (CAR - C)", REGISTRY).players] == [2]
    found = parse.find_players("Sebastian Aho", REGISTRY)
    assert found.players == [] and "which one" in found.problems[0]


def test_draft_file_sections():
    text = "# comment\n[Team A]\nNathan MacKinnon (COL - C)\n\n[Team B]\nJ.T. Miller (NYR - C)\n"
    assert {k: v.strip() for k, v in sections(text).items()} == {
        "Team A": "Nathan MacKinnon (COL - C)", "Team B": "J.T. Miller (NYR - C)"}


def test_a_new_roster_moves_players_between_teams_and_off_the_taken_list():
    data = teams.load(path=teams.Path("does-not-exist.json"))
    teams.set_team(data, "A", [RosterPlayer(1, "X", "COL", ["C"]), RosterPlayer(2, "Y", "CAR", ["C"])],
                   dt.date(2026, 9, 28))
    teams.mark_taken(data, [3])
    teams.set_team(data, "B", [RosterPlayer(2, "Y", "CAR", ["C"]), RosterPlayer(3, "Z", "NYI", ["D"])],
                   dt.date(2026, 10, 5))
    assert [p.id for p in teams.players(data, "A")] == [1]
    assert data["taken"] == []
    assert teams.rostered_ids(data) == {1, 2, 3}
    assert teams.updated(data, "B") == "2026-10-05"


@pytest.mark.parametrize("path", ["data/league/draft_2026.txt"])
def test_draft_file_has_every_team(path):
    from config.league import LEAGUE_TEAMS, MY_TEAM, SCHEDULE

    names = set(sections(open(path, encoding="utf-8").read()))
    assert len(names) == LEAGUE_TEAMS and names == set(SCHEDULE) | {MY_TEAM}


def test_compare_yahoo_reads_a_pasted_projection_row():
    from scripts.compare_yahoo import parse_row

    line = ("Esa LindellPlayer NoteDAL - D\t8:00 pm @ BOS\tW (Sep 30)\t84\t313.24\t503\t163\t18%\t"
            "5.7\t22.9\t31.4\t16.8\t0.9\t0.9\t0\t1.9\t1\t85.7\t0\t36.5\t170")
    row = parse_row(line)
    assert row["gp"] == 84 and row["fpts"] == 313.24
    assert row["per_game"]["blk"] == pytest.approx(170 / 84)
    assert parse_row("Forwards/Defensemen\tOpp\tRoster Status") is None


def test_team_page_slots_come_from_the_row_start_or_the_line_above():
    text = ("Pos\tForwards\tAction\n"
            "C\nNathan MacKinnonPlayer NoteCOL - C\nDTD\n"
            "BN\tTim StutzlePlayer NoteOTT - C,LW\tvs BOS\n"
            "IR+\nKiefer SherwoodPlayer NoteSJ - LW,RW\nIR-LT\n"
            "J.T. Miller")
    found = parse.find_players(text, REGISTRY)
    assert [(p.id, p.slot) for p in found.players] == [(1, "C"), (4, "BN"), (5, "IR+"), (6, None)]
    assert found.tagged == {1, 4, 5}


def test_pasted_roster_replaces_mine_and_says_what_changed():
    players = [RosterPlayer(1, "Nathan MacKinnon", "COL", ["C"], "C"),
               RosterPlayer(6, "J.T. Miller", "NYR", ["C", "RW"], "BN"),
               RosterPlayer(9, "Gone Guy", "BOS", ["D"], "D")]
    pasted = [RosterPlayer(1, "Nathan MacKinnon", "COL", ["C"], "BN"),
              RosterPlayer(6, "J.T. Miller", "NYR", ["C"], None),  # no tag, no slot: keep both
              RosterPlayer(5, "Kiefer Sherwood", "SJS", ["LW", "RW"], "LW")]
    changes = roster.replace(players, pasted, tagged={1, 5})
    assert [(p.id, p.positions, p.slot) for p in players] == [
        (1, ["C"], "BN"), (6, ["C", "RW"], "BN"), (5, ["LW", "RW"], "LW")]
    assert changes == ["Added: Kiefer Sherwood", "Dropped: Gone Guy", "Slots: Nathan MacKinnon C->BN"]


def test_overfilled_slots_are_cleared_but_ir_kept():
    players = []
    pasted = [RosterPlayer(i, f"P{i}", "BOS", ["C"], "C") for i in range(3)]
    pasted.append(RosterPlayer(9, "Hurt", "BOS", ["D"], "IR"))
    changes = roster.replace(players, pasted, tagged=set())
    assert [p.slot for p in players] == [None, None, None, "IR"]
    assert "Too many players in C" in changes[-1]
