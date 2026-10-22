"""The Telegram image: the dashboard's card (site/index.html?card), rendered by
headless Chromium (Playwright), so Telegram and the dashboard share one design
(Nico, 2026-10-09). The page gets its data injected (a file:// page can't fetch
data.json). Returns None when the browser isn't there (a local run without
`playwright install chromium`, or an install that failed in CI): the caller
then falls back to the matplotlib chart (notify/charts.py).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PAGE = Path("site/index.html")
WIDTH = 390  # CSS pixels: a phone's width, so Telegram shows it at about 80% (the card has no text under 13px)
SCALE = 2  # device pixels per CSS pixel: sharp on a phone's screen
TIMEOUT_MS = 20_000
TIME_ZONE = "Europe/Helsinki"  # "updated" in Nico's time; CI runs in UTC


def card_png(data: dict, page: Path = PAGE, kind: str = "plan", key: str | None = None) -> bytes | None:
    """A card as a PNG, or None if it can't be rendered: the plan's (`kind` "plan"),
    a move's ("move", its `key`) or Monday's result ("result")."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright isn't installed: no card image")
        return None
    script = (f"window.__CARD__ = {json.dumps(kind)}; window.__CARD_KEY__ = {json.dumps(key)}; "
              f"window.__DATA__ = {json.dumps(data, ensure_ascii=False)};")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                tab = browser.new_page(viewport={"width": WIDTH, "height": 800}, device_scale_factor=SCALE,
                                       color_scheme="light", timezone_id=TIME_ZONE, locale="en-GB")
                tab.add_init_script(script)
                tab.goto(page.resolve().as_uri(), timeout=TIMEOUT_MS)
                tab.wait_for_selector("body[data-ready='1']", timeout=TIMEOUT_MS)
                return tab.locator("main").screenshot(timeout=TIMEOUT_MS)
            finally:
                browser.close()
    except Exception as e:  # a missing browser, a page error: the fallback chart goes out
        logger.warning("Card image failed: %s", str(e).splitlines()[0] if str(e) else type(e).__name__)
        return None
