"""Build the draft board for this league's points scoring.

    python -m scripts.build_draft_rankings            # uses cached data < 1 day old
    python -m scripts.build_draft_rankings --refresh  # re-pull depth charts, ADP, rosters

Outputs:
    data/draft_rankings.csv  - full sortable table (open in Excel/Sheets)
    data/draft_board.json    - data for the draft-day board page
Projections come from Yahoo's own export (data/yahoo/*.csv, see
draft/yahoo_projections.py). --source consensus uses the older in-house model
+ Kodo Hockey + NHL.com blend instead (draft/consensus.py). Edit data/draft_overrides.csv to set goalie starts
(the model's opinion, still blended) or skater games (applied after blending,
for firm injury news) and re-run.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from draft import consensus
from draft import projections as pj
from draft import sources
from draft.value import assign_tiers, value_positions, waiver_pool
from draft.yahoo_projections import load_yahoo_projections

OVERRIDES_FILE = Path("data/draft_overrides.csv")
YAHOO_OVERRIDES_FILE = Path("data/yahoo/overrides.csv")
CSV_OUT = Path("data/draft_rankings.csv")
JSON_OUT = Path("data/draft_board.json")
BOARD_TEMPLATE = Path("draft/board_template.html")
BOARD_OUT = Path("data/draft_board.html")                 # open locally in a browser
BOARD_ARTIFACT_OUT = Path("data/draft_board.artifact.html")  # body-only, for publishing
BOARD_SIZE = 420  # 224 get drafted; the rest is cushion for search
LINEUP_GROUPS = {"f1", "f2", "f3", "f4", "d1", "d2", "d3", "g", "ir"}
FORWARD_POS = {"C", "LW", "RW"}
DFO_SLOT_POS = {"c": "C", "lw": "LW", "rw": "RW"}

REFRESH = False
SOURCE = "yahoo"


class NameIndex:
    """(normalized name, team) lookups with a unique-name fallback and
    position-group tie-breaking (VAN has two Elias Petterssons)."""

    def __init__(self, entries: list[tuple[int, str, str | None, str]]):
        # entries: (id, name, team, fantasy position)
        self.entries = entries
        self.by_name: dict[str, list[tuple[int, str | None, str]]] = defaultdict(list)
        for pid, name, team, pos in entries:
            self.by_name[pj.norm_name(name)].append((pid, team, pos))

    def find(self, name: str, team: str | None = None, pos: str | None = None) -> int | None:
        hits = self.by_name.get(pj.norm_name(name), [])
        if pos:
            group = pj.fantasy_pos_group(pos)
            hits = [h for h in hits if pj.fantasy_pos_group(h[2]) == group] or hits
        if team and len(hits) > 1:
            team = pj.norm_team(team)
            hits = [h for h in hits if h[1] == team] or hits
        return hits[0][0] if len(hits) == 1 else None


def roster_index(roster: dict[int, dict]) -> NameIndex:
    return NameIndex([(pid, p["name"], p["team"], pj.NHL_TO_FANTASY_POS[p["position"]])
                      for pid, p in roster.items()])


def _group_pos(group: str) -> str | None:
    """Position implied by a DailyFaceoff group: f1 -> forward, d2 -> D, g -> G."""
    return {"f": "C", "d": "D", "g": "G"}.get(group[0]) if group != "ir" else None


def extend_roster(roster: dict[int, dict], dfo_rows: list[dict], history: NameIndex) -> list[str]:
    """Add DailyFaceoff-listed players missing from the NHL roster feed
    (unsigned RFAs, PTOs, long-term injured) using their stats history."""
    index = roster_index(roster)
    missing = []
    for row in dfo_rows:
        if row["group"] not in LINEUP_GROUPS:
            continue
        pos = _group_pos(row["group"])
        if index.find(row["name"], row["team"], pos) is not None:
            continue
        pid = history.find(row["name"], pos=pos)
        if pid is None:
            missing.append(f"{row['name']} ({row['team']} {row['group']})")
            continue
        _, name, _, fpos = next(e for e in history.entries if e[0] == pid)
        roster[pid] = {
            "id": pid,
            "name": name,
            "team": pj.norm_team(row["team"]),
            "position": {v: k for k, v in pj.NHL_TO_FANTASY_POS.items()}[fpos],
            "birthDate": sources.player_birth_date(pid),
        }
        index = roster_index(roster)
    return missing


def load_depth(roster: dict[int, dict], dfo_rows: list[dict]) -> dict[int, pj.Depth]:
    index = roster_index(roster)
    depth: dict[int, pj.Depth] = defaultdict(pj.Depth)
    for row in dfo_rows:
        pos = _group_pos(row["group"])
        pid = index.find(row["name"], row["team"], pos)
        if pid is None:
            continue
        d = depth[pid]
        if row["group"] in ("pp1", "pp2"):
            d.pp = d.pp or row["group"]
        elif row["group"] in LINEUP_GROUPS:
            d.group, d.slot, d.injury = row["group"], row["slot"], row["injury"]
    return dict(depth)


def load_overrides(path: Path = OVERRIDES_FILE) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return [r for r in rows if r.get("name")]


def write_board_page(board_data: dict) -> None:
    # "<\/" keeps a player name from ever closing the <script> tag.
    embedded = json.dumps(board_data, separators=(",", ":")).replace("</", "<\\/")
    page = BOARD_TEMPLATE.read_text(encoding="utf-8").replace("/*__BOARD_DATA__*/null", embedded)
    BOARD_ARTIFACT_OUT.write_text(page, encoding="utf-8")
    head = (
        '<!doctype html>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    )
    BOARD_OUT.write_text(head + page, encoding="utf-8")


def blend_published(projs: list[pj.Projection], proj_index: NameIndex) -> dict[int, consensus.SourceView]:
    """Blend Kodo + NHL.com projections into each player's line (in place)."""
    kodo = sources.kodo_projections(REFRESH)
    kodo_sk = {r["id"]: r for r in kodo["skaters"]}
    kodo_g = {r["id"]: r for r in kodo["goalies"]}
    nhl = sources.nhl_com_projections(REFRESH)

    nhl_points: dict[int, float] = {}
    for r in nhl["skaters"]:
        pid = proj_index.find(r["name"], r["team"], "D" if r["pos"] == "D" else "C")
        if pid is None:
            print(f"! NHL.com projection not matched: {r['name']} ({r['team']})")
        else:
            nhl_points[pid] = r["points"]
    nhl_wins: dict[int, float] = {}
    for r in nhl["goalies"]:
        # "A or B: 18" means the job is split/undecided: credit each half.
        for name in r["names"]:
            pid = proj_index.find(name, r["team"], "G")
            if pid is None:
                print(f"! NHL.com goalie not matched: {name} ({r['team']})")
            else:
                nhl_wins[pid] = nhl_wins.get(pid, 0) + r["wins"] / len(r["names"])
    absent = {proj_index.find(r["name"], r["team"]) for r in nhl["absences"]} - {None}

    views = {}
    for p in projs:
        if p.pos == "G":
            views[p.player_id] = consensus.blend_goalie(p, kodo_g.get(p.player_id), nhl_wins.get(p.player_id))
        else:
            views[p.player_id] = consensus.blend_skater(p, kodo_sk.get(p.player_id), nhl_points.get(p.player_id))
        if p.player_id in absent:
            p.flags.append("NHL.com: key injury/absence")
        if views[p.player_id].spread > 0.2:
            p.flags.append("sources disagree")
    consensus.cap_team_starts([p for p in projs if p.pos == "G"])
    return views


def _source_summary(view: consensus.SourceView | None) -> dict:
    if view is None:
        return {}
    out = {"model": round(view.model_fpts)}
    if view.kodo_fpts is not None:
        out["kodo"] = round(view.kodo_fpts)
    if view.nhl_points is not None:
        out["nhl_pts"] = view.nhl_points
    if view.nhl_wins is not None:
        out["nhl_w"] = round(view.nhl_wins, 1)
    return out


def model_consensus_projections(
    roster: dict[int, dict], dfo_rows: list[dict], adp_rows: list[dict]
) -> tuple[list[pj.Projection], dict[int, consensus.SourceView], list[str]]:
    """In-house model blended with Kodo + NHL.com (the pre-Yahoo pipeline)."""
    last = pj.SEASONS[-1]
    skater_reports = {
        s: {rep: sources.season_report(f"skater/{rep}", s) for rep in ("summary", "realtime", "faceoffwins")}
        for s in pj.SEASONS
    }
    goalie_reports = {s: sources.season_report("goalie/summary", s) for s in pj.SEASONS}
    team_rows = sources.season_report("team/summary", last)

    skater_seasons = pj.skater_seasons(skater_reports)
    goalie_seasons = pj.goalie_seasons(goalie_reports)
    history = NameIndex(
        [(r["playerId"], r["skaterFullName"], None, pj.NHL_TO_FANTASY_POS[r["positionCode"]])
         for s in pj.SEASONS for r in skater_reports[s]["summary"]]
        + [(r["playerId"], r["goalieFullName"], None, "G") for s in pj.SEASONS for r in goalie_reports[s]]
    )
    # De-duplicate history (same player appears once per season).
    history = NameIndex(list({e[0]: e for e in history.entries}.values()))

    unmatched_dfo = extend_roster(roster, dfo_rows, history)
    depth = load_depth(roster, dfo_rows)

    index = roster_index(roster)
    adp_pids = {index.find(a["name"], a["team"], a["pos"]) for a in adp_rows} - {None}
    rookie_ids = {
        pid for pid, p in roster.items()
        if pid not in skater_seasons and p["position"] != "G"
        and (pid in adp_pids or (depth.get(pid) and depth[pid].group in LINEUP_GROUPS - {"f4", "ir"}))
    }

    teams, league_ctx = pj.team_contexts(team_rows, sources.team_name_to_abbrev())
    projs = pj.project_skaters(skater_seasons, roster, depth, rookie_ids)
    projs += pj.project_goalies(goalie_seasons, roster, depth, teams, league_ctx)
    by_pid = {p.player_id: p for p in projs}
    proj_index = NameIndex([(p.player_id, p.name, p.team, p.pos) for p in projs])

    overrides = []
    for o in load_overrides():
        pid = proj_index.find(o["name"], o.get("team") or None)
        if pid is None:
            print(f"! override not matched: {o['name']}")
        else:
            overrides.append((by_pid[pid], float(o["games"]), o.get("note")))

    # Goalie starts overrides are the model's opinion and get blended below.
    for p, games, note in overrides:
        if p.pos == "G":
            pj.rescale_goalie(p, games)

    views = blend_published(projs, proj_index)

    # Skater games overrides are firm injury news: applied after blending.
    for p, games, note in overrides:
        if p.pos != "G":
            pj.rescale_skater(p, games)
        if note:
            p.flags.append(note)

    # Eligibility guess: NHL position + ADP site position + DailyFaceoff slot.
    fp_pos = {index.find(a["name"], a["team"], a["pos"]): a["pos"] for a in adp_rows}
    for p in projs:
        p.elig = {p.pos}
        if p.pos in FORWARD_POS:
            p.elig |= {fp_pos.get(p.player_id)} & FORWARD_POS
            if p.depth.slot in DFO_SLOT_POS:
                p.elig.add(DFO_SLOT_POS[p.depth.slot])
    return projs, views, unmatched_dfo


def yahoo_source_projections(roster: dict[int, dict], dfo_rows: list[dict]) -> list[pj.Projection]:
    """Yahoo's projections with Yahoo's eligibility; NHL roster/DailyFaceoff
    only add the NHL id, age and current line for display."""
    projs = load_yahoo_projections()
    depth = load_depth(roster, dfo_rows)
    index = roster_index(roster)
    for p in projs:
        pid = index.find(p.name, p.team, p.pos)
        if pid is None:
            continue
        p.player_id = pid
        p.age = pj.age_on(roster[pid].get("birthDate"))
        p.depth = depth.get(pid, pj.Depth())

    proj_index = NameIndex([(p.player_id, p.name, p.team, p.pos) for p in projs])
    by_pid = {p.player_id: p for p in projs}
    for o in load_overrides(YAHOO_OVERRIDES_FILE):
        pid = proj_index.find(o["name"], o.get("team") or None)
        if pid is None:
            print(f"! override not matched: {o['name']}")
            continue
        p = by_pid[pid]
        (pj.rescale_goalie if p.pos == "G" else pj.rescale_skater)(p, float(o["games"]))
        if o.get("note"):
            p.flags.append(o["note"])
    return projs


def main() -> None:
    roster = {p["id"]: p for p in sources.current_rosters(REFRESH)}
    dfo_rows = sources.dailyfaceoff_combinations(REFRESH)
    adp_rows = sources.fantasypros_adp(REFRESH)

    views: dict[int, consensus.SourceView] = {}
    unmatched_dfo: list[str] = []
    if SOURCE == "yahoo":
        projs = yahoo_source_projections(roster, dfo_rows)
    else:
        projs, views, unmatched_dfo = model_consensus_projections(roster, dfo_rows, adp_rows)
    proj_index = NameIndex([(p.player_id, p.name, p.team, p.pos) for p in projs])

    adp_by_pid: dict[int, dict] = {}
    unmatched_adp = []
    for a in adp_rows:
        pid = proj_index.find(a["name"], a["team"], a["pos"])
        if pid is None:
            unmatched_adp.append(a)
        else:
            adp_by_pid[pid] = a

    levels, value_pos = value_positions(projs)

    def vorp(p: pj.Projection) -> float:
        return p.fpts - levels[value_pos[p.player_id]]

    board = sorted(projs, key=vorp, reverse=True)[:BOARD_SIZE]
    tiers = assign_tiers([vorp(p) for p in board])

    rows = []
    pos_rank: dict[str, int] = defaultdict(int)
    for rank, (p, tier) in enumerate(zip(board, tiers), start=1):
        pos = value_pos[p.player_id]
        pos_rank[pos] += 1
        a = adp_by_pid.get(p.player_id, {})
        s = p.stats
        rows.append({
            "rank": rank,
            "tier": tier,
            "id": p.player_id,
            "name": p.name,
            "team": p.team,
            "pos": pos,
            "elig": "/".join(x for x in ("C", "LW", "RW", "D", "G") if x in p.elig),
            "pos_rank": f"{pos}{pos_rank[pos]}",
            "age": p.age,
            "vorp": round(vorp(p), 1),
            "fpts": round(p.fpts, 1),
            "fpg": round(p.fpts_per_game, 2),
            "games": round(p.games, 1),
            "yahoo_adp": a.get("yahoo"),
            "adp_pos": a.get("pos"),
            "line": p.depth.slot and f"{p.depth.group}-{p.depth.slot}".upper(),
            "pp": (p.depth.pp or "").upper(),
            "flags": ", ".join(p.flags),
            "src": _source_summary(views.get(p.player_id)),
            "yahoo_rank": p.yahoo_rank,
            "market": a.get("yahoo") or (p.yahoo_rank * 1.05 if p.yahoo_rank else None),
            "status": p.status,
            **({k: round(s[k], 1) for k in pj.SKATER_STATS} if p.pos != "G" else {}),
            **({"gs": round(s["gs"], 1), "w": round(s["w"], 1), "ga": round(s["ga"], 1),
                "sv": round(s["sv"]), "so": round(s["so"], 1), "sv_pct": round(s["sv_pct"], 3)}
               if p.pos == "G" else {}),
        })

    # Market rank among board players by Yahoo ADP; vs_adp > 0 means the
    # market drafts him later than this board ranks him (a bargain for you).
    adp_sorted = sorted((r for r in rows if r["yahoo_adp"]), key=lambda r: r["yahoo_adp"])
    for i, r in enumerate(adp_sorted, start=1):
        r["adp_rank"] = i
        r["vs_adp"] = i - r["rank"]

    fields = ["rank", "tier", "name", "team", "pos", "elig", "pos_rank", "age", "vorp", "fpts", "fpg", "games",
              "yahoo_adp", "vs_adp", "yahoo_rank", "status", "model_fpts", "kodo_fpts", "nhl_com", "line", "pp", "flags", *pj.SKATER_STATS,
              "gs", "w", "ga", "sv", "so", "sv_pct"]
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            src = r["src"]
            w.writerow(r | {"model_fpts": src.get("model"), "kodo_fpts": src.get("kodo"),
                            "nhl_com": src.get("nhl_pts", src.get("nhl_w"))})

    board_data = {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "replacement": {k: round(v, 1) for k, v in levels.items()},
        "waiver_examples": {pos: [p.name for p in pool[:3]] for pos, pool in waiver_pool(projs).items()},
        "players": rows,
        "unprojected_adp": [a for a in unmatched_adp if a["yahoo"] and a["yahoo"] <= 250],
    }
    JSON_OUT.write_text(json.dumps(board_data, indent=1), encoding="utf-8")
    write_board_page(board_data)

    print(f"Replacement levels: { {k: round(v) for k, v in sorted(levels.items())} }")
    print(f"Wrote {CSV_OUT}, {JSON_OUT} and {BOARD_OUT} ({len(rows)} players, source: {SOURCE})")
    if unmatched_adp:
        print("ADP players without a projection:",
              ", ".join(f"{a['name']} ({a['team']}, ADP {a['yahoo']})" for a in unmatched_adp))
    if unmatched_dfo:
        print(f"DailyFaceoff players with no NHL history ({len(unmatched_dfo)}):", ", ".join(unmatched_dfo))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download depth charts, ADP and rosters")
    parser.add_argument("--source", choices=("yahoo", "consensus"), default="yahoo",
                        help="yahoo = Yahoo's exported projections (data/yahoo/*.csv); "
                             "consensus = in-house model + Kodo + NHL.com")
    args = parser.parse_args()
    REFRESH, SOURCE = args.refresh, args.source
    main()
