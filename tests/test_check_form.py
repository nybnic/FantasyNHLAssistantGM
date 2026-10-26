import datetime as dt

import pytest

from scripts import check_form


def _row(monday, pid, form, miss, n=3, group="F", proj=5.0):
    return {"monday": monday, "pid": pid, "group": group, "form": form,
            "proj": {k: proj for k in check_form.K_SCALES}, **{w: (n, proj + miss) for w in check_form.WINDOWS}}


def test_the_slope_reads_how_much_of_a_start_keeps_showing():
    mondays = [dt.date(2025, 10, 20) + dt.timedelta(days=7 * i) for i in range(3)]
    rows = [_row(m, pid, form, 0.5 * form + 0.1 * ((pid * 7 + i) % 3 - 1))
            for i, m in enumerate(mondays) for pid, form in enumerate([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0] * 2)]
    b, se, n = check_form.slope(rows, "next week")
    assert b == pytest.approx(0.5, abs=0.03) and 0 < se < 0.05 and n == 42
    # A level shift on one Monday (everyone over) is not form: demeaned within the Monday.
    shifted = [{**r, "next week": (r["next week"][0], r["next week"][1] + (3.0 if r["monday"] == mondays[0] else 0))}
               for r in rows]
    assert check_form.slope(shifted, "next week")[0] == pytest.approx(b)


def test_accuracy_scores_mae_pairwise_and_bias():
    m = dt.date(2025, 10, 20)
    rows = [_row(m, 1, 0.0, -1.0, proj=6.0), _row(m, 2, 0.0, 1.0, proj=4.0)]  # actuals 5.0 and 5.0
    rows[1]["next week"] = (3, 4.5)
    mae, pair, bias = check_form.accuracy(rows, "next week", 1.0)
    assert mae == pytest.approx(0.75) and pair == 1.0 and bias == pytest.approx(0.25)
