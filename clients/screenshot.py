"""Read Yahoo app screenshots with free, offline OCR (RapidOCR).

League > Transactions: each add, drop and trade, with its date.

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
import functools
import io
import re

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


class ScreenshotError(Exception):
    """The image couldn't be read; the message is safe to show."""


@functools.cache
def _ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR  # slow import; only when a screenshot arrives
    except ImportError as e:  # e.g. a system library opencv needs is missing on the runner
        raise ScreenshotError(f"the OCR library won't load ({e})") from None
    return RapidOCR()


def read(image: bytes) -> dict:
    """{"kind": "team", "rows": [...]}, {"kind": "matchup", ...} (see _matchup)
    or {"kind": "transactions", "rows": [...]} (see _transactions)."""
    img = _open(image)
    lines = _read_lines(img)
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
    "labels": (left header texts, right header texts),
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
    labels = ([t for x, _, t in header if x < 0.5 * w], [t for x, _, t in header if x >= 0.5 * w])
    return {"score": score, "projected": projected, "labels": labels, "rows": rows}


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


def _icon_action(img: Image.Image, y: float, h: float) -> str | None:
    """"add" (a green +) or "drop" (a red -) from the icon mid-row."""
    w = img.width
    crop = img.crop((int(0.44 * w), int(y - 0.3 * h), int(0.56 * w), int(y + 1.3 * h)))
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
            when = next((_tx_when(t2) for x2, y2, _, t2 in lines if x2 > 0.4 * w and abs(y2 - y) < h), None)
            if when:
                headers.append((y, h, m.group(1).lower(), when))
    blocks = []
    for k, (y, h, kind, when) in enumerate(headers):
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
        if players and len(teams) == (2 if kind == "trade" else 1):
            blocks.append({"type": kind, "when": when, "teams": teams, "players": players})
    return blocks


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
