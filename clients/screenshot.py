"""Read Yahoo app screenshots with free, offline OCR (RapidOCR).

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
_MIN_VIVID = 30  # coloured pixels for a badge to count as coloured, not grey
_POINTS = re.compile(r"^-?\d+\.\d\d$")
_SCORE_PAIR = re.compile(r"^(-?\d+\.\d\d)\s*/\s*(-?\d+\.\d\d)$")  # the scrolled-down header
_CENTRE_SLOT = re.compile(r"^(LW|RW|BN|IR\+?|C|D|G)$")


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
    """{"kind": "team", "rows": [...]} or {"kind": "matchup", ...} (see _matchup)."""
    img = _open(image)
    lines = _read_lines(img)
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
    if len(vivid) < _MIN_VIVID:
        return None
    hue = float(np.median(vivid[:, 0])) * 360
    return next((slot for (lo, hi), slot in _HUE_SLOTS if lo <= hue < hi), None)
