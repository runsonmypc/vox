import pytest


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path, monkeypatch):
    """Keep tests from touching the real ~/.local/share/vox/history.db."""
    monkeypatch.setattr("vox.history.DEFAULT_HISTORY_PATH", tmp_path / "history.db")
