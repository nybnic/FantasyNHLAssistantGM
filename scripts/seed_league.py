"""Seed state/league.json with every other team's roster (one-time setup).

    python -m scripts.seed_league data/league/draft_2026.txt

The file has a "[Team name]" line before each team's players, in any format
league/parse.py understands (Yahoo's draft results copy as-is). Your own
team (config.league.MY_TEAM) is skipped: state/roster.json holds it.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from config.league import LEAGUE_TEAMS, MY_TEAM, SCHEDULE
from league import parse, teams

_HEADER = re.compile(r"^\[(.+)\]\s*$")


def sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        header = _HEADER.match(line.strip())
        if header:
            current = header.group(1)
            out[current] = []
        elif current:
            out[current].append(line)
    return {team: "\n".join(lines) for team, lines in out.items()}


def main(path: str) -> None:
    by_team = sections(Path(path).read_text(encoding="utf-8"))
    problems = [f"unknown team {t!r} (not in config.league.SCHEDULE)"
                for t in by_team if t not in SCHEDULE and t != MY_TEAM]
    if len(by_team) != LEAGUE_TEAMS:
        problems.append(f"{len(by_team)} teams, expected {LEAGUE_TEAMS}")
    registry = parse.registry()
    data = teams.load()
    today = dt.date.today()
    for team, text in by_team.items():
        if team == MY_TEAM:
            continue
        found = parse.find_players(text, registry)
        problems += [f"{team}: {p}" for p in found.problems]
        missing = len([line for line in text.splitlines() if line.strip()]) - len(found.players)
        if missing > 0 and not found.problems:
            problems.append(f"{team}: {missing} line(s) matched no NHL player")
        teams.set_team(data, team, found.players, today)
        print(f"{team}: {len(found.players)} players")
    if problems:
        print("Check these (players not found aren't on an NHL roster, so they can't be free agents either):\n  "
              + "\n  ".join(problems))
    teams.save(data)
    print(f"Saved {teams.LEAGUE_FILE}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
