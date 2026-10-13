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


def test_dfo_status_decides_not_its_injured_section():
    # Celebrini, Oct 10: in DFO's injured section, "dtd". Out tonight (off the chart), then a day-to-day return.
    listed_dtd = LineInfo(groups={"ir"}, injury="dtd")
    assert availability.skater(listed_dtd, True, days_ahead=3, missed=2).prob == availability.HEALTHY_PLAY
    assert availability.skater(listed_dtd, True, days_ahead=1).prob == availability.DTD_RETURN[0]
    listed_out = LineInfo(groups={"ir"}, injury="out")  # out: the curve for the games he's missed, no IR floor
    assert availability.skater(listed_out, True, days_ahead=2).prob == availability.return_curve(1)[0]
    no_status = LineInfo(groups={"ir"})  # the section alone: IR
    assert availability.skater(no_status, True, days_ahead=2).prob == availability.return_curve(3)[0]
    goalie = LineInfo(groups={"ir"}, injury="dtd")
    assert availability.goalie(1, "A", TONIGHT, goalie, None, [], 0.6).prob == 0.0
    assert availability.goalie(1, "A", TONIGHT, goalie, None, [], 0.6, days_ahead=3).prob > 0.5


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


def test_a_backup_blown_out_last_game_hands_the_next_start_back():
    # Knight started 7 of 10, then his backup gave up 6: Knight's odds rise, the backup's fall.
    starters = [1, 1, 1, 2, 1, 1, 2, 1, 1, 2]  # every other day, the last two days ago
    starts = [(TONIGHT - dt.timedelta(days=2 * (10 - i)), pid) for i, pid in enumerate(starters)]
    plain_knight = availability.goalie(1, "Knight", TONIGHT, None, None, starts, 0.65)
    plain_backup = availability.goalie(2, "Backup", TONIGHT, None, None, starts, 0.35)
    knight = availability.goalie(1, "Knight", TONIGHT, None, None, starts, 0.65, last_result="L bad")
    backup = availability.goalie(2, "Backup", TONIGHT, None, None, starts, 0.35, last_result="L bad")
    assert backup.prob == pytest.approx(availability.with_odds(plain_backup.prob, 0.32))
    assert backup.prob < plain_backup.prob and "after a blowout loss" in backup.note
    assert knight.prob > plain_knight.prob and "other goalie after a blowout loss" in knight.note
    # A win changes nothing; DailyFaceoff's pick still beats it all.
    assert availability.goalie(2, "Backup", TONIGHT, None, None, starts, 0.35, last_result="W").prob == plain_backup.prob
    assert availability.goalie(2, "Backup", TONIGHT, None, {"goalie_name": "Backup", "confirmed": True}, starts, 0.35,
                               last_result="L bad").prob == availability.CONFIRMED_START


def test_after_a_loss_the_pair_still_sums_to_one_early_in_the_season():
    # Two starts so far, one each (a raw 50%); the backup lost the last one badly.
    starts = [(TONIGHT - dt.timedelta(days=4), 1), (TONIGHT - dt.timedelta(days=2), 2)]
    knight = availability.goalie(1, "Knight", TONIGHT, None, None, starts, 0.62, last_result="L bad").prob
    backup = availability.goalie(2, "Backup", TONIGHT, None, None, starts, 0.38, last_result="L bad").prob
    assert knight + backup == pytest.approx(1.0, abs=0.02)


def test_start_result_reads_a_goalie_game():
    from clients.nhl_stats import GoalieGame

    def start(w, ga):
        return GoalieGame(1, "A", "BOS", "TOR", TONIGHT, 1, True, True, {"w": w, "ga": ga})
    assert availability.start_result(start(1, 5), relieved=False) == "W"
    assert availability.start_result(start(0, 3), relieved=False) == "L"
    assert availability.start_result(start(0, 5), relieved=False) == "L bad"
    assert availability.start_result(start(0, 2), relieved=True) == "L bad"


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


def test_an_injured_starters_starts_go_to_his_healthy_teammates(monkeypatch):
    # Garand (backup, share 0.25) behind Shesterkin (0.70, out on IR), a third goalie at 0.05.
    from clients.dfo_lines import LineInfo
    day = dt.date(2026, 10, 14)
    base = availability.start_share(2, day, None, [], 0.25)
    assert availability.start_share(2, day, None, [], 0.25, absent=0.7, rivals=0.05) == pytest.approx(
        base + 0.7 * base / (base + 0.05))
    assert availability.start_share(2, day, None, [], 0.25, absent=5.0, rivals=0.0) == availability.CONFIRMED_START

    shares = {1: 0.70, 2: 0.25, 3: 0.05}
    mates = [(1, LineInfo(groups={"ir"}, injury="ir"), 20), (2, LineInfo(groups={"g"}), 0), (3, None, 0)]

    def share_of(pid, info):
        return shares[pid]

    monkeypatch.setattr(availability, "ABSENT_TO_TEAMMATES", 0.0)
    assert availability.absences(2, day, 0, mates, [], share_of) == (0.0, 0.05)  # off: nothing freed
    monkeypatch.setattr(availability, "ABSENT_TO_TEAMMATES", 1.0)
    absent, rivals = availability.absences(2, day, 0, mates, [], share_of)
    assert (absent, rivals) == (pytest.approx(0.70), 0.05)  # out tonight: all of his share
    later, _ = availability.absences(2, day, 30, mates, [], share_of)
    curve = availability.return_curve(max(21, availability.IR_MIN_MISSED))
    assert later == pytest.approx(0.70 * (1 - availability.ahead(curve, 30, 0.0)))  # some chance he's back
    assert later < absent


def test_a_news_timeline_beats_the_return_curve():
    out_season = LineInfo(groups={"ir"}, injury="out", out_until=dt.date.max)
    four_weeks = LineInfo(groups={"ir"}, injury="ir", out_until=TONIGHT + dt.timedelta(days=28))
    curve_only = LineInfo(groups={"ir"}, injury="ir")
    assert availability.skater(out_season, True, 40, 2, TONIGHT).prob == 0.0
    assert availability.skater(four_weeks, True, 20, 0, TONIGHT).prob == 0.0
    after = availability.skater(four_weeks, True, 30, 0, TONIGHT).prob
    assert 0 < after == availability.ahead(availability.return_curve(availability.IR_MIN_MISSED), 3, 0.0)
    # No timeline, or no date to read it against: the return curve as before.
    assert availability.skater(curve_only, True, 20, 0, TONIGHT).prob == availability.skater(curve_only, True, 20).prob
    assert availability.skater(four_weeks, True, 20).prob == availability.skater(curve_only, True, 20).prob
    # A goalie out for the season starts nothing, however far ahead.
    later = TONIGHT + dt.timedelta(days=40)
    assert availability.goalie(1, "Igor Shesterkin", later, out_season, None, [], 0.7, days_ahead=40).prob == 0.0


def test_a_teammate_known_out_frees_more_of_his_starts():
    curve = LineInfo(groups={"ir"}, injury="out")
    season = LineInfo(groups={"ir"}, injury="out", out_until=dt.date.max)

    def share_of(pid, info):
        return 0.7

    by_curve, _ = availability.absences(2, TONIGHT, 7, [(1, curve, 2)], [], share_of)
    by_news, _ = availability.absences(2, TONIGHT, 7, [(1, season, 2)], [], share_of)
    assert by_news == pytest.approx(availability.ABSENT_TO_TEAMMATES_KNOWN * 0.7)
    assert by_curve < by_news
