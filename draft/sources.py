"""Data fetchers for draft prep, all cached to disk under data/cache/.

Sources (shapes verified against live responses, Sep 2026):
- NHL stats REST (api.nhle.com/stats/rest/en): one aggregated row per player
  per season (traded players have teamAbbrevs like "EDM,PIT,COL").
  skater/summary  -> goals, assists, plusMinus, penaltyMinutes, ppGoals,
                     ppPoints, shGoals, shPoints, gameWinningGoals, shots
  skater/realtime -> hits, blockedShots
  skater/faceoffwins -> totalFaceoffWins
  goalie/summary  -> gamesStarted, wins, goalsAgainst, saves, shotsAgainst, shutouts
  team/summary    -> shotsAgainstPerGame, wins, gamesPlayed
- NHL web API (api-web.nhle.com/v1/roster/{TEAM}/current): current rosters
  with birthDate and positionCode.
- DailyFaceoff team line-combinations pages: __NEXT_DATA__ JSON with each
  player's line group (f1..f4, d1..d3, g, pp1/pp2, pk1/pk2, ir), slot
  (lw/c/rw/ld/rd/g1/g2/ir1..) and injuryStatus ("out", "dtd", None).
- FantasyPros NHL ADP table (Yahoo + ESPN columns).
- Kodo Hockey projections (hockey.kodoanalytics.com/projections): server-
  rendered tables keyed by NHL player id (<tr data-pid>), cells labelled by
  data-label. Skaters: GP G A P +/- PPP SHP SOG FOW HIT BLK (300 per view;
  F/C/L/R/D views together cover ~1000 skaters). Goalies (view=G): GS W L
  SHO SV% GAA SV.
- NHL.com fantasy staff projections (84-game): point totals for F/D plus a
  "Key injuries / absences" list, and goalie win totals - some backup lines
  read "A or B, G, TEAM: n".
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

CACHE_DIR = Path("data/cache")
STATS_URL = "https://api.nhle.com/stats/rest/en"
WEB_URL = "https://api-web.nhle.com/v1"
DFO_URL = "https://www.dailyfaceoff.com"
FANTASYPROS_ADP_URL = "https://www.fantasypros.com/nhl/adp/overall.php"
KODO_URL = "https://hockey.kodoanalytics.com/projections"
NHL_POINTS_URL = "https://www.nhl.com/news/nhl-points-projections-fantasy-hockey-2026-27"
NHL_GOALIE_WINS_URL = "https://www.nhl.com/news/topic/fantasy/2026-2027-fantasy-hockey-goalie-win-projections"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

DAY = 24 * 3600


def _cached(name: str, max_age: float | None, fetch: Callable[[], Any]) -> Any:
    """Return cached JSON for `name`, refetching when older than `max_age`
    seconds (None = never expires, used for completed seasons)."""
    path = CACHE_DIR / f"{name}.json"
    if path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def season_report(report: str, season_id: int, max_age: float | None = None) -> list[dict]:
    """All regular-season rows of an NHL stats report, e.g.
    season_report("skater/summary", 20252026)."""

    def fetch() -> list[dict]:
        params = {"limit": -1, "cayenneExp": f"seasonId={season_id} and gameTypeId=2"}
        return _get(f"{STATS_URL}/{report}", params).json()["data"]

    return _cached(f"{report.replace('/', '_')}_{season_id}", max_age, fetch)


def team_name_to_abbrev() -> dict[str, str]:
    """'Washington Capitals' -> 'WSH' (team/summary rows carry no triCode)."""

    def fetch() -> dict[str, str]:
        rows = _get(f"{STATS_URL}/team").json()["data"]
        return {t["fullName"]: t["triCode"] for t in rows}

    return _cached("team_names", None, fetch)


def current_team_abbrevs(refresh: bool = False) -> list[str]:
    def fetch() -> list[str]:
        standings = _get(f"{WEB_URL}/standings/now").json()["standings"]
        return sorted(t["teamAbbrev"]["default"] for t in standings)

    return _cached("team_abbrevs", 0 if refresh else 30 * DAY, fetch)


def current_rosters(refresh: bool = False) -> list[dict]:
    """Flattened current rosters: id, name, team, position, birthDate."""

    def fetch() -> list[dict]:
        players = []
        for team in current_team_abbrevs(refresh):
            roster = _get(f"{WEB_URL}/roster/{team}/current").json()
            for group in ("forwards", "defensemen", "goalies"):
                for p in roster.get(group, []):
                    players.append({
                        "id": p["id"],
                        "name": f"{p['firstName']['default']} {p['lastName']['default']}",
                        "team": team,
                        "position": p["positionCode"],
                        "birthDate": p.get("birthDate"),
                    })
        return players

    return _cached("rosters_current", 0 if refresh else DAY, fetch)


def player_birth_date(player_id: int) -> str | None:
    def fetch() -> dict:
        landing = _get(f"{WEB_URL}/player/{player_id}/landing").json()
        return {"birthDate": landing.get("birthDate")}

    return _cached(f"player_{player_id}", None, fetch)["birthDate"]


_NEXT_DATA_RE =re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def dailyfaceoff_combinations(refresh: bool = False) -> list[dict]:
    """Every player listed on DailyFaceoff's line-combination pages:
    team, name, group (f1/d2/g/pp1/ir...), slot (c/lw/g1/ir2...), injury."""

    def page_props(path: str) -> dict:
        text = _get(f"{DFO_URL}{path}").text
        return json.loads(_NEXT_DATA_RE.search(text).group(1))["props"]["pageProps"]

    def fetch() -> list[dict]:
        teams = page_props("/teams/colorado-avalanche/line-combinations")["sortedTeams"]
        rows = []
        for team in teams:
            combos = page_props(f"/teams/{team['slug']}/line-combinations")["combinations"]
            for p in combos["players"]:
                rows.append({
                    "team": team["shortName"],
                    "name": p["name"],
                    "group": p["groupIdentifier"],
                    "slot": p["positionIdentifier"],
                    "injury": p.get("injuryStatus"),
                    "updated": combos.get("updatedAt"),
                })
        return rows

    return _cached("dailyfaceoff_combinations", 0 if refresh else DAY, fetch)


_FP_ROW_RE = re.compile(
    r"<tr><td>(\d+)</td>\s*<td class=\"player-label\">.*?class=\"player-name\">([^<]+)</a>"
    r"\s*<small>([^<]*)</small></td>\s*<td class=\"center\">([^<]*)</td>"
    r"<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>",
    re.S,
)


def fantasypros_adp(refresh: bool = False) -> list[dict]:
    """FantasyPros NHL ADP rows: rank, name, team, pos (e.g. "C1"), yahoo, espn, avg."""

    def num(s: str) -> float | None:
        s = html.unescape(s).replace("\xa0", "").strip()
        return float(s) if s and s != "-" else None

    def fetch() -> list[dict]:
        text = _get(FANTASYPROS_ADP_URL).text
        return [
            {
                "rank": int(m[0]),
                "name": html.unescape(m[1]).strip(),
                "team": m[2].strip(),
                "pos": re.sub(r"\d+$", "", m[3].strip()),
                "yahoo": num(m[4]),
                "espn": num(m[5]),
                "avg": num(m[6]),
            }
            for m in _FP_ROW_RE.findall(text)
        ]

    return _cached("fantasypros_adp", 0 if refresh else DAY, fetch)


def _strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).replace("\xa0", " ").strip()


def _num(text: str) -> float | str:
    try:
        return float(text.replace("+", ""))
    except ValueError:
        return text


_KODO_ROW_RE = re.compile(r'<tr data-pid="(\d+)"[^>]*>(.*?)</tr>', re.S)
_KODO_CELL_RE = re.compile(r'<td[^>]*data-label="([^"]+)"[^>]*>(.*?)</td>', re.S)


def _kodo_table(view: str) -> list[dict]:
    rows = []
    for pid, body in _KODO_ROW_RE.findall(_get(KODO_URL, {"view": view}).text):
        cells = {label: _strip_tags(value) for label, value in _KODO_CELL_RE.findall(body)}
        name = re.search(r'class="name plink"[^>]*>([^<]+)<', body)
        row = {k: _num(v) for k, v in cells.items() if k not in ("Player", "Goalie")}
        row.update(id=int(pid), name=html.unescape(name.group(1)) if name else "")
        rows.append(row)
    return rows


def kodo_projections(refresh: bool = False) -> dict[str, list[dict]]:
    """{'skaters': [...], 'goalies': [...]} from Kodo Hockey, one row per NHL id."""

    def fetch() -> dict[str, list[dict]]:
        skaters: dict[int, dict] = {}
        for view in ("F", "C", "L", "R", "D"):
            for row in _kodo_table(view):
                skaters.setdefault(row["id"], row)
        return {"skaters": list(skaters.values()), "goalies": _kodo_table("G")}

    return _cached("kodo_projections", 0 if refresh else DAY, fetch)


def _article_lines(url: str) -> list[str]:
    # NHL.com sends no charset, so requests would guess Latin-1 and mangle "Stützle".
    page = _get(url).content.decode("utf-8", errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return [line.strip() for line in text.split("\n") if line.strip()]


_NHL_SKATER_RE = re.compile(r"^(.+?), ([FD]), ([A-Z]{3})(?: \(([^)]*)\))?(?:: (\d+))?$")
_NHL_GOALIE_RE = re.compile(r"^(.+?)(?:, ([A-Z]{3}))?(?: \(([^)]*)\))?: (\d+)$")


def nhl_com_projections(refresh: bool = False) -> dict[str, list[dict]]:
    """NHL.com fantasy staff 84-game projections:
    skaters [{name, pos, team, points, note}], absences [{name, pos, team}],
    goalies [{names: [...], team, wins, note}] ("A or B" lines list both)."""

    def fetch() -> dict[str, list[dict]]:
        skaters, absences = [], []
        for line in _article_lines(NHL_POINTS_URL):
            m = _NHL_SKATER_RE.match(line)
            if not m:
                continue
            name, pos, team, note, points = m.groups()
            if points is None:
                absences.append({"name": name, "pos": pos, "team": team})
            else:
                skaters.append({"name": name, "pos": pos, "team": team, "points": int(points), "note": note})

        goalies, in_section = [], False
        for line in _article_lines(NHL_GOALIE_WINS_URL):
            if line.startswith("GOALIE WIN PROJECTIONS"):
                in_section = True
            elif line.startswith("TEAM WIN PROJECTIONS"):
                break
            elif in_section and ", G" in line and (m := _NHL_GOALIE_RE.match(line)):
                names_part, team, note, wins = m.groups()
                names = [re.sub(r",\s*G$", "", n.strip()) for n in names_part.split(" or ")]
                goalies.append({"names": names, "team": team, "wins": int(wins), "note": note})
        return {"skaters": skaters, "absences": absences, "goalies": goalies}

    return _cached("nhl_com_projections", 0 if refresh else DAY, fetch)
