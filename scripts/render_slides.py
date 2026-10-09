"""Print docs/model-overview.html (the model explained in 5 slides) to
docs/model-overview.pdf with headless Chromium, the browser the Telegram card
uses (notify/snapshot.py). `--png DIR` also saves each slide as an image.

    python -m scripts.render_slides
    python -m scripts.render_slides --png data/charts/slides
"""
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

SOURCE = Path("docs/model-overview.html")
TARGET = Path("docs/model-overview.pdf")


def main_() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--png", type=Path, help="also save each slide as a PNG in this folder")
    args = parser.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5)
        page.goto(SOURCE.resolve().as_uri())
        page.pdf(path=str(TARGET), width="13.333in", height="7.5in", print_background=True,
                 prefer_css_page_size=True)
        if args.png:
            args.png.mkdir(parents=True, exist_ok=True)
            for i, slide in enumerate(page.locator("section.slide").all(), start=1):
                slide.screenshot(path=str(args.png / f"slide-{i}.png"))
        browser.close()
    print(f"{TARGET} written")


if __name__ == "__main__":
    main_()
