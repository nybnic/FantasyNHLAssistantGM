"""Export the draft-day paper: a ranked list to enter into Yahoo's pre-draft
rankings, and a one-page cheat sheet (rules + position tiers).

    python -m scripts.build_draft_rankings      # first, so the board data is current
    python -m scripts.export_draft_sheets

Writes data/draft_list.txt, data/draft_list.csv (rank,name,team,position for
rankings importers), data/draft_cheatsheet.md and the visual
data/draft_cheatsheet.html (rules, round checklist, position tiers).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from draft import cheatsheet
from draft.scoring import DRAFT_ROUNDS, GOALIE_WEIGHTS, LEAGUE_TEAMS, SKATER_WEIGHTS
from draft.value import assign_tiers

BOARD_JSON = Path("data/draft_board.json")
HTML_OUT = Path("data/draft_cheatsheet.html")                     # open locally
HTML_ARTIFACT_OUT = Path("data/draft_cheatsheet.artifact.html")   # body-only, for publishing
LIST_OUT = Path("data/draft_list.txt")
CSV_OUT = Path("data/draft_list.csv")
SHEET_OUT = Path("data/draft_cheatsheet.md")
LIST_SIZE = LEAGUE_TEAMS * DRAFT_ROUNDS  # one name per pick in the draft
# Yahoo's pre-draft rankings only take players in its rankable pool: roughly
# those with a Yahoo ADP or a top-~277 preseason rank (the 300-row import on
# Sep 28 2026 rejected 60, all low-rostered depth players). Nobody else sees
# them ranked either. The CSV carries the whole board so the imported list
# still runs well past the last pick; the brief flags the rest as "search".
POOL_PRESEASON_RANK = 277
# Paste Yahoo's import message ("60 lines didn't match a player: Line 114: ...")
# here to replace the pool estimate with the exact rejected rows.
UNMATCHED_FILE = Path("data/yahoo/import_unmatched.txt")
UNMATCHED_RE = re.compile(r"Line \d+: \d+,([^,;]+),([A-Z]{2,3}),")
YAHOO_TEAM = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
SHEET_DEPTH = {"C": 30, "LW": 24, "RW": 24, "D": 48, "G": 32}


def team(p: dict) -> str:
    return YAHOO_TEAM.get(p["team"], p["team"])


def adp(p: dict) -> str:
    return str(round(p["yahoo_adp"])) if p.get("yahoo_adp") else "-"


def status(p: dict) -> str:
    return f" [{p['status']}]" if p.get("status") else ""


def off_yahoo_ids(players: list[dict]) -> set[int]:
    """Players Yahoo's pre-draft rankings can't hold: the rows its import
    rejected if UNMATCHED_FILE has them, else the pool estimate."""
    if UNMATCHED_FILE.exists():
        rejected = set(UNMATCHED_RE.findall(UNMATCHED_FILE.read_text(encoding="utf-8")))
        return {p["id"] for p in players if (p["name"], team(p)) in rejected}
    return {p["id"] for p in players
            if not p.get("yahoo_adp") and (p.get("yahoo_rank") or 9999) > POOL_PRESEASON_RANK}


# Optionally never list a player more than this many spots before his raw
# projected-points rank. With faceoffs at 0.2 a buffer of 16 won in mock drafts
# (+1.2% / +2.5%); with the final 0.1 it lost to plain value order in both an
# ADP-drafting (-2.2%) and a points-drafting (-0.8%) league, so it's off.
POINTS_RANK_BUFFER: int | None = None


def draft_order(players: list[dict]) -> list[dict]:
    """Board value order with no goalie in the first round's worth of spots
    (goalie plan: none in round 1), so autopick can't take one there either."""
    if POINTS_RANK_BUFFER is None:
        ordered = sorted(players, key=lambda p: p["rank"])
    else:
        points_rank = {p["id"]: i for i, p in enumerate(sorted(players, key=lambda p: -p["fpts"]), start=1)}
        key = {p["id"]: max(p["rank"], points_rank[p["id"]] - POINTS_RANK_BUFFER) for p in players}
        ordered = sorted(players, key=lambda p: (key[p["id"]], p["rank"]))
    skaters = [p for p in ordered if p["pos"] != "G"]
    first_round = skaters[:LEAGUE_TEAMS]
    taken = {p["id"] for p in first_round}
    return first_round + [p for p in ordered if p["id"] not in taken]


def write_list(players: list[dict], off_yahoo: set[int], generated: str) -> None:
    lines = [
        f"NOT FOR EVERYONE! - draft ranking ({len(players)} players, built {generated})",
        "Enter in this order into Yahoo: League > Edit Pre-Draft Rankings.",
        "Value order, with no goalie in the first 16 (goalie plan). After your first goalie,",
        "skip goalies until round 4 (cheat sheet rule 1).",
        "{search} = not in Yahoo's rankings pool: won't show in the sorted list, find him by name.",
        "",
    ]
    for i, p in enumerate(players, start=1):
        lines.append(f"{i:>3}. {p['name']} ({p['elig'].replace('/', ',')} - {team(p)}){status(p)}"
                     + (" {search}" if p["id"] in off_yahoo else ""))
    LIST_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(players: list[dict]) -> None:
    """rank,name,team,position - importers match on name. Multi-position is
    "C/LW" so no field needs quoting; plain UTF-8 without BOM so the header
    reads "rank"."""
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "team", "position"])
        for i, p in enumerate(players, start=1):
            w.writerow([i, p["name"], team(p), p["elig"]])


def write_sheet(players: list[dict], replacement: dict, generated: str) -> None:
    out = [
        "# Draft cheat sheet: Not for everyone!",
        "",
        f"16 teams · snake · {DRAFT_ROUNDS} rounds · 45 s per pick. Built {generated} from Yahoo's projections.",
        "",
        "## Every pick",
        "",
        "Take the **highest-ranked available player on your list** unless one of these applies:",
        "",
        "1. **Goalies:** none in round 1 · first in rounds 2-3 · second in rounds 4-7 · third from round 8 · never more than 3.",
        "2. **Roster fit:** don't take a player whose position you've filled (2 C, 2 LW, 2 RW, 4 D) while other slots are open.",
        "   C/W dual-eligible players are the flexible fillers; a 3rd goalie is the best use of a bench spot.",
        "3. **Timing (only if there's time):** if the top player's ADP is well after your next pick and the next one on",
        "   your list is almost as good but won't last, take that one first.",
        "",
        "Avoid at their ADP (they score far less here than their draft spot): Kaprizov, Robertson, Quinn Hughes, Necas,",
        "Caufield, Jack Hughes, Hutson. Hellebuyck is ranked for ~25 starts (holdout).",
        "",
        "## Your picks by draft slot",
        "",
        "Round 1 pick = your slot S; then 33-S, 32+S, 65-S, 64+S ... (every 32 picks: 32k+S and 32k+33-S).",
        "",
        "## Position tiers",
        "",
        "Value = projected season points above the best player left on waivers at that position "
        + "(" + ", ".join(f"{pos} {round(replacement[pos])}" for pos in ("C", "LW", "RW", "D", "G")) + ").",
        "A new tier starts after a clear drop: when a tier is almost gone, the next player at that position is a step down.",
        "Dual-eligible players are listed under the position where they're worth most.",
        "",
    ]
    for pos, depth in SHEET_DEPTH.items():
        group = [p for p in players if p["pos"] == pos][:depth]
        tiers = assign_tiers([p["vorp"] for p in group], min_gap=10.0, max_width=40.0)
        out += [f"### {pos}", "", "| Tier | # | Player | Elig | Team | Value | ADP |", "|---|---|---|---|---|---|---|"]
        last = None
        for i, (p, t) in enumerate(zip(group, tiers), start=1):
            tier_cell = f"**{t}**" if t != last else ""
            last = t
            wing = "LW" in p["elig"] or "RW" in p["elig"]
            fo = f" · {round(p['fow'])} FOW" if wing and (p.get("fow") or 0) >= 500 else ""
            out.append(f"| {tier_cell} | {i} | {p['name']}{status(p)}{fo} | {p['elig']} | {team(p)} "
                       f"| {round(p['vorp'])} | {adp(p)} |")
        out.append("")
    SHEET_OUT.write_text("\n".join(out), encoding="utf-8")


BRIEF_OUT = Path("data/draft_brief.md")


def write_brief(players: list[dict], ranked: list[dict], off_yahoo: set[int], generated: str) -> None:
    """Everything a fresh Claude session needs to give fast pick advice from
    screenshots: instructions, rules, and the full list with tiers/ADP."""
    pos_tier: dict[int, int] = {}
    for pos in ("C", "LW", "RW", "D", "G"):
        group = [p for p in players if p["pos"] == pos]
        for p, t in zip(group, assign_tiers([p["vorp"] for p in group], min_gap=10.0, max_width=40.0)):
            pos_tier[p["id"]] = t
    list_pos = {p["id"]: i for i, p in enumerate(ranked, start=1)}
    avoid = sorted((p for p in players if p.get("yahoo_adp") and p["yahoo_adp"] <= 50
                    and list_pos.get(p["id"], 999) >= p["yahoo_adp"] + 40), key=lambda p: p["yahoo_adp"])
    names = {p["name"] for p in players}

    out = [
        "# Live draft brief: Not for everyone! (Yahoo NHL, H2H points)",
        "",
        f"Built {generated} from Yahoo's projections. Attach this file at the start of the draft session.",
        "",
        "## Your job (Claude)",
        "",
        "I'm drafting live with 45 s per pick. I'll send a screenshot of Yahoo's draft room (available players,",
        "usually sorted by my own rankings, plus my roster) and the round, typically 2-3 picks before my turn.",
        "",
        "Reply FAST and SHORT, no preamble:",
        "1. Three names in priority order, all visible as available in my screenshot (exception: rule 10).",
        "2. One line of reason (which rule or tier decided it).",
        "3. Optionally one warning (e.g. goalie deadline, slots vs picks left, a run on a position).",
        "",
        "I take the first of your three still available at my pick. Use only this file and the screenshot;",
        "don't research. If the screenshot is unreadable, say so in one line.",
        "",
        "## League",
        "",
        "16 teams, snake, 14 rounds. Starters: 2 C, 2 LW, 2 RW, 4 D, 2 G; bench 2; IR + IR+ (not drafted).",
        "Daily lineups. Minimum 3 goalie appearances per week. Max 2 adds per week (36 per season).",
        "Scoring: " + ", ".join(f"{k.upper()} {v:g}" for k, v in SKATER_WEIGHTS.items())
        + " | " + ", ".join(f"{k.upper()} {v:g}" for k, v in GOALIE_WEIGHTS.items()) + ".",
        "My picks: round r pick = (r-1)*16 + S in odd rounds, (r-1)*16 + 17-S in even rounds (S = my slot).",
        "",
        "## Rules (in priority order)",
        "",
        "1. Default: the highest player on MY LIST below who is available and fits.",
        "2. Goalies: none in round 1; the 1st by end of round 3; the 2nd in rounds 4-8 (by end of round 8);",
        "   a 3rd from round 8; never more than 3. Skip goalies outside these windows even if they top the list.",
        "3. Roster fit: don't add a position already filled while other starting slots are open. C/W",
        "   dual-eligible players fill either. Best bench: 3rd goalie + one C/W forward.",
        "4. From round 9: open starting slots must be <= picks left. If equal, only fill open slots.",
        "5. Don't force defensemen early: good D last into rounds 10-14 (mock drafts: forcing D cost 1.5-2%).",
        "6. Tie-break between players close on the list: earlier ADP first (the other is likelier to last).",
        "   Tier cliff: if a player is the last of his position tier and the next tier is a clear drop, prefer him.",
        "7. Third goalie only (never G1/G2): " + ", ".join(n for n in cheatsheet.G3_ONLY if n in names) + ".",
        "8. Let them go at ADP (far below their market price here): "
        + ", ".join(f"{p['name']} (ADP {round(p['yahoo_adp'])})" for p in avoid) + ".",
        "9. Status O/IR players are fine if listed: after the draft they can move to IR/IR+.",
        "10. Notes \"search\" = not in Yahoo's rankings pool, so he never shows in my sorted view (nor anyone",
        "   else's; they rarely get drafted). Skip them until my last 2 picks. Then, if one is above every",
        "   visible player who fits, name him with \"(search)\" as the 1st option and a visible one 2nd.",
        "   Otherwise he's a waiver add after the draft.",
        "",
        "## My list",
        "",
        "`#` = my list order (value order; no goalie in the first 16). Value = projected season",
        "points above the best waiver player at his position. Tier = tier within his position (e.g. C3).",
        "FOW shown for wing-eligible faceoff takers; GS = projected goalie starts; search = rule 10.",
        "",
        "| # | Player | Elig | Team | Value | Tier | ADP | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(ranked, start=1):
        notes = []
        if p.get("status"):
            notes.append(p["status"])
        if p["pos"] == "G":
            notes.append(f"{round(p['games'])} GS")
        elif ("LW" in p["elig"] or "RW" in p["elig"]) and (p.get("fow") or 0) >= 500:
            notes.append(f"{round(p['fow'])} FOW")
        if p["name"] in cheatsheet.G3_ONLY:
            notes.append("G3 only")
        if p["id"] in off_yahoo:
            notes.append("search")
        out.append(f"| {i} | {p['name']} | {p['elig']} | {team(p)} | {round(p['vorp'])} | "
                   f"{p['pos']}{pos_tier.get(p['id'], '')} | {adp(p)} | {', '.join(notes)} |")
    BRIEF_OUT.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    data = json.loads(BOARD_JSON.read_text(encoding="utf-8"))
    players = data["players"]
    generated = data["generated"].replace("T", " ")
    ranked = draft_order(players)
    off_yahoo = off_yahoo_ids(players)
    write_list(ranked[:LIST_SIZE], off_yahoo, generated)
    write_csv(ranked)
    write_sheet(players, data["replacement"], generated)
    page = cheatsheet.render(players, ranked[:LIST_SIZE], data["replacement"], generated)
    HTML_ARTIFACT_OUT.write_text(page, encoding="utf-8")
    head = '<!doctype html>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    HTML_OUT.write_text(head + page, encoding="utf-8")
    write_brief(players, ranked, off_yahoo, generated)
    top = {p["id"] for p in ranked[:LIST_SIZE]}
    print(f"Wrote {LIST_OUT} ({LIST_SIZE} players), {CSV_OUT} ({len(ranked)}), {SHEET_OUT}, {HTML_OUT} and {BRIEF_OUT}")
    print(f"Yahoo can rank {len(ranked) - len(off_yahoo)} of {len(ranked)}; {len(top & off_yahoo)} of the top "
          f"{LIST_SIZE} are 'search' players ({'from ' + str(UNMATCHED_FILE) if UNMATCHED_FILE.exists() else 'estimated'})")


if __name__ == "__main__":
    main()
