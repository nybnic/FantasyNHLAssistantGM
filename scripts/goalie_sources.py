"""What the starting-goalie sources serve, for writing and checking their readers
(clients/goalie_client.py, clients/gdt_goalies.py) from the Check workflow, where a
development sandbox has no network.

    python -m scripts.goalie_sources            # GamedayTweets' goalies page, raw
"""
from __future__ import annotations

import re
import sys

import requests

from clients.cache import USER_AGENT

PAGES = ["https://www.gamedaytweets.com/goalies", "https://gamedaytweets.com/goalies"]
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def show(url: str, agent: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": agent}, timeout=30)
    except requests.RequestException as exc:
        print(f"{url}: {type(exc).__name__}: {exc}")
        return ""
    text = resp.text
    print(f"\n===== {url} ({agent[:20]}): {resp.status_code}, {len(text)} chars, final {resp.url}, "
          f"type {resp.headers.get('content-type')}")
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    print("title:", title.group(1).strip() if title else None)
    print("scripts:", re.findall(r'<script[^>]+src="([^"]+)"', text)[:30])
    print("json blobs:", re.findall(r'<script[^>]*(?:id|type)="([^"]+)"', text)[:20])
    print("api-ish urls:", sorted(set(re.findall(r'["\'](/?(?:api|_next/data|data)[^"\'\s]{0,120})["\']', text)))[:30])
    for word in ("Confirmed", "Likely", "Expected", "Unconfirmed", "Projected"):
        hits = [m.start() for m in re.finditer(word, text)]
        print(f"'{word}': {len(hits)} hits")
        for h in hits[:2]:
            print("   ...", re.sub(r"\s+", " ", text[max(0, h - 600):h + 300]), "...")
    return text


def main() -> None:
    for url in PAGES:
        for agent in (USER_AGENT, BROWSER):
            text = show(url, agent)
            if text and len(text) > 5000:
                print("\n----- body, first 6000 chars without tags' scripts")
                print(re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S))[:6000])
                print("\n----- raw, first 4000 chars")
                print(text[:4000])
                return
    sys.exit(1)


if __name__ == "__main__":
    main()
