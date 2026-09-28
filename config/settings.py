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


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        dry_run=os.environ.get("DRY_RUN", "0") == "1",
    )
