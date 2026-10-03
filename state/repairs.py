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


REPAIRS = [("2026-10-01 totals view", _totals_view), ("2026-10-01 murashov dropped", _murashov_dropped),
           ("2026-10-03 league adds from the log", _league_adds_from_log)]
