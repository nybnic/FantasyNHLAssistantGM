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


def test_html_messages_and_link_buttons(monkeypatch):
    import json
    calls = _capture(monkeypatch)
    telegram.send_message("123:SECRET", "42", "<b>Week 2</b>", html=True, links=[("Why?", "https://x/#move=1:2")])
    assert calls[0]["json"]["parse_mode"] == "HTML"
    assert calls[0]["json"]["reply_markup"]["inline_keyboard"] == [[{"text": "Why?", "url": "https://x/#move=1:2"}]
                                                                   + DASHBOARD_ROW]
    telegram.send_message("123:SECRET", "42", "plain")
    assert "parse_mode" not in calls[1]["json"]
    long = "<b>" + "x" * 1100 + "</b>"  # an HTML caption isn't cut (a cut tag would be refused)
    telegram.send_photo("123:SECRET", "42", b"png", long, html=True)
    assert calls[2]["data"]["caption"] == long and calls[2]["data"]["parse_mode"] == "HTML"
    assert json.loads(calls[2]["data"]["reply_markup"])["inline_keyboard"] == [DASHBOARD_ROW]


def _answers(monkeypatch, *answers):
    """requests.post answering each call with the next of `answers` (an exception is raised)."""
    calls, queue = [], list(answers)

    def post(url, **k):
        calls.append(url)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(telegram.requests, "post", post)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    return calls


RESET = requests.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")


def test_a_dropped_connection_on_a_read_is_tried_again(monkeypatch):
    """2026-11-25: one reset on getWebhookInfo failed a whole run."""
    calls = _answers(monkeypatch, RESET, _Response(502, {"ok": False}), _Response(200, {"ok": True, "result": {"url": ""}}))
    assert telegram.webhook_info("123:SECRET") == {"url": ""}
    assert len(calls) == 3


def test_a_read_that_stays_down_still_fails(monkeypatch):
    calls = _answers(monkeypatch, RESET, RESET, RESET)
    with pytest.raises(requests.ConnectionError):
        telegram.webhook_info("123:SECRET")
    assert len(calls) == 3


def test_a_refused_call_is_not_tried_again(monkeypatch):
    calls = _answers(monkeypatch, _Response(400, {"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(requests.HTTPError):
        telegram.get_updates("123:SECRET", 0)
    assert len(calls) == 1


def test_a_send_that_may_have_arrived_is_not_repeated(monkeypatch):
    """A reset or a 5xx after the message went out would post it twice."""
    for failure in (RESET, requests.ReadTimeout("read timed out"), _Response(502, {"ok": False})):
        calls = _answers(monkeypatch, failure, _Response(200, {"ok": True, "result": {"message_id": 9}}))
        with pytest.raises(requests.RequestException):
            telegram.send_message("123:SECRET", "42", "Add X")
        assert len(calls) == 1


def test_a_send_telegram_never_took_is_tried_again(monkeypatch):
    ok = {"ok": True, "result": {"message_id": 9}}
    calls = _answers(monkeypatch, requests.ConnectTimeout("connect timed out"),
                     _Response(429, {"ok": False, "description": "Too Many Requests: retry after 1"}), _Response(200, ok))
    assert telegram.send_message("123:SECRET", "42", "Add X") == 9
    calls = _answers(monkeypatch, _Response(429, {"ok": False}), _Response(200, ok))
    assert telegram.send_photo("123:SECRET", "42", b"png", "card") == 9
    assert len(calls) == 2
