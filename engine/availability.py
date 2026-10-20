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
# When a teammate goalie is out (DFO injured), the starts he'd have made go to
# the healthy goalies in proportion to their shares, this part of them (the
# rest to a call-up). Without it the absent starter's starts went nowhere and
# his backup's share rose only as he started (Nico, 2026-10-13: Garand behind
# an injured Shesterkin). scripts/fit_goalie_absence.py, fit 2023-25, checked
# on 2025-26: a starter out 3+ games, back along the return curve, 0.30 (each
# season alone 0.25-0.35; 2025-26 log loss 0.599 -> 0.587, the backup's
# predicted starts 38% -> 47%, actual 47%); out long (15+ games, as news with a
# timeline tells), all of his share, 0.45 (alone 0.35-0.65; 2025-26 log loss
# 0.811 -> 0.724, the backup 28% -> 44%, actual 48%).
ABSENT_TO_TEAMMATES = 0.30
ABSENT_TO_TEAMMATES_KNOWN = 0.45
# The goalie who started last night starts tonight at this share of his usual
# rate. scripts/fit_goalie_starts.py: 0.09 in 2023-24, 0.13 in 2024-25, 0.16 in
# 2025-26 (rising); 0.15 = 2024-26 pooled. Was 0.35, a judgment call.
BACK_TO_BACK_REPEAT = 0.15
# How the team's last start went moves the odds he starts its next game
# (odds multiplier): coaches go back to a winner and sit a goalie after a loss,
# most after a blowout (5+ goals against, or pulled). Next game only, and not
# on back-to-backs (too few to fit). scripts/fit_goalie_starts.py, fit 2023-25:
# W 1.01, L 0.62, L bad 0.32; 2025-26 alone 0.96 / 0.63 / 0.35; log loss
# 0.651 -> 0.625 on 2025-26. Two games on it's mixed (W 1.34, L 1.22, L bad
# 0.74: the benched goalie returns) and not modeled.
LAST_RESULT_ODDS = {"W": 1.0, "L": 0.62, "L bad": 0.32}
BAD_START_GA = 5

# Superseded 2026-10-20 by each goalie's own record (Nico: Kuemper, pulled in
# his own last start, at 70% because his partner had lost since): LAST_RESULT_ODDS
# saw only the team's last start, for its next game. Now every goalie with a
# start in the team's last 10 has his own last start's result, by how many
# team games ago it was (1 = he started the last one), and his form (goals
# saved above average over his last FORM_STARTS starts) moving his odds, and
# his team's shares are re-normalized among them (with_form): a conditional
# logit, scripts/fit_goalie_form.py, fit 2023-25, checked on 2025-26 (each
# season alone within ~0.1 of these). By team games ahead (FORM_HORIZONS); W
# 1 game ago is 1. Log loss on 2025-26: next game 0.690 (the old rule) ->
# 0.658; games 2-3 0.701 (shares) -> 0.687; 4-7, 8-15, 16-30 ~0.002-0.003
# better (form most of it); past 30 games nothing (not fit). Not on back-to-backs.
FORM_STARTS = 5
FORM_HORIZONS = (1, 3, 7, 15, 30)  # the last team game ahead each row of OWN_START_ODDS covers
OWN_START_ODDS = (  # {(his last result, team games ago, 3 = 3+): odds x}, per FORM_HORIZONS
    {("W", 2): 1.74, ("W", 3): 1.17, ("L", 1): 0.59, ("L", 2): 1.13, ("L", 3): 0.89,
     ("L bad", 1): 0.31, ("L bad", 2): 0.63, ("L bad", 3): 0.39},
    {("W", 2): 1.07, ("W", 3): 0.68, ("L", 1): 0.91, ("L", 2): 0.94, ("L", 3): 0.69,
     ("L bad", 1): 0.58, ("L bad", 2): 0.63, ("L bad", 3): 0.43},
    {("W", 2): 1.01, ("W", 3): 0.84, ("L", 1): 0.86, ("L", 2): 0.86, ("L", 3): 0.84,
     ("L bad", 1): 0.65, ("L bad", 2): 0.74, ("L bad", 3): 0.64},
    {("W", 2): 1.03, ("W", 3): 0.83, ("L", 1): 0.88, ("L", 2): 0.94, ("L", 3): 0.97,
     ("L bad", 1): 0.77, ("L bad", 2): 0.84, ("L bad", 3): 0.89},
    {("W", 2): 1.07, ("W", 3): 0.90, ("L", 1): 0.95, ("L", 2): 0.98, ("L", 3): 1.10,
     ("L bad", 1): 0.85, ("L bad", 2): 0.92, ("L bad", 3): 1.06},
)
FORM_ODDS = (1.042, 1.020, 1.019, 1.020, 1.021)  # odds x per goal saved above average, per FORM_HORIZONS
GAMES_PER_DAY = 82 / 186  # a team's games a day over the regular season: days ahead -> team games ahead


@dataclass
class Availability:
    prob: float
    note: str = ""


@dataclass(frozen=True)
class StartForm:
    """A goalie's own record as of today (OWN_START_ODDS, FORM_ODDS)."""
    result: str  # his last start: "W", "L" or "L bad" (start_result)
    ago: int  # team games since it (1 = he started his team's last game)
    gsaa: float  # goals saved above average over his last FORM_STARTS starts


def games_ahead(days_ahead: int, next_game: bool) -> int:
    """Team games ahead: 1 for his team's next game, else from the days."""
    return 1 if next_game else max(2, round(days_ahead * GAMES_PER_DAY) + 1)


def form_odds(form: StartForm | None, games: int) -> float:
    """His odds multiplier `games` team games ahead (1 = the next): his own
    last result by how long ago, and his form. 1 past the fit's 30 games, or
    without a start in his team's last 10."""
    if form is None or games > FORM_HORIZONS[-1]:
        return 1.0
    k = next(i for i, last in enumerate(FORM_HORIZONS) if games <= last)
    return OWN_START_ODDS[k].get((form.result, min(form.ago, 3)), 1.0) * FORM_ODDS[k] ** form.gsaa


def with_form(share: float, odds: float, rivals: list[tuple[float, float]]) -> float:
    """His share with each goalie's odds (his, and `rivals`: his healthy
    teammates' (share, odds)), the team's total share kept: the fit's
    conditional logit. Odds of 1 all round leave his share as it was."""
    weighted = share * odds + sum(s * o for s, o in rivals)
    return share * odds / weighted * (share + sum(s for s, _ in rivals)) if weighted > 0 else share


def form_note(form: StartForm) -> str:
    how = {"W": "won", "L": "lost", "L bad": "pulled or 5+ goals against in"}[form.result]
    return f"{how} his last start" + (f", {form.ago} games ago" if form.ago > 1 else "")


def _injured(info: LineInfo | None) -> str | None:
    """"IR" or "out" by DFO's status. DFO's injured section ("ir" group) holds
    every injured player whatever his status, so it means IR only when no
    status is given (Nico, 2026-10-10: Celebrini sat there "dtd" and was read
    as IR). Day-to-day is not injured here: it has its own curve."""
    if info is None:
        return None
    if info.injury == "ir" or (info.injury is None and "ir" in info.groups):
        return "IR"
    return "out" if info.injury == "out" else None


def _sidelined_dtd(info: LineInfo | None) -> bool:
    """Day-to-day and in DFO's injured section: off tonight's chart, so out
    tonight, then back as day-to-day players come back."""
    return info is not None and info.injury == "dtd" and "ir" in info.groups


def news_back(info: LineInfo | None, today: dt.date | None, days_ahead: int) -> float | None:
    """P(plays) `days_ahead` days on by the news's timeline (`LineInfo.out_until`),
    or None when the news gives none: 0 until the first day he could be back,
    then the return curve of a 3-game absence from that day (a judgment call:
    when players come back after a stated span isn't fit yet; the DFO archive
    keeps the news from 2026-10-13 to fit it)."""
    if info is None or info.out_until is None or today is None:
        return None
    day = today + dt.timedelta(days=days_ahead)
    if day < info.out_until:
        return 0.0
    return ahead(return_curve(IR_MIN_MISSED), (day - info.out_until).days + 1, 0.0)


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


def skater(info: LineInfo | None, team_has_lines: bool, days_ahead: int = 0, missed: int = 0,
           today: dt.date | None = None) -> Availability:
    """His odds of playing `days_ahead` days from tonight's status, after
    `missed` team games in a row before tonight. `team_has_lines` is False
    when DFO had no chart for the team (fetch failed): then being unlisted
    says nothing and he's assumed to play. With `today`, an injury's news
    timeline beats the return curve (`news_back`)."""
    injured = _injured(info)
    if injured:
        news = news_back(info, today, days_ahead)
        return Availability(ahead(_curve(injured, missed), days_ahead, 0.0) if news is None else news, injured)
    if info is None:
        if not team_has_lines:
            return Availability(HEALTHY_PLAY)
        return Availability(ahead(_curve(None, missed), days_ahead, UNLISTED_PLAY), "not in lineup")
    if info.injury == "dtd" or info.game_time_decision:
        return Availability(ahead(DTD_RETURN, days_ahead, 0.0 if _sidelined_dtd(info) else DOUBTFUL_PLAY),
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
    last_result: str | None = None,
    absent: float = 0.0,
    rivals: float = 0.0,
    missed: int = 0,
    form: tuple[StartForm | None, float, list[tuple[float, float]]] | None = None,
) -> Availability:
    """`dfo_starter` is DailyFaceoff's pick for his team tonight
    ({"goalie_name", "confirmed"}) or None; `team_starts` is his team's
    (date, starter id) history this season, oldest first. An injured goalie
    `days_ahead` from tonight starts at his usual share once back (the skater
    return curves; goalies' weren't fit separately). `absent`, `rivals`: his
    injured teammates' starts that day and his healthy teammates' shares
    (`absences`); `missed`: team games in a row he has missed, when he's
    injured (his return curve). `form`: (his own record, his odds that day,
    his healthy teammates' (share, odds)), each goalie's own record moving
    who starts (OWN_START_ODDS). Without it, the old rule: `last_result`, how
    the team's last start went, for its next game (LAST_RESULT_ODDS, kept as
    the baseline scripts/fit_goalie_form.py measures against)."""
    injured = _injured(info)
    if injured or _sidelined_dtd(info):
        news = news_back(info, date - dt.timedelta(days=days_ahead), days_ahead) if injured else None
        back = ahead(_curve(injured, missed) if injured else DTD_RETURN, days_ahead, 0.0) if news is None else news
        return Availability(back * start_share(player_id, date, info, team_starts, prior_share) if back else 0.0,
                            injured or "day-to-day")

    if dfo_starter:
        confirmed = dfo_starter["confirmed"]
        if normalize_name(dfo_starter["goalie_name"]) == normalize_name(name):
            return Availability(CONFIRMED_START if confirmed else LIKELY_START,
                                "confirmed starter" if confirmed else "likely starter")
        other = dfo_starter["goalie_name"]
        return Availability(1 - (CONFIRMED_START if confirmed else LIKELY_START),
                            f"{other} {'confirmed' if confirmed else 'likely'} to start")

    share = start_share(player_id, date, info, team_starts, prior_share, absent, rivals)
    past = [(d, pid) for d, pid in team_starts if d < date]
    if past and past[-1][0] == date - dt.timedelta(days=1):
        if past[-1][1] == player_id:
            return Availability(share * BACK_TO_BACK_REPEAT, f"~{share * BACK_TO_BACK_REPEAT:.0%} to start (started last night)")
        p = 1 - (1 - share) * BACK_TO_BACK_REPEAT
        return Availability(p, f"~{p:.0%} to start (back-to-back, other goalie started last night)")
    if form is not None:
        own, odds, mates = form
        p = min(with_form(share, odds, mates), CONFIRMED_START)
        return Availability(p, f"~{p:.0%} to start" + (f" ({form_note(own)})" if own else " (not confirmed yet)"))
    if past and last_result in LAST_RESULT_ODDS and LAST_RESULT_ODDS[last_result] != 1.0:
        last_id, odds = past[-1][1], LAST_RESULT_ODDS[last_result]
        how = "a blowout loss" if last_result == "L bad" else "a loss"
        if last_id == player_id:
            p = with_odds(share, odds)
            return Availability(p, f"~{p:.0%} to start (after {how})")
        # The starts he gives up go to the others in proportion to their shares.
        # His share: of the last 10, at most what this goalie's leaves (early in
        # the season 1 start of 2 isn't a 50% share).
        recent = [pid for _, pid in past][-RECENT_STARTS:]
        theirs = min(recent.count(last_id) / len(recent), 1 - share, 0.95)
        p = min(share * (1 - with_odds(theirs, odds)) / (1 - theirs), CONFIRMED_START)
        return Availability(p, f"~{p:.0%} to start (other goalie after {how})")
    return Availability(share, f"~{share:.0%} to start (not confirmed yet)")


def with_odds(p: float, multiplier: float) -> float:
    """`p` with its odds multiplied."""
    if p <= 0 or p >= 1:
        return p
    odds = p / (1 - p) * multiplier
    return odds / (1 + odds)


def start_result(start, relieved: bool) -> str:
    """How a start went, as LAST_RESULT_ODDS keys it: "W", "L", or "L bad"
    (5+ goals against, or pulled); overtime losses are losses. `start` is a GoalieGame."""
    if start.stats["w"]:
        return "W"
    return "L bad" if relieved or start.stats["ga"] >= BAD_START_GA else "L"


def start_share(
    player_id: int,
    date: dt.date,
    info: LineInfo | None,
    team_starts: list[tuple[dt.date, int]],
    prior_share: float | None,
    absent: float = 0.0,
    rivals: float = 0.0,
) -> float:
    """His share of his team's starts: the last 10 blended with a prior, plus
    his part of `absent` (the starts injured teammates would have made) by his
    share against `rivals` (his healthy teammates' shares)."""
    if prior_share is None:
        prior_share = DEPTH_SHARE.get(info.goalie_depth, UNKNOWN_SHARE) if info else UNKNOWN_SHARE
    recent = [pid for d, pid in team_starts if d < date][-RECENT_STARTS:]
    share = (SHARE_PRIOR_GAMES * prior_share + recent.count(player_id)) / (SHARE_PRIOR_GAMES + len(recent))
    if absent > 0 and share + rivals > 0:
        share = min(share + absent * share / (share + rivals), CONFIRMED_START)
    return share


def absences(player_id: int, date: dt.date, days_ahead: int, teammates: list[tuple], team_starts,
             share_of) -> tuple[float, float]:
    """(absent, rivals) for `start_share`: `teammates` are his team's other
    goalies as (id, LineInfo or None, games missed in a row); `share_of(id, info)`
    is a goalie's own blended share. An injured teammate's share counts as absent
    times his odds of still being out that day (his return curve), times
    ABSENT_TO_TEAMMATES; with a news timeline (`news_back`), times
    ABSENT_TO_TEAMMATES_KNOWN. A healthy one's is a rival's."""
    absent = rivals = 0.0
    for pid, info, missed in teammates:
        if pid == player_id:
            continue
        share = share_of(pid, info)
        injured = _injured(info)
        if injured or _sidelined_dtd(info):
            news = news_back(info, date - dt.timedelta(days=days_ahead), days_ahead) if injured else None
            if news is None:
                back = ahead(_curve(injured, missed) if injured else DTD_RETURN, days_ahead, 0.0)
                absent += ABSENT_TO_TEAMMATES * share * (1 - back)
            else:
                absent += ABSENT_TO_TEAMMATES_KNOWN * share * (1 - news)
        else:
            rivals += share
    return absent, rivals


def second_of_back_to_back(share: float) -> float:
    """Start odds for the second night of a back-to-back that's still days
    away, when nobody knows yet who starts the first night."""
    return share * share * BACK_TO_BACK_REPEAT + (1 - share) * (1 - (1 - share) * BACK_TO_BACK_REPEAT)
