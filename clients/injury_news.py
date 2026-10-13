"""How long an injured player is out, read from injury news text (DailyFaceoff's
latest news on its line charts: "Shesterkin (lower-body) will miss the rest of
the 2026-27 season", "expected to be out for four more weeks", "expected to
miss 3-to-3.5 months").

Only what the text states plainly: the rest of the season, or a span of days,
weeks or months. A range counts from its low end (the earliest he could be
back). Anything else (day-to-day, "week-to-week", no timeline) gives None and
the return curves decide. Which day he's really back after the stated span is
not fit yet (the archive keeps the news from 2026-10-13 to fit it).
"""
from __future__ import annotations

import datetime as dt
import re

SEASON = dt.date.max  # out for the rest of the season
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3,
         "several": 3}
DAYS_PER = {"day": 1, "week": 7, "month": 30}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted((re.escape(w) for w in WORDS), key=len, reverse=True)) + ")"
_SEASON_RE = re.compile(r"(rest|remainder) of the (\d{4}-\d{2} )?(regular )?season|season-ending|"
                        r"out for the (\d{4}-\d{2} )?season|miss the (\d{4}-\d{2} )?season", re.I)
_SPAN_RE = re.compile(r"\b" + _NUM + r"(?:\s*(?:-to-|-|to|or)\s*" + _NUM + r")?[\s-]*(?:more |additional |)"
                      r"(day|week|month)s?\b", re.I)
_NOT_A_SPAN = re.compile(r"(day|week)-to-(day|week)", re.I)


def _number(token: str) -> float:
    token = token.lower()
    return WORDS[token] if token in WORDS else float(token)


def out_until(text: str | None, posted: dt.date) -> dt.date | None:
    """The first day he could be back by the news posted on `posted`: SEASON
    for the rest of the season, None when the text gives no timeline."""
    if not text:
        return None
    if _SEASON_RE.search(text):
        return SEASON
    clean = _NOT_A_SPAN.sub("", text)
    spans = [_number(m.group(1)) * DAYS_PER[m.group(3).lower()] for m in _SPAN_RE.finditer(clean)]
    if not spans:
        return None
    return posted + dt.timedelta(days=round(min(spans)))
