"""Probe: DailyFaceoff's starting-goalies page as served, for one team's games
(the raw entry fields), to see what a stale label looks like."""
import datetime as dt
import json
import sys

from clients import goalie_client
from clients.cache import get

team = sys.argv[1] if len(sys.argv) > 1 else "Anaheim"
for day in ("2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"):
    resp = get(goalie_client.URL.format(date=day))
    print(f"\n=== {day}: status {resp.status_code}, final url {resp.url}")
    m = goalie_client._NEXT_DATA_RE.search(resp.text)
    if not m:
        print("no __NEXT_DATA__"); continue
    props = json.loads(m.group(1))["props"]["pageProps"]
    print("pageProps keys:", sorted(props))
    print("pageProps non-data:", {k: v for k, v in props.items() if k != "data" and not isinstance(v, (dict, list))})
    entries = props["data"]
    print(len(entries), "entries; keys:", sorted(entries[0]) if entries else None)
    for e in entries:
        if team.lower() in json.dumps(e).lower():
            print(json.dumps(e, indent=1, ensure_ascii=False)[:6000])
    print("parsed:", goalie_client.parse_entries(entries, goalie_client._team_codes()).get("ANA"))
