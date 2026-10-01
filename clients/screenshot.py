"""Read a Yahoo app team screenshot with free, offline OCR (RapidOCR): each
row's slot, the name as shown ("M. SCHEIFELE"), team and positions.

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


class ScreenshotError(Exception):
    """The image couldn't be read; the message is safe to show."""


@functools.cache
def _ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR  # slow import; only when a screenshot arrives
    except ImportError as e:  # e.g. a system library opencv needs is missing on the runner
        raise ScreenshotError(f"the OCR library won't load ({e})") from None
    return RapidOCR()


def read_roster(image: bytes) -> list[dict]:
    try:
        img = Image.open(io.BytesIO(image)).convert("RGB")
    except OSError:
        raise ScreenshotError("that isn't an image I can open") from None
    lines = _read_lines(img)
    rows = []
    for i, (x, y, h, text) in enumerate(lines):
        m = _NAME.match(text)
        if not m or x < 0.2 * img.width:
            continue
        positions, team = [], ""
        below = [t for bx, by, _, t in lines[i + 1:i + 4] if abs(bx - x) < 2 * h and 0 < by - y < 2 * h]
        if below and (pt := _POS_TEAM.match(below[0].replace(" ", ""))):
            positions, team = pt.group(1).split(","), pt.group(2)
        rows.append({"slot": _slot(img, y, h), "name": f"{m.group(1)}. {m.group(2)}", "team": team,
                     "positions": positions})
    return rows


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
