"""Yahoo's own season projections, exported from the league's player list.

Export layout (data/yahoo/skaters.csv, data/yahoo/goalies.csv):
- column 2 is "<Name><status?><note marker><TEAM> - <positions>", e.g.
  "Jake OettingerOPlayer NoteDAL - G" or "Leon DraisaitlNo new player
  NotesEDM - C,LW". Status is Yahoo's injury/availability tag (O, NA, IR,
  IR-LT, DTD); positions are Yahoo's real eligibility.
- "GP*" projected games (goalies: appearances), "Fan Pts" = Yahoo's total
  under this league's scoring, "Pre-Season" = Yahoo's preseason rank.
- the skater +/- header arrives as "#ERROR!" (the spreadsheet read "+/-"
  as a formula); unprojected players have "-" in every stat.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from draft import scoring
from draft.projections import Depth, Projection, norm_team

SKATERS_FILE = Path("data/yahoo/skaters.csv")
GOALIES_FILE = Path("data/yahoo/goalies.csv")

_PLAYER_RE = re.compile(
    r"^(.*?)(O|NA|IR-LT|IR|DTD|GTD|SUSP)?(?:No new player Notes|New Player Notes?|Player Notes?)"
    r"([A-Z]{2,3}) - (.+)$"
)
SKATER_COLUMNS = {
    "G": "g", "A": "a", "#ERROR!": "pm", "+/-": "pm", "PIM": "pim", "PPG": "ppg", "PPA": "ppa",
    "SHG": "shg", "SHA": "sha", "GWG": "gwg", "SOG": "sog", "FW": "fow", "HIT": "hit", "BLK": "blk",
}
GOALIE_COLUMNS = {"GS": "gs", "W": "w", "GA": "ga", "SV": "sv", "SHO": "so"}
STATUS_LABELS = {
    "O": "Yahoo: Out", "NA": "Yahoo: Not active", "IR": "Yahoo: IR", "IR-LT": "Yahoo: IR long-term",
    "DTD": "Yahoo: Day-to-day", "GTD": "Yahoo: Game-time decision", "SUSP": "Yahoo: Suspended",
}


def _num(value: str) -> float | None:
    value = value.replace(",", "").replace("%", "").strip()
    if value in ("", "-"):
        return None
    return float(value)


def _read(path: Path, columns: dict[str, str], is_goalie: bool) -> list[Projection]:
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    # Yahoo's sort-arrow icons come through as private-use characters ("Pre-Season").
    header = [re.sub(r"[-]", "", h).strip() for h in rows[0]]
    out = []
    for i, row in enumerate(rows[1:], start=1):
        cell = dict(zip(header, row))
        m = _PLAYER_RE.match(row[2])
        if not m:
            raise ValueError(f"{path}: can't parse player cell {row[2]!r}")
        name, status, team, positions = m.groups()
        stats = {key: _num(cell.get(col, "-")) for col, key in columns.items() if col in cell}
        if any(v is None for v in stats.values()):
            continue  # Yahoo doesn't project him
        games = _num(cell["GP*"]) or 0.0
        elig = set(positions.split(","))
        if is_goalie:
            stats["sv_pct"] = stats["sv"] / (stats["sv"] + stats["ga"]) if stats["sv"] else 0.0
            fpts = scoring.goalie_points(stats)
            games = stats["gs"] or games
        else:
            fpts = scoring.skater_points(stats)
        yahoo_fpts = _num(cell["Fan Pts"]) or 0.0
        if abs(fpts - yahoo_fpts) > 0.6:
            raise ValueError(f"{name}: computed {fpts:.2f} pts but Yahoo says {yahoo_fpts} - scoring changed?")
        flags = [STATUS_LABELS[status]] if status else []
        p = Projection(
            player_id=-(i + (100_000 if is_goalie else 0)),  # replaced by the NHL id when matched
            name=name.strip(),
            team=norm_team(team),
            pos=positions.split(",")[0],
            age=None,
            games=games,
            stats=stats,
            fpts=fpts,
            depth=Depth(),
            flags=flags,
            elig=elig,
            yahoo_rank=_num(cell.get("Pre-Season", "-")),
            status=status,
        )
        out.append(p)
    return out


def load_yahoo_projections(skaters: Path = SKATERS_FILE, goalies: Path = GOALIES_FILE) -> list[Projection]:
    return _read(skaters, SKATER_COLUMNS, False) + _read(goalies, GOALIE_COLUMNS, True)
