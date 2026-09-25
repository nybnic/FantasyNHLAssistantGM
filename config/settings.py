"""Environment-driven configuration.

Locally, values can come from a `.env` file (via python-dotenv); in GitHub
Actions they come from repo secrets passed through as env vars (see
.github/workflows/assistant_gm.yml).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class Settings:
    yahoo_client_id: str
    yahoo_client_secret: str
    yahoo_oauth_json: str
    yahoo_league_key: str

    telegram_bot_token: str
    telegram_chat_id: str

    dry_run: bool
    state_file: Path
    oauth_token_file: Path

    github_repository: str | None
    github_secret_pat: str | None

    goalie_lookahead_days: int


def load_settings() -> Settings:
    return Settings(
        yahoo_client_id=_env("YAHOO_CLIENT_ID", required=True),
        yahoo_client_secret=_env("YAHOO_CLIENT_SECRET", required=True),
        yahoo_oauth_json=_env("YAHOO_OAUTH_JSON", required=True),
        yahoo_league_key=_env("YAHOO_LEAGUE_KEY", required=True),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", required=True),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID", required=True),
        dry_run=_env("DRY_RUN", "0") == "1",
        state_file=Path(_env("STATE_FILE", "state/last_run.json")),
        oauth_token_file=Path(_env("OAUTH_TOKEN_FILE", "oauth2.json")),
        github_repository=_env("GITHUB_REPOSITORY"),
        github_secret_pat=_env("GH_PAT"),
        goalie_lookahead_days=int(_env("GOALIE_LOOKAHEAD_DAYS", "1")),
    )
