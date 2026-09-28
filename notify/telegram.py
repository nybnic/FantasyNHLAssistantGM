"""Telegram Bot API: messages with inline buttons, and polling for taps.

No server: each scheduled run polls getUpdates, so a tap is picked up by the
next run (within ~30 minutes).
"""
from __future__ import annotations

import requests

API_URL = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, **payload) -> dict:
    resp = requests.post(API_URL.format(token=token, method=method), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["result"]


def _keyboard(buttons: list[tuple[str, str]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}


def send_message(token: str, chat_id: str, text: str, buttons: list[tuple[str, str]] | None = None) -> int:
    """Send `text`; `buttons` are (label, callback data) pairs. Returns the message id."""
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = _keyboard(buttons)
    return _call(token, "sendMessage", **payload)["message_id"]


def get_updates(token: str, offset: int) -> list[dict]:
    return _call(token, "getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])


def mark_handled(token: str, chat_id: str, message_id: int, label: str) -> None:
    """Replace a message's buttons with a single inert label, e.g. "Recorded: Done"."""
    _call(token, "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
          reply_markup=_keyboard([(label, "noop")]))


def answer_callback(token: str, callback_id: str, text: str) -> None:
    """Acknowledge a tap. Telegram rejects answers to old taps; that's harmless."""
    try:
        _call(token, "answerCallbackQuery", callback_query_id=callback_id, text=text)
    except requests.HTTPError:
        pass
