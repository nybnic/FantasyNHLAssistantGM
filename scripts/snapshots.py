"""Every picture the bot shows, at a phone's width, for review after a change to
site/index.html: the plan's Telegram card, each planned move's card, and the
dashboard's five tabs (full length), from a plan run's data.

    python -m scripts.snapshots                       # site/data.json -> data/charts/snapshots/
    python -m scripts.snapshots --data data/charts/data.json --out /tmp/shots

Needs Playwright's Chromium (`python -m playwright install chromium`), or
CHROMIUM_EXECUTABLE pointing at one. Monday's result card needs the week's
result (`python main.py --dry-run --report N` draws it).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from notify import snapshot

TABS = ["today", "matchup", "moves", "schedule", "watch"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=Path("site/data.json"))
    ap.add_argument("--out", type=Path, default=Path("data/charts/snapshots"))
    args = ap.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    keys = [p["key"] for p in data.get("plan", []) if p.get("key") and any(r["key"] == p["key"] for r in data["moves"])]
    shots = [("card", {"__CARD__": "plan"}, "")] + [(f"move-{k.replace(':', '-')}", {"__CARD__": "move", "__CARD_KEY__": k}, "")
                                                    for k in keys]
    shots += [(f"tab-{t}", {}, f"#{t}") for t in TABS]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"))
        try:
            for name, globals_, hash_ in shots:
                tab = browser.new_page(viewport={"width": snapshot.WIDTH, "height": 844}, device_scale_factor=snapshot.SCALE,
                                       timezone_id=snapshot.TIME_ZONE, locale="en-GB")
                errors = []
                tab.on("pageerror", lambda e: errors.append(str(e)))
                tab.add_init_script("".join(f"window.{k} = {json.dumps(v)};" for k, v in globals_.items())
                                    + f"window.__DATA__ = {json.dumps(data, ensure_ascii=False)};")
                tab.goto(snapshot.PAGE.resolve().as_uri() + hash_)
                tab.wait_for_selector("body[data-ready='1']", timeout=snapshot.TIMEOUT_MS)
                path = args.out / f"{name}.png"
                (tab.locator("main") if globals_ else tab).screenshot(path=path, **({} if globals_ else {"full_page": True}))
                box = tab.locator("main").bounding_box()
                print(f"{path}  {box['width']:.0f} x {box['height']:.0f} px" + (f"  ERRORS: {errors}" if errors else ""))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
