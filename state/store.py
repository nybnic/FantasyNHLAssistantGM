"""Tracks which recommendations were already sent, so scheduled re-runs
don't repeat a notification for something that hasn't changed.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from engine.models import Recommendation

RETENTION_DAYS = 4


def load(state_file: Path) -> dict[str, str]:
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8")).get("sent", {})


def filter_new(sent: dict[str, str], recommendations: list[Recommendation]) -> list[Recommendation]:
    return [r for r in recommendations if r.key not in sent]


def save(state_file: Path, sent: dict[str, str], newly_sent: list[Recommendation]) -> None:
    today_iso = date.today().isoformat()
    updated = dict(sent)
    for rec in newly_sent:
        updated[rec.key] = today_iso

    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    updated = {k: v for k, v in updated.items() if v >= cutoff}

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"sent": updated}, indent=2, sort_keys=True), encoding="utf-8"
    )
