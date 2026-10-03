"""One-time repairs of bot-owned state after an incident (docs/operations.md,
Past incidents). Each runs once (state["repairs_done"]) and fixes exactly
what the incident did.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from clients.names import normalize_name
from config.league import TIMEZONE
from league import teams
from league.roster import RosterPlayer

SCHENN, MURASHOV = 8475170, 8483703


def apply(state: dict, players: list[RosterPlayer], league: dict) -> None:
    done = state.setdefault("repairs_done", [])
    for name, repair in REPAIRS:
        if name not in done:
            repair(state, players, league)
            done.append(name)


def _totals_view(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-01: a matchup screenshot of the week's Totals view listed Brayden
    # Schenn, dropped that day for Jack McBain, without a slot; he was saved to
    # my roster and counted as an add. Murashov's restoring here was a mistake:
    # Nico had dropped him (next repair).
    state["adds"] = [a for a in state["adds"]
                     if not (a["id"] == SCHENN and a["source"] == "roster" and a["date"] == "2026-10-01")]
    if any(p.id == SCHENN and p.slot is None for p in players) and all(p.id != MURASHOV for p in players):
        players[:] = [p for p in players if p.id != SCHENN] + [
            RosterPlayer(MURASHOV, "Sergei Murashov", "PIT", ["G"], "BN")]


def _murashov_dropped(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-01: Nico dropped Sergei Murashov (told in chat); the repair above
    # had put him back on the roster.
    players[:] = [p for p in players if p.id != MURASHOV]
    state["seen_mine"].pop(str(MURASHOV), None)
    teams.put_on_waivers(league, MURASHOV, dt.date(2026, 10, 1))


def _league_adds_from_log(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-03: other teams' adds were logged only from 2026-10-01 (4 of 13
    # seen), though every Transactions screenshot since Sep 30 was applied.
    # Rebuild the log from transactions_seen, and date the rosters by the
    # newest move seen (when it was sent isn't recorded: a lower bound).
    names = {normalize_name(t).replace(" ", ""): t for t in league["teams"]}
    adds: dict[str, list[str]] = {}
    for key, when in sorted(state["transactions_seen"].items(), key=lambda kv: kv[1]):
        _, kind, team, who = key.split("|")
        if kind not in ("add", "add/drop") or team not in names:
            continue
        nhl_date = (dt.datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(TIMEZONE))
                    .astimezone(ZoneInfo("America/New_York")).date().isoformat())
        adds.setdefault(names[team], []).extend([nhl_date] * (len(who.split(",")) if kind == "add" else 1))
    state["league_adds"] = adds
    if state["transactions_seen"] and not league.get("moves_through"):
        league["moves_through"] = max(state["transactions_seen"].values())


# Nico's League > Standings and All Matchups screenshots of 2026-10-03 10:53
# Helsinki, sent in a development session before the bot could read them:
# week 1's pairings, and each team's score and Yahoo projection then (through
# Oct 2's games).
WEEK_1_BOARD = [
    ("Nico's Groovy Team", 87.40, 175.62, "Bahelin Boys", 114.25, 166.68),
    ("HAN-NES", 96.35, 174.62, "HC Bulju", 114.95, 204.09),
    ("Viktorios", 99.25, 178.96, "Bottom three", 145.15, 215.92),
    ("Gwp", 101.20, 170.36, "Vanilla Thunder", 86.00, 150.76),
    ("Retrot Chicken Wings", 93.80, 174.75, "Pastasauce", 94.55, 176.29),
    ("Lazy Lew", 119.20, 207.19, "Löllöt Höntsääjät", 86.85, 156.30),
    ("Randy", 80.65, 162.98, "Vantaa", 134.85, 206.77),
    ("Bellova", 65.45, 142.92, "Jättiläisentie Giants", 85.15, 139.48),
]


def _week_1_board(state: dict, players: list[RosterPlayer], league: dict) -> None:
    week = state.setdefault("league_weeks", {}).setdefault("1", {"pairs": [], "scores": {}})
    for a, a_score, a_proj, b, b_score, b_proj in WEEK_1_BOARD:
        if sorted((a, b)) not in week["pairs"]:
            week["pairs"].append(sorted((a, b)))
        for team, score, proj in ((a, a_score, a_proj), (b, b_score, b_proj)):
            week["scores"].setdefault(team, {"score": score, "projected": proj, "date": "2026-10-03"})
    if not state.get("standings"):  # everyone 0-0-0 before week 1 ends
        state["standings"] = {"week": 0, "at": "2026-10-03",
                              "teams": {t: {"w": 0, "l": 0, "t": 0, "pf": 0.0}
                                        for row in WEEK_1_BOARD for t in (row[0], row[3])}}


REPAIRS = [("2026-10-01 totals view", _totals_view), ("2026-10-01 murashov dropped", _murashov_dropped),
           ("2026-10-03 league adds from the log", _league_adds_from_log),
           ("2026-10-03 week 1 board and standings", _week_1_board)]
