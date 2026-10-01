import pytest

from league import positions


@pytest.fixture(autouse=True)
def _positions_file(tmp_path, monkeypatch):
    """Tests never touch the bot's real state/positions.json."""
    monkeypatch.setattr(positions, "POSITIONS_FILE", tmp_path / "positions.json")
