"""engine/tenure.py: how long an add stays, by where he sits on my roster."""
import pytest

from engine import tenure


def test_kaplan_meier_counts_the_seasons_end_as_censoring_not_a_drop():
    # Three adds with 10 weeks left: gone after 1 week, after 3, still there at the end (10 = censored).
    # One with 2 weeks left, held both: censored, so it isn't a drop at week 2.
    curve = tenure.fit([("b", 1, 10), ("b", 3, 10), ("b", 10, 10), ("b", 2, 2)], horizon=4)["b"]
    # Week 1: all 4 at risk (the 2-week one has a week 2), one ends: 3/4.
    assert curve[0] == pytest.approx(0.75)
    # Week 2: at risk the two held 2+ weeks that have a week 3 (the censored one hasn't): none end.
    assert curve[1] == pytest.approx(0.75)
    # Week 3: two at risk (held 3 and 10), one ends: 0.75 x 1/2.
    assert curve[2] == pytest.approx(0.375) and curve[3] == pytest.approx(0.375)


def test_survival_is_one_in_the_first_week_and_holds_its_last_value_beyond_the_curve():
    table = {"b": (0.8, 0.5)}
    assert tenure.survival("b", 0, table) == 1.0 and tenure.survival("b", 1, table) == 0.8
    assert tenure.survival("b", 9, table) == 0.5
    assert tenure.survival("unknown", 3, table) == 1.0  # no curve: the whole season, as before a fit


def test_bins_and_how_far_off_gaps_realize():
    assert [tenure.skater_bin(n) for n in (0, 1, 2, 3, 5, 6, 12)] == [
        "skater, lowest", "skater, 2nd lowest", "skater, 3rd lowest", "skater, 4th-6th", "skater, 4th-6th",
        "skater, 7th+", "skater, 7th+"]
    assert tenure.realizes(3) == 0.84 and tenure.realizes(12) == 0.76 and tenure.realizes(40) == 0.74
