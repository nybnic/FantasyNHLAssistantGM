"""Visual draft-day cheat sheet: rules, a 14-round checklist and position
tiers, rendered into draft/cheatsheet_template.html from the board data."""
from __future__ import annotations

import html
from pathlib import Path

from draft.scoring import DRAFT_ROUNDS
from draft.value import assign_tiers

TEMPLATE = Path("draft/cheatsheet_template.html")
DEPTH = {"C": 30, "LW": 24, "RW": 24, "D": 40, "G": 28}
YAHOO_TEAM = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}

# Goalies Yahoo credits with a starter's workload they may not get
# (Yahoo vs Kodo starts, Sep 2026 analysis). Third-goalie picks only.
G3_ONLY = {
    "Sergei Murashov": "46 GS in Yahoo vs 27; not the starter yet",
    "Dan Vladar": "1A/1B split, currently out",
    "Jordan Binnington": "Hofer is the starter",
    "Alex Nedeljkovic": "backup; 34 GS vs 22",
    "Frederik Andersen": "injured; 37 GS vs 25",
    "Thatcher Demko": "IR, hip surgery",
}

# Round checklist: (focus, checks, after-round target chips).
# From mock drafts (Sep 2026): goalie checkpoints cost nothing and help if the
# league takes goalies early; forcing defensemen early cost 1.5-2% in every
# league type tested (good D last into rounds 10-14), so D only gets the
# feasibility check "open starting slots <= picks left".
ROUNDS = [
    ("Best skater on your list",
     ["No goalie this round", "An elite C or a top D are both fine"],
     [("info", "0 G")]),
    ("Best name on your list: a top goalie counts",
     ["Goalie at the top of your list? Take him (first-goalie window)", "Otherwise the best skater"],
     [("info", "G 0–1")]),
    ("Goalie deadline: have 1 G after this pick",
     ["No goalie yet? Take the top goalie on your list now", "Already have one? Skip goalies until round 4"],
     [("need", "G = 1")]),
    ("Second-goalie window opens",
     ["Best name on your list; a 2nd goalie is allowed from now",
      "Glance at the tiers: is a position about to drop a tier?"],
     [("info", "G 1–2")]),
    ("Keep taking value",
     ["Faceoffs are only worth 0.1 each: use them as a tie-breaker, not a reason to pick",
      "No need to reach for D: good ones last to rounds 10+"],
     []),
    ("Keep taking value",
     ["Skip the 'third goalie only' names as your second goalie",
      "Two C slots, four wing slots: count what you have"],
     []),
    ("Plan your second goalie",
     ["Only one goalie? Your second must come by the end of round 8",
      "Check the G tier: take G2 now if the tier is almost gone"],
     []),
    ("Second-goalie deadline · third-goalie window opens",
     ["Have 2 G after this pick", "A 3rd goalie is now allowed: it helps reach 3 goalie games a week"],
     [("need", "G = 2")]),
    ("Count your open starting slots",
     ["Open starting slots (of 2C 2LW 2RW 4D 2G) must be ≤ picks left (6)",
      "Empty position? Its best name moves up for you"],
     [("info", "6 picks left")]),
    ("Fill starting slots first",
     ["Open starting slots ≤ picks left (5)?", "D is deep: late D (hits + blocks) are fine for the last D slots"],
     [("info", "5 picks left")]),
    ("Fill starting slots first",
     ["Open starting slots ≤ picks left (4)? If equal, only fill slots from now on", "Never more than 3 goalies"],
     [("info", "4 picks left")]),
    ("Fill starting slots, then bench",
     ["All 12 starting slots covered, or exactly enough picks left for them",
      "Bench plan: 3rd goalie + one C/W forward"],
     [("info", "3 picks left")]),
    ("Bench: 3rd goalie or C/W forward",
     ["Third goalie if you don't have one (best bench use in daily lineups)",
      "Otherwise the best C/W forward on your list"],
     [("info", "2 picks left")]),
    ("Last pick: best name that fits",
     ["Every starting slot filled", "Tagged O/IR? After the draft, move him to IR/IR+ and add a free agent"],
     [("ok", "2C 2LW 2RW 4D 2G + 2 BN")]),
]
assert len(ROUNDS) == DRAFT_ROUNDS


def goalie_window(rnd: int) -> tuple[str, str]:
    """(css class, timeline label) for the goalie window in a round."""
    if rnd == 1:
        return "no", "no G"
    if rnd <= 3:
        return "g1", "G1"
    if rnd <= 7:
        return "g2", "G2"
    if rnd == 8:
        return "g2", "G2·G3"
    return "g3", "G3"


def _team(p: dict) -> str:
    return YAHOO_TEAM.get(p["team"], p["team"])


def _adp(p: dict) -> str:
    return str(round(p["yahoo_adp"])) if p.get("yahoo_adp") else "–"


def render(players: list[dict], ranked: list[dict], replacement: dict, generated: str) -> str:
    """The page body (no <html>/<head>), for publishing or wrapping locally."""
    esc = html.escape
    timeline = "".join(
        f'<div class="tl-{cls}"><b>R{r}</b>{label}</div>'
        for r in range(1, DRAFT_ROUNDS + 1) for cls, label in [goalie_window(r)]
    )

    rounds = []
    for r, (focus, checks, targets) in enumerate(ROUNDS, start=1):
        cls, _ = goalie_window(r)
        items = "".join(
            f'<li><label><input type="checkbox" id="r{r}c{i}"><span>{esc(c)}</span></label></li>'
            for i, c in enumerate(checks)
        )
        chips = "".join(f'<span class="chip {k}">{esc(t)}</span>' for k, t in targets)
        rounds.append(
            f'<div class="round k-{cls}"><div class="rn"><b>R{r}</b><span class="pk" data-round="{r}"></span></div>'
            f'<div><div class="focus">{esc(focus)}</div><ul class="checks">{items}</ul></div>'
            f'<div class="target">{"After this round" if chips else ""}<div class="chips">{chips}</div></div></div>'
        )

    list_pos = {p["id"]: i for i, p in enumerate(ranked, start=1)}
    avoid = sorted(
        (p for p in players
         if p.get("yahoo_adp") and p["yahoo_adp"] <= 50 and list_pos.get(p["id"], 999) >= p["yahoo_adp"] + 40),
        key=lambda p: p["yahoo_adp"],
    )
    avoid_html = "".join(
        f'<span class="namechip">{esc(p["name"])} <small>ADP {round(p["yahoo_adp"])} · '
        f'your list {"#" + str(list_pos[p["id"]]) if p["id"] in list_pos else "not in top " + str(len(ranked))}</small></span>'
        for p in avoid
    )
    names = {p["name"] for p in players}
    g3_html = "".join(
        f'<span class="namechip">{esc(n)} <small>{esc(why)}</small></span>'
        for n, why in G3_ONLY.items() if n in names
    )

    top_value = max(p["vorp"] for p in players)
    columns = []
    for pos, depth in DEPTH.items():
        group = [p for p in players if p["pos"] == pos][:depth]
        tiers = assign_tiers([p["vorp"] for p in group], min_gap=10.0, max_width=40.0)
        rows, last = [], None
        for p, t in zip(group, tiers):
            if t != last:
                rows.append(f'<div class="tierlabel">Tier {t}</div>')
                last = t
            tags = ""
            if p.get("status"):
                tags += f'<span class="tag st">{esc(p["status"])}</span>'
            wing = "LW" in p["elig"] or "RW" in p["elig"]
            if wing and (p.get("fow") or 0) >= 500:
                tags += f'<span class="tag fo" title="{round(p["fow"])} projected faceoff wins">FO</span>'
            width = max(3, 100 * max(0, p["vorp"]) / top_value)
            rows.append(
                f'<div class="p"><div><div class="n">{esc(p["name"])}{tags}</div>'
                f'<div class="m">{esc(p["elig"])} · {esc(_team(p))} · value {round(p["vorp"])}</div></div>'
                f'<div class="adp">ADP<br>{_adp(p)}</div><div class="bar" style="width:{width:.0f}%"></div></div>'
            )
        columns.append(
            f'<div class="pos"><header><h3>{pos}</h3><small>waiver level {round(replacement[pos])}</small></header>'
            f'{"".join(rows)}</div>'
        )

    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("__TIMELINE__", timeline)
            .replace("__ROUNDS__", "".join(rounds))
            .replace("__AVOID__", avoid_html)
            .replace("__G3ONLY__", g3_html)
            .replace("__TIERS__", "".join(columns))
            .replace("__GENERATED__", esc(generated)))
