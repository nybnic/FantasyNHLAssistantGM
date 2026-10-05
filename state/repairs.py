"""One-time repairs of bot-owned state after an incident (docs/operations.md,
Past incidents). Each runs once (state["repairs_done"]) and fixes exactly
what the incident did.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

from clients.names import normalize_name
from config.league import TIMEZONE
from league import teams
from league.roster import RosterPlayer

SCHENN, MURASHOV = 8475170, 8483703
STOLARZ, NIKISHIN, LINDHOLM = 8476932, 8482100, 8477496


def apply(state: dict, players: list[RosterPlayer], league: dict) -> None:
    done = state.setdefault("repairs_done", [])
    for name, repair in REPAIRS:
        if name not in done:
            repair(state, players, league)
            done.append(name)


def _totals_view(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-01: a matchup screenshot of the week's Totals view listed Brayden
    # Schenn, dropped that day for Jack McBain, without a slot; he was saved to
    # my roster and counted as an add. Murashov's restoring here was a mistake:
    # Nico had dropped him (next repair).
    state["adds"] = [a for a in state["adds"]
                     if not (a["id"] == SCHENN and a["source"] == "roster" and a["date"] == "2026-10-01")]
    if any(p.id == SCHENN and p.slot is None for p in players) and all(p.id != MURASHOV for p in players):
        players[:] = [p for p in players if p.id != SCHENN] + [
            RosterPlayer(MURASHOV, "Sergei Murashov", "PIT", ["G"], "BN")]


def _murashov_dropped(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-01: Nico dropped Sergei Murashov (told in chat); the repair above
    # had put him back on the roster.
    players[:] = [p for p in players if p.id != MURASHOV]
    state["seen_mine"].pop(str(MURASHOV), None)
    teams.put_on_waivers(league, MURASHOV, dt.date(2026, 10, 1))


def _league_adds_from_log(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-03: other teams' adds were logged only from 2026-10-01 (4 of 13
    # seen), though every Transactions screenshot since Sep 30 was applied.
    # Rebuild the log from transactions_seen, and date the rosters by the
    # newest move seen (when it was sent isn't recorded: a lower bound).
    names = {normalize_name(t).replace(" ", ""): t for t in league["teams"]}
    adds: dict[str, list[str]] = {}
    for key, when in sorted(state["transactions_seen"].items(), key=lambda kv: kv[1]):
        _, kind, team, who = key.split("|")
        if kind not in ("add", "add/drop") or team not in names:
            continue
        nhl_date = (dt.datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(TIMEZONE))
                    .astimezone(ZoneInfo("America/New_York")).date().isoformat())
        adds.setdefault(names[team], []).extend([nhl_date] * (len(who.split(",")) if kind == "add" else 1))
    state["league_adds"] = adds
    if state["transactions_seen"] and not league.get("moves_through"):
        league["moves_through"] = max(state["transactions_seen"].values())


# Nico's League > Standings and All Matchups screenshots of 2026-10-03 10:53
# Helsinki, sent in a development session before the bot could read them:
# week 1's pairings, and each team's score and Yahoo projection then (through
# Oct 2's games).
WEEK_1_BOARD = [
    ("Nico's Groovy Team", 87.40, 175.62, "Bahelin Boys", 114.25, 166.68),
    ("HAN-NES", 96.35, 174.62, "HC Bulju", 114.95, 204.09),
    ("Viktorios", 99.25, 178.96, "Bottom three", 145.15, 215.92),
    ("Gwp", 101.20, 170.36, "Vanilla Thunder", 86.00, 150.76),
    ("Retrot Chicken Wings", 93.80, 174.75, "Pastasauce", 94.55, 176.29),
    ("Lazy Lew", 119.20, 207.19, "Löllöt Höntsääjät", 86.85, 156.30),
    ("Randy", 80.65, 162.98, "Vantaa", 134.85, 206.77),
    ("Bellova", 65.45, 142.92, "Jättiläisentie Giants", 85.15, 139.48),
]


def _week_1_board(state: dict, players: list[RosterPlayer], league: dict) -> None:
    week = state.setdefault("league_weeks", {}).setdefault("1", {"pairs": [], "scores": {}})
    for a, a_score, a_proj, b, b_score, b_proj in WEEK_1_BOARD:
        if sorted((a, b)) not in week["pairs"]:
            week["pairs"].append(sorted((a, b)))
        for team, score, proj in ((a, a_score, a_proj), (b, b_score, b_proj)):
            week["scores"].setdefault(team, {"score": score, "projected": proj, "date": "2026-10-03"})
    if not state.get("standings"):  # everyone 0-0-0 before week 1 ends
        state["standings"] = {"week": 0, "at": "2026-10-03",
                              "teams": {t: {"w": 0, "l": 0, "t": 0, "pf": 0.0}
                                        for row in WEEK_1_BOARD for t in (row[0], row[3])}}


def _week_1_orig_proj(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-03: Nico sent the website's week 1 matchup header in chat: Yahoo's
    # original projection, 188.20 - 165.01 (Live Proj 175.62 - 166.68).
    state.setdefault("results", {}).setdefault("1", {"opponent": "Bahelin Boys"})["yahoo_orig"] = [188.20, 165.01]
    scores = state.setdefault("league_weeks", {}).setdefault("1", {"pairs": [], "scores": {}})["scores"]
    for team, orig, live in (("Nico's Groovy Team", 188.20, 175.62), ("Bahelin Boys", 165.01, 166.68)):
        scores.setdefault(team, {}).update(orig_proj=orig, live_proj=live)


# scripts/retro_forecast.py, run 2026-10-03 (Check workflow): week 1 from the
# drafted rosters as of Sep 29, no injury report or confirmed goalies.
# Team: (expected, sd, P(goalie minimum)).
WEEK_1_RETRO = {
    "Pastasauce": (160.11, 28.14, 0.578), "Vanilla Thunder": (152.44, 27.79, 0.606),
    "Nico's Groovy Team": (160.91, 28.15, 0.928), "Viktorios": (166.93, 28.9, 0.617),
    "HAN-NES": (169.67, 29.16, 0.92), "Gwp": (142.47, 25.87, 0.45), "Löllöt Höntsääjät": (140.31, 25.63, 0.408),
    "Vantaa": (161.63, 27.84, 0.489), "Bottom three": (159.89, 28.39, 0.858), "Lazy Lew": (145.24, 25.06, 0.326),
    "Randy": (167.67, 28.71, 0.589), "HC Bulju": (157.21, 27.9, 0.545), "Bellova": (158.35, 28.22, 0.625),
    "Bahelin Boys": (160.01, 29.08, 0.782), "Jättiläisentie Giants": (162.35, 29.45, 0.708),
    "Retrot Chicken Wings": (174.54, 30.01, 0.728),
}


def _week_1_retro_forecast(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-03: week 1's first plan came mid-week (Thu Oct 1), so our forecast
    # from before its games is rebuilt (Nico asked): marked "retro", so it's
    # never mistaken for one made at the time.
    week = state.setdefault("league_weeks", {}).setdefault("1", {"pairs": [], "scores": {}})
    week["ours"] = {"at": "2026-09-29", "retro": "rebuilt 2026-10-03 by scripts/retro_forecast.py: drafted "
                    "rosters, no injury report or confirmed goalies", "moves_through": "2026-09-28",
                    "teams": {t: {"expected": e, "sd": sd, "so_far": 0.0, "goalie_min": g}
                              for t, (e, sd, g) in WEEK_1_RETRO.items()}}
    (me, me_sd, _), (them, them_sd, _) = WEEK_1_RETRO["Nico's Groovy Team"], WEEK_1_RETRO["Bahelin Boys"]
    win = 0.5 * (1 + math.erf((me - them) / math.sqrt(2 * (me_sd ** 2 + them_sd ** 2))))
    state.setdefault("results", {}).setdefault("1", {"opponent": "Bahelin Boys"})["retro"] = {
        "expected": [me, them], "sd": [me_sd, them_sd], "win": round(win, 4)}


# Nico's lineup in his team screenshots of 2026-10-05 08:25 Helsinki, as the
# fixed reader reads them (Celebrini's slot was in the second screenshot).
LINEUP_OCT_5 = {8480855: "C", 8476460: "C", 8476887: "LW", 8477949: "LW", 8485406: "RW", 8474564: "RW",
                8476457: "D", 8476902: "D", 8480865: "D", 8480145: "D", 8484801: "BN", 8480807: "BN",
                8481519: "G", 8478872: "G"}
MISREAD_MOVES = ["2026-09-30T10:59|add|nicosgroovyteam|elindell,astolarz",
                 "2026-10-01T10:32|add/drop|gwp|astolarz,anikishin",
                 "2026-10-02T10:21|add/drop|lazylew|vpodlkolzin,mwood,elindholm"]


def _stolarz_misread(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-05: in a website Transactions screenshot, three rows took the
    # player of the row below them (its team or date unread): Nico's Lindell
    # add took Gwp's Stolarz add, Gwp's Stolarz drop took Bottom three's
    # Nikishin add, Lazy Lew's add/drop took Gwp's Lindholm add. Stolarz went
    # on my roster as an add, and Nikishin and Lindholm moved teams. Then
    # team screenshots read Celebrini twice, the first without his slot, so
    # every slot was cleared. Nico never had Stolarz.
    players[:] = [p for p in players if p.id != STOLARZ]
    state["adds"] = [a for a in state["adds"] if not (a["id"] == STOLARZ and a["source"] == "transactions")]
    state["seen_mine"].pop(str(STOLARZ), None)
    if {p.id for p in players} == set(LINEUP_OCT_5) and all(p.slot is None for p in players):
        for p in players:
            p.slot = LINEUP_OCT_5[p.id]
    for pid, wrong, right in ((NIKISHIN, "Gwp", "Bottom three"), (LINDHOLM, "Lazy Lew", "Gwp")):
        entry = next((q for q in league["teams"].get(wrong, {}).get("players", []) if q["id"] == pid), None)
        if entry and right in league["teams"]:
            teams.add_player(league, right, RosterPlayer(**{**entry, "slot": None}))
    for team, date, real in (("Gwp", "2026-10-01", 0), ("Lazy Lew", "2026-10-02", 1)):  # adds logged twice
        logged = state["league_adds"].get(team, [])
        if logged.count(date) > real:
            logged.remove(date)
    for key in MISREAD_MOVES:
        state["transactions_seen"].pop(key, None)



# Nico's screenshot of the website's Standings, sent in a development session
# on 2026-10-05 after week 1: team, W, L, T, points for.
WEEK_1_STANDINGS = [
    ("Lazy Lew", 1, 0, 0, 244.50), ("Vantaa", 1, 0, 0, 205.90), ("Bottom three", 1, 0, 0, 198.80),
    ("HC Bulju", 1, 0, 0, 196.45), ("Retrot Chicken Wings", 1, 0, 0, 192.00), ("Bahelin Boys", 1, 0, 0, 189.40),
    ("Gwp", 1, 0, 0, 175.15), ("Jättiläisentie Giants", 1, 0, 0, 153.25), ("Randy", 0, 1, 0, 191.40),
    ("Pastasauce", 0, 1, 0, 187.90), ("Viktorios", 0, 1, 0, 186.50), ("HAN-NES", 0, 1, 0, 182.40),
    ("Nico's Groovy Team", 0, 1, 0, 163.85), ("Löllöt Höntsääjät", 0, 1, 0, 150.05),
    ("Vanilla Thunder", 0, 1, 0, 146.75), ("Bellova", 0, 1, 0, 143.80),
]


def _week_1_standings(state: dict, players: list[RosterPlayer], league: dict) -> None:
    if (state.get("standings") or {}).get("week", 0) < 1:
        state["standings"] = {"week": 1, "at": "2026-10-05",
                              "teams": {t: {"w": w, "l": l, "t": tie, "pf": pf} for t, w, l, tie, pf in WEEK_1_STANDINGS}}
    state.setdefault("results", {}).setdefault("1", {"opponent": "Bahelin Boys"}).setdefault(
        "yahoo_final", {"score": [163.85, 189.40], "at": "2026-10-05T12:00+00:00"})

REPAIRS = [("2026-10-01 totals view", _totals_view), ("2026-10-01 murashov dropped", _murashov_dropped),
           ("2026-10-03 league adds from the log", _league_adds_from_log),
           ("2026-10-03 week 1 board and standings", _week_1_board),
           ("2026-10-03 week 1 orig proj", _week_1_orig_proj),
           ("2026-10-03 week 1 retro forecast", _week_1_retro_forecast),
           ("2026-10-05 stolarz misread", _stolarz_misread),
           ("2026-10-05 week 1 standings", _week_1_standings)]
