"""One-time repairs of bot-owned state after an incident (docs/operations.md,
Past incidents). Each runs once (state["repairs_done"]) and fixes exactly
what the incident did.
"""
from __future__ import annotations

import datetime as dt

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


REPAIRS = [("2026-10-01 totals view", _totals_view), ("2026-10-01 murashov dropped", _murashov_dropped)]
