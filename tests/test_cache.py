import os
import time

import pytest

from clients import cache


def _cached(tmp_path, monkeypatch, hours_old: float):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    path = tmp_path / "schedule.json"
    path.write_text('["old"]', encoding="utf-8")
    then = time.time() - hours_old * cache.HOUR
    os.utime(path, (then, then))


def _down():
    raise ConnectionError("NHL API down")


def test_a_failed_refetch_falls_back_to_a_recent_copy(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch, hours_old=5)
    assert cache.cached_json("schedule", 3 * cache.HOUR, _down) == ["old"]
    assert cache.cached_json("schedule", 3 * cache.HOUR, lambda: ["new"]) == ["new"]  # and refreshes when it can


def test_a_failed_refetch_with_only_an_old_copy_still_fails(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch, hours_old=49)
    with pytest.raises(ConnectionError):
        cache.cached_json("schedule", 3 * cache.HOUR, _down)


def test_a_failed_first_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    with pytest.raises(ConnectionError):
        cache.cached_json("schedule", 3 * cache.HOUR, _down)


def test_the_latest_stats_window_is_refetched_hourly(monkeypatch):
    """A plan run after the last puck fetches a night whose games may not all
    be in: that fetch mustn't stand for 6 hours (bot.common.nhl_date)."""
    import datetime as dt
    from clients import nhl_stats
    ages = []
    monkeypatch.setattr(nhl_stats, "cached_json", lambda name, max_age, fetch, **k: ages.append(max_age) or [])
    today = dt.date(2026, 10, 15)
    for end in (today, today - dt.timedelta(days=1), today - dt.timedelta(days=2), today - dt.timedelta(days=5)):
        nhl_stats._report_rows("skater/summary", 20262027, end - dt.timedelta(days=19), end, today)
    assert ages == [cache.HOUR, cache.HOUR, 6 * cache.HOUR, None]


def _answers(monkeypatch, *statuses):
    calls = []

    def fake_get(*a, **k):
        resp = cache.requests.Response()
        resp.status_code = statuses[len(calls)]
        calls.append(resp.status_code)
        return resp
    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    return calls


def test_a_server_error_is_tried_again(monkeypatch):
    calls = _answers(monkeypatch, 503, 200)
    assert cache.get("https://example.com").status_code == 200
    assert calls == [503, 200]


def test_a_refusal_is_not_tried_again(monkeypatch):
    """A 403 (DFO's bot check, 2026-11-04) won't pass in 6 s."""
    calls = _answers(monkeypatch, 403, 200)
    with pytest.raises(cache.requests.HTTPError):
        cache.get("https://example.com")
    assert calls == [403]


def _paged_api(monkeypatch, total: int, short_by: int = 0):
    """The stats API since 2026-11-20: at most 100 rows a request, whatever `limit` asks."""
    from clients import nhl_stats
    rows = [{"playerId": i, "gameId": 1, "gameDate": "2026-11-16"} for i in range(total)]
    asked = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def fake_get(url, params):
        asked.append(params["start"])
        start = params["start"]
        page = rows[start:start + 100]
        if short_by and start + 100 >= total:
            page = page[:-short_by]
        return Resp({"data": page, "total": total})
    monkeypatch.setattr(nhl_stats, "get", fake_get)
    return asked


def test_stats_are_read_page_by_page(monkeypatch):
    """2026-11-21: `limit=-1` began returning 100 rows of 4,032, so the week's
    box scores banked 3-0 against Yahoo's 149-120."""
    from clients import nhl_stats
    asked = _paged_api(monkeypatch, 250)
    rows = nhl_stats._all_rows("skater/summary", {}, nhl_stats.GAME_SORT)
    assert [r["playerId"] for r in rows] == list(range(250))
    assert sorted(asked) == [0, 100, 200]


def test_a_short_answer_is_never_taken_as_complete(monkeypatch):
    from clients import nhl_stats
    _paged_api(monkeypatch, 250, short_by=10)
    with pytest.raises(nhl_stats.PartialReport):
        nhl_stats._all_rows("skater/summary", {}, nhl_stats.GAME_SORT)


def test_a_cached_single_page_is_fetched_again(tmp_path, monkeypatch):
    """The truncated copies cached on 2026-11-20/21 (exactly one page) are replaced however fresh."""
    import json
    from clients import nhl_stats
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    (tmp_path / "w.json").write_text(json.dumps([{}] * nhl_stats.PAGE_ROWS), encoding="utf-8")
    assert cache.cached_json("w", cache.HOUR, lambda: [1, 2], valid=nhl_stats._not_one_page) == [1, 2]
    assert cache.cached_json("w", cache.HOUR, lambda: [3], valid=nhl_stats._not_one_page) == [1, 2]
    (tmp_path / "w.json").write_text(json.dumps([{}] * nhl_stats.PAGE_ROWS), encoding="utf-8")
    with pytest.raises(ConnectionError):  # nor kept as a fallback
        cache.cached_json("w", cache.HOUR, _down, valid=nhl_stats._not_one_page)


def _stats_window(tmp_path, monkeypatch, end: str, dates: list[str]):
    """A cached goalie/summary window of 2026-27 from Nov 4 to `end`, one row per date."""
    import json
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    folder = tmp_path / "nhl_stats"
    folder.mkdir(exist_ok=True)
    rows = [{"gameDate": d, "playerId": i} for i, d in enumerate(dates)]
    (folder / f"goalie_summary_20262027_2026-11-04_{end}.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _answer(monkeypatch, dates: list[str]):
    from clients import nhl_stats

    class Resp:
        def json(self):
            return {"data": [{"gameDate": d, "playerId": i} for i, d in enumerate(dates)]}
    monkeypatch.setattr(nhl_stats, "get", lambda *a, **k: Resp())


def test_a_stats_window_that_comes_back_short_keeps_the_fuller_copy(tmp_path, monkeypatch):
    """2026-11-20 06:42 UTC: the API sent this week's goalie games missing, the bot
    counted 0 of the 3 needed and told Nico to pick up a goalie."""
    import datetime as dt
    from clients import health, nhl_stats
    health.clear()
    full = _stats_window(tmp_path, monkeypatch, "2026-11-19", ["2026-11-05", "2026-11-16", "2026-11-17", "2026-11-18"])
    _answer(monkeypatch, ["2026-11-05", "2026-11-19"])  # Nov 16-18 gone
    today = dt.date(2026, 11, 20)
    rows = nhl_stats._report_rows("goalie/summary", 20262027, dt.date(2026, 11, 4), today, today)
    assert rows == full
    assert "NHL stats" in health.problems()
    health.clear()


def test_a_stats_window_that_grows_is_taken(tmp_path, monkeypatch):
    import datetime as dt
    from clients import nhl_stats
    _stats_window(tmp_path, monkeypatch, "2026-11-19", ["2026-11-05", "2026-11-16"])
    _answer(monkeypatch, ["2026-11-05", "2026-11-16", "2026-11-19"])
    today = dt.date(2026, 11, 20)
    rows = nhl_stats._report_rows("goalie/summary", 20262027, dt.date(2026, 11, 4), today, today)
    assert [r["gameDate"] for r in rows] == ["2026-11-05", "2026-11-16", "2026-11-19"]


def test_a_first_fetch_of_a_stats_window_is_taken(tmp_path, monkeypatch):
    import datetime as dt
    from clients import nhl_stats
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    _answer(monkeypatch, ["2026-11-05"])
    today = dt.date(2026, 11, 20)
    assert len(nhl_stats._report_rows("goalie/summary", 20262027, dt.date(2026, 11, 4), today, today)) == 1


def test_a_short_copy_saved_today_loses_to_a_fuller_one(tmp_path, monkeypatch):
    """2026-11-21: the 20 Nov copy, saved short before the check existed, was the
    latest; the 19 Nov one, full, must still win."""
    import datetime as dt
    from clients import health, nhl_stats
    health.clear()
    full = _stats_window(tmp_path, monkeypatch, "2026-11-19", ["2026-11-05", "2026-11-16", "2026-11-17"])
    _stats_window(tmp_path, monkeypatch, "2026-11-20", ["2026-11-05"])
    _stats_window(tmp_path, monkeypatch, "2026-11-21", ["2026-11-05"])  # fresh, so not refetched
    today = dt.date(2026, 11, 21)
    assert nhl_stats._report_rows("goalie/summary", 20262027, dt.date(2026, 11, 4), today, today) == full
    health.clear()


def test_a_capped_answer_is_paged(tmp_path, monkeypatch):
    import datetime as dt
    from clients import nhl_stats
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    every = [{"gameDate": "2026-11-05", "playerId": i, "gameId": 1} for i in range(5)]
    asked = []

    class Resp:
        def __init__(self, params):
            self.params = params

        def json(self):
            start, size = self.params.get("start", 0), 2  # the API serves at most 2 rows
            return {"data": every[start:start + size], "total": len(every)}

    monkeypatch.setattr(nhl_stats, "get", lambda url, params: asked.append(params) or Resp(params))
    today = dt.date(2026, 11, 20)
    rows = nhl_stats._report_rows("goalie/summary", 20262027, dt.date(2026, 11, 4), today, today)
    assert sorted(r["playerId"] for r in rows) == [0, 1, 2, 3, 4]
    assert all("sort" in p for p in asked[1:])
