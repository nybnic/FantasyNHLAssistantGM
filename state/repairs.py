"""One-time repairs of bot-owned state after an incident (docs/operations.md,
Past incidents). Each is idempotent: it fixes exactly what the incident did
and does nothing once fixed, so it is safe on every run until removed.
"""
from __future__ import annotations

from league.roster import RosterPlayer

SCHENN, MURASHOV = 8475170, 8483703


def apply(state: dict, players: list[RosterPlayer]) -> None:
    # 2026-10-01: a matchup screenshot of the week's Totals view listed Brayden
    # Schenn, dropped that day for Jack McBain, without a slot; he was saved to
    # my roster and counted as an add, and Sergei Murashov (a bench goalie the
    # screenshots didn't reach) was taken off it.
    state["adds"] = [a for a in state["adds"]
                     if not (a["id"] == SCHENN and a["source"] == "roster" and a["date"] == "2026-10-01")]
    if any(p.id == SCHENN and p.slot is None for p in players) and all(p.id != MURASHOV for p in players):
        players[:] = [p for p in players if p.id != SCHENN] + [
            RosterPlayer(MURASHOV, "Sergei Murashov", "PIT", ["G"], "BN")]
