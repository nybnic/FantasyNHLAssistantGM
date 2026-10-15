import pytest

from clients import nhl_client
from league import positions


@pytest.fixture(autouse=True)
def _positions_file(tmp_path, monkeypatch):
    """Tests never touch the bot's real state/positions.json."""
    monkeypatch.setattr(positions, "POSITIONS_FILE", tmp_path / "positions.json")


@pytest.fixture(autouse=True)
def _no_schedule(monkeypatch):
    """Tests stay offline: no NHL games unless a test sets its own
    (bot.common.nhl_date asks for the day's games on every plan run)."""
    monkeypatch.setattr(nhl_client, "games_on", lambda d: [])
