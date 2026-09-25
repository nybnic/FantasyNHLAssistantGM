"""Yahoo OAuth2 session handling.

Loads the persisted token JSON (the YAHOO_OAUTH_JSON secret) into a local
file, builds a `yahoo_oauth.OAuth2` session - which auto-refreshes an
expired access token using the refresh token - and exposes a way to
re-persist the token file as a GitHub secret if it changed during the run
(Yahoo's refresh token isn't guaranteed to stay the same across refreshes).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess

from yahoo_oauth import OAuth2

from config.settings import Settings

logger = logging.getLogger(__name__)


def _write_token_file(settings: Settings) -> None:
    payload = json.loads(settings.yahoo_oauth_json)
    payload.setdefault("consumer_key", settings.yahoo_client_id)
    payload.setdefault("consumer_secret", settings.yahoo_client_secret)
    settings.oauth_token_file.write_text(json.dumps(payload), encoding="utf-8")


def get_session(settings: Settings) -> OAuth2:
    """Return a ready-to-use, auto-refreshed Yahoo OAuth2 session.

    Requires the token file to already contain access_token/refresh_token/
    token_type/token_time (produced by scripts/setup_yahoo_oauth.py) -
    otherwise yahoo_oauth falls back to an interactive browser flow, which
    would hang in CI.
    """
    _write_token_file(settings)
    session = OAuth2(None, None, from_file=str(settings.oauth_token_file))
    if not session.token_is_valid():
        raise RuntimeError(
            "Yahoo OAuth token could not be refreshed. Re-run "
            "scripts/setup_yahoo_oauth.py locally and update the "
            "YAHOO_OAUTH_JSON secret."
        )
    return session


def persist_token_if_changed(settings: Settings, original_json: str) -> None:
    """Re-upload the token file as a GitHub secret if it changed.

    No-op if the token file was never written (e.g. get_session failed
    before writing it), or if GH_PAT/GITHUB_REPOSITORY aren't set - which is
    also the normal case for local dry runs.
    """
    if not settings.oauth_token_file.exists():
        return

    current = settings.oauth_token_file.read_text(encoding="utf-8")
    if json.loads(current) == json.loads(original_json):
        return

    if not settings.github_repository or not settings.github_secret_pat:
        logger.warning(
            "Yahoo token changed but GH_PAT/GITHUB_REPOSITORY isn't set; "
            "the refreshed token will NOT be persisted for the next run."
        )
        return

    logger.info("Yahoo token changed, updating YAHOO_OAUTH_JSON secret")
    subprocess.run(
        [
            "gh", "secret", "set", "YAHOO_OAUTH_JSON",
            "--repo", settings.github_repository,
            "--body", current,
        ],
        check=True,
        env={**os.environ, "GH_TOKEN": settings.github_secret_pat},
    )
