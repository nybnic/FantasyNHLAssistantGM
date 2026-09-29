"""Where each player went in this league's draft: the best record of how
the other managers value players (a first-rounder "feels" like a star to
them whatever our model says). Used by /trade to guess who'd accept.

data/league/draft_2026.txt lists each team's picks in draft order.
"""
from __future__ import annotations

import re
from pathlib import Path

from clients.names import normalize_name

DRAFT_FILE = Path("data/league/draft_2026.txt")
_HEADER = re.compile(r"^\[(.+)\]\s*$")


def rounds(path: Path = DRAFT_FILE) -> dict[str, int]:
    """Normalized player name -> the round he was drafted in."""
    out: dict[str, int] = {}
    pick = 0
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _HEADER.match(line):
            pick = 0
            continue
        pick += 1
        out.setdefault(normalize_name(line.split("(")[0]), pick)
    return out
