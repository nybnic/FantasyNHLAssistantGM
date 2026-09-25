"""Shared data model for everything the checks produce."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Recommendation:
    key: str  # stable id used for dedupe against state/last_run.json
    category: str  # "goalie" | "injury" | "lineup"
    message: str
