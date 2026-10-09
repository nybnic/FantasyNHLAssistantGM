import pytest
import requests

from notify import telegram


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = str(body)

    def json(self):
        return self._body


def test_errors_carry_telegrams_reason_but_not_the_token(monkeypatch):
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: _Response(
        400, {"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(requests.HTTPError) as err:
        telegram.send_message("123:SECRET", "42", "hi")
    assert "chat not found" in str(err.value) and "SECRET" not in str(err.value)


def test_relabeling_an_already_labeled_message_is_harmless(monkeypatch):
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: _Response(
        400, {"ok": False, "description": "Bad Request: message is not modified"}))
    telegram.mark_handled("123:SECRET", "42", 7, "Recorded: Skipped")


def test_a_photo_goes_up_as_a_file_with_caption_and_buttons(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, **k: calls.append(k) or _Response(
        200, {"ok": True, "result": {"message_id": 9}}))
    assert telegram.send_photo("123:SECRET", "42", b"png", "x" * 2000, [("Done", "done:1")]) == 9
    sent = calls[0]
    assert sent["files"]["photo"][1] == b"png" and len(sent["data"]["caption"]) == 1024
    assert "done:1" in sent["data"]["reply_markup"]


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, **k: calls.append(k) or _Response(
        200, {"ok": True, "result": {"message_id": 9}}))
    return calls


DASHBOARD_ROW = [{"text": "Dashboard", "url": telegram.DASHBOARD_URL + "?v=1760000000"}]


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(telegram.time, "time", lambda: 1760000000.5)


def test_every_message_links_the_dashboard_under_its_own_buttons(monkeypatch):
    calls = _capture(monkeypatch)
    telegram.send_message("123:SECRET", "42", "Week 2: 1-0")
    telegram.send_message("123:SECRET", "42", "Add X", [("Done", "done:1"), ("Skip", "skip:1")])
    plain, card = (c["json"]["reply_markup"]["inline_keyboard"] for c in calls)
    assert plain == [DASHBOARD_ROW]
    assert [b["text"] for b in card[0]] == ["Done", "Skip"] and card[1] == DASHBOARD_ROW
    assert "http" not in calls[0]["json"]["text"]  # a button, no link preview


def test_a_photo_and_a_handled_card_keep_the_dashboard_link(monkeypatch):
    import json
    calls = _capture(monkeypatch)
    telegram.send_photo("123:SECRET", "42", b"png")
    assert json.loads(calls[0]["data"]["reply_markup"])["inline_keyboard"] == [DASHBOARD_ROW]
    telegram.mark_handled("123:SECRET", "42", 7, "Recorded: Done")
    assert calls[1]["json"]["reply_markup"]["inline_keyboard"][-1] == DASHBOARD_ROW


def test_each_message_links_the_dashboard_fresh_not_a_cached_copy(monkeypatch):
    monkeypatch.setattr(telegram.time, "time", lambda: 1760000123.0)
    assert telegram.dashboard_link() == "https://nybnic.github.io/FantasyNHLAssistantGM/?v=1760000123"
