"""Read Yahoo screenshots with free, offline OCR (RapidOCR).

Transactions, in three layouts, each add, drop and trade with its date: the
app's League > Transactions tab, the website's Transactions page (full names,
times in US Eastern), and the app's league chat ("Gwp added Elias Lindholm",
dated "hier à 18:48" or "13m ago", so read against the time it was sent).

League tab's standings: each team's W-L-T and points for. All Matchups (the
week's scoreboard): every pairing, with each team's score and Yahoo's
projected total.

Team tab: each row's slot, the name as shown ("M. SCHEIFELE"), team and
positions. Matchup tab: the same for both teams side by side (the slot badge
sits between them), each player's points and Yahoo projection, and the
header's score and projected totals.

OCR reads names and "C • WPG" lines well but misses single-letter slot
badges, so the slot comes from the badge's colour where Yahoo gives each its
own (C blue, D gold, G lime) and from OCR of the badge crop otherwise (LW/RW
share teal, BN/IR/IR+ are grey). A slot that can't be read is None, and the
player keeps the slot they had. Checked on Nico's dark-mode iPhone
screenshots (2026-10-01); a light-mode or desktop layout may need new hues.

league/parse.match_shown_names turns the rows into players.
"""
from __future__ import annotations

import colorsys
import datetime as dt
import functools
import io
import re
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image

_NAME = re.compile(r"^([A-Z])\s?\.\s?([A-Z][A-Z'’ .-]*[A-Z])")
_POS_TEAM = re.compile(r"^((?:LW|RW|C|D|G)(?:,(?:LW|RW|C|D|G))*)[^A-Z]*([A-Z]{2,3})$")
# Badge hues (degrees), measured on the screenshots above.
_HUE_SLOTS = [((190, 250), "C"), ((140, 190), "TEAL"), ((60, 100), "G"), ((25, 60), "D")]
# A badge counts as coloured, not grey, when this share of its sampled pixels
# is vivid. A share, not a count: Telegram shrinks photos to 1280 px, and a
# matchup badge's letter is ~1.3-3.8% vivid at any size, a grey badge 0%.
_MIN_VIVID_SHARE = 0.005
_MIN_VIVID = 8
_POINTS = re.compile(r"^-?\d+\.\d\d$")
_SCORE_PAIR = re.compile(r"^(-?\d+\.\d\d)\s*/\s*(-?\d+\.\d\d)$")  # the scrolled-down header
_CENTRE_SLOT = re.compile(r"^(LW|RW|BN|IR\+?|C|D|G)$")
_TX_TYPE = re.compile(r"^(Add/Drop|Add|Drop|Trade)$", re.I)
# "mer.sept.3002:04PM" once spaces are gone: weekday, month, day, then a
# zero-padded hh:mm (so "oct.110:32AM" is Oct 1, 10:32). French or English.
_TX_DATE = re.compile(r"^[A-Za-zé]{3,4}\.?,?([A-Za-zéûô]{3,5})\.?(\d{1,2}),?(\d{2}):(\d{2})(AM|PM)$", re.I)
_TX_PLAYER = re.compile(r"^([A-Z])\.\s*(.+?)\s*((?:LW|RW|C|D|G)(?:,(?:LW|RW|C|D|G))*)\s*(\(.*)?$")
_MONTHS = {"jan": 1, "fev": 2, "feb": 2, "mar": 3, "avr": 4, "apr": 4, "mai": 5, "may": 5, "juin": 6, "jun": 6,
           "juil": 7, "jul": 7, "aou": 8, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
# The website: "Oct2,3:21am", in US Eastern (checked on 2026-10-02: its 18 rows
# match the app's Helsinki times shifted by 7 h).
_WEB_DATE = re.compile(r"^([A-Za-z]{3,4})\.?(\d{1,2}),(\d{1,2}):(\d{2})(am|pm)$", re.I)
WEB_TIME = ZoneInfo("America/New_York")
# "JackMcBainUTA-C,LW", "Elias Lindholm Bos-c目": a name glued to Yahoo's team and
# positions (OCR drops spaces and mixes case).
_PLAYER_TAIL = re.compile(r"([A-Za-z]{2,3})\s*-\s*((?:LW|RW|C|D|G)(?:\s*,\s*(?:LW|RW|C|D|G))*)", re.I)
_TEAMS = {"ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LA", "MIN", "MTL",
          "NSH", "NJ", "NYI", "NYR", "OTT", "PHI", "PIT", "SJ", "SEA", "STL", "TB", "TOR", "UTA", "VAN", "VGK",
          "WSH", "WPG"}
# The league chat: "hier à 18:48" (OCR: "hiera18:48"), "13m ago", "Yesterday at 6:48 PM".
_CHAT_AGO = re.compile(r"^(?:ilya)?(\d{1,2})(m|min|h)(?:ago)?$", re.I)
_CHAT_DAY = re.compile(r"^(avant-?hier|hier|yesterday|aujourd'?hui|today|[a-z]{3,9}\.?)(?:à|a|at|,)?"
                       r"(\d{1,2}):(\d{2})(am|pm)?$", re.I)
_DAYS_AGO = {"avanthier": 2, "hier": 1, "yesterday": 1, "aujourdhui": 0, "today": 0}
_WEEKDAYS = {"lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})")  # the phone's status bar
_RECORD = re.compile(r"(\d+)-(\d+)-(\d+)$")  # "0-0-0", also at the end of "Nico · 0-0-0"
# "Week 2", also with the week picker's arrows read as text: "Week 2 1" is week 2.
_WEEK = re.compile(r"^week\s*(\d{1,2})\b", re.I)
_GAMES_PLAYED = re.compile(r"^(\d+)/(\d+)$")
_CHAT_MOVE = re.compile(r"^(.+?)(added|dropped)(.+?)(?:and(dropped)(.+))?$")


class ScreenshotError(Exception):
    """The image couldn't be read; the message is safe to show."""


@functools.cache
def _ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR  # slow import; only when a screenshot arrives
    except ImportError as e:  # e.g. a system library opencv needs is missing on the runner
        raise ScreenshotError(f"the OCR library won't load ({e})") from None
    return RapidOCR()


def read(image: bytes, now: dt.datetime | None = None) -> dict:
    """{"kind": "team", "rows": [...]}, {"kind": "matchup", ...} (see _matchup),
    {"kind": "transactions", "rows": [...]} (see _transactions), {"kind":
    "standings", "rows": [...]} (_standings) or {"kind": "scoreboard", ...} (_scoreboard). `now`, the
    phone's local time when it was sent, dates the chat's "hier à 18:48" and
    places the website's Eastern times on the phone's clock."""
    now = now or dt.datetime.now().astimezone()
    img = _open(image)
    lines = _read_lines(img)
    if any(_squash(t) == "yahoofantasy" for _, _, _, t in lines):
        return {"kind": "transactions", "rows": _chat_transactions(img, lines, now)}
    if any(x > 0.55 * img.width and _WEB_DATE.match(t.replace(" ", "")) for x, _, _, t in lines):
        return {"kind": "transactions", "rows": _web_transactions(img, lines, now)}
    if any(_squash(t) == "allmatchups" for _, _, _, t in lines[:8]):
        return {"kind": "scoreboard", **_scoreboard(lines, img.width)}
    if any(_squash(t) == "origproj" for _, _, _, t in lines):
        return {"kind": "web_matchup", **_web_matchup(lines, img.width)}
    if _standings(lines, img.width):
        return {"kind": "standings", "rows": _standings(lines, img.width)}
    if any("transactions" in t.replace(" ", "").lower() for _, _, _, t in lines[:8]):
        return {"kind": "transactions", "rows": _transactions(img, lines)}
    if _is_matchup(lines, img.width):
        return {"kind": "matchup", **_matchup(img, lines)}
    return {"kind": "team", "rows": _roster_rows(img, lines)}


def read_roster(image: bytes) -> list[dict]:
    img = _open(image)
    return _roster_rows(img, _read_lines(img))


def _open(image: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(image)).convert("RGB")
    except OSError:
        raise ScreenshotError("that isn't an image I can open") from None


def _roster_rows(img: Image.Image, lines: list) -> list[dict]:
    rows = []
    for i, (x, y, h, text) in enumerate(lines):
        m = _NAME.match(text)
        if not m or x < 0.2 * img.width:
            continue
        below = [t for bx, by, _, t in lines[i + 1:i + 4] if abs(bx - x) < 2 * h and 0 < by - y < 2 * h]
        positions, team = _pos_team(below[0]) if below else ([], "")
        rows.append({"slot": _slot(img, y, h), "name": f"{m.group(1)}. {m.group(2)}", "team": team,
                     "positions": positions})
    return rows


def _pos_team(text: str) -> tuple[list[str], str]:
    """(["C", "LW"], "NYI") from a "C,LW • NYI" line (OCR often drops the bullet)."""
    m = _POS_TEAM.match(text.replace(" ", ""))
    return (m.group(1).split(","), m.group(2)) if m else ([], "")


def _is_matchup(lines: list, width: int) -> bool:
    """Names in both columns on one row: the matchup tab (the team tab has a
    photo left of each name, so no name starts at the left edge)."""
    left = [(y, h) for x, y, h, t in lines if x < 0.1 * width and _NAME.match(t)]
    right = [y for x, y, _, t in lines if x > 0.5 * width and _NAME.match(t)]
    return any(abs(ry - y) < h for y, h in left for ry in right)


def _matchup(img: Image.Image, lines: list) -> dict:
    """{"score": (mine, theirs) or None, "projected": (mine, theirs) or None,
    "labels": (left header texts, right header texts), "week": the week picker's
    week or None (scrolled out of view), "totals": the Matchup Totals view
    (each player's numbers are the week's, not a day's),
    "rows": [{"slot", "mine": player or None, "theirs": player or None}]},
    a player being {"name", "team", "positions", "points", "projected"}.
    Mine is the left column: the app puts your team first."""
    w = img.width
    names = [(x, y, h, m) for x, y, h, t in lines if (m := _NAME.match(t)) and (x < 0.15 * w or x > 0.5 * w)]
    # "G.WSH" under a name looks like a name too: drop lines just below another.
    names = [n for n in names if not any((n[0] < 0.5 * w) == (o[0] < 0.5 * w) and 0.5 * o[2] < n[1] - o[1] < 2 * o[2]
                                         for o in names)]
    anchors: list[tuple[float, float]] = []  # (y, h) of each row's name line
    for _, y, h, _ in names:
        if all(abs(y - ay) >= h for ay, _ in anchors):
            anchors.append((y, h))
    anchors.sort()

    def player(y: float, h: float, side: str) -> dict | None:
        on_side = (lambda x: x < 0.15 * w) if side == "left" else (lambda x: x > 0.5 * w)
        name = next((m for x, ny, _, m in names if on_side(x) and abs(ny - y) < h), None)
        if not name:
            return None
        below = [_pos_team(t) for x, ly, _, t in lines if on_side(x) and 0.5 * h < ly - y < 2 * h]
        positions, team = next((pt for pt in below if pt[0]), ([], ""))
        in_column = (lambda x: 0.2 * w < x < 0.45 * w) if side == "left" else (lambda x: 0.5 * w < x < 0.75 * w)
        numbers = [float(t) for x, ly, _, t in lines if in_column(x) and -h < ly - y < 2 * h and _POINTS.match(t)]
        return {"name": f"{name.group(1)}. {name.group(2)}", "team": team, "positions": positions,
                "points": numbers[0] if numbers else None,
                "projected": numbers[1] if len(numbers) > 1 else None}

    rows = []
    for y, h in anchors:
        centre = [t for x, ly, _, t in lines if 0.4 * w < x < 0.58 * w and -h < ly - y < 3 * h
                  and _CENTRE_SLOT.match(t.replace(" ", ""))]
        slot = centre[0].replace(" ", "") if centre else _badge_colour(
            img.crop((int(0.44 * w), int(y - 0.4 * h), int(0.56 * w), int(y + 3 * h))))
        rows.append({"slot": slot if slot != "TEAL" else None,
                     "mine": player(y, h, "left"), "theirs": player(y, h, "right")})

    top = anchors[0][0] - anchors[0][1] if anchors else img.height
    header = [(x, y, t) for x, y, _, t in lines if y < top]
    score = projected = None
    for _, _, t in header:
        if m := _SCORE_PAIR.match(t.replace(" ", "")):
            score = (float(m.group(1)), float(m.group(2)))
    numbers = sorted((y, x, float(t)) for x, y, t in header if _POINTS.match(t))
    pairs = []  # two numbers side by side, mine on the left
    for i, (y, x, v) in enumerate(numbers):
        level = [(x2, v2) for y2, x2, v2 in numbers[i + 1:] if abs(y2 - y) < 20]
        if len(level) == 1 and all(abs(y - py) >= 20 for py, _ in pairs):
            pairs.append((y, tuple(v for _, v in sorted([(x, v), level[0]]))))
    pairs = [pair for _, pair in pairs]
    if score is None and pairs:
        score, pairs = pairs[0], pairs[1:]
    if pairs:
        projected = pairs[0]
    # Before the week's first game the top card shows dashes for the score and
    # only Yahoo's projections, which would read as the score: "Games Played
    # 0/43" for both says it's 0 - 0.
    played = [int(m.group(1)) for _, _, t in header if (m := _GAMES_PLAYED.match(t.replace(" ", "")))]
    if len(played) == 2 and not any(played) and score and any(score):
        score, projected = (0.0, 0.0), projected or score
    labels = ([t for x, _, t in header if x < 0.5 * w], [t for x, _, t in header if x >= 0.5 * w])
    header_lines = [(x, y, 0.0, t) for x, y, t in header]
    return {"score": score, "projected": projected, "labels": labels, "rows": rows,
            "week": _week_label(header_lines), "totals": any(_squash(t) == "matchuptotals" for _, _, t in header)}


def _month(text: str) -> int | None:
    t = text.lower().replace("é", "e").replace("û", "u").replace("ô", "o")
    return _MONTHS.get(t[:4]) or _MONTHS.get(t[:3])


def _tx_when(text: str) -> tuple[int, int, int, int] | None:
    """(month, day, hour, minute) from a transaction's date line."""
    m = _TX_DATE.match(text.replace(" ", ""))
    if not m or not (month := _month(m.group(1))):
        return None
    hour = int(m.group(3)) % 12 + (12 if m.group(5).upper() == "PM" else 0)
    return month, int(m.group(2)), hour, int(m.group(4))


def _tx_player(text: str) -> dict | None:
    m = _TX_PLAYER.match(text.strip())
    if not m or not any(ch.islower() for ch in m.group(2)):
        return None
    return {"name": f"{m.group(1)}. {m.group(2).strip()}", "positions": m.group(3).split(",")}


def _icon_action(img: Image.Image, y: float, h: float, x0: float = 0.44, x1: float = 0.56) -> str | None:
    """"add" (a green +) or "drop" (a red -) from the icon between x0 and x1
    (shares of the width; the app's Transactions tab has it mid-row)."""
    w = img.width
    crop = img.crop((int(x0 * w), int(y - 0.3 * h), int(x1 * w), int(y + 1.3 * h)))
    px = np.asarray(crop).reshape(-1, 3)[::2] / 255
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px])
    vivid = hsv[(hsv[:, 1] > 0.35) & (hsv[:, 2] > 0.4)]
    if len(vivid) < 4:
        return None
    hue = float(np.median(vivid[:, 0])) * 360
    return "add" if 90 <= hue < 180 else "drop" if hue < 25 or hue > 330 else None


def _transactions(img: Image.Image, lines: list) -> list[dict]:
    """Each transaction block: {"type": "add" | "drop" | "add/drop" | "trade",
    "when": (month, day, hour, minute), "teams": [team] (two for a trade),
    "players": [{"name", "positions", "action"}]}, action being "add", "drop",
    or for a trade "from:0" / "from:1" (traded away by that team). Newest first,
    as on screen; blocks cut off by the screen's edge are left out."""
    w = img.width
    headers = []
    for i, (x, y, h, t) in enumerate(lines):
        if x < 0.15 * w and (m := _TX_TYPE.match(t.replace(" ", ""))):
            when = next((d for x2, y2, _, t2 in lines if x2 > 0.4 * w and abs(y2 - y) < h
                         and (d := _tx_when(t2))), None)
            headers.append((y, h, m.group(1).lower(), when))  # undated still ends the block above
    blocks = []
    for k, (y, h, kind, when) in enumerate(headers):
        if not when:
            continue
        end = headers[k + 1][0] if k + 1 < len(headers) else img.height
        body = [(x, ly, lh, t) for x, ly, lh, t in lines if y + 0.8 * h < ly < end - 0.5 * h]
        left = [(ly, lh, t) for x, ly, lh, t in body if x < 0.3 * w]
        right = [(ly, lh, t) for x, ly, lh, t in body if x > 0.4 * w]
        players = []
        if kind == "trade":
            names = [t for _, _, t in left if not _tx_player(t) and "trading" not in t.lower()][:1]
            names += [t for _, _, t in right if not _tx_player(t) and "trading" not in t.lower()][:1]
            for side, rows in ((0, left), (1, right)):
                for ly, lh, t in rows:
                    if p := _tx_player(t):
                        players.append({**p, "action": f"from:{side}"})
            teams = names
        else:
            teams = [t for _, _, t in left if not _tx_player(t)][:1]
            for ly, lh, t in right:
                if p := _tx_player(t):
                    action = kind if kind in ("add", "drop") else _icon_action(img, ly, lh)
                    if action:
                        players.append({**p, "action": action})
            # More than one move's players: the block below lost its header and
            # ran into this one, so its players come after this block's own.
            players = next((players[:n] for n in range(len(players), 0, -1) if _one_move(players[:n])), [])
        if players and len(teams) == (2 if kind == "trade" else 1):
            blocks.append({"type": kind, "when": when, "teams": teams, "players": players})
    return blocks


def _record(text: str) -> tuple[int, int, int] | None:
    """(W, L, T) from "0-0-0" or "Nico · 0-0-0" (OCR reads some zeros as O)."""
    m = _RECORD.search(text.replace("O", "0").replace(" ", ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _standings(lines: list, width: int) -> list[dict]:
    """League tab: [{"team", "w", "l", "t", "pf"}], one per row whose record
    (mid-right) and points for (far right) sit level with a team name; empty
    when fewer than 3 rows read (not a standings screen)."""
    rows = []
    for rx, ry, rh, rt in lines:
        if not 0.6 * width < rx < 0.8 * width or not _record(rt) or not _RECORD.fullmatch(rt.replace("O", "0")):
            continue
        name = next((t for x, y, h, t in lines if 0.15 * width < x < 0.5 * width and abs(y - ry) < rh
                     and not _record(t)), None)
        pf = next((t for x, y, h, t in lines if x > 0.8 * width and abs(y - ry) < rh and _POINTS.match(t)), None)
        if name and pf:
            w, l, t = _record(rt)
            rows.append({"team": name, "w": w, "l": l, "t": t, "pf": float(pf)})
    return rows if len(rows) >= 3 else []


def _scoreboard(lines: list, width: int) -> dict:
    """All Matchups: {"week": n or None, "teams": [{"team", "score", "projected"}],
    "pairs": [(i, j)]} (indexes into teams). A card is two team lines, each
    with its score at the right and, on the manager line below it ("Nico ·
    0-0-0"), Yahoo's projected total. Cards are told apart by the gap
    between them; a team cut off by the screen's edge is left unpaired."""
    week = _week_label(lines)
    numbers = [(y, h, float(t)) for x, y, h, t in lines if x > 0.7 * width and _POINTS.match(t)]
    left = [(x, y, h, t) for x, y, h, t in lines if 0.18 * width < x < 0.6 * width]
    manager = {y for x, y, h, t in left
               if _record(t) or any(abs(y2 - y) < h / 2 and _record(t2) for x2, y2, _, t2 in left if x2 != x)}

    def number_at(y: float, h: float) -> float | None:
        near = [(abs(ny - y), v) for ny, nh, v in numbers if abs(ny - y) < max(h, nh)]
        return min(near)[1] if near else None

    teams = []
    for x, y, h, t in left:
        if y in manager or _record(t):
            continue
        score = number_at(y, h)
        if score is None:
            continue
        below = next((my for my in sorted(manager) if 0 < my - y < 3 * h), None)
        teams.append({"team": t, "score": score, "projected": number_at(below, h) if below else None, "y": y})
    pairs, i = [], 0
    while i + 1 < len(teams):
        if teams[i + 1]["y"] - teams[i]["y"] < 0.16 * width:
            pairs.append((i, i + 1))
            i += 2
        else:
            i += 1
    for team in teams:
        del team["y"]
    return {"week": week, "teams": teams, "pairs": pairs}


def _web_matchup(lines: list, width: int) -> dict:
    """A matchup's header on Yahoo's website: {"teams": (left, right),
    "score", "orig": Yahoo's projection from before the week ("Orig Proj"),
    "live": its projection now ("Live Proj"), each (left, right) or None,
    "week": the label if in view}. Yahoo keeps "Orig Proj" after the week,
    so it can be sent any time."""
    numbers = [(x, y, h, float(t)) for x, y, h, t in lines if _POINTS.match(t)]

    def pair_at(y: float, h: float) -> tuple[float, float] | None:
        level = sorted((x, v) for x, ny, nh, v in numbers if abs(ny - y) < max(h, nh))
        return (level[0][1], level[-1][1]) if len(level) >= 2 else None

    labels = {_squash(t): (y, h) for _, y, h, t in lines if _squash(t) in ("origproj", "liveproj")}
    orig_y, orig_h = labels["origproj"]
    above = sorted((y, h) for _, y, h, _ in numbers if y < orig_y - orig_h)
    score = next((p for y, h in above if (p := pair_at(y, h))), None)

    def name(side) -> str | None:
        shown = [(h, t) for x, y, h, t in lines if side(x) and y < orig_y and not _POINTS.match(t)]
        return max(shown)[1] if shown else None

    return {"teams": (name(lambda x: x < 0.3 * width), name(lambda x: x > 0.7 * width)), "score": score,
            "orig": pair_at(orig_y, orig_h),
            "live": pair_at(*labels["liveproj"]) if "liveproj" in labels else None,
            "week": _week_label(lines)}


def _week_label(lines: list) -> int | None:
    """The fantasy week a screen shows ("Week 2"), or None when it's out of view."""
    return next((int(m.group(1)) for _, _, _, t in lines if (m := _WEEK.match(t.strip()))
                 and 1 <= int(m.group(1)) <= 26), None)


def _squash(text: str) -> str:
    return re.sub(r"\s", "", text).lower()


def _split_name(name: str) -> str:
    """"JackMcBain" -> "Jack McBain": OCR drops the space, so put it back at the
    first lower-to-upper step (only there, so McBain keeps his capital B)."""
    name = name.strip(" .,")
    return name if " " in name else re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name, count=1)


def _team_code(letters: str) -> str | None:
    up = letters.upper()
    return next((t for t in (up, up.replace("L", "I"), up.replace("0", "O")) if t in _TEAMS), None)


def _player_line(text: str) -> dict | None:
    """{"name", "team", "positions"} from "VasilyPodkolzinEDM-LW,RW" (the website,
    the chat's cards). The team code is 2-3 letters glued to the surname, so the
    first split that gives a real team wins ("SherwoodsJ" is Sherwood of SJ)."""
    fallback = None
    for m in _PLAYER_TAIL.finditer(text):
        letters, name = m.group(1), text[:m.start()]
        team = _team_code(letters)
        if not team and len(letters) == 3 and (team := _team_code(letters[1:])):
            name += letters[0]
        player = {"name": _split_name(name), "team": team or "",
                  "positions": [p.strip().upper() for p in m.group(2).split(",")]}
        if not re.search(r"[a-z]", player["name"]):
            continue
        if team:
            return player
        fallback = fallback or player
    return fallback


def _source_action(text: str) -> str | None:
    """The website's line under a player: where he came from or went."""
    t = _squash(text)
    if t.startswith("to"):  # "To Waivers", "To Free Agents"
        return "drop" if "waiver" in t or "freeagent" in t else None
    return "add" if t in ("freeagent", "waiver", "waivers") else None


def _web_transactions(img: Image.Image, lines: list, now: dt.datetime) -> list[dict]:
    """The website's Transactions page: players on the left (each with "Free
    Agent", "Waiver" or "To Waivers" under him), the team and its date on the
    right. A trade is two rows, "Traded to" each team. Newest first; rows cut by
    the screen's edge (a player above the team's line, or none level with the
    date) are left out."""
    w = img.width
    right = [(y, h, t) for x, y, h, t in lines if x > 0.55 * w]
    blocks = []
    for y, h, t in right:
        m = _WEB_DATE.match(t.replace(" ", ""))
        month = m and _month(m.group(1))
        if not month:
            continue
        hour = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "pm" else 0)
        when = _place(now, month, int(m.group(2)), hour, int(m.group(4)), WEB_TIME)
        above = [(ty, tt) for ty, _, tt in right if y - 2.5 * h < ty < y - 0.3 * h
                 and not _WEB_DATE.match(tt.replace(" ", "")) and _squash(tt) != "tradedto"]
        if not above or not when:
            continue
        team_y, team = max(above)
        trade = any(_squash(tt) == "tradedto" and team_y - h < ty < y + h for ty, _, tt in right)
        blocks.append({"top": team_y, "date": y, "team": team.split("(")[0].strip(), "trade": trade,
                       "when": when, "players": []})
    left = [(y, h, t) for x, y, h, t in lines if 0.08 * w < x < 0.5 * w]
    for i, (y, h, t) in enumerate(left):
        if not blocks or not (p := _player_line(t)):
            continue
        block = min(blocks, key=lambda b: abs((b["top"] + b["date"]) / 2 - y))
        if abs((block["top"] + block["date"]) / 2 - y) > 3 * h:
            continue
        below = [(ly, lt) for ly, _, lt in left[i + 1:] if 0 < ly - y < 1.8 * h]
        source = below[0] if below and not _player_line(below[0][1]) else None
        action = _source_action(source[1]) if source else None
        if not block["trade"] and not action:
            action = _icon_action(img, y, h, 0.04, 0.13)
        block["players"].append({"y": y, "h": h, "last": source[0] if source else y, "action": action,
                                 **{k: p[k] for k in ("name", "positions")}})

    def complete(b: dict) -> bool:
        ps = b["players"]
        return bool(ps) and ps[0]["y"] <= b["top"] + 0.5 * ps[0]["h"] and ps[-1]["last"] >= b["date"] - ps[-1]["h"]

    rows, k = [], 0
    while k < len(blocks):
        b = blocks[k]
        if b["trade"]:
            other = blocks[k + 1] if k + 1 < len(blocks) else None
            if other and other["trade"] and other["when"] == b["when"] and complete(b) and complete(other):
                # b's players went to b's team, from other's team: teams = [giver of b's players, ...]
                players = [{"name": p["name"], "positions": p["positions"], "action": "from:0"} for p in b["players"]]
                players += [{"name": p["name"], "positions": p["positions"], "action": "from:1"}
                            for p in other["players"]]
                rows.append({"type": "trade", "when": b["when"], "teams": [other["team"], b["team"]],
                             "players": players})
                k += 2
                continue
        elif (b := _own_players(b)) and complete(b) and all(p["action"] for p in b["players"]):
            actions = {p["action"] for p in b["players"]}
            rows.append({"type": "add/drop" if len(actions) == 2 else actions.pop(), "when": b["when"],
                         "teams": [b["team"]], "players": [{"name": p["name"], "positions": p["positions"],
                                                            "action": p["action"]} for p in b["players"]]})
        k += 1
    return rows


def _one_move(players: list[dict]) -> bool:
    """Whether these are one Yahoo move: an add, a drop, or an add then its
    drop (both layouts list the added player first). Two adds or two drops are
    two moves read as one."""
    return [p["action"] for p in players] in (["add"], ["drop"], ["add", "drop"])


def _own_players(block: dict) -> dict | None:
    """A website row with the players of one move: when a neighbour's team or
    date wasn't read, its players join the nearest row, so a row holding more
    than one move keeps the run of players centred on its team and date (none
    if no run is one move)."""
    ps = block["players"]
    if _one_move(ps) or not all(p["action"] for p in ps):
        return block
    centre = (block["top"] + block["date"]) / 2
    runs = [ps[i:j] for i in range(len(ps)) for j in range(i + 1, len(ps) + 1) if _one_move(ps[i:j])]
    if not runs:
        return None
    best = min(runs, key=lambda r: (abs((r[0]["y"] + r[-1]["last"]) / 2 - centre), -len(r)))
    return {**block, "players": best}


def _place(now: dt.datetime, month: int, day: int, hour: int, minute: int,
           tz: dt.tzinfo | None = None) -> tuple[int, int, int, int] | None:
    """(month, day, hour, minute) on the phone's clock (now's zone) of a time
    shown in `tz` (default: the phone's own), in the year that keeps it at or
    before now."""
    year = now.year - (1 if month > now.month + 1 else 0)
    try:
        at = dt.datetime(year, month, day, hour, minute, tzinfo=tz or now.tzinfo).astimezone(now.tzinfo)
    except ValueError:  # a misread day
        return None
    return at.month, at.day, at.hour, at.minute


def _chat_when(text: str, now: dt.datetime) -> tuple[int, int, int, int] | None:
    """A league chat message's time: "13mago", "hiera18:48", "avant-hier a13:16"."""
    t = _squash(text).replace("â", "a")
    if t in ("now", "justnow", "alinstant", "àlinstant"):
        at = now
    elif m := _CHAT_AGO.match(t):
        at = now - dt.timedelta(**{"hours" if m.group(2) == "h" else "minutes": int(m.group(1))})
    elif m := _CHAT_DAY.match(t):
        day = m.group(1).replace("-", "").replace("'", "").rstrip(".")
        if day in _DAYS_AGO:
            back = _DAYS_AGO[day]
        elif day[:3] in _WEEKDAYS:
            back = (now.weekday() - _WEEKDAYS[day[:3]]) % 7 or 7
        else:
            return None
        hour = int(m.group(2)) % 12 + (12 if m.group(4) == "pm" else 0) if m.group(4) else int(m.group(2))
        date = now.date() - dt.timedelta(days=back)
        return date.month, date.day, hour, int(m.group(3))
    else:
        return None
    return at.month, at.day, at.hour, at.minute


def _chat_transactions(img: Image.Image, lines: list, now: dt.datetime) -> list[dict]:
    """The app's league chat: each "Yahoo Fantasy" message dated beside its
    header ("hier à 18:48"), "Gwp added Elias Lindholm" (maybe wrapped, maybe
    "... and dropped ...") with a card per player below; messages sent together
    share one header. "Transactions have been processed" lists waiver claims
    under "Jyri (Gwp)". Messages above the first header have no date and are
    left out. Newest first, like the other layouts. Trades aren't read here."""
    w = img.width
    if clock := next((m for x, y, _, t in lines if y < 0.05 * img.height and x < 0.3 * w
                      and (m := _CLOCK.match(t.replace(" ", "")))), None):
        shot = now.replace(hour=int(clock.group(1)) % 24, minute=int(clock.group(2)))
        now = shot if shot <= now else shot - dt.timedelta(days=1)  # taken a bit before it was sent
    messages, when, last = [], None, None  # last: the line a sentence may continue onto
    for x, y, h, t in lines:
        s = _squash(t)
        if x < 0.12 * w or y < 0.05 * img.height:
            continue  # the "create a poll" tip, the status bar
        if s == "yahoofantasy":
            when = next((d for x2, y2, _, t2 in lines if x2 > 0.35 * w and abs(y2 - y) < h
                         and (d := _chat_when(t2, now))), None)
            last = None
            continue
        if x < 0.22 * w:  # message text
            if last and 0 < y - last < 1.6 * h and "text" in messages[-1]:
                messages[-1]["text"] += t.replace(" ", "")  # a wrapped sentence
            elif "added" in s or "dropped" in s:
                messages.append({"when": when, "text": t.replace(" ", ""), "cards": []})
            else:
                last = None
                continue
            last = y
            continue
        last = None
        if 0.22 * w <= x < 0.45 * w and (m := re.match(r"^[^(]*\((.+)\)$", t.strip())):
            messages.append({"when": when, "team": m.group(1).strip(), "cards": []})  # a waiver claim
        elif 0.22 * w <= x < 0.45 * w and messages and (p := _player_line(t)):
            messages[-1]["cards"].append({**p, "action": _icon_action(img, y, h, 0.17, 0.25)})
    rows = []
    for msg in messages:
        if msg["when"] is None:
            continue
        if "team" in msg:  # a processed claim: its cards, + or -
            team, players = msg["team"], [{"name": c["name"], "positions": c["positions"],
                                           "action": c["action"] or "add"} for c in msg["cards"]]
        else:
            m = _CHAT_MOVE.match(msg["text"])
            if not m:
                continue
            team = m.group(1)
            said = [(_split_name(m.group(3)), "add" if m.group(2) == "added" else "drop")]
            if m.group(4):
                said.append((_split_name(m.group(5)), "drop"))
            cards = msg["cards"] if len(msg["cards"]) == len(said) else []
            players = []
            for i, (name, action) in enumerate(said):
                card = next((c for c in msg["cards"] if _squash(c["name"]) == _squash(name)),
                            cards[i] if cards else None)
                players.append({"name": card["name"] if card else name,
                                "positions": card["positions"] if card else [], "action": action})
        if not players:
            continue
        actions = {p["action"] for p in players}
        rows.append({"type": "add/drop" if len(actions) == 2 else actions.pop(), "when": msg["when"],
                     "teams": [team], "players": players})  # the team as shown, spaces lost: matched squashed
    return rows[::-1]


def _read_lines(img: Image.Image) -> list[tuple[float, float, float, str]]:
    """(x, y, height, text) of each text line, top to bottom."""
    result, _ = _ocr()(np.asarray(img)[:, :, ::-1])  # RapidOCR wants BGR
    lines = []
    for box, text, _score in result or []:
        (x0, y0), _, (_, y1), _ = box
        lines.append((x0, y0, y1 - y0, text.strip()))
    return sorted(lines, key=lambda line: line[1])


def _slot(img: Image.Image, y: float, h: float) -> str | None:
    """The slot badge left of the photo, level with the name line at y."""
    w = img.width
    badge = img.crop((int(0.02 * w), int(y - 0.4 * h), int(0.14 * w), int(y + 3.4 * h)))
    colour = _badge_colour(badge)
    if colour not in ("TEAL", None):
        return colour
    gray = badge.convert("L")
    big = gray.resize((gray.width * 2, gray.height * 2)).convert("RGB")
    result, _ = _ocr()(np.asarray(big), use_det=False, use_cls=False, use_rec=True)
    text = "".join(r[1] for r in result or []).replace(" ", "").upper()
    if colour == "TEAL":
        return "RW" if "R" in text else "LW" if "L" in text else None
    if "IR+" in text or "IR*" in text:
        return "IR+"
    return "IR" if "IR" in text else "BN" if "BN" in text or "B" in text else None


def _badge_colour(badge: Image.Image) -> str | None:
    """The slot a coloured badge stands for ("TEAL" for LW/RW), or None if grey."""
    px = np.asarray(badge).reshape(-1, 3)[::3] / 255
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px])
    vivid = hsv[(hsv[:, 1] > 0.35) & (hsv[:, 2] > 0.45)]
    if len(vivid) < max(_MIN_VIVID, _MIN_VIVID_SHARE * len(px)):
        return None
    hue = float(np.median(vivid[:, 0])) * 360
    return next((slot for (lo, hi), slot in _HUE_SLOTS if lo <= hue < hi), None)
