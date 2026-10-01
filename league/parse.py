"""Find NHL players in text copied from Yahoo, however it's formatted.

Works on the draft results ("Nathan MacKinnon (COL - C)"), a team page
copied from the browser ("Nathan MacKinnonPlayer NoteCOL - C ...") or a
plain list of names: it looks for every known player's full name in each
line, then reads Yahoo's "TEAM - POS,POS" next to it when there is one, to
tell apart players with the same name (two Sebastian Ahos, two Elias
Petterssons) and to take Yahoo's position eligibility.

On a team page each row starts with the Yahoo slot (C, LW, RW, D, G, BN,
IR, IR+), on the player's line ("BN  Nathan MacKinnon ...") or on a line of
its own just above it; that becomes the player's `slot`.
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
_SLOT = r"(BN|IR\+|IR|LW|RW|C|D|G)"
_SLOT_LINE = re.compile(rf"^\s*{_SLOT}\s*$")
_SLOT_LEAD = re.compile(rf"^\s*{_SLOT}\s")


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
    tagged: set[int] = field(default_factory=set)  # ids whose positions came from Yahoo's "TEAM - POS"


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
    slot_above = None
    for line in text.splitlines():
        if _SLOT_LINE.match(line):
            slot_above = _SLOT_LINE.match(line).group(1)
            continue
        norm = " " + normalize_name(re.sub(r"\s", " ", line))  # table copies are tab-separated
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
        if not hits:
            continue
        lead = _SLOT_LEAD.match(line)
        slot, slot_above = (lead.group(1) if lead else slot_above), None
        for _, _, name in sorted(hits):
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
            if positions:
                found.tagged.add(c["id"])
            found.players.append(RosterPlayer(
                id=c["id"], name=c["name"], team=c["team"],
                positions=positions or [NHL_TO_YAHOO_POS[c["position"]]], slot=slot,
            ))
            slot = None  # a slot belongs to the first player on its line
    return found
