"""Telegram Bot API: messages with inline buttons, and polling for taps.

No server of our own: each run reads new messages and taps, either polling
getUpdates (picked up by the next half-hourly run) or, with the webhook relay
set up, from the relay's queue (the relay starts a run right away).
"""
from __future__ import annotations

import requests

API_URL = "https://api.telegram.org/bot{token}/{method}"
FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramError(requests.HTTPError):
    """A rejected Bot API call. The message carries Telegram's reason but not
    the URL, which contains the bot token."""


def _call(token: str, method: str, **payload) -> dict:
    resp = requests.post(API_URL.format(token=token, method=method), json=payload, timeout=30)
    if not resp.ok:
        try:
            reason = resp.json().get("description", "")
        except ValueError:
            reason = resp.text[:200]
        raise TelegramError(f"Telegram {method} failed ({resp.status_code}): {reason}", response=resp)
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


def get_relayed_updates(relay_url: str, relay_token: str, offset: int) -> tuple[list[dict], str | None]:
    """Updates queued by the webhook relay (relay/worker.js), which stands in
    for getUpdates while a webhook is set, plus its last error starting a run."""
    resp = requests.post(f"{relay_url}/updates", json={"offset": offset},
                         headers={"Authorization": f"Bearer {relay_token}"}, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    return body["result"], body.get("dispatch_error")


def webhook_info(token: str) -> dict:
    return _call(token, "getWebhookInfo")


def set_webhook(token: str, url: str, secret: str) -> None:
    _call(token, "setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])


def delete_webhook(token: str) -> None:
    _call(token, "deleteWebhook")


def mark_handled(token: str, chat_id: str, message_id: int, label: str) -> None:
    """Replace a message's buttons with a single inert label, e.g. "Recorded: Done".
    Cosmetic, so a refusal (already labeled, message too old) is ignored."""
    try:
        _call(token, "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
              reply_markup=_keyboard([(label, "noop")]))
    except TelegramError:
        pass


def answer_callback(token: str, callback_id: str, text: str) -> None:
    """Acknowledge a tap. Telegram rejects answers to old taps; that's harmless."""
    try:
        _call(token, "answerCallbackQuery", callback_query_id=callback_id, text=text)
    except requests.HTTPError:
        pass


def download_file(token: str, file_id: str) -> bytes:
    """A file someone sent the bot (a screenshot). Errors leave out the
    download URL, which contains the token."""
    path = _call(token, "getFile", file_id=file_id)["file_path"]
    resp = requests.get(FILE_URL.format(token=token, path=path), timeout=60)
    if not resp.ok:
        raise TelegramError(f"Telegram file download failed ({resp.status_code})", response=resp)
    return resp.content
