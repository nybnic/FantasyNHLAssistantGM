"""docs/league-rules.md and config/league.py must describe the same scoring."""
import re
from pathlib import Path

from config.league import GOALIE_WEIGHTS, SCHEDULE, SKATER_WEIGHTS, fantasy_points

DOC = Path(__file__).resolve().parent.parent / "docs" / "league-rules.md"
_ROW = re.compile(r"^\|[^|]+\|\s*`(\w+)`\s*\|\s*(-?[\d.]+)\s*\|$", re.M)


def test_documented_scoring_matches_the_config():
    documented = {code: float(points) for code, points in _ROW.findall(DOC.read_text(encoding="utf-8"))}
    assert documented == {**SKATER_WEIGHTS, **GOALIE_WEIGHTS}


def test_documented_worked_examples_add_up():
    skater = {"g": 1, "ppg": 1, "a": 1, "pm": 1, "sog": 3, "hit": 2, "blk": 1}
    assert fantasy_points(skater, SKATER_WEIGHTS) == 11.5
    assert fantasy_points({"gs": 1, "w": 1, "sv": 30, "ga": 2}, GOALIE_WEIGHTS) == 13.5
    assert fantasy_points({"gs": 1, "w": 1, "sv": 25, "so": 1}, GOALIE_WEIGHTS) == 18.75


def test_documented_schedule_matches_the_config():
    section = DOC.read_text(encoding="utf-8").split("## Our regular-season schedule")[1].split("\n## ")[0]
    pairs = re.findall(r"\|\s*(\d+)\s*\|\s*([^|\n]+?)\s*(?=\|)", section)
    assert {int(week): team for week, team in pairs} == dict(enumerate(SCHEDULE, start=1))
