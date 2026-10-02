"""Who plays: skater availability and goalie start odds, tonight and on
later days.

Tonight's status (DailyFaceoff's injuries and line charts) fades over the
days ahead along return curves measured from game logs: a regular who is out
tonight is far from certain to be back next week, and far from gone for six.

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
from clients.names import normalize_name

HEALTHY_PLAY = 0.97  # healthy scratches, late illness
DOUBTFUL_PLAY = 0.6  # day-to-day or game-time decision
UNLISTED_PLAY = 0.3  # not on his team's line chart: scratched or in the minors

# P(plays a team game) this many days after a night he's out, for regulars,
# by how many team games in a row he has missed counting that night
# (scripts/fit_absence.py, fit on 2024-25; on 2025-26 within 0.06, except
# 6-15 missed at 2+ weeks, up to 0.13 higher). The longer the absence, the
# slower the return; some never regain their spot (1-2 missed level off at
# ~0.67). Past the last bucket the last value holds.
RETURN_BUCKETS = ((1, 2), (3, 6), (7, 13), (14, 20), (21, 27), (28, 41))
RETURN_CURVES: tuple[tuple[int | None, tuple[float, ...]], ...] = (  # (most games missed, curve)
    (2, (0.32, 0.44, 0.56, 0.62, 0.65, 0.67)),
    (5, (0.19, 0.29, 0.41, 0.51, 0.54, 0.59)),
    (15, (0.09, 0.18, 0.27, 0.32, 0.37, 0.45)),
    (None, (0.03, 0.09, 0.16, 0.22, 0.29, 0.36)),
)
IR_MIN_MISSED = 3  # NHL IR is 7+ days: someone just placed there returns like a 3-5 game absence
# Day-to-day or a game-time decision: doubtful tonight, then a quick return
# (a judgment call: DFO statuses aren't archived, so this can't be fit).
DTD_RETURN = (0.85,) + (HEALTHY_PLAY,) * 5

# A healthy goalie's odds of still holding his starts this many days on
# (injuries, a lost job), per RETURN_BUCKETS: his starts then over his share
# of the last 10, relative to 3-6 days on (the first days dip for
# back-to-backs, handled apart, and the last-10 share drifting to the mean).
# scripts/fit_absence.py --goalies: fit on 2024-25, 2023-24 and 2025-26 within 0.04.
# The long run treats a goalie as there all week or not at all, so a lost
# goalie takes all his starts with him (what makes a third goalie insurance).
GOALIE_KEEP = (1.0, 1.0, 0.98, 0.95, 0.93, 0.90)

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


def ahead(curve: tuple[float, ...], days_ahead: int, tonight: float) -> float:
    """P(plays) `days_ahead` days from now, given tonight's odds and a return curve."""
    if days_ahead <= 0:
        return tonight
    return next((p for (first, last), p in zip(RETURN_BUCKETS, curve) if days_ahead <= last), curve[-1])


def return_curve(missed: int) -> tuple[float, ...]:
    """The return curve after `missed` team games in a row (tonight's included)."""
    return next(curve for most, curve in RETURN_CURVES if most is None or missed <= most)


def _curve(injured: str | None, missed_before: int) -> tuple[float, ...]:
    missed = missed_before + 1  # out tonight too
    return return_curve(max(missed, IR_MIN_MISSED) if injured == "IR" else missed)


def skater(info: LineInfo | None, team_has_lines: bool, days_ahead: int = 0, missed: int = 0) -> Availability:
    """His odds of playing `days_ahead` days from tonight's status, after
    `missed` team games in a row before tonight. `team_has_lines` is False
    when DFO had no chart for the team (fetch failed): then being unlisted
    says nothing and he's assumed to play."""
    injured = _injured(info)
    if injured:
        return Availability(ahead(_curve(injured, missed), days_ahead, 0.0), injured)
    if info is None:
        if not team_has_lines:
            return Availability(HEALTHY_PLAY)
        return Availability(ahead(_curve(None, missed), days_ahead, UNLISTED_PLAY), "not in lineup")
    if info.injury == "dtd" or info.game_time_decision:
        return Availability(ahead(DTD_RETURN, days_ahead, DOUBTFUL_PLAY),
                            "day-to-day" if info.injury == "dtd" else "game-time decision")
    return Availability(HEALTHY_PLAY)


def goalie(
    player_id: int,
    name: str,
    date: dt.date,
    info: LineInfo | None,
    dfo_starter: dict | None,
    team_starts: list[tuple[dt.date, int]],
    prior_share: float | None,
    days_ahead: int = 0,
) -> Availability:
    """`dfo_starter` is DailyFaceoff's pick for his team tonight
    ({"goalie_name", "confirmed"}) or None; `team_starts` is his team's
    (date, starter id) history this season, oldest first. An injured goalie
    `days_ahead` from tonight starts at his usual share once back (the skater
    return curves; goalies' weren't fit separately)."""
    injured = _injured(info)
    if injured:
        back = ahead(_curve(injured, 0), days_ahead, 0.0)
        return Availability(back * start_share(player_id, date, info, team_starts, prior_share) if back else 0.0,
                            injured)

    if dfo_starter:
        confirmed = dfo_starter["confirmed"]
        if normalize_name(dfo_starter["goalie_name"]) == normalize_name(name):
            return Availability(CONFIRMED_START if confirmed else LIKELY_START,
                                "confirmed starter" if confirmed else "likely starter")
        other = dfo_starter["goalie_name"]
        return Availability(1 - (CONFIRMED_START if confirmed else LIKELY_START),
                            f"{other} {'confirmed' if confirmed else 'likely'} to start")

    share = start_share(player_id, date, info, team_starts, prior_share)
    past = [(d, pid) for d, pid in team_starts if d < date]
    if past and past[-1][0] == date - dt.timedelta(days=1):
        if past[-1][1] == player_id:
            return Availability(share * BACK_TO_BACK_REPEAT, f"~{share * BACK_TO_BACK_REPEAT:.0%} to start (started last night)")
        p = 1 - (1 - share) * BACK_TO_BACK_REPEAT
        return Availability(p, f"~{p:.0%} to start (back-to-back, other goalie started last night)")
    return Availability(share, f"~{share:.0%} to start (not confirmed yet)")


def start_share(
    player_id: int,
    date: dt.date,
    info: LineInfo | None,
    team_starts: list[tuple[dt.date, int]],
    prior_share: float | None,
) -> float:
    """His share of his team's starts: the last 10 blended with a prior."""
    if prior_share is None:
        prior_share = DEPTH_SHARE.get(info.goalie_depth, UNKNOWN_SHARE) if info else UNKNOWN_SHARE
    recent = [pid for d, pid in team_starts if d < date][-RECENT_STARTS:]
    return (SHARE_PRIOR_GAMES * prior_share + recent.count(player_id)) / (SHARE_PRIOR_GAMES + len(recent))


def second_of_back_to_back(share: float) -> float:
    """Start odds for the second night of a back-to-back that's still days
    away, when nobody knows yet who starts the first night."""
    return share * share * BACK_TO_BACK_REPEAT + (1 - share) * (1 - (1 - share) * BACK_TO_BACK_REPEAT)
