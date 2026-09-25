"""Runs every Phase-1 check and collects the results.

Phase 2 (once league rules are confirmed) will add a value-based lineup
check and a waiver-wire check here, alongside these two.
"""
from __future__ import annotations

from typing import Any

from engine import goalie_checks, roster_checks
from engine.models import Recommendation


def build_recommendations(
    roster: list[dict[str, Any]],
    roster_slots: dict[str, dict],
    goalie_lookahead_days: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    recs += roster_checks.check_ir_eligible(roster, roster_slots)
    recs += roster_checks.check_empty_active_slots(roster, roster_slots)
    recs += goalie_checks.check_goalies(roster, lookahead_days=goalie_lookahead_days)
    return recs
