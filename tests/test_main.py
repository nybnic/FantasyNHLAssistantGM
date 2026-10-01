import datetime as dt

import pytest

import main
from bot import common, daily, ingest, weekly
from clients import goalie_client, nhl_client, screenshot
from config.settings import load_settings
from engine import matchup, report
from league import parse, teams, weeks
from league.roster import RosterPlayer
from notify import charts, telegram
from state import gm_state

NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)


def _setup(monkeypatch, tmp_path, updates):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent, handled = [], []
    monkeypatch.setattr(telegram, "get_updates", lambda token, offset: updates)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(a[2]) or 1)
    monkeypatch.setattr(telegram, "mark_handled", lambda token, chat, mid, label: handled.append(label))
    monkeypatch.setattr(telegram, "answer_callback", lambda *a: None)
    settings = load_settings()
    state = gm_state.load(tmp_path / "state.json")
    state["pending"]["lineup-2026-11-10-2100"] = {
        "type": "lineup", "date": "2026-11-10", "assignment": {"1": "BN", "2": "C"}, "message_id": 7,
    }
    players = [RosterPlayer(1, "A", "BOS", ["C"], "C"), RosterPlayer(2, "B", "TOR", ["C"], "BN")]
    return settings, state, players, sent, handled


def _tap(data, chat_id=42, update_id=5):
    return {"update_id": update_id, "callback_query": {
        "id": "cb", "data": data, "message": {"message_id": 7, "chat": {"id": chat_id}}}}


def test_done_applies_the_recommended_lineup(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("done:lineup-2026-11-10-2100")])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [p.slot for p in players] == ["BN", "C"]
    assert state["pending"] == {}
    assert state["decisions"][0]["decision"] == "done"
    assert state["telegram_offset"] == 6
    assert handled == ["Recorded: Done"]


def test_skip_leaves_the_roster_alone(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("skip:lineup-2026-11-10-2100")])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert handled == ["Recorded: Skipped"]


def test_taps_from_other_chats_are_ignored(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:lineup-2026-11-10-2100", chat_id=999)])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert "lineup-2026-11-10-2100" in state["pending"]


def test_chat_id_secret_is_whitespace_tolerant(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/start"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " 42\n")
    settings = load_settings()
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert sent == [ingest.HELP]


def test_messages_from_other_chats_are_logged(monkeypatch, tmp_path, caplog):
    message = {"update_id": 9, "message": {"chat": {"id": 999}, "text": "/start"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert sent == []
    assert "chat ...999: TELEGRAM_CHAT_ID is ...42" in caplog.text


def test_roster_command_replies_with_the_roster(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/roster"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert "A (BOS, C)" in sent[0] and "/myteam" in sent[0]


def _relay(monkeypatch, updates, dispatch_error=None):
    monkeypatch.setenv("RELAY_URL", "https://relay.example/")
    monkeypatch.setenv("RELAY_TOKEN", "r")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "w")
    calls = []

    def relayed(url, token, offset):
        calls.append((url, token, offset))
        return updates, dispatch_error
    monkeypatch.setattr(telegram, "get_relayed_updates", relayed)
    return calls


def test_relay_replaces_polling_when_configured(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/help"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    calls = _relay(monkeypatch, [message])
    settings = load_settings()
    assert ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings)) is None
    assert calls == [("https://relay.example", "r", 0)]
    assert sent == [ingest.HELP]
    assert state["telegram_offset"] == 10


def test_relay_dispatch_errors_alert_once_a_day(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [], dispatch_error="GitHub 401: Bad credentials")
    settings = load_settings()
    outbox = common.Outbox(settings)
    for _ in range(2):
        problem = ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, outbox)
        ingest.report_relay(problem, state, outbox, NOW)
    assert len(sent) == 1 and "GitHub 401" in sent[0]


def _webhook(monkeypatch, info):
    calls = []
    monkeypatch.setattr(telegram, "webhook_info", lambda token: info)
    monkeypatch.setattr(telegram, "set_webhook", lambda token, url, secret: calls.append(("set", url, secret)))
    monkeypatch.setattr(telegram, "delete_webhook", lambda token: calls.append(("delete",)))
    return calls



def test_webhook_follows_the_relay_setting(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [])
    calls = _webhook(monkeypatch, {"url": ""})
    assert ingest.sync_webhook(load_settings(), NOW) is None
    assert calls == [("set", "https://relay.example/telegram", "w")]

    monkeypatch.delenv("RELAY_URL")
    calls = _webhook(monkeypatch, {"url": "https://relay.example/telegram"})
    ingest.sync_webhook(load_settings(), NOW)
    assert calls == [("delete",)]


def test_webhook_backlog_with_a_recent_error_is_a_problem(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [])
    info = {"url": "https://relay.example/telegram", "pending_update_count": 2,
            "last_error_date": int(NOW.timestamp()) - 60, "last_error_message": "Connection timed out"}
    calls = _webhook(monkeypatch, info)
    assert "Connection timed out" in ingest.sync_webhook(load_settings(), NOW)
    assert calls == []

    info["last_error_date"] -= 7200  # an old error with a fresh message in flight is fine
    assert ingest.sync_webhook(load_settings(), NOW) is None


REGISTRY = [{"id": 10, "name": "Nick Suzuki", "team": "MTL", "position": "C"},
            {"id": 11, "name": "Joey Daccord", "team": "SEA", "position": "G"}]


def _message(text, update_id=9):
    return {"update_id": update_id, "message": {"chat": {"id": 42}, "text": text}}


def test_opp_then_paste_updates_this_weeks_opponent(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(
        monkeypatch, tmp_path, [_message("/opp", 9), _message("Nick SuzukiPlayer NoteMTL - C", 10)])
    monkeypatch.setattr(parse, "registry", lambda: REGISTRY)
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2026, 10, 1))
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert [p["name"] for p in league["teams"]["Bahelin Boys"]["players"]] == ["Nick Suzuki"]
    assert state["awaiting"] is None
    assert "Bahelin Boys: 1 players saved" in sent[1]


def test_opp_with_a_team_name_in_the_playoffs_records_the_opponent(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(
        monkeypatch, tmp_path, [_message("/opp vantaa\nNick Suzuki (MTL - C)")])
    monkeypatch.setattr(parse, "registry", lambda: REGISTRY)
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2027, 3, 16))
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert state["opponents"] == {"24": "Vantaa"}
    assert "Vantaa" in league["teams"]
    assert state["week_requested"]  # the playoff week's plan follows


def test_taken_removes_a_player_from_the_free_agents(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [_message("/taken Joey Daccord")])
    monkeypatch.setattr(parse, "registry", lambda: REGISTRY)
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert league["taken"] == [11]


def test_done_on_an_add_swaps_the_players(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "drop": 2, "message_id": 7,
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [(p.id, p.slot) for p in players] == [(1, "C"), (11, "BN")]
    assert state["decisions"][0]["type"] == "add"


def test_weekly_plan_waits_for_noon_on_the_weeks_first_day(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    morning = dt.datetime(2026, 10, 5, 7, tzinfo=dt.timezone.utc)  # 10:00 Helsinki
    weekly.weekly_step(state, players, {"teams": {}, "taken": []}, morning, False, common.Outbox(settings),
                     build_context=lambda d: 1 / 0)
    assert state["weeks"] == {} and sent == []


def test_trade_command_is_judged_later_in_the_run(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/trade B for Nobody"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert state["trade_request"] == "B for Nobody"
    daily.trade_step(state, players, league, NOW, common.Outbox(settings))
    assert sent == ["No rostered player called 'Nobody'."]
    assert state["trade_request"] is None


def _roster_paste(n):
    return "\n".join(f"BN\tPlayer {NAMES[i]}" for i in range(n))


NAMES = [f"{c}son" for c in "ABCDEFGHIJKLMNOPQ"]
ROSTER_REGISTRY = [{"id": 100 + i, "name": f"Player {NAMES[i]}", "team": "BOS", "position": "C"} for i in range(17)]


def test_myteam_then_paste_replaces_my_roster(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(
        monkeypatch, tmp_path, [_message("/myteam", 9), _message(_roster_paste(12), 10)])
    monkeypatch.setattr(parse, "registry", lambda: ROSTER_REGISTRY)
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [p.id for p in players] == list(range(100, 112))
    assert state["awaiting"] is None
    assert sent[1].startswith("Roster saved: 12 players.\nAdded: Player Ason")
    assert "Dropped: A, B" in sent[1]


def test_myteam_rejects_a_partial_paste(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [_message("/myteam\n" + _roster_paste(3))])
    monkeypatch.setattr(parse, "registry", lambda: ROSTER_REGISTRY)
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [p.id for p in players] == [1, 2]
    assert "Your roster is unchanged" in sent[0]


def test_no_first_briefing_after_the_window_closes(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    game = nhl_client.ScheduledGame(1, dt.datetime(2026, 10, 1, 23, tzinfo=dt.timezone.utc), "TOR", "BOS")
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [game])
    late = dt.datetime(2026, 10, 1, 17, 35, tzinfo=dt.timezone.utc)  # 20:35 Helsinki
    daily.briefing_step(state, players, late, False, common.Outbox(settings), build_context=lambda d: 1 / 0)
    assert state["briefings"] == {} and sent == []


SHOT_NAMES = ["Ason", "Bson", "Cson", "Dson", "Eson", "Fson", "Gson", "Hson", "Ison", "Json", "Kson", "Lson"]
SHOT_REGISTRY = [{"id": 200 + i, "name": f"Paul {n}", "team": "BOS", "position": "C"} for i, n in enumerate(SHOT_NAMES)]
SHOTS = {"top": [{"slot": s, "name": f"P. {n.upper()}", "team": "BOS", "positions": ["C"]}
                 for n, s in zip(SHOT_NAMES[:8], ["C", "C", "LW", "LW", "RW", "RW", "D", "D"])],
         "bottom": [{"slot": s, "name": f"P. {n.upper()}", "team": "", "positions": []}  # overlaps "top" by one
                    for n, s in zip(SHOT_NAMES[7:], ["D", "D", "G", "G", "BN"])]}


def _photo(file_id, update_id, date=1_790_000_000):
    return {"update_id": update_id, "message": {"chat": {"id": 42}, "date": date,
                                                "photo": [{"file_id": "small"}, {"file_id": file_id}]}}


def _screenshots(monkeypatch, tmp_path, updates):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, updates)
    monkeypatch.setattr(parse, "registry", lambda: SHOT_REGISTRY)
    monkeypatch.setattr(telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(screenshot, "read", lambda image: {"kind": "team", "rows": SHOTS[image.decode()]})
    return settings, state, players, sent


def test_two_screenshots_in_one_run_replace_my_roster(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9), _photo("bottom", 10)])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [(p.id, p.slot) for p in players][:1] == [(200, "C")] and len(players) == 12
    assert players[-1].slot == "BN"
    assert sent[0].startswith("Roster saved: 12 players.")


def test_screenshots_across_runs_wait_for_the_rest(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9)])
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert [p.id for p in players] == [1, 2]
    assert "read 8 players so far" in sent[0] and "/save" in sent[0]
    monkeypatch.setattr(telegram, "get_updates", lambda token, offset: [_photo("bottom", 10, 1_790_000_060)])
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert len(players) == 12 and sent[1].startswith("Roster saved")


def test_a_screenshot_without_players_says_so(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9)])
    monkeypatch.setattr(screenshot, "read", lambda image: {"kind": "team", "rows": []})
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert "couldn't find any players" in sent[0] and state["screenshots"] is None


OPP_NAMES = ["Aho", "Bo", "Cho", "Do", "Eho", "Fo", "Gho", "Ho", "Iho", "Jo", "Kho", "Lo"]
OPP_REGISTRY = [{"id": 300 + i, "name": f"Rick {n}", "team": "TOR", "position": "C"} for i, n in enumerate(OPP_NAMES)]
SLOTS = ["C", "C", "LW", "LW", "RW", "RW", "D", "D", "D", "D", "G", "G"]
WEEK1 = 1_790_838_000  # Thu 1 Oct 2026, 03:00 in New York: before that day's games


def _matchup_shot(rows, score=(13.4, 51.5)):
    return {"kind": "matchup", "score": score, "projected": (159.79, 164.52), "labels": ([], ["BAHELIN BOYS"]),
            "rows": rows}


def _row(i, mine=True, points=1.0):
    def player(name, team):
        return {"name": name, "team": team, "positions": ["C"], "points": points, "projected": 9.0}
    return {"slot": SLOTS[i],
            "mine": player(f"P. {SHOT_NAMES[i].upper()}", "BOS") if mine else None,
            "theirs": player(f"R. {OPP_NAMES[i].upper()}", "TOR")}


def _matchup(monkeypatch, tmp_path, shots, players=None):
    updates = [_photo(name, 20 + i, WEEK1 + i) for i, name in enumerate(shots)]
    settings, state, _, sent, _ = _setup(monkeypatch, tmp_path, updates)
    monkeypatch.setattr(parse, "registry", lambda: SHOT_REGISTRY + OPP_REGISTRY)
    monkeypatch.setattr(telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(screenshot, "read", lambda image: shots[image.decode()])
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [])
    players = players if players is not None else [RosterPlayer(200 + i, f"Paul {n}", "BOS", ["C"], "BN")
                                                   for i, n in enumerate(SHOT_NAMES)]
    league = {"teams": {"Bahelin Boys": {"updated": "2026-09-29", "players": [
        {"id": 300 + i, "name": f"Rick {n}", "team": "TOR", "positions": ["C"], "slot": None}
        for i, n in enumerate(OPP_NAMES[:11])] + [
        {"id": 399, "name": "Old Guy", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    return state, players, league, sent


def test_matchup_screenshots_update_both_rosters_and_the_live_score(monkeypatch, tmp_path):
    shots = {"top": _matchup_shot([_row(i) for i in range(7)]),
             "bottom": _matchup_shot([_row(i, points=0.0) for i in range(6, 12)])}
    state, players, league, sent = _matchup(monkeypatch, tmp_path, shots)
    assert [p.slot for p in players] == SLOTS  # slots from the matchup, Lson is a G here
    assert {p["id"] for p in league["teams"]["Bahelin Boys"]["players"]} == set(range(300, 312))
    assert state["live_score"]["score"] == [13.4, 51.5] and state["live_score"]["through"] == "2026-10-01"
    assert state["live_score"]["goalies"] == [0.0, 0.0]  # rows 10-11 are G, read with 0 points
    assert state["week_requested"] and state["matchup_shots"] is None
    assert sent[0].startswith("Week 1 vs Bahelin Boys: 13.40 - 51.50 (Yahoo projects 160 - 165)")
    assert "new: Rick Lo; gone: Old Guy" in sent[0]


def test_a_partial_matchup_saves_the_score_and_waits_for_the_rest(monkeypatch, tmp_path):
    state, players, league, sent = _matchup(monkeypatch, tmp_path, {"top": _matchup_shot([_row(i) for i in range(7)])})
    assert state["live_score"]["score"] == [13.4, 51.5]
    assert "Read 7 of your players so far" in sent[0] and state["matchup_shots"]
    assert all(p.slot == "BN" for p in players) and not state["week_requested"]


def test_someone_elses_matchup_changes_no_roster(monkeypatch, tmp_path):
    mine = [RosterPlayer(500 + i, f"Other {i}", "SEA", ["C"], "BN") for i in range(12)]
    state, players, league, sent = _matchup(monkeypatch, tmp_path,
                                            {"top": _matchup_shot([_row(i) for i in range(12)])}, players=mine)
    assert [p.id for p in players] == list(range(500, 512))
    assert "doesn't look like yours" in sent[0]


def test_yahoos_score_is_split_by_the_goalie_rows_else_by_box_scores(monkeypatch):
    monkeypatch.setattr(matchup, "_so_far", lambda roster, ctx, days, history=None: (5.0, 3.0, 1))
    assert weekly._banked(13.4, 8.2, [], None, []) == (pytest.approx(5.2), 8.2, 1)
    assert weekly._banked(13.4, None, [], None, []) == (pytest.approx(10.4), 3.0, 1)


def test_the_plan_goes_out_at_the_start_of_the_week_and_once_from_wednesday():
    mon, tue, wed, thu = (dt.date(2026, 10, d) for d in (5, 6, 7, 8))  # week 2
    assert weeks.midweek(2) == wed and weeks.midweek(1) == dt.date(2026, 9, 30)
    assert weekly.plan_due(None, mon, 2)
    assert not weekly.plan_due({"sent": "x"}, tue, 2)
    assert weekly.plan_due({"sent": "x"}, wed, 2) and weekly.plan_due({"sent": "x"}, thu, 2)
    assert not weekly.plan_due({"sent": "x", "midweek": "y"}, thu, 2)


def test_the_dashboard_data_carries_the_summary_and_every_view(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(weekly, "SITE_DIR", tmp_path)
    me = matchup.TeamWeek("me", 13.4, 153.0, 400.0, 26, 1, 3, 1.0)
    them = matchup.TeamWeek("them", 51.5, 156.0, 400.0, 23, 1, 3, 1.0)
    wk = SimpleNamespace(me=me, them=them, days=[dt.date(2026, 9, 29), dt.date(2026, 10, 4)], season_used=1,
                         max_moves=1, yahoo_projected=[159.79, 164.52])
    views = {"decision": {"points": []}, "schedule": {}, "budget": {}, "adds": {}, "streamer_text": "x"}
    path = weekly.write_dashboard(views, wk, 1, "Bahelin Boys", "Mid-week: chase", NOW, dry_run=False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "data.json"
    assert data["summary"]["so_far"] == [13.4, 51.5] and data["summary"]["stance"] == "chase"
    assert data["summary"]["adds_left"] == {"season": 35, "week": 1}
    assert set(data) >= {"decision", "schedule", "budget", "adds"} and "streamer_text" not in data


TX_REGISTRY = [{"id": 700 + i, "name": n, "team": "TOR", "position": pos} for i, (n, pos) in enumerate(
    [("Jared McCann", "C"), ("Justin Faulk", "D"), ("Dylan Cozens", "C"), ("John Tavares", "C"), ("Esa Lindell", "D")])]


def _tx(kind, when, teams_, players):
    return {"type": kind, "when": list(when), "teams": teams_,
            "players": [{"name": n, "positions": ["C"], "action": a} for n, a in players]}


def _transactions(monkeypatch, tmp_path, shots, league=None, players=None):
    updates = [_photo(name, 40 + i, WEEK1 + i) for i, name in enumerate(shots)]
    settings, state, default_players, sent, _ = _setup(monkeypatch, tmp_path, updates)
    monkeypatch.setattr(parse, "registry", lambda: TX_REGISTRY)
    monkeypatch.setattr(telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(screenshot, "read", lambda image: {"kind": "transactions", "rows": shots[image.decode()]})
    league = league or {"teams": {
        "Pastasauce": {"updated": "2026-09-29", "players": [
            {"id": 702, "name": "Dylan Cozens", "team": "TOR", "positions": ["C"], "slot": None},
            {"id": 701, "name": "Justin Faulk", "team": "TOR", "positions": ["D"], "slot": None}]},
        "Vanilla Thunder": {"updated": "2026-09-29", "players": [
            {"id": 703, "name": "John Tavares", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    players = players if players is not None else default_players
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    return state, players, league, sent


def _ids(league, team):
    return {p["id"] for p in league["teams"][team]["players"]}


def test_transactions_move_players_between_teams_and_the_free_agents(monkeypatch, tmp_path):
    shot = [_tx("trade", (9, 30, 14, 4), ["Pastasauce", "Vanilla Thunder"],
                [("D. Cozens", "from:0"), ("J. Tavares", "from:1")]),
            _tx("add/drop", (9, 30, 14, 3), ["Pastasauce"], [("J. McCann", "add"), ("J. Faulk", "drop")]),
            _tx("add", (9, 30, 10, 59), ["Nico's Groovy Team"], [("E. Lindell", "add")])]
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"t": shot})
    assert _ids(league, "Pastasauce") == {700, 703} and _ids(league, "Vanilla Thunder") == {702}
    assert 701 not in teams.rostered_ids(league)  # Faulk is a free agent again
    assert 704 in [p.id for p in players]  # my own add, onto my roster
    assert sent[0].startswith("Transactions: 3 new (Wed 30 Sep 10:59 - Wed 30 Sep 14:04).")
    assert "Pastasauce: +Jared McCann, -Justin Faulk, -Dylan Cozens, +John Tavares" in sent[0]


def test_transactions_already_applied_are_skipped_and_a_gap_is_flagged(monkeypatch, tmp_path):
    old = [_tx("add", (9, 30, 14, 3), ["Pastasauce"], [("J. McCann", "add")])]
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"a": old, "b": old})
    assert sent == [sent[0]] and "1 new" in sent[0]
    later = [_tx("drop", (10, 3, 9, 0), ["Pastasauce"], [("J. McCann", "drop")])]
    monkeypatch.setattr(telegram, "get_updates", lambda token, offset: [_photo("c", 60, WEEK1 + 99)])
    monkeypatch.setattr(screenshot, "read", lambda image: {"kind": "transactions", "rows": later})
    settings = load_settings()
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert "may be missing" in sent[-1] and 700 not in _ids(league, "Pastasauce")


def test_taken_marks_the_suggested_player_and_asks_for_the_next_best(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("taken:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "add": {"id": 900}, "drop": None, "message_id": 7}
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert league["taken"] == [900] and state["week_requested"]
    assert state["decisions"][-1]["decision"] == "taken" and handled == ["Taken: finding the next best"]


def test_free_agents_take_yahoos_positions_once_a_screenshot_showed_them(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [])
    monkeypatch.setattr(nhl_client, "current_rosters", lambda: [
        {"id": 800, "name": "Jack McBain", "team": "UTA", "position": "C"},
        {"id": 801, "name": "Nobody Seen", "team": "UTA", "position": "C"}])
    common.learn_positions([RosterPlayer(800, "Jack McBain", "UTA", ["C", "LW"])])
    pool = {p.id: p.positions for p in common.free_agents([], {"teams": {}, "taken": []})}
    assert pool == {800: ["C", "LW"], 801: ["C"]}


def test_my_adds_count_once_however_they_are_learned(monkeypatch, tmp_path):
    shot = [_tx("add", (9, 30, 10, 59), ["Nico's Groovy Team"], [("E. Lindell", "add")]),
            _tx("add", (10, 5, 2, 30), ["Nico's Groovy Team"], [("J. McCann", "add")]),  # Sunday night in New York
            _tx("trade", (10, 5, 9, 0), ["Nico's Groovy Team", "Pastasauce"],
                [("D. Cozens", "from:1")])]  # a trade costs no add
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"t": shot})
    assert [(a["id"], a["date"], a["source"]) for a in state["adds"]] == [
        (704, "2026-09-30", "transactions"), (700, "2026-10-04", "transactions")]
    # A day later Nico taps Done on the bot's recommendation of that same add: still one add.
    state["pending"]["add-1"] = {"type": "add", "date": "2026-09-30", "drop": None, "message_id": 7, "add": {
        "id": 704, "name": "Esa Lindell", "team": "TOR", "positions": ["D"], "slot": None}}
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2026, 10, 1))
    monkeypatch.setattr(telegram, "get_updates", lambda token, offset: [_tap("done:add-1", update_id=99)])
    settings = load_settings()
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert len(state["adds"]) == 2


def test_an_add_counts_in_the_week_it_was_made_not_the_week_it_was_suggested(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-04", "drop": None, "message_id": 7,  # Sun, week 1
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2026, 10, 5))  # made on Monday, week 2
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert matchup.adds_used(state["adds"], weeks.days(2)) == (1, 1)


def test_new_players_on_my_team_page_count_as_adds_unless_another_team_had_them(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9), _photo("bottom", 10)])
    players[:] = [RosterPlayer(200 + i, f"Paul {n}", "BOS", ["C"], "BN") for i, n in enumerate(SHOT_NAMES[:10])]
    players.append(RosterPlayer(290, "Old Guy", "BOS", ["C"], "BN"))
    league = {"teams": {"Bellova": {"updated": "2026-09-29", "players": [
        {"id": 211, "name": "Paul Lson", "team": "BOS", "positions": ["C"], "slot": None}]}}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert [(a["id"], a["source"]) for a in state["adds"]] == [(210, "roster")]
    assert league["teams"]["Bellova"]["players"] == []  # he's mine now, by trade or not
    assert "Counted as adds: Paul Kson (35 left)." in sent[0]
    assert "Not counted as adds, since another team had them (a trade?): Paul Lson (Bellova)" in sent[0]


def test_a_roster_update_with_many_new_players_counts_no_adds(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9), _photo("bottom", 10)])
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert state["adds"] == [] and "12 players are new to me, too many to be adds" in sent[0]


def test_a_weekly_plan_that_fails_stays_due_for_the_next_run(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    noon = dt.datetime(2026, 10, 5, 9, 30, tzinfo=dt.timezone.utc)  # Monday 12:30 Helsinki
    state["week_requested"] = True
    with pytest.raises(ZeroDivisionError):  # e.g. the NHL API down
        weekly.weekly_step(state, players, {"teams": {}, "taken": []}, noon, False, common.Outbox(settings),
                         build_context=lambda d: 1 / 0)
    assert state["weeks"] == {} and state["week_requested"] and sent == []


def test_a_failing_step_doesnt_stop_the_ones_after_it():
    ran = []
    failures = main.run_steps([("trade", lambda: 1 / 0), ("briefing", lambda: ran.append("briefing"))])
    assert ran == ["briefing"] and [(name, type(e)) for name, e in failures] == [("trade", ZeroDivisionError)]


def test_a_failure_alerts_once_a_day_without_the_token(monkeypatch, tmp_path):
    settings, state, _, sent, _ = _setup(monkeypatch, tmp_path, [])
    settings.telegram_bot_token = "123:secret"
    failures = [("weekly plan", RuntimeError("GET https://api.telegram.org/bot123:secret/x failed")),
                ("briefing", ValueError("x"))]
    main.alert_failure(failures, settings, state, common.Outbox(settings), NOW)
    main.alert_failure(failures, settings, state, common.Outbox(settings), NOW)
    assert sent == ["Assistant GM run failed in weekly plan (and 1 more step): "
                    "RuntimeError: GET https://api.telegram.org/bot<token>/x failed"]


def test_a_dropped_player_is_on_waivers_until_a_claim_can_play(monkeypatch, tmp_path):
    shot = [_tx("add/drop", (9, 30, 14, 3), ["Pastasauce"], [("J. McCann", "add"), ("J. Faulk", "drop")]),
            _tx("trade", (9, 30, 14, 4), ["Pastasauce", "Vanilla Thunder"],
                [("D. Cozens", "from:0"), ("J. Tavares", "from:1")])]  # traded players skip waivers
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"t": shot})
    assert league["waivers"] == {"701": "2026-10-02"}  # dropped Wed 30 Sep, plays for a claimer from Fri
    pool = [RosterPlayer(701, "Justin Faulk", "TOR", ["D"]), RosterPlayer(704, "Esa Lindell", "TOR", ["D"])]
    assert weekly.waiver_days(pool, league, dt.date(2026, 10, 1)) == {701: dt.date(2026, 10, 2)}
    assert weekly.waiver_days(pool, league, dt.date(2026, 10, 2)) == {} and league["waivers"] == {}


def test_my_drop_on_done_goes_on_waivers(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "drop": 2, "message_id": 7,
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2026, 10, 1))
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert league["waivers"] == {"2": "2026-10-03"}


def test_todays_rosters_are_kept_until_the_first_puck(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [])
    first_puck = dt.datetime(2026, 10, 1, 23, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [nhl_client.ScheduledGame(1, first_puck, "BOS", "TOR")])
    league = {"teams": {"Bahelin Boys": {"updated": "2026-09-29", "players": [
        {"id": 300, "name": "Rick Aho", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    weekly.snapshot_rosters(state, players, league, NOW)
    saved = state["day_rosters"]["2026-10-01"]
    assert [p["id"] for p in saved["me"]] == [1, 2] and saved["them"]["team"] == "Bahelin Boys"
    players.append(RosterPlayer(9, "Late Add", "NYR", ["C"], "BN"))
    weekly.snapshot_rosters(state, players, league, first_puck)  # games have started: no change
    assert [p["id"] for p in state["day_rosters"]["2026-10-01"]["me"]] == [1, 2]


def test_each_days_roster_keeps_dropped_players_and_leaves_out_later_adds(tmp_path):
    state = gm_state.load(tmp_path / "none.json")
    state["day_rosters"] = {"2026-09-30": {"me": [
        {"id": 1, "name": "A", "team": "BOS", "positions": ["C"], "slot": "C"},
        {"id": 5, "name": "Dropped Thu", "team": "BOS", "positions": ["D"], "slot": "D"}]}}
    current = [RosterPlayer(1, "A", "BOS", ["C"], "C"), RosterPlayer(9, "Added Thu", "NYR", ["C"], "BN"),
               RosterPlayer(7, "Missed by the snapshot", "TOR", ["LW"], "LW")]
    days = [dt.date(2026, 9, 29), dt.date(2026, 9, 30), dt.date(2026, 10, 1)]
    by_day = weekly.rosters_by_day(state, "me", current, days, dt.date(2026, 10, 1), {9: dt.date(2026, 10, 1)})
    assert sorted(p.id for p in by_day[dt.date(2026, 9, 30)]) == [1, 5, 7]
    assert sorted(p.id for p in by_day[dt.date(2026, 9, 29)]) == [1, 7]  # no snapshot: today's, less the add
    assert dt.date(2026, 10, 1) not in by_day  # today isn't banked yet


def test_done_on_an_add_into_an_ir_spot_makes_the_ir_move(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "drop": None, "message_id": 7,
                                 "ir": {"2": "IR+"}, "add": {"id": 11, "name": "Joey Daccord", "team": "SEA",
                                                             "positions": ["G"], "slot": None}}
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    assert [(p.id, p.slot) for p in players] == [(1, "C"), (2, "IR+"), (11, "BN")]


def test_the_briefing_mentions_each_ir_move_once(monkeypatch, tmp_path):
    from clients.dfo_lines import LineInfo
    _, state, players, _, _ = _setup(monkeypatch, tmp_path, [])
    lines = {"TOR": {"b": LineInfo(groups={"f2"}, injury="out")}}
    monkeypatch.setattr(matchup, "drop_candidates", lambda roster, ctx, lines: [])
    assert "move B to IR+" in daily._new_ir_note(state, players, None, lines)
    assert daily._new_ir_note(state, players, None, lines) == ""  # said already
    assert daily._new_ir_note(state, players, None, {}) == "" and state["ir_noted"] == []  # healed: forgotten
    assert "move B to IR+" in daily._new_ir_note(state, players, None, lines)  # out again: said again


def test_a_weeks_plans_are_logged_first_and_latest(tmp_path):
    from engine import matchup
    state = gm_state.load(tmp_path / "none.json")
    assert state["results"]["1"]["first"]["win"] == 0.476  # week 1, from before the log
    me, them = matchup.TeamWeek("me", 10, 150, 400, 20, 1, 3, 1.0), matchup.TeamWeek("t", 5, 140, 500, 20, 1, 3, 1.0)
    weekly.record_plan(state, 2, "Retrot Chicken Wings", me, them, NOW)
    me.expected = 160
    weekly.record_plan(state, 2, "Retrot Chicken Wings", me, them, NOW + dt.timedelta(days=2))
    week = state["results"]["2"]
    assert week["first"]["expected"] == [150, 140] and week["last"]["expected"] == [160, 140]
    assert week["first"]["sd"] == [20.0, pytest.approx(22.36)] and week["opponent"] == "Retrot Chicken Wings"


def test_last_weeks_result_goes_out_monday_noon_once(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    view = {"week": 1, "opponent": "Bahelin Boys", "finished": True, "final": [170.0, 160.0],
            "goalie_min": [True, True]}
    monkeypatch.setattr(weekly, "week_result", lambda *a: view)
    monkeypatch.setattr(report, "result_text", lambda v: "Week 1 result")
    monkeypatch.setattr(charts, "result_chart", lambda v: None)
    morning, noon = (dt.datetime(2026, 10, 5, h, tzinfo=dt.timezone.utc) for h in (7, 9))  # 10:00, 12:00 Helsinki
    weekly.report_step(state, players, {"teams": {}, "taken": []}, morning, common.Outbox(settings), lambda d: None)
    assert sent == []
    weekly.report_step(state, players, {"teams": {}, "taken": []}, noon, common.Outbox(settings), lambda d: None)
    weekly.report_step(state, players, {"teams": {}, "taken": []}, noon, common.Outbox(settings), lambda d: None)
    assert sent == ["Week 1 result"] and state["results"]["1"]["final"]["score"] == [170.0, 160.0]


def test_a_weeks_result_scores_each_day_with_that_days_roster(monkeypatch, tmp_path):
    from dataclasses import dataclass

    @dataclass
    class Log:
        date: dt.date
        stats: dict
        started: bool = True

    class Ctx:
        today = dt.date(2026, 10, 5)
        skater_games = {1: [Log(dt.date(2026, 9, 30), {"g": 1})], 5: [Log(dt.date(2026, 9, 29), {"a": 1})],
                        300: [Log(dt.date(2026, 10, 2), {"g": 2})]}
        goalie_games = {}

        def skater(self, pid, position):
            return type("P", (), {"xfp": 2.0})()

    _, state, players, _, _ = _setup(monkeypatch, tmp_path, [])
    state["day_rosters"] = {"2026-09-29": {"me": [{"id": 5, "name": "Dropped", "team": "BOS", "positions": ["C"],
                                                   "slot": "C"}]}}
    league = {"teams": {"Bahelin Boys": {"updated": "2026-09-29", "players": [
        {"id": 300, "name": "Rick Aho", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    view = weekly.week_result(state, 1, players, league, Ctx())
    assert view["final"] == [2.75 + 4.0, 8.0] and view["finished"]
    assert view["goalie_min"] == [False, False]  # nobody's goalies played: goalie points (none) zeroed


def test_a_tap_on_an_add_records_its_players(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("skip:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "drop": 2, "drop_name": "B", "message_id": 7,
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    ingest.process_updates(settings, state, players, {"teams": {}, "taken": []}, common.Outbox(settings))
    d = state["decisions"][-1]
    assert (d["decision"], d["add"], d["add_name"], d["drop"], d["drop_name"]) == ("skip", 11, "Joey Daccord", 2, "B")


def _news_setup(monkeypatch, tmp_path, moves):
    from engine import matchup
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    first_puck = dt.datetime(2026, 10, 7, 23, tzinfo=dt.timezone.utc)  # Wed 7 Oct, week 2
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [nhl_client.ScheduledGame(1, first_puck, "BOS", "TOR")])
    me = matchup.TeamWeek("me", 0, 150, 400, 20, 1, 3, 1.0)
    plan = weekly.PlanMoves(type("Wk", (), {"me": me, "them": me})(), None, moves, moves, [], players, 0)
    calls = []
    monkeypatch.setattr(weekly, "plan_moves", lambda *a: calls.append(1) or plan)
    state["opponents"] = {}
    return settings, state, players, sent, calls


def _move(add_id, name, drop=None):
    from engine import matchup
    return matchup.Move(RosterPlayer(add_id, name, "NYR", ["C"]), drop, 3.0, 0.0, 0.0, 3, 0.48, 0.55)


def test_the_evening_news_check_sends_only_adds_not_offered_this_week(monkeypatch, tmp_path):
    old, new = _move(9, "Offered Monday"), _move(10, "New Streamer")
    settings, state, players, sent, calls = _news_setup(monkeypatch, tmp_path, [old, new])
    state["decisions"].append({"rec_id": "r", "type": "add", "decision": "skip", "date": "2026-10-05", "add": 9,
                               "drop": None})
    evening = dt.datetime(2026, 10, 7, 16, 45, tzinfo=dt.timezone.utc)  # 19:45 Helsinki
    weekly.news_step(state, players, {"teams": {}, "taken": []}, evening, common.Outbox(settings))
    weekly.news_step(state, players, {"teams": {}, "taken": []}, evening, common.Outbox(settings))  # once an evening
    assert calls == [1] and sent[0].startswith("News since the plan: an add now clears the price")
    assert sent[1].startswith("Add New Streamer") and len(sent) == 2


def test_no_news_check_on_a_plan_day_or_without_adds_left(monkeypatch, tmp_path):
    settings, state, players, sent, calls = _news_setup(monkeypatch, tmp_path, [_move(10, "New")])
    evening = dt.datetime(2026, 10, 7, 16, 45, tzinfo=dt.timezone.utc)
    state["weeks"]["2"] = {"sent": "2026-10-05T09:00+00:00", "midweek": "2026-10-07T09:00+00:00"}
    weekly.news_step(state, players, {"teams": {}, "taken": []}, evening, common.Outbox(settings))
    state["weeks"]["2"] = {"sent": "2026-10-05T09:00+00:00"}
    state["adds"] = [{"id": i, "name": None, "date": "2026-10-06", "source": "done"} for i in (1, 2)]
    weekly.news_step(state, players, {"teams": {}, "taken": []}, evening, common.Outbox(settings))
    assert calls == [] and sent == []


def test_the_plan_leads_with_the_action():
    assert weekly._action_line([_move(10, "Beniers", RosterPlayer(2, "Stamkos", "NSH", ["C"]))], "") == \
        "Do now: add Beniers for Stamkos (win 48% -> 55%). Details below."
    assert weekly._action_line([], "IR: ...") == "No add is worth one of yours right now. See the IR note below."
    assert weekly._action_line([], "", adds_left=0) == "No adds left this week (they reset Monday)."


def test_other_drop_records_the_add_then_asks_who_went(monkeypatch, tmp_path):
    settings, state, players, sent, handled = _setup(monkeypatch, tmp_path, [_tap("other:add-1")])
    monkeypatch.setattr(parse, "registry", lambda: [{"id": 2, "name": "Bobby Bee", "team": "TOR", "position": "C"}])
    monkeypatch.setattr(common, "nhl_today", lambda: dt.date(2026, 10, 6))
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-06", "drop": 1, "drop_name": "A", "message_id": 7,
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    league = {"teams": {}, "taken": []}
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert [p.id for p in players] == [1, 2, 11] and state["awaiting_drop"] == "add-1"  # not the suggested drop
    assert sent == ["Who did you drop for Joey Daccord? Send the name."] and handled == ["Recorded: Done, which drop?"]
    assert [a["id"] for a in state["adds"]] == [11]
    monkeypatch.setattr(telegram, "get_updates", lambda token, offset: [_message("Bobby Bee", 12)])
    ingest.process_updates(settings, state, players, league, common.Outbox(settings))
    assert [p.id for p in players] == [1, 11] and league["waivers"] == {"2": "2026-10-08"}
    d = state["decisions"][-1]
    assert (d["decision"], d["drop"], d["drop_name"]) == ("done", 2, "B") and sent[-1] == "Noted: you dropped B."


def test_a_plan_on_a_matchup_screenshot_logs_yahoos_score_next_to_the_box_scores(monkeypatch, tmp_path):
    from engine import matchup
    _, state, players, _, _ = _setup(monkeypatch, tmp_path, [])
    state["live_score"] = {"week": 1, "opponent": "Bahelin Boys", "through": "2026-10-01", "score": [13.4, 51.5],
                           "projected": [160.0, 165.0], "goalies": [None, None], "at": "2026-10-01T07:15+00:00"}
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [])
    monkeypatch.setattr(nhl_client, "current_teams", lambda: [])
    monkeypatch.setattr(nhl_client, "current_rosters", lambda: [])
    monkeypatch.setattr(goalie_client, "get_starters", lambda d: {})
    monkeypatch.setattr(matchup, "_so_far", lambda roster, ctx, days, history=None: (10.0, 2.0, 1))

    class Ctx:
        today = dt.date(2026, 10, 1)
    league = {"teams": {}, "taken": []}
    wk = weekly.week_inputs(dt.date(2026, 10, 1), 1, players, league, state, lambda d: Ctx(), "Bahelin Boys")
    assert wk.live and wk.live_check == {"through": "2026-10-01", "yahoo": [13.4, 51.5], "box": [12.0, 12.0]}


def test_other_teams_adds_are_logged_from_transactions(monkeypatch, tmp_path):
    shot = [_tx("add/drop", (9, 30, 14, 3), ["Pastasauce"], [("J. McCann", "add"), ("J. Faulk", "drop")]),
            _tx("add", (9, 30, 10, 59), ["Nico's Groovy Team"], [("E. Lindell", "add")])]
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"t": shot})
    assert state["league_adds"] == {"Pastasauce": ["2026-09-30"]}  # mine go to the adds ledger instead
