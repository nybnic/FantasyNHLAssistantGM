import datetime as dt

import pytest

from clients.dfo_lines import LineInfo
from engine import availability

TONIGHT = dt.date(2026, 11, 10)


def test_skater_injury_and_lineup_status():
    assert availability.skater(LineInfo(groups={"f1"}), True).prob == availability.HEALTHY_PLAY
    assert availability.skater(LineInfo(groups={"ir"}, injury="dtd"), True).prob == 0.0
    assert availability.skater(LineInfo(groups={"f3"}, injury="dtd"), True).prob == availability.DOUBTFUL_PLAY
    assert availability.skater(None, True).prob == availability.UNLISTED_PLAY
    # No chart for his team (fetch failed): unlisted means nothing.
    assert availability.skater(None, False).prob == availability.HEALTHY_PLAY


def test_goalie_uses_dailyfaceoff_when_it_names_a_starter():
    confirmed = {"goalie_name": "Jake Oettinger", "confirmed": True}
    assert availability.goalie(1, "Jake Oettinger", TONIGHT, None, confirmed, [], 0.7).prob == 0.97
    other = availability.goalie(2, "Casey DeSmith", TONIGHT, None, confirmed, [], 0.3)
    assert other.prob == pytest.approx(0.03)
    assert "Oettinger confirmed" in other.note


def test_goalie_start_share_blends_prior_with_recent_starts():
    starts = [(TONIGHT - dt.timedelta(days=d), 1) for d in range(20, 10, -1)]  # 10 straight starts
    share = availability.goalie(1, "A", TONIGHT, None, None, starts, 0.5).prob
    assert share == pytest.approx((4 * 0.5 + 10) / 14)


def test_back_to_back_flips_the_odds():
    starts = [(TONIGHT - dt.timedelta(days=3), 1), (TONIGHT - dt.timedelta(days=1), 1)]
    started_last_night = availability.goalie(1, "A", TONIGHT, None, None, starts, 0.7)
    backup = availability.goalie(2, "B", TONIGHT, None, None, starts, 0.3)
    assert started_last_night.prob < 0.35
    assert backup.prob > 0.6


def test_injured_goalie_never_starts():
    info = LineInfo(groups={"g"}, goalie_depth=1, injury="out")
    assert availability.goalie(1, "A", TONIGHT, info, None, [], 0.7).prob == 0.0


def test_tonights_status_fades_along_the_return_curves():
    out, ir = LineInfo(groups={"f2"}, injury="out"), LineInfo(groups={"ir"}, injury="ir")
    short, long_ = availability.return_curve(1), availability.return_curve(20)
    assert availability.skater(out, True).prob == 0.0
    assert availability.skater(out, True, days_ahead=2).prob == short[0]  # out tonight: a 1-game absence
    assert availability.skater(out, True, days_ahead=10).prob == short[2]
    assert availability.skater(out, True, days_ahead=10, missed=19).prob == long_[2]  # out a month: slower
    # Just placed on IR: at least the 3-5 game curve. Past six weeks the last value holds.
    assert availability.skater(ir, True, days_ahead=10).prob == availability.return_curve(3)[2]
    assert availability.skater(ir, True, days_ahead=90, missed=19).prob == long_[-1]
    assert availability.skater(None, True, days_ahead=4).prob == short[1]  # scratched tonight
    dtd = LineInfo(groups={"f3"}, injury="dtd")
    assert availability.skater(dtd, True, 1).prob == 0.85
    assert availability.skater(dtd, True, 3).prob == availability.HEALTHY_PLAY
    # An injured goalie starts at his usual share once back.
    goalie = availability.goalie(1, "A", TONIGHT, ir, None, [], 0.6, days_ahead=30)
    assert goalie.prob == pytest.approx(availability.return_curve(3)[-1] * 0.6)
    assert availability.goalie(1, "A", TONIGHT, ir, None, [], 0.6).prob == 0.0


def test_return_curves_are_slower_the_longer_the_absence():
    curves = [curve for _, curve in availability.RETURN_CURVES]
    assert all(a > b for earlier, later in zip(curves, curves[1:]) for a, b in zip(earlier, later))
    assert all(list(c) == sorted(c) for c in curves)  # and rise with time
