import datetime as dt

from bot import goalie_news, weekly
from clients import goalie_client, nhl_client
from league.roster import RosterPlayer
from state import gm_state

UTC = dt.timezone.utc
TUESDAY = dt.date(2026, 10, 27)  # week 5
GAME = nhl_client.ScheduledGame(1, dt.datetime(2026, 10, 27, 23, tzinfo=UTC), "TOR", "OTT")  # 01:00 Helsinki


def _setup(monkeypatch, tmp_path, starters):
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [GAME] if d == TUESDAY else [])
    reads = []
    monkeypatch.setattr(goalie_client, "get_starters",
                        lambda date, max_age=1800: reads.append(max_age) or dict(starters))
    state = gm_state.load(tmp_path / "state.json")
    players = [RosterPlayer(1, "A", "TOR", ["G"], "G")]
    return state, players, reads


def test_goalie_news_is_read_fresh_from_19_to_22_local(monkeypatch, tmp_path):
    state, players, reads = _setup(monkeypatch, tmp_path, {})
    for hour, minute in ((15, 59), (20, 0)):  # 17:59 and 22:00 Helsinki
        goalie_news.news_step(state, players, dt.datetime(2026, 10, 27, hour, minute, tzinfo=UTC))
    assert reads == []
    for hour in (17, 19):  # 19:00 and 21:00
        goalie_news.news_step(state, players, dt.datetime(2026, 10, 27, hour, tzinfo=UTC))
    assert reads == [goalie_news.FRESH] * 2


def test_a_new_or_confirmed_starter_weighs_the_plan_again(monkeypatch, tmp_path):
    likely = {"TOR": {"goalie_name": "Joseph Woll", "confirmed": False}}
    state, players, _ = _setup(monkeypatch, tmp_path, likely)
    at = dt.datetime(2026, 10, 27, 17, tzinfo=UTC)
    goalie_news.news_step(state, players, at)
    assert state["goalie_recheck"] and state["goalie_news"] == {"date": "2026-10-27", "starters": likely}

    state["goalie_recheck"] = False
    goalie_news.news_step(state, players, at + dt.timedelta(minutes=30))  # nothing new
    assert not state["goalie_recheck"]

    confirmed = {"TOR": {"goalie_name": "Joseph Woll", "confirmed": True}}
    monkeypatch.setattr(goalie_client, "get_starters", lambda date, max_age=1800: dict(confirmed))
    goalie_news.news_step(state, players, at + dt.timedelta(hours=1))
    assert state["goalie_recheck"] and state["goalie_news"]["starters"] == confirmed

    runs = []
    monkeypatch.setattr(weekly, "run_plan", lambda *a: runs.append(a[-1]))
    weekly.plan_check_step(state, players, {"teams": {}, "taken": []}, at + dt.timedelta(hours=1), None)
    assert runs == ["quiet"] and not state["goalie_recheck"]
    assert state["news_checked"] == "2026-10-27"  # 20:00 Helsinki: the evening's check too


def test_an_empty_read_keeps_what_was_seen(monkeypatch, tmp_path):
    state, players, _ = _setup(monkeypatch, tmp_path, {})
    state["goalie_news"] = {"date": "2026-10-27", "starters": {"TOR": {"goalie_name": "Woll", "confirmed": True}}}
    goalie_news.news_step(state, players, dt.datetime(2026, 10, 27, 18, tzinfo=UTC))
    assert state["goalie_news"]["starters"] and not state["goalie_recheck"]
