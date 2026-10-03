"""The other 15 teams' rosters, as far as the Assistant GM knows them.

Seeded from the draft results (scripts/seed_league.py), then kept current
by League > Transactions screenshots (every team's adds, drops and trades;
`moves_through` says how far they reach), and refreshed by a team's Yahoo
page (/opp) or a matchup screenshot. Everyone on a current NHL
roster who isn't on a fantasy roster here, on yours, or in `taken` (/taken)
counts as a free agent. Players dropped in the last day or so are on waivers
(`waivers`): a claim, not an instant add.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from config.league import WAIVER_DAYS
from league.roster import RosterPlayer

LEAGUE_FILE = Path("state/league.json")
# Dropped on NHL date D: on waivers for WAIVER_DAYS, the claim is processed
# overnight after, so he first plays for the claimer on D + 2. Unverified:
# check the "W (date)" Yahoo shows next to a player dropped today.
CLAIM_PLAYS_AFTER_DAYS = WAIVER_DAYS + 1


def load(path: Path = LEAGUE_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("teams", {})
    data.setdefault("taken", [])
    data.setdefault("waivers", {})  # player id -> first day a claim of him can play
    return data


def save(data: dict, path: Path = LEAGUE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def players(data: dict, team: str) -> list[RosterPlayer]:
    return [RosterPlayer(**p) for p in data["teams"].get(team, {}).get("players", [])]


def moves_through(data: dict) -> dt.date | None:
    """The day through which every league move is known from Transactions
    screenshots (`moves_through`, a Helsinki time; bot/ingest.finish_transactions)."""
    through = data.get("moves_through")
    return dt.date.fromisoformat(through[:10]) if through else None


def updated(data: dict, team: str) -> str | None:
    """The date a team's roster is known as of: its last page or matchup
    screenshot, or later when Transactions screenshots carry it forward."""
    own = data["teams"].get(team, {}).get("updated")
    through = moves_through(data)
    return max(own, through.isoformat()) if own and through else own


def set_team(data: dict, team: str, roster: list[RosterPlayer], date: dt.date) -> None:
    """Replace a team's roster. Its players are no longer merely "taken"."""
    data["teams"][team] = {"updated": date.isoformat(), "players": [asdict(p) for p in roster]}
    ids = {p.id for p in roster}
    data["taken"] = [pid for pid in data["taken"] if pid not in ids]
    for other, entry in data["teams"].items():
        if other != team:
            entry["players"] = [p for p in entry["players"] if p["id"] not in ids]


def add_player(data: dict, team: str, player: RosterPlayer) -> None:
    """A team picked up `player` (an add, a claim, a trade): off every other
    roster and the taken list, onto `team`'s if its roster is known, else just taken."""
    for entry in data["teams"].values():
        entry["players"] = [p for p in entry["players"] if p["id"] != player.id]
    data["taken"] = [pid for pid in data["taken"] if pid != player.id]
    if team in data["teams"]:
        data["teams"][team]["players"].append(asdict(RosterPlayer(player.id, player.name, player.team,
                                                                  player.positions, None)))
    else:
        mark_taken(data, [player.id])


def remove_player(data: dict, team: str, player_id: int) -> None:
    """`team` let `player_id` go: he's a free agent (or on waivers) again."""
    if team in data["teams"]:
        entry = data["teams"][team]
        entry["players"] = [p for p in entry["players"] if p["id"] != player_id]
    data["taken"] = [pid for pid in data["taken"] if pid != player_id]


def put_on_waivers(data: dict, player_id: int, dropped: dt.date) -> None:
    """`player_id` was dropped on NHL date `dropped`: a claim of him plays
    from CLAIM_PLAYS_AFTER_DAYS later."""
    data.setdefault("waivers", {})[str(player_id)] = (dropped + dt.timedelta(days=CLAIM_PLAYS_AFTER_DAYS)).isoformat()


def on_waivers(data: dict, today: dt.date) -> dict[int, dt.date]:
    """Player id -> the first day a claim of him can play, for players still
    on waivers today (older entries are forgotten)."""
    data["waivers"] = {pid: day for pid, day in data.get("waivers", {}).items() if day > today.isoformat()}
    return {int(pid): dt.date.fromisoformat(day) for pid, day in data["waivers"].items()}


def mark_taken(data: dict, player_ids: list[int]) -> None:
    data["taken"] = sorted(set(data["taken"]) | set(player_ids))


def rostered_ids(data: dict) -> set[int]:
    ids = set(data["taken"])
    for entry in data["teams"].values():
        ids.update(p["id"] for p in entry["players"])
    return ids
