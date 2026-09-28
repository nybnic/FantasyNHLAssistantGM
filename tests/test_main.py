import main
from config.settings import load_settings
from league.roster import RosterPlayer
from state import gm_state


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
    main.process_updates(settings, state, players, main.Outbox(settings))
    assert [p.slot for p in players] == ["BN", "C"]
    assert state["pending"] == {}
    assert state["decisions"][0]["decision"] == "done"
    assert state["telegram_offset"] == 6
    assert handled == ["Recorded: Done"]


def test_skip_leaves_the_roster_alone(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("skip:lineup-2026-11-10-2100")])
    main.process_updates(settings, state, players, main.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert handled == ["Recorded: Skipped"]


def test_taps_from_other_chats_are_ignored(monkeypatch, tmp_path):
    settings, state, players, _, _ = _setup(monkeypatch, tmp_path, [_tap("done:lineup-2026-11-10-2100", chat_id=999)])
    main.process_updates(settings, state, players, main.Outbox(settings))
    assert [p.slot for p in players] == ["C", "BN"]
    assert "lineup-2026-11-10-2100" in state["pending"]


def test_roster_command_replies_with_the_roster(monkeypatch, tmp_path):
    message = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/roster"}}
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [message])
    main.process_updates(settings, state, players, main.Outbox(settings))
    assert "A (BOS, C)" in sent[0]
