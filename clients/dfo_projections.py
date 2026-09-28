"""DailyFaceoff's customizable projections (powered by 5v5hockey.com).

dailyfaceoff.com/projections only embeds https://5v5hockey.com/projections-embedded/,
a free, no-login page that renders full-season projections from JavaScript
object literals inside one large inline <script> (not JSON). Verified live
Sep 2026 ("Projections updated Sept. 23, 2026"): ~590 skaters and ~65
goalies, each row repeated once per table view on the page.

Rows carry name + team nickname only (no NHL player id), so they're matched
to current NHL rosters by name, using the team to break ties.

Skaters: GP, G, A, plus_minus, PIM, PPG, PPA, SOG, FOW, BLK, HIT, ATOI (min).
Goalies: GS, W, L, t_o, SO, SV, GA, SA.
No shorthanded goals/assists or game-winning goals - callers keep those
from another prior.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path

from clients import nhl_client
from clients.cache import DAY, cached_json, get

URL = "https://5v5hockey.com/projections-embedded/"
SNAPSHOT_DIR = Path("data/projections")

_UPDATED_RE = re.compile(r"Projections updated\s+([^<\n]+)")
_ROW_START_RE = re.compile(r"\bid:\s*'")
_QUOTED = r"'((?:[^'\\]|\\.)*)'"
_PLAYER_NAME_RE = re.compile(r"player:\s*\{[^}]*'name':\s*" + _QUOTED)
_TEAM_NAME_RE = re.compile(r"team:\s*\{[^}]*'name':\s*" + _QUOTED)
_FIELD_RE = re.compile(r"\b(Pos|alt_pos|[A-Za-z_]+):\s*(\"[^\"]*\"|-?\d+(?:\.\d+)?)\s*,")

SKATER_FIELDS = {
    "G": "g", "A": "a", "plus_minus": "pm", "PIM": "pim", "PPG": "ppg", "PPA": "ppa",
    "SOG": "sog", "FOW": "fow", "BLK": "blk", "HIT": "hit",
}
GOALIE_FIELDS = {"GS": "gs", "W": "w", "GA": "ga", "SV": "sv", "SO": "so"}


def _unescape(text: str) -> str:
    return html.unescape(re.sub(r"\\(.)", r"\1", text))


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", ascii_name.lower().replace("-", " ")).strip()


def parse(page: str) -> dict:
    """{'updated': str | None, 'skaters': [...], 'goalies': [...]} from the page.
    Each row: name, team (nickname), position ('F'/'D'/'G'), gp or gs, stats
    as season totals, toi (minutes per game, skaters only)."""
    updated = _UPDATED_RE.search(page)
    starts = [m.start() for m in _ROW_START_RE.finditer(page)]
    seen: set[str] = set()
    skaters, goalies = [], []
    for i, start in enumerate(starts):
        block = page[start: starts[i + 1] if i + 1 < len(starts) else len(page)]
        key = re.match(r"id:\s*" + _QUOTED, block).group(1)
        if key in seen:
            continue
        name_m, team_m = _PLAYER_NAME_RE.search(block), _TEAM_NAME_RE.search(block)
        if not (name_m and team_m):
            continue
        # The row's own fields sit between the team dict's "}," and the row's "}".
        after_team = block.find("},", team_m.end()) + 2
        row_end = block.find("}", after_team)
        fields = {k: v.strip('"') for k, v in _FIELD_RE.findall(block[after_team:row_end])}
        position = fields.get("Pos")
        if position not in ("F", "D", "G"):
            continue
        seen.add(key)
        row = {"name": _unescape(name_m.group(1)), "team": _unescape(team_m.group(1)), "position": position}
        mapping = GOALIE_FIELDS if position == "G" else SKATER_FIELDS
        try:
            row["stats"] = {ours: float(fields[theirs]) for theirs, ours in mapping.items()}
            if position == "G":
                row["gs"] = float(fields["GS"])
            else:
                row["gp"] = float(fields["GP"])
                row["toi"] = float(fields["ATOI"])
        except (KeyError, ValueError):
            continue
        if position != "G" and row["gp"] <= 0:
            continue
        (goalies if position == "G" else skaters).append(row)
    return {"updated": updated.group(1).strip() if updated else None, "skaters": skaters, "goalies": goalies}


def fetch() -> dict:
    """Parsed projections, cached for a day. Each new "updated" version is
    also kept under data/projections/ so the preseason set can be scored
    against real results later, even if DFO updates in-season."""
    data = cached_json("dfo_projections", DAY, lambda: parse(get(URL).text))
    if data["updated"]:
        stamp = re.sub(r"[^0-9A-Za-z]+", "_", data["updated"]).strip("_")
        snapshot = SNAPSHOT_DIR / f"dfo_{stamp}.json"
        if not snapshot.exists():
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps(data), encoding="utf-8")
    return data


def _position_group(position: str) -> str:
    return position if position in ("D", "G") else "F"


def match_to_nhl_ids(rows: list[dict], registry: list[dict]) -> dict[int, dict]:
    """NHL player id -> projection row.

    `registry` is a list of {id, name, team, position} - current rosters plus
    last season's players, so injured players missing from "current" rosters
    still match. Tries the full name, breaking ties by team and then position
    (Vancouver has two Elias Petterssons); then falls back to last name + team
    for first-name variants (Alex/Alexander). Ambiguous rows are skipped."""
    team_names = nhl_client.team_full_names()
    by_name: dict[str, list[dict]] = {}
    by_last: dict[str, list[dict]] = {}
    seen_ids: set[int] = set()
    for p in registry:
        if p["id"] in seen_ids:
            continue
        seen_ids.add(p["id"])
        name = normalize_name(p["name"])
        by_name.setdefault(name, []).append(p)
        by_last.setdefault(name.split(" ")[-1], []).append(p)

    def on_team(candidates: list[dict], nickname: str) -> list[dict]:
        return [c for c in candidates if team_names.get(c["team"], "").lower().endswith(nickname.lower())]

    matched = {}
    for row in rows:
        name = normalize_name(row["name"])
        candidates = by_name.get(name, [])
        if len(candidates) > 1:
            candidates = on_team(candidates, row["team"]) or candidates
        if len(candidates) > 1:
            candidates = [c for c in candidates if _position_group(c["position"]) == row["position"]]
        if not candidates:
            candidates = [
                c for c in on_team(by_last.get(name.split(" ")[-1], []), row["team"])
                if _position_group(c["position"]) == row["position"]
            ]
        if len(candidates) == 1:
            matched[candidates[0]["id"]] = row
    return matched
