"""Minimal Telegram Bot API notifier (sendMessage only)."""
from __future__ import annotations

import requests

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        API_URL.format(token=bot_token),
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
