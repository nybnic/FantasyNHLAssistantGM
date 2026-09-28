"""Who actually plays tonight: skater availability and goalie start odds.

Goalie starts matter far more than goalie matchups (a start is worth ~8.8
points; best vs worst matchup differs by ~0.8), so start odds get the most
care:
1. DailyFaceoff's starting-goalie page, when it names tonight's starter.
2. Otherwise the goalie's share of his team's last 10 starts, blended with a
   prior (DFO's projected season starts, else DFO depth chart g1/g2), and
   adjusted for back-to-backs: a goalie who started last night rarely
   starts again.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from clients.dfo_lines import LineInfo
from clients.dfo_projections import normalize_name

HEALTHY_PLAY = 0.97  # healthy scratches, late illness
DOUBTFUL_PLAY = 0.6  # day-to-day or game-time decision
UNLISTED_PLAY = 0.3  # not on his team's line chart: scratched or in the minors

CONFIRMED_START = 0.97
LIKELY_START = 0.8
RECENT_STARTS = 10
SHARE_PRIOR_GAMES = 4
DEPTH_SHARE = {1: 0.65, 2: 0.35}
UNKNOWN_SHARE = 0.3
BACK_TO_BACK_REPEAT = 0.35  # relative odds of starting both ends of a back-to-back


@dataclass
class Availability:
    prob: float
    note: str = ""


def _injured(info: LineInfo | None) -> str | None:
    if info and (info.injury in ("out", "ir") or "ir" in info.groups):
        return "IR" if (info.injury == "ir" or "ir" in info.groups) else "out"
    return None


def skater(info: LineInfo | None, team_has_lines: bool) -> Availability:
    """`team_has_lines` is False when DFO had no chart for the team (fetch
    failed): then being unlisted says nothing and he's assumed to play."""
    injured = _injured(info)
    if injured:
        return Availability(0.0, injured)
    if info is None:
        return Availability(UNLISTED_PLAY, "not in lineup") if team_has_lines else Availability(HEALTHY_PLAY)
    if info.injury == "dtd":
        return Availability(DOUBTFUL_PLAY, "day-to-day")
    if info.game_time_decision:
        return Availability(DOUBTFUL_PLAY, "game-time decision")
    return Availability(HEALTHY_PLAY)


def goalie(
    player_id: int,
    name: str,
    date: dt.date,
    info: LineInfo | None,
    dfo_starter: dict | None,
    team_starts: list[tuple[dt.date, int]],
    prior_share: float | None,
) -> Availability:
    """`dfo_starter` is DailyFaceoff's pick for his team tonight
    ({"goalie_name", "confirmed"}) or None; `team_starts` is his team's
    (date, starter id) history this season, oldest first."""
    injured = _injured(info)
    if injured:
        return Availability(0.0, injured)

    if dfo_starter:
        confirmed = dfo_starter["confirmed"]
        if normalize_name(dfo_starter["goalie_name"]) == normalize_name(name):
            return Availability(CONFIRMED_START if confirmed else LIKELY_START,
                                "confirmed starter" if confirmed else "likely starter")
        other = dfo_starter["goalie_name"]
        return Availability(1 - (CONFIRMED_START if confirmed else LIKELY_START),
                            f"{other} {'confirmed' if confirmed else 'likely'} to start")

    if prior_share is None:
        prior_share = DEPTH_SHARE.get(info.goalie_depth, UNKNOWN_SHARE) if info else UNKNOWN_SHARE
    past = [(d, pid) for d, pid in team_starts if d < date]
    recent = [pid for _, pid in past[-RECENT_STARTS:]]
    share = (SHARE_PRIOR_GAMES * prior_share + recent.count(player_id)) / (SHARE_PRIOR_GAMES + len(recent))

    if past and past[-1][0] == date - dt.timedelta(days=1):
        if past[-1][1] == player_id:
            return Availability(share * BACK_TO_BACK_REPEAT, f"~{share * BACK_TO_BACK_REPEAT:.0%} to start (started last night)")
        p = 1 - (1 - share) * BACK_TO_BACK_REPEAT
        return Availability(p, f"~{p:.0%} to start (back-to-back, other goalie started last night)")
    return Availability(share, f"~{share:.0%} to start (not confirmed yet)")
