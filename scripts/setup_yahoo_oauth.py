"""One-time local setup: authorize Assistant GM against your Yahoo account
and discover your NHL league key.

Usage:
    python scripts/setup_yahoo_oauth.py

You need YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET set as env vars first (from
your Yahoo Developer app, Fantasy Sports API, Read permission). A browser
window will open asking you to approve access; paste the verifier code
Yahoo shows you back into this terminal.
"""
from __future__ import annotations

import json
import os
import sys

import yahoo_fantasy_api as yfa
from yahoo_oauth import OAuth2

TOKEN_FILE = "oauth2.json"


def main() -> None:
    client_id = os.environ.get("YAHOO_CLIENT_ID")
    client_secret = os.environ.get("YAHOO_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET env vars first.")

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"consumer_key": client_id, "consumer_secret": client_secret}, f)

    session = OAuth2(None, None, from_file=TOKEN_FILE)

    game = yfa.Game(session, "nhl")
    league_ids = game.league_ids(game_codes=["nhl"])

    print("\nYour NHL league key(s):")
    for league_key in league_ids:
        league = game.to_league(league_key)
        league_settings = league.settings()
        print(f"  {league_key}  -  {league_settings.get('name')}")

    print(
        "\nCopy the league key for THIS league and save it as the "
        "YAHOO_LEAGUE_KEY secret/env var.\n"
    )

    with open(TOKEN_FILE, encoding="utf-8") as f:
        token_json = f.read()

    print("Save the following as the YAHOO_OAUTH_JSON GitHub secret:\n")
    print(token_json)


if __name__ == "__main__":
    main()
