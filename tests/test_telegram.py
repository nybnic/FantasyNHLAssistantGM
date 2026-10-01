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
