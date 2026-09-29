"""Environment-driven configuration.

Locally, values can come from a `.env` file (via python-dotenv); in GitHub
Actions they come from repo secrets (see .github/workflows/assistant_gm.yml).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    dry_run: bool
    # Webhook relay (relay/): set all three for instant replies, none to poll.
    relay_url: str | None = None
    relay_token: str | None = None
    webhook_secret: str | None = None


def _env(name: str) -> str | None:
    # Pasted secrets often carry a stray space or newline.
    return (os.environ.get(name) or "").strip() or None


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        dry_run=os.environ.get("DRY_RUN", "0") == "1",
        relay_url=(_env("RELAY_URL") or "").rstrip("/") or None,
        relay_token=_env("RELAY_TOKEN"),
        webhook_secret=_env("TELEGRAM_WEBHOOK_SECRET"),
    )
