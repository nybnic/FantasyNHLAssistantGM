"""Assistant GM entrypoint - run on a schedule by GitHub Actions.

Pulls the Yahoo roster, the real NHL schedule, and confirmed starting
goalies, then sends a Telegram message if any roster action looks worth
taking. Never modifies the Yahoo roster itself (notify-only).
"""
from __future__ import annotations

import logging

from auth import yahoo_oauth
from clients import yahoo_client
from config.settings import load_settings
from engine.recommend import build_recommendations
from notify import telegram
from state import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    original_token_json = settings.yahoo_oauth_json

    try:
        session = yahoo_oauth.get_session(settings)
        yl = yahoo_client.connect(session, settings.yahoo_league_key)

        roster = yahoo_client.get_roster(yl)
        roster_slots = yahoo_client.get_roster_slots(yl)

        recommendations = build_recommendations(
            roster, roster_slots, settings.goalie_lookahead_days
        )

        sent = store.load(settings.state_file)
        new_recs = store.filter_new(sent, recommendations)

        if not new_recs:
            logger.info(
                "Nothing new to report (%d total recommendations, all already sent)",
                len(recommendations),
            )
            return

        message = "Assistant GM:\n\n" + "\n\n".join(f"- {r.message}" for r in new_recs)

        if settings.dry_run:
            print(message)
        else:
            telegram.send_message(
                settings.telegram_bot_token, settings.telegram_chat_id, message
            )
            store.save(settings.state_file, sent, new_recs)
    finally:
        yahoo_oauth.persist_token_if_changed(settings, original_token_json)


if __name__ == "__main__":
    main()
