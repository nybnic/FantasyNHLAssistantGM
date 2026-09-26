"""Season projections built from the last three NHL regular seasons.

Deliberately simple and transparent - the point is to rank players under
*this league's* scoring, not to out-forecast professional projections:

Skaters: per-game rates weighted 3-2-1 (newest season heaviest), shrunk
toward a positional baseline for small samples, age-adjusted on offensive
stats, times projected games played (availability history + current
DailyFaceoff role/injury status).

Goalies: per-start line = current team's shots against (regressed to league
average) x the goalie's regressed save %, wins from team strength + goalie
quality, times projected starts (DailyFaceoff depth order + history, then
the hand-maintained overrides in data/draft_overrides.csv).
"""
from __future__ import annotations

import datetime as dt
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from draft import scoring

SEASONS = (20232024, 20242025, 20252026)
SEASON_WEIGHTS = {20232024: 1.0, 20242025: 2.0, 20252026: 3.0}
PAST_SEASON_GAMES = 82
SEASON_GAMES = 84  # 2026-27 is the first 84-game season
SEASON_START = dt.date(2026, 10, 1)

SKATER_STATS = ("g", "a", "pm", "pim", "ppg", "ppa", "shg", "sha", "gwg", "sog", "fow", "hit", "blk")
OFFENSIVE_STATS = ("g", "a", "ppg", "ppa", "gwg", "sog", "shg", "sha")

# Games of positional-baseline stats blended into every skater's rates.
SKATER_PRIOR_GAMES = 15.0
# Shots of league-average save % blended into every goalie's save %.
GOALIE_PRIOR_SHOTS = 1500.0

# DailyFaceoff team codes -> NHL triCodes (FantasyPros uses the same short forms).
TEAM_ALIASES = {
    "LA": "LAK", "MON": "MTL", "NAS": "NSH", "NJ": "NJD", "SJ": "SJS",
    "TB": "TBL", "WAS": "WSH", "VEG": "VGK", "CLB": "CBJ", "WIN": "WPG",
}

NHL_TO_FANTASY_POS = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}


def norm_team(team: str | None) -> str | None:
    return TEAM_ALIASES.get(team, team) if team else team


# Sources disagree on formal vs short first names ("Nicholas"/"Nick").
FIRST_NAME_ALIASES = {
    "nicholas": "nick", "nicolas": "nick", "zachary": "zack", "zach": "zack",
    "alexander": "alex", "alexandre": "alex", "matthew": "matt", "mitchell": "mitch",
    "joshua": "josh", "jacob": "jake", "christopher": "chris", "daniel": "dan",
    "danny": "dan", "samuel": "sam", "william": "will", "benjamin": "ben",
    "joseph": "joe", "michael": "mike", "cameron": "cam", "maxwell": "max",
    "maximilian": "max", "thomas": "tom", "anthony": "tony", "timothy": "tim",
    "gabriel": "gabe",
}


def norm_name(name: str) -> str:
    """Matching key that survives accents, punctuation, middle names and
    nicknames: 'Tim Stützle' -> 'tim stutzle', 'Elias Nils Pettersson' ->
    'elias pettersson', 'Nicholas Robertson' -> 'nick robertson'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.lower().replace(".", "").replace("'", "").replace("-", " ")
    for suffix in (" jr", " sr", " ii", " iii"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    parts = text.split()
    if len(parts) > 2:
        parts = [parts[0], parts[-1]]
    if parts:
        parts[0] = FIRST_NAME_ALIASES.get(parts[0], parts[0])
    return " ".join(parts)


def fantasy_pos_group(pos: str) -> str:
    """F / D / G - coarse position used to tell same-named players apart."""
    return pos if pos in ("D", "G") else "F"


def age_on(birth_date: str | None, on: dt.date = SEASON_START) -> int | None:
    if not birth_date:
        return None
    b = dt.date.fromisoformat(birth_date)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def age_multiplier(age: int | None) -> float:
    """Multiplier on offensive rates: young players still improving, 30+ declining."""
    if age is None:
        return 1.0
    table = {20: 1.12, 21: 1.09, 22: 1.06, 23: 1.04, 24: 1.02, 29: 0.99, 30: 0.98,
             31: 0.97, 32: 0.95, 33: 0.93, 34: 0.91}
    if age <= 20:
        return table[20]
    if age >= 35:
        return 0.88
    return table.get(age, 1.0)


@dataclass
class Depth:
    """A player's current DailyFaceoff listing."""
    group: str | None = None     # f1..f4, d1..d3, g, ir
    slot: str | None = None      # lw/c/rw/ld/rd/g1/g2/ir1...
    pp: str | None = None        # pp1 / pp2
    injury: str | None = None    # out / dtd


@dataclass
class Projection:
    player_id: int
    name: str
    team: str
    pos: str                     # C / LW / RW / D / G
    age: int | None
    games: float                 # projected GP (skaters) or GS (goalies)
    stats: dict[str, float]
    fpts: float
    depth: Depth = field(default_factory=Depth)
    flags: list[str] = field(default_factory=list)
    # Fantasy positions he can likely fill in Yahoo (NHL position + how
    # ADP sites list him + where DailyFaceoff has him lined up).
    elig: set[str] = field(default_factory=set)
    yahoo_rank: float | None = None   # Yahoo's preseason rank (drives autodraft queues)
    status: str | None = None         # Yahoo availability tag: O, NA, IR, IR-LT, DTD

    @property
    def fpts_per_game(self) -> float:
        return self.fpts / self.games if self.games else 0.0


def _index(rows: Iterable[dict]) -> dict[int, dict]:
    return {r["playerId"]: r for r in rows}


def skater_seasons(reports: dict[int, dict[str, list[dict]]]) -> dict[int, dict[int, dict]]:
    """{player_id: {season: stat line}} merged from summary/realtime/faceoffs."""
    out: dict[int, dict[int, dict]] = defaultdict(dict)
    for season, reps in reports.items():
        realtime = _index(reps["realtime"])
        faceoffs = _index(reps["faceoffwins"])
        for s in reps["summary"]:
            pid = s["playerId"]
            rt = realtime.get(pid, {})
            fo = faceoffs.get(pid, {})
            out[pid][season] = {
                "name": s["skaterFullName"],
                "pos": s["positionCode"],
                "gp": s["gamesPlayed"] or 0,
                "g": s["goals"] or 0,
                "a": s["assists"] or 0,
                "pm": s["plusMinus"] or 0,
                "pim": s["penaltyMinutes"] or 0,
                "ppg": s["ppGoals"] or 0,
                "ppa": (s["ppPoints"] or 0) - (s["ppGoals"] or 0),
                "shg": s["shGoals"] or 0,
                "sha": (s["shPoints"] or 0) - (s["shGoals"] or 0),
                "gwg": s["gameWinningGoals"] or 0,
                "sog": s["shots"] or 0,
                "fow": fo.get("totalFaceoffWins") or 0,
                "hit": rt.get("hits") or 0,
                "blk": rt.get("blockedShots") or 0,
                "toi": s.get("timeOnIcePerGame") or 0,
            }
    return out


def positional_baselines(seasons: dict[int, dict[int, dict]]) -> dict[str, dict[str, float]]:
    """Per-game rates of a 'fringe regular' at each NHL position: pooled
    rates of players with 20-60 GP last season (call-ups/depth players), the
    realistic prior for someone with little NHL history."""
    last = SEASONS[-1]
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for by_season in seasons.values():
        line = by_season.get(last)
        if not line or not 20 <= line["gp"] <= 60:
            continue
        pos = "F" if line["pos"] in "CLR" else line["pos"]
        for k in (*SKATER_STATS, "gp"):
            totals[pos][k] += line[k]
    base: dict[str, dict[str, float]] = {}
    for pos, t in totals.items():
        base[pos] = {k: t[k] / t["gp"] for k in SKATER_STATS}
    base["F"]["pm"] = base["D"]["pm"] = 0.0
    return base


def rookie_baselines(seasons: dict[int, dict[int, dict]], roster: dict[int, dict]) -> dict[str, dict[str, float]]:
    """Per-game rates of last season's young regulars (age <= 21 on
    2025-10-01, 40+ GP): the prior for rookies with no NHL games at all."""
    last = SEASONS[-1]
    cutoff = dt.date(2025, 10, 1)
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for pid, by_season in seasons.items():
        line = by_season.get(last)
        age = age_on(roster.get(pid, {}).get("birthDate"), cutoff)
        if not line or line["gp"] < 40 or age is None or age > 21:
            continue
        pos = "F" if line["pos"] in "CLR" else line["pos"]
        for k in (*SKATER_STATS, "gp"):
            totals[pos][k] += line[k]
    base = {pos: {k: t[k] / t["gp"] for k in SKATER_STATS} for pos, t in totals.items()}
    for pos in base:
        base[pos]["pm"] = 0.0
    return base


def _weighted_rates(by_season: dict[int, dict], prior: dict[str, float], prior_games: float) -> dict[str, float]:
    wgp = sum(SEASON_WEIGHTS[s] * line["gp"] for s, line in by_season.items())
    rates = {}
    for k in SKATER_STATS:
        wsum = sum(SEASON_WEIGHTS[s] * line[k] for s, line in by_season.items())
        rates[k] = (wsum + prior_games * prior[k]) / (wgp + prior_games)
    return rates


def _availability(by_season: dict[int, dict]) -> float:
    """Share of team games played, weighted like the rates, with one
    pseudo-season of 88% as a prior. Only seasons with NHL games count, so
    a 2023-24 spent in juniors isn't treated as a missed season."""
    played = {s: l for s, l in by_season.items() if l["gp"] > 0}
    num = sum(SEASON_WEIGHTS[s] * min(l["gp"] / PAST_SEASON_GAMES, 1.0) for s, l in played.items())
    den = sum(SEASON_WEIGHTS[s] for s in played)
    return (num + 0.88) / (den + 1.0)


def _apply_depth_to_availability(avail: float, depth: Depth, flags: list[str]) -> float:
    if depth.group in ("f1", "f2", "d1", "d2"):
        avail = max(avail, 0.82)
    elif depth.group in ("f3", "f4", "d3"):
        avail = max(avail, 0.72)
    elif depth.group == "ir":
        if depth.injury == "out":
            avail *= 0.75
            flags.append("INJ-out")
        else:
            avail *= 0.97
            flags.append("INJ-dtd")
    else:
        # Not in DailyFaceoff's projected lineup: press-box / AHL risk.
        avail *= 0.55
        flags.append("no-lineup-spot")
    return min(avail, 1.0)


def project_skaters(
    seasons: dict[int, dict[int, dict]],
    roster: dict[int, dict],
    depth: dict[int, Depth],
    rookie_ids: set[int] = frozenset(),
) -> list[Projection]:
    """Project every rostered skater with NHL history, plus `rookie_ids`
    (no NHL games yet, but in a lineup or drafted in public leagues)."""
    base = positional_baselines(seasons)
    rookie_base = rookie_baselines(seasons, roster)
    last = SEASONS[-1]
    out = []
    for pid, player in roster.items():
        if player["position"] == "G":
            continue
        pos_code = player["position"]
        group = "D" if pos_code == "D" else "F"
        if pid in seasons:
            by_season = seasons[pid]
            rates = _weighted_rates(by_season, base[group], SKATER_PRIOR_GAMES)
        elif pid in rookie_ids:
            by_season = {}
            rates = dict(rookie_base[group])
        else:
            continue

        age = age_on(player.get("birthDate"))
        # The rookie prior is already an age<=21 group; don't boost it twice.
        mult = age_multiplier(age) if by_season else 1.0
        for k in OFFENSIVE_STATS:
            rates[k] *= mult
        rates["pm"] *= 0.5  # plus/minus barely repeats year to year
        # GWG is mostly luck: blend half toward the league-typical 15% of goals.
        rates["gwg"] = 0.5 * rates["gwg"] + 0.5 * 0.15 * rates["g"]

        flags: list[str] = []
        d = depth.get(pid, Depth())
        avail = _apply_depth_to_availability(_availability(by_season), d, flags)
        games = SEASON_GAMES * avail
        total_gp = sum(l["gp"] for l in by_season.values())
        if not by_season:
            flags.append("ROOKIE-no-NHL-games")
        elif total_gp < 40:
            flags.append(f"small-sample({total_gp}GP)")
        elif last not in by_season:
            flags.append("no-2025-26-games")

        stats = {k: rates[k] * games for k in SKATER_STATS}
        out.append(Projection(
            player_id=pid,
            name=player["name"],
            team=player["team"],
            pos=NHL_TO_FANTASY_POS[pos_code],
            age=age,
            games=games,
            stats=stats,
            fpts=scoring.skater_points(stats),
            depth=d,
            flags=flags,
        ))
    return out


# --- goalies -----------------------------------------------------------------

def goalie_seasons(reports: dict[int, list[dict]]) -> dict[int, dict[int, dict]]:
    out: dict[int, dict[int, dict]] = defaultdict(dict)
    for season, rows in reports.items():
        for r in rows:
            out[r["playerId"]][season] = {
                "gp": r["gamesPlayed"] or 0,
                "gs": r["gamesStarted"] or 0,
                "w": r["wins"] or 0,
                "ga": r["goalsAgainst"] or 0,
                "sv": r["saves"] or 0,
                "sa": r["shotsAgainst"] or 0,
                "so": r["shutouts"] or 0,
            }
    return out


@dataclass
class TeamContext:
    shots_against_pg: float
    win_pct: float


def team_contexts(team_rows: list[dict], name_to_abbrev: dict[str, str]) -> tuple[dict[str, TeamContext], TeamContext]:
    """Last season's team shots against / win %, each regressed halfway to
    league average (team defence and results only partly carry over)."""
    lg_sa = sum(t["shotsAgainstPerGame"] for t in team_rows) / len(team_rows)
    ctx = {}
    for t in team_rows:
        abbrev = name_to_abbrev.get(t["teamFullName"])
        if not abbrev:
            continue
        ctx[abbrev] = TeamContext(
            shots_against_pg=0.5 * t["shotsAgainstPerGame"] + 0.5 * lg_sa,
            win_pct=0.5 * (t["wins"] / t["gamesPlayed"]) + 0.5 * 0.5,
        )
    return ctx, TeamContext(shots_against_pg=lg_sa, win_pct=0.5)


def league_save_pct(seasons: dict[int, dict[int, dict]]) -> float:
    last = SEASONS[-1]
    sv = sum(s[last]["sv"] for s in seasons.values() if last in s)
    sa = sum(s[last]["sa"] for s in seasons.values() if last in s)
    return sv / sa


def default_goalie_starts(by_season: dict[int, dict], depth: Depth) -> float:
    """Depth-chart based default, refined by the goalie's own recent workload.
    data/draft_overrides.csv takes precedence over this."""
    played = {s: l for s, l in by_season.items() if l["gp"] > 0}
    hist = None
    if played:
        w = sum(SEASON_WEIGHTS[s] for s in played)
        hist = sum(SEASON_WEIGHTS[s] * l["gs"] for s, l in played.items()) / w * SEASON_GAMES / PAST_SEASON_GAMES
    if depth.slot == "g1":
        return min(max(hist if hist is not None else 48.0, 44.0), 62.0)
    if depth.slot == "g2":
        return min(max(hist if hist is not None else 26.0, 22.0), 36.0)
    if depth.group == "ir":
        factor = 0.97 if depth.injury == "dtd" else 0.6
        return factor * (hist or 20.0)
    return 5.0


def project_goalies(
    seasons: dict[int, dict[int, dict]],
    roster: dict[int, dict],
    depth: dict[int, Depth],
    teams: dict[str, TeamContext],
    league_ctx: TeamContext,
) -> list[Projection]:
    lg_sv = league_save_pct(seasons)
    out = []
    for pid, player in roster.items():
        if player["position"] != "G":
            continue
        by_season = seasons.get(pid, {})
        d = depth.get(pid, Depth())
        flags: list[str] = []

        w = {s: SEASON_WEIGHTS[s] for s in by_season}
        saves = sum(w[s] * l["sv"] for s, l in by_season.items())
        shots = sum(w[s] * l["sa"] for s, l in by_season.items())
        # Unknown goalies regress to slightly below average.
        prior_sv = lg_sv - 0.003
        sv_pct = (saves + GOALIE_PRIOR_SHOTS * prior_sv) / (shots + GOALIE_PRIOR_SHOTS)
        starts_hist = sum(w[s] * l["gs"] for s, l in by_season.items())
        so_rate = (sum(w[s] * l["so"] for s, l in by_season.items()) + 40 * 0.05) / (starts_hist + 40)
        if sum(l["gs"] for l in by_season.values()) < 30:
            flags.append("small-sample")

        team = teams.get(player["team"], league_ctx)
        sa_per_start = team.shots_against_pg
        # ~+5% win probability per +.010 save % over league average.
        win_rate = min(max(team.win_pct + (sv_pct - lg_sv) * 5.0, 0.3), 0.7)

        if d.group == "ir":
            flags.append("INJ-out" if d.injury == "out" else "INJ-dtd")
        elif d.slot not in ("g1", "g2"):
            flags.append("not-top-2")

        starts = default_goalie_starts(by_season, d)
        per_start = {
            "gs": 1.0,
            "w": win_rate,
            "sv": sa_per_start * sv_pct,
            "ga": sa_per_start * (1 - sv_pct),
            "so": so_rate,
        }
        stats = {k: v * starts for k, v in per_start.items()}
        stats["sv_pct"] = sv_pct
        out.append(Projection(
            player_id=pid,
            name=player["name"],
            team=player["team"],
            pos="G",
            age=age_on(player.get("birthDate")),
            games=starts,
            stats=stats,
            fpts=scoring.goalie_points(stats),
            depth=d,
            flags=flags,
        ))
    return out


def rescale_goalie(p: Projection, starts: float) -> None:
    """Apply a projected-starts override, keeping per-start rates."""
    if p.games:
        factor = starts / p.games
        for k in ("gs", "w", "sv", "ga", "so"):
            p.stats[k] *= factor
    p.games = starts
    p.fpts = scoring.goalie_points(p.stats)


def rescale_skater(p: Projection, games: float) -> None:
    if p.games:
        factor = games / p.games
        for k in SKATER_STATS:
            p.stats[k] *= factor
    p.games = games
    p.fpts = scoring.skater_points(p.stats)
