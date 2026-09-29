import datetime as dt

import main
from config.settings import load_settings
from league.roster import RosterPlayer
from state import gm_state

NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)


def _setup(monkeypatch, tmp_path, updates):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent, handled = [], []
    monkeypatch.setattr(main.telegram, "get_updates", lambda token, offset: updates)
    monkeypatch.setattr(main.telegram, "send_message", lambda *a, **k: sent.append(a[2]) or 1)
    monkeypatch.setattr(main.telegram, "mark_handled", lambda token, chat, mid, label: handled.append(label))
    monkeypatch.setattr(main.telegram, "answer_callback", lambda *a: None)
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
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [p.slot for p in players] == ["BN", "C"]
    assert state["pending"] == {}
    assert state["decisions"][0]["decision"] == "done"
    assert state["telegram_offset"] == 6
    assert handled == ["Recorded: Done"]


def test_skip_leaves_the_roster_alone(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("skip:lineup-2026-11-10-2100")])
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert handled == ["Recorded: Skipped"]


def test_taps_from_other_chats_are_ignored(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:lineup-2026-11-10-2100", chat_id=999)])
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert "lineup-2026-11-10-2100" in state["pending"]


def test_chat_id_secret_is_whitespace_tolerant(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/start"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " 42\n")
    settings = load_settings()
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert sent == [main.HELP]


def test_messages_from_other_chats_are_logged(monkeypatch, tmp_path, caplog):
    message = {"update_id": 9, "message": {"chat": {"id": 999}, "text": "/start"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert sent == []
    assert "chat ...999: TELEGRAM_CHAT_ID is ...42" in caplog.text


def test_roster_command_replies_with_the_roster(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/roster"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert "A (BOS, C)" in sent[0]


def _relay(monkeypatch, updates, dispatch_error=None):
    monkeypatch.setenv("RELAY_URL", "https://relay.example/")
    monkeypatch.setenv("RELAY_TOKEN", "r")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "w")
    calls = []

    def relayed(url, token, offset):
        calls.append((url, token, offset))
        return updates, dispatch_error
    monkeypatch.setattr(main.telegram, "get_relayed_updates", relayed)
    return calls


def test_relay_replaces_polling_when_configured(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/help"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    calls = _relay(monkeypatch, [message])
    settings = load_settings()
    assert main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings)) is None
    assert calls == [("https://relay.example", "r", 0)]
    assert sent == [main.HELP]
    assert state["telegram_offset"] == 10


def test_relay_dispatch_errors_alert_once_a_day(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [], dispatch_error="GitHub 401: Bad credentials")
    settings = load_settings()
    outbox = main.Outbox(settings)
    for _ in range(2):
        problem = main.process_updates(settings, state, players, {"teams": {}, "taken": []}, outbox)
        main.report_relay(problem, state, outbox, NOW)
    assert len(sent) == 1 and "GitHub 401" in sent[0]


def _webhook(monkeypatch, info):
    calls = []
    monkeypatch.setattr(main.telegram, "webhook_info", lambda token: info)
    monkeypatch.setattr(main.telegram, "set_webhook", lambda token, url, secret: calls.append(("set", url, secret)))
    monkeypatch.setattr(main.telegram, "delete_webhook", lambda token: calls.append(("delete",)))
    return calls



def test_webhook_follows_the_relay_setting(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [])
    calls = _webhook(monkeypatch, {"url": ""})
    assert main.sync_webhook(load_settings(), NOW) is None
    assert calls == [("set", "https://relay.example/telegram", "w")]

    monkeypatch.delenv("RELAY_URL")
    calls = _webhook(monkeypatch, {"url": "https://relay.example/telegram"})
    main.sync_webhook(load_settings(), NOW)
    assert calls == [("delete",)]


def test_webhook_backlog_with_a_recent_error_is_a_problem(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    _relay(monkeypatch, [])
    info = {"url": "https://relay.example/telegram", "pending_update_count": 2,
            "last_error_date": int(NOW.timestamp()) - 60, "last_error_message": "Connection timed out"}
    calls = _webhook(monkeypatch, info)
    assert "Connection timed out" in main.sync_webhook(load_settings(), NOW)
    assert calls == []

    info["last_error_date"] -= 7200  # an old error with a fresh message in flight is fine
    assert main.sync_webhook(load_settings(), NOW) is None


REGISTRY = [{"id": 10, "name": "Nick Suzuki", "team": "MTL", "position": "C"},
            {"id": 11, "name": "Joey Daccord", "team": "SEA", "position": "G"}]


def _message(text, update_id=9):
    return {"update_id": update_id, "message": {"chat": {"id": 42}, "text": text}}


def test_opp_then_paste_updates_this_weeks_opponent(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(
        monkeypatch, tmp_path, [_message("/opp", 9), _message("Nick SuzukiPlayer NoteMTL - C", 10)])
    monkeypatch.setattr(main.parse, "registry", lambda: REGISTRY)
    monkeypatch.setattr(main, "_nhl_today", lambda: dt.date(2026, 10, 1))
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert [p["name"] for p in league["teams"]["Bahelin Boys"]["players"]] == ["Nick Suzuki"]
    assert state["awaiting"] is None
    assert "Bahelin Boys: 1 players saved" in sent[1]


def test_opp_with_a_team_name_in_the_playoffs_records_the_opponent(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(
        monkeypatch, tmp_path, [_message("/opp vantaa\nNick Suzuki (MTL - C)")])
    monkeypatch.setattr(main.parse, "registry", lambda: REGISTRY)
    monkeypatch.setattr(main, "_nhl_today", lambda: dt.date(2027, 3, 16))
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert state["opponents"] == {"24": "Vantaa"}
    assert "Vantaa" in league["teams"]


def test_taken_removes_a_player_from_the_free_agents(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [_message("/taken Joey Daccord")])
    monkeypatch.setattr(main.parse, "registry", lambda: REGISTRY)
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert league["taken"] == [11]


def test_done_on_an_add_swaps_the_players(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "drop": 2, "message_id": 7,
                                 "add": {"id": 11, "name": "Joey Daccord", "team": "SEA", "positions": ["G"],
                                         "slot": None}}
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [(p.id, p.slot) for p in players] == [(1, "C"), (11, "BN")]
    assert state["decisions"][0]["type"] == "add"


def test_weekly_plan_waits_for_noon_on_the_weeks_first_day(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    morning = dt.datetime(2026, 10, 5, 7, tzinfo=dt.timezone.utc)  # 10:00 Helsinki
    main.weekly_step(state, players, {"teams": {}, "taken": []}, morning, False, main.Outbox(settings),
                     build_context=lambda d: 1 / 0)
    assert state["weeks"] == {} and sent == []


def test_trade_command_is_judged_later_in_the_run(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/trade B for Nobody"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert state["trade_request"] == "B for Nobody"
    main.trade_step(state, players, league, NOW, main.Outbox(settings))
    assert sent == ["No rostered player called 'Nobody'."]
    assert state["trade_request"] is None
