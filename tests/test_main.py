import datetime as dt

import pytest

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
    assert "A (BOS, C)" in sent[0] and "/myteam" in sent[0]


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


def _roster_paste(n):
    return "\n".join(f"BN\tPlayer {NAMES[i]}" for i in range(n))


NAMES = [f"{c}son" for c in "ABCDEFGHIJKLMNOPQ"]
ROSTER_REGISTRY = [{"id": 100 + i, "name": f"Player {NAMES[i]}", "team": "BOS", "position": "C"} for i in range(17)]


def test_myteam_then_paste_replaces_my_roster(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(
        monkeypatch, tmp_path, [_message("/myteam", 9), _message(_roster_paste(12), 10)])
    monkeypatch.setattr(main.parse, "registry", lambda: ROSTER_REGISTRY)
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [p.id for p in players] == list(range(100, 112))
    assert state["awaiting"] is None
    assert sent[1].startswith("Roster saved: 12 players.\nAdded: Player Ason")
    assert "Dropped: A, B" in sent[1]


def test_myteam_rejects_a_partial_paste(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [_message("/myteam\n" + _roster_paste(3))])
    monkeypatch.setattr(main.parse, "registry", lambda: ROSTER_REGISTRY)
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [p.id for p in players] == [1, 2]
    assert "Your roster is unchanged" in sent[0]


def test_no_first_briefing_after_the_window_closes(monkeypatch, tmp_path):
    settings, state, players, sent, _ = _setup(monkeypatch, tmp_path, [])
    game = main.nhl_client.ScheduledGame(1, dt.datetime(2026, 10, 1, 23, tzinfo=dt.timezone.utc), "TOR", "BOS")
    monkeypatch.setattr(main.nhl_client, "games_on", lambda d: [game])
    late = dt.datetime(2026, 10, 1, 17, 35, tzinfo=dt.timezone.utc)  # 20:35 Helsinki
    main.briefing_step(state, players, late, False, main.Outbox(settings), build_context=lambda d: 1 / 0)
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
    monkeypatch.setattr(main.parse, "registry", lambda: SHOT_REGISTRY)
    monkeypatch.setattr(main.telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(main.screenshot, "read", lambda image: {"kind": "team", "rows": SHOTS[image.decode()]})
    return settings, state, players, sent


def test_two_screenshots_in_one_run_replace_my_roster(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9), _photo("bottom", 10)])
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
    assert [(p.id, p.slot) for p in players][:1] == [(200, "C")] and len(players) == 12
    assert players[-1].slot == "BN"
    assert sent[0].startswith("Roster saved: 12 players.")


def test_screenshots_across_runs_wait_for_the_rest(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9)])
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert [p.id for p in players] == [1, 2]
    assert "read 8 players so far" in sent[0] and "/save" in sent[0]
    monkeypatch.setattr(main.telegram, "get_updates", lambda token, offset: [_photo("bottom", 10, 1_790_000_060)])
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert len(players) == 12 and sent[1].startswith("Roster saved")


def test_a_screenshot_without_players_says_so(monkeypatch, tmp_path):
    settings, state, players, sent = _screenshots(monkeypatch, tmp_path, [_photo("top", 9)])
    monkeypatch.setattr(main.screenshot, "read", lambda image: {"kind": "team", "rows": []})
    main.process_updates(settings, state, players, {"teams": {}, "taken": []}, main.Outbox(settings))
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
    monkeypatch.setattr(main.parse, "registry", lambda: SHOT_REGISTRY + OPP_REGISTRY)
    monkeypatch.setattr(main.telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(main.screenshot, "read", lambda image: shots[image.decode()])
    monkeypatch.setattr(main.nhl_client, "games_on", lambda d: [])
    players = players if players is not None else [RosterPlayer(200 + i, f"Paul {n}", "BOS", ["C"], "BN")
                                                   for i, n in enumerate(SHOT_NAMES)]
    league = {"teams": {"Bahelin Boys": {"updated": "2026-09-29", "players": [
        {"id": 300 + i, "name": f"Rick {n}", "team": "TOR", "positions": ["C"], "slot": None}
        for i, n in enumerate(OPP_NAMES[:11])] + [
        {"id": 399, "name": "Old Guy", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
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
    monkeypatch.setattr(main.matchup, "_so_far", lambda roster, ctx, days: (5.0, 3.0, 1))
    assert main._banked(13.4, 8.2, [], None, []) == (pytest.approx(5.2), 8.2, 1)
    assert main._banked(13.4, None, [], None, []) == (pytest.approx(10.4), 3.0, 1)


def test_the_plan_goes_out_at_the_start_of_the_week_and_once_from_wednesday():
    mon, tue, wed, thu = (dt.date(2026, 10, d) for d in (5, 6, 7, 8))  # week 2
    assert main.weeks.midweek(2) == wed and main.weeks.midweek(1) == dt.date(2026, 9, 30)
    assert main.plan_due(None, mon, 2)
    assert not main.plan_due({"sent": "x"}, tue, 2)
    assert main.plan_due({"sent": "x"}, wed, 2) and main.plan_due({"sent": "x"}, thu, 2)
    assert not main.plan_due({"sent": "x", "midweek": "y"}, thu, 2)


def test_the_dashboard_data_carries_the_summary_and_every_view(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(main, "SITE_DIR", tmp_path)
    me = main.matchup.TeamWeek("me", 13.4, 153.0, 400.0, 26, 1, 3, 1.0)
    them = main.matchup.TeamWeek("them", 51.5, 156.0, 400.0, 23, 1, 3, 1.0)
    wk = SimpleNamespace(me=me, them=them, days=[dt.date(2026, 9, 29), dt.date(2026, 10, 4)], season_used=1,
                         max_moves=1, yahoo_projected=[159.79, 164.52])
    views = {"decision": {"points": []}, "schedule": {}, "budget": {}, "adds": {}, "streamer_text": "x"}
    path = main.write_dashboard(views, wk, 1, "Bahelin Boys", "Mid-week: chase", NOW, dry_run=False)
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
    monkeypatch.setattr(main.parse, "registry", lambda: TX_REGISTRY)
    monkeypatch.setattr(main.telegram, "download_file", lambda token, file_id: file_id.encode())
    monkeypatch.setattr(main.screenshot, "read", lambda image: {"kind": "transactions", "rows": shots[image.decode()]})
    league = league or {"teams": {
        "Pastasauce": {"updated": "2026-09-29", "players": [
            {"id": 702, "name": "Dylan Cozens", "team": "TOR", "positions": ["C"], "slot": None},
            {"id": 701, "name": "Justin Faulk", "team": "TOR", "positions": ["D"], "slot": None}]},
        "Vanilla Thunder": {"updated": "2026-09-29", "players": [
            {"id": 703, "name": "John Tavares", "team": "TOR", "positions": ["C"], "slot": None}]}}, "taken": []}
    players = players if players is not None else default_players
    main.process_updates(settings, state, players, league, main.Outbox(settings))
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
    assert 701 not in main.teams.rostered_ids(league)  # Faulk is a free agent again
    assert 704 in [p.id for p in players]  # my own add, onto my roster
    assert sent[0].startswith("Transactions: 3 new (Wed 30 Sep 10:59 - Wed 30 Sep 14:04).")
    assert "Pastasauce: +Jared McCann, -Justin Faulk, -Dylan Cozens, +John Tavares" in sent[0]


def test_transactions_already_applied_are_skipped_and_a_gap_is_flagged(monkeypatch, tmp_path):
    old = [_tx("add", (9, 30, 14, 3), ["Pastasauce"], [("J. McCann", "add")])]
    state, players, league, sent = _transactions(monkeypatch, tmp_path, {"a": old, "b": old})
    assert sent == [sent[0]] and "1 new" in sent[0]
    later = [_tx("drop", (10, 3, 9, 0), ["Pastasauce"], [("J. McCann", "drop")])]
    monkeypatch.setattr(main.telegram, "get_updates", lambda token, offset: [_photo("c", 60, WEEK1 + 99)])
    monkeypatch.setattr(main.screenshot, "read", lambda image: {"kind": "transactions", "rows": later})
    settings = load_settings()
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert "may be missing" in sent[-1] and 700 not in _ids(league, "Pastasauce")


def test_taken_marks_the_suggested_player_and_asks_for_the_next_best(monkeypatch, tmp_path):
    settings, state, players, _, handled = _setup(monkeypatch, tmp_path, [_tap("taken:add-1")])
    state["pending"]["add-1"] = {"type": "add", "date": "2026-10-01", "add": {"id": 900}, "drop": None, "message_id": 7}
    league = {"teams": {}, "taken": []}
    main.process_updates(settings, state, players, league, main.Outbox(settings))
    assert league["taken"] == [900] and state["week_requested"]
    assert state["decisions"][-1]["decision"] == "taken" and handled == ["Taken: finding the next best"]
