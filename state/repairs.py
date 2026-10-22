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
from league import positions, teams
from league.roster import RosterPlayer

SCHENN, MURASHOV = 8475170, 8483703
STOLARZ, NIKISHIN, LINDHOLM = 8476932, 8482100, 8477496
ERIKSSON_EK = 8478493


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

# Nico's League > Standings screenshot of 2026-10-18 17:25 Helsinki, sent in a
# Claude session (not to the bot): two weeks played. The simulation had run
# from week 1's, so his week-2 loss was missing from the playoff odds (89%).
WEEK_2_STANDINGS = [
    ("Lazy Lew", 2, 0, 0, 488.20), ("Retrot Chicken Wings", 2, 0, 0, 444.10), ("Bahelin Boys", 2, 0, 0, 439.95),
    ("HC Bulju", 2, 0, 0, 436.35), ("Pastasauce", 1, 1, 0, 430.70), ("Bottom three", 1, 1, 0, 425.60),
    ("HAN-NES", 1, 1, 0, 405.50), ("Vantaa", 1, 1, 0, 392.75), ("Jättiläisentie Giants", 1, 1, 0, 390.55),
    ("Bellova", 1, 1, 0, 357.20), ("Vanilla Thunder", 1, 1, 0, 341.55), ("Gwp", 1, 1, 0, 310.15),
    ("Randy", 0, 2, 0, 438.35), ("Löllöt Höntsääjät", 0, 2, 0, 373.35), ("Nico's Groovy Team", 0, 2, 0, 369.35),
    ("Viktorios", 0, 2, 0, 364.35),
]


def _week_2_standings(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # Left alone if a newer screenshot has reached the bot since.
    if (state.get("standings") or {}).get("week", 0) < 2:
        state["standings"] = {"week": 2, "at": "2026-10-18",
                              "teams": {t: {"w": w, "l": l, "t": tie, "pf": pf} for t, w, l, tie, pf in WEEK_2_STANDINGS}}
    # Week 2's final, Yahoo's: points for minus week 1's (163.85, 192.00).
    state.setdefault("results", {}).setdefault("2", {"opponent": "Retrot Chicken Wings"}).setdefault(
        "yahoo_final", {"score": [205.50, 252.10], "at": "2026-10-18T14:25+00:00"})


def _eriksson_ek_unread(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-12: Löllöt Höntsääjät's team screenshots read "J. ERIKSSONEK" (Joel
    # Eriksson Ek, their C), which matched no one; the roster was saved without
    # him: first the plan offered him as an add, then (marked taken by Nico) their
    # projection went without him. Left alone if a later screenshot or
    # transaction has put him on a team's roster.
    team = "Löllöt Höntsääjät"
    if team in league["teams"] and all(p["id"] != ERIKSSON_EK for e in league["teams"].values() for p in e["players"]):
        teams.add_player(league, team, RosterPlayer(ERIKSSON_EK, "Joel Eriksson Ek", "MIN", ["C"], None))


# Nico's Team tab screenshots of 2026-10-13 19:42 Helsinki: player id -> slot.
LINEUP_OCT_13 = {8484801: "C", 8476460: "C", 8477949: "LW", 8476887: "LW", 8485406: "RW", 8474564: "RW",
                 8480807: "D", 8476457: "D", 8480865: "D", 8480145: "D", 8481519: "G", 8478872: "G",
                 8480855: "BN", 8481596: "BN"}


def _forsberg_slot_unread(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-13: the screenshots read no slot for Forsberg (his old BN was
    # full) and a "C • SJ" line as a player, so every slot was cleared.
    # Left alone if the roster has changed since.
    if {p.id for p in players} == set(LINEUP_OCT_13) and all(p.slot is None for p in players):
        for p in players:
            p.slot = LINEUP_OCT_13[p.id]


def _score_after_last_puck(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-15: matchup screenshots sent 06:52 Helsinki (23:52 New York), every
    # game of both rosters over (Final or No Game on all 28 rows), were dated
    # Wednesday after its first puck, so the score was dropped (through None) and
    # the reply gave box scores through Tuesday: "91-98, win 39%" against Yahoo's
    # 120.05-103.80. The score covers every day before Thursday 15 Oct.
    live = state.get("live_score") or {}
    if live.get("at") == "2026-10-15T03:53:14+00:00" and live.get("through") is None:
        live["through"] = "2026-10-15"


def _no_game_unread(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-17: matchup screenshots sent 06:47 Helsinki, every row Final or No
    # Game, but three "No Game"s went unread (clients/screenshot.py measured them
    # from the other side's taller name box), so the score was dropped (through
    # None) and the reply gave box scores missing Friday: "192-170, win 87%"
    # against Yahoo's 203.15-196.85. The score covers every day before Saturday 17 Oct.
    live = state.get("live_score") or {}
    if live.get("at") == "2026-10-17T03:47:39+00:00" and live.get("through") is None:
        live["through"] = "2026-10-17"


# The scoreboard (engine/scorecard.week_value) plays the drafted roster against
# the real one, day by day. The draft (Yahoo, Sep 28: state/roster.json's seed,
# 1e9e0d5) and my active roster each day since, from the day_rosters snapshots
# in git history (Oct 1 on) and the seed roster with Samuelsson on IR+ (a669458)
# for Sep 29-30.
DRAFT_ROSTER = [
    (8484801, "Macklin Celebrini", "SJS", ["C"]), (8476460, "Mark Scheifele", "WPG", ["C"]),
    (8476887, "Filip Forsberg", "NSH", ["LW"]), (8477949, "Alex Tuch", "WSH", ["LW", "RW"]),
    (8485406, "Porter Martone", "PHI", ["RW"]), (8474564, "Steven Stamkos", "NSH", ["C", "RW"]),
    (8480865, "Noah Dobson", "MTL", ["D"]), (8480807, "Mattias Samuelsson", "BUF", ["D"]),
    (8480145, "Neal Pionk", "WPG", ["D"]), (8476457, "Adam Larsson", "SEA", ["D"]),
    (SCHENN, "Brayden Schenn", "NYI", ["C", "LW"]), (8478872, "Karel Vejmelka", "UTA", ["G"]),
    (8481519, "Spencer Knight", "CHI", ["G"]), (MURASHOV, "Sergei Murashov", "PIT", ["G"]),
]
CELEBRINI, SAMUELSSON, MARTONE = 8484801, 8480807, 8485406
LINDELL, MCBAIN, PINTO, LANDESKOG, MERILAINEN = 8476902, 8480855, 8481596, 8476455, 8482447
SINCE_DRAFT = [  # (first NHL date, out, in) of my active roster
    ("2026-10-01", [SCHENN, MURASHOV], [SAMUELSSON, LINDELL, MCBAIN]),  # Samuelsson back from IR+
    ("2026-10-07", [CELEBRINI], [PINTO]),  # Celebrini to IR
    ("2026-10-12", [LINDELL], [CELEBRINI]),
    ("2026-10-16", [MARTONE], [LANDESKOG]),
    ("2026-10-20", [PINTO], [MERILAINEN]),
]
SINCE_DRAFT_PLAYERS = {LINDELL: ("Esa Lindell", "DAL", ["D"]), MCBAIN: ("Jack McBain", "UTA", ["C", "LW"]),
                       PINTO: ("Shane Pinto", "OTT", ["C"]), LANDESKOG: ("Gabriel Landeskog", "COL", ["LW", "RW"]),
                       MERILAINEN: ("Leevi Meriläinen", "VAN", ["G"])}


def _since_draft_days(last: dt.date) -> dict[str, list[int]]:
    roster = {p[0] for p in DRAFT_ROSTER} - {SAMUELSSON}  # on IR+ from the start
    changes = {d: (out, into) for d, out, into in SINCE_DRAFT}
    days, d = {}, dt.date(2026, 9, 29)
    while d <= last:
        if d.isoformat() in changes:
            out, into = changes[d.isoformat()]
            roster = (roster - set(out)) | set(into)
        days[d.isoformat()] = sorted(roster)
        d += dt.timedelta(days=1)
    return days


def _scoreboard_since_draft(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-21: the scoreboard needs the drafted roster and every day's roster;
    # day_rosters keeps 14 days. Days the bot has recorded itself stay as they are.
    state["draft_roster"] = [{"id": i, "name": n, "team": t, "positions": pos} for i, n, t, pos in DRAFT_ROSTER]
    for i, n, t, pos in DRAFT_ROSTER:
        state.setdefault("my_players", {}).setdefault(str(i), {"name": n, "team": t, "positions": pos})
    for i, (n, t, pos) in SINCE_DRAFT_PLAYERS.items():
        state["my_players"].setdefault(str(i), {"name": n, "team": t, "positions": pos})
    for day, ids in _since_draft_days(dt.date(2026, 10, 20)).items():
        state.setdefault("my_days", {}).setdefault(day, ids)


def _points_for_by_week(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-21: standings keep only the latest week; each week's points for
    # (cumulative) now go to league_weeks too, so every team's week can be checked
    # against our forecast (scripts/check_forecasts.py). Weeks 1-2 from the
    # repairs above, the latest from the saved standings.
    weeks_ = {1: {t: pf for t, *_, pf in WEEK_1_STANDINGS}, 2: {t: pf for t, *_, pf in WEEK_2_STANDINGS}}
    latest = state.get("standings") or {}
    if latest.get("week"):
        weeks_[latest["week"]] = {t: v["pf"] for t, v in latest["teams"].items()}
    for week, pf in weeks_.items():
        state.setdefault("league_weeks", {}).setdefault(str(week), {"pairs": [], "scores": {}}).setdefault(
            "points_for", pf)


# The plan's last forecast of each move I made since the Board kept them (Oct 9),
# from state/board.json in git history: the latest Board on or before the move's
# day that had it in the plan. Pinto (Oct 7) and McBain (Oct 1) predate it.
MOVE_FORECASTS = {
    f"{LANDESKOG}:{MARTONE}": {"date": "2026-10-14", "at": "2026-10-15T03:53+00:00", "value": 0.1062,
                               "now_wins": 0.0476, "ahead_wins": [0.065, -0.0375], "week_pts": 4.01,
                               "ahead_week_pts": [7.61, -4.57], "later_wins": 0.0312, "price": 0.0851},
    f"{MERILAINEN}:{PINTO}": {"date": "2026-10-20", "at": "2026-10-20T17:31+00:00", "value": 0.4495,
                              "now_wins": 0.1321, "ahead_wins": [-0.035, 0.0179], "week_pts": 14.05,
                              "ahead_week_pts": [-4.16, 2.21], "later_wins": 0.3346, "price": 0.1241},
}


def _move_forecasts(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-21: each move is now checked against the plan's forecast of it;
    # the forecasts are logged from now on, these two from git history.
    for key, f in MOVE_FORECASTS.items():
        state.setdefault("move_forecasts", {}).setdefault(key, f)


# Nico's two screenshots of HC Bulju's team page (Yahoo app), sent in a development
# session on 2026-10-22 (14:40) and read with clients/screenshot.py: id, name, NHL
# team and positions as Yahoo showed them. Thomas Chabot was in IR+ ("O").
HC_BULJU_OCT_22 = [
    (8483445, "Cutter Gauthier", "ANA", ["C", "LW", "RW"]), (8475158, "Ryan O'Reilly", "NSH", ["C"]),
    (8486067, "Gavin McKenna", "TOR", ["LW"]), (8480893, "Kirill Marchenko", "TOR", ["LW", "RW"]),
    (8476389, "Vincent Trocheck", "UTA", ["C", "RW"]), (8477960, "Adrian Kempe", "LAK", ["RW"]),
    (8478460, "Zach Werenski", "CBJ", ["D"]), (8480803, "Evan Bouchard", "EDM", ["D"]),
    (8479410, "Mikhail Sergachev", "UTA", ["D"]), (8476958, "Jaccob Slavin", "CAR", ["D"]),
    (8479542, "Brandon Hagel", "TBL", ["LW", "RW"]), (8485406, "Porter Martone", "PHI", ["RW"]),
    (8478469, "Thomas Chabot", "OTT", ["D"]), (8478435, "Dan Vladar", "PHI", ["G"]),
    (8474596, "Jake Allen", "NJD", ["G"]),
]


def _hc_bulju_team_page(state: dict, players: list[RosterPlayer], league: dict) -> None:
    # 2026-10-22: HC Bulju's page as bot/ingest.save_team would have saved it: the same 15
    # players the transactions had left them with, now dated, with Yahoo's positions learned
    # (Marchenko LW/RW, Trocheck C/RW; both had one) and Marchenko's team (TOR, not CBJ).
    # Slots stay unset, as for every other team: Chabot's IR+ is read from DFO's status.
    roster = [RosterPlayer(pid, name, team, pos, None) for pid, name, team, pos in HC_BULJU_OCT_22]
    teams.set_team(league, "HC Bulju", roster, dt.date(2026, 10, 22))
    known = positions.load()
    if positions.learn(known, {p.id: p.positions for p in roster}):
        positions.save(known)


REPAIRS = [("2026-10-01 totals view", _totals_view), ("2026-10-01 murashov dropped", _murashov_dropped),
           ("2026-10-03 league adds from the log", _league_adds_from_log),
           ("2026-10-03 week 1 board and standings", _week_1_board),
           ("2026-10-03 week 1 orig proj", _week_1_orig_proj),
           ("2026-10-03 week 1 retro forecast", _week_1_retro_forecast),
           ("2026-10-05 stolarz misread", _stolarz_misread),
           ("2026-10-05 week 1 standings", _week_1_standings),
           ("2026-10-12 eriksson ek unread", _eriksson_ek_unread),
           ("2026-10-13 forsberg slot unread", _forsberg_slot_unread),
           ("2026-10-15 score after the last puck", _score_after_last_puck),
           ("2026-10-17 no game unread", _no_game_unread),
           ("2026-10-18 week 2 standings", _week_2_standings),
           ("2026-10-21 scoreboard since the draft", _scoreboard_since_draft),
           ("2026-10-21 points for by week", _points_for_by_week),
           ("2026-10-21 move forecasts", _move_forecasts),
           ("2026-10-22 hc bulju team page", _hc_bulju_team_page)]
