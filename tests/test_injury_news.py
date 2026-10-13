"""clients/injury_news.py: an expected return read from injury news."""
import datetime as dt

from clients.injury_news import SEASON, out_until

POSTED = dt.date(2026, 10, 10)


def test_plain_timelines_are_read():
    assert out_until("Shesterkin (lower-body) will miss the rest of the 2026-27 season. \r\n", POSTED) == SEASON
    assert out_until("Korpisalo (lower-body) is expected to be out for four more weeks.", POSTED) == \
        POSTED + dt.timedelta(days=28)
    # A range counts from its low end.
    assert out_until("Veleno (lower-body) will be placed on LTIR and is expected to miss 3-to-3.5 months.",
                     POSTED) == POSTED + dt.timedelta(days=90)
    assert out_until("He is expected to miss 4-6 weeks.", POSTED) == POSTED + dt.timedelta(days=28)
    assert out_until("out at least 10 days with an upper-body injury", POSTED) == POSTED + dt.timedelta(days=10)
    assert out_until("suffered a season-ending knee injury", POSTED) == SEASON


def test_no_timeline_leaves_it_to_the_return_curves():
    assert out_until("He is day-to-day with a lower-body injury.", POSTED) is None
    assert out_until("Listed as week-to-week.", POSTED) is None
    assert out_until("He has missed the last two games.", POSTED) is None
    assert out_until("", POSTED) is None and out_until(None, POSTED) is None


def test_a_number_word_inside_another_word_is_no_number():
    assert out_until("He needs an extra week of rest.", POSTED) is None  # "extr-a week"
    assert out_until("Expected back in a week or two.", POSTED) == POSTED + dt.timedelta(days=7)


def test_dfo_line_info_carries_the_news_timeline(monkeypatch):
    from clients import dfo_lines
    monkeypatch.setattr(dfo_lines, "rows", lambda team: [
        {"name": "Igor Shesterkin", "group": "ir", "slot": "ir1", "injury": "out", "gtd": False,
         "news": "Shesterkin (lower-body) will miss the rest of the 2026-27 season.", "news_at": "2026-10-10"},
        {"name": "Dylan Garand", "group": "g", "slot": "g1", "injury": None, "gtd": False,
         "news": "Garand will start for the next month.", "news_at": "2026-10-11"},
        {"name": "Old Row", "group": "f4", "slot": "lw4", "injury": None, "gtd": False},  # cached before news
    ])
    lines = dfo_lines.team_lines("NYR")
    assert lines["igor shesterkin"].out_until == SEASON
    assert lines["dylan garand"].out_until is None  # healthy: his news is no injury timeline
    assert lines["old row"].out_until is None
