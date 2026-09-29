"""Find NHL players in text copied from Yahoo, however it's formatted.

Works on the draft results ("Nathan MacKinnon (COL - C)"), a team page
copied from the browser ("Nathan MacKinnonPlayer NoteCOL - C ...") or a
plain list of names: it looks for every known player's full name in each
line, then reads Yahoo's "TEAM - POS,POS" next to it when there is one, to
tell apart players with the same name (two Sebastian Ahos, two Elias
Petterssons) and to take Yahoo's position eligibility.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from clients import nhl_client, nhl_stats
from clients.names import normalize_name
from league.roster import RosterPlayer
from model.context import current_season_id

NHL_TO_YAHOO_POS = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
YAHOO_TO_NHL_TEAM = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL"}
# Browser copies glue it on: "Player NoteSJ - LW,RW".
_TEAM_POS = re.compile(r"(?<![A-Z])([A-Z]{2,3})\s-\s((?:C|LW|RW|D|G)(?:,(?:C|LW|RW|D|G))*)")


def registry() -> list[dict]:
    """Every player worth recognizing (id, name, team, NHL position): current
    NHL rosters first, then anyone who played last season."""
    last_season = current_season_id(dt.date.today()) - 10_001
    return nhl_client.current_rosters() + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": g.position}
        for g in nhl_stats.skater_games(last_season)
    ] + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": "G"}
        for g in nhl_stats.goalie_games(last_season)
    ]


@dataclass
class Found:
    players: list[RosterPlayer] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # lines that looked like a player but didn't resolve


def _index(players: list[dict]) -> dict[str, dict[int, dict]]:
    by_name: dict[str, dict[int, dict]] = {}
    for p in players:
        by_name.setdefault(normalize_name(p["name"]), {}).setdefault(p["id"], p)
    return by_name


def find_players(text: str, players: list[dict]) -> Found:
    by_name = _index(players)
    names = sorted(by_name, key=len, reverse=True)
    found = Found()
    seen: set[int] = set()
    for line in text.splitlines():
        norm = " " + normalize_name(line)
        hits = []
        for name in names:
            at = norm.find(" " + name)
            if at >= 0 and not any(a <= at < b for a, b, _ in hits):
                hits.append((at, at + len(name) + 1, name))
        tag = _TEAM_POS.search(line)
        if tag and len(hits) != 1:
            tag = None  # can't tell whose it is
        if not hits and tag:
            found.problems.append(f"{line.strip()[:60]}: no player by that name")
        for _, _, name in hits:
            candidates = list(by_name[name].values())
            positions: list[str] = []
            if tag:
                team = YAHOO_TO_NHL_TEAM.get(tag.group(1), tag.group(1))
                positions = tag.group(2).split(",")
                if len(candidates) > 1:
                    candidates = [c for c in candidates if NHL_TO_YAHOO_POS[c["position"]] in positions
                                  or (c["position"] in "CLR" and set(positions) & {"C", "LW", "RW"})] or candidates
                if len(candidates) > 1:
                    candidates = [c for c in candidates if c["team"] == team] or candidates
            if len(candidates) > 1:
                options = ", ".join(f"{c['team']} {c['position']}" for c in candidates)
                found.problems.append(f"{candidates[0]['name']}: which one ({options})? Add Yahoo's \"TEAM - POS\"")
                continue
            c = candidates[0]
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            found.players.append(RosterPlayer(
                id=c["id"], name=c["name"], team=c["team"],
                positions=positions or [NHL_TO_YAHOO_POS[c["position"]]],
            ))
    return found
