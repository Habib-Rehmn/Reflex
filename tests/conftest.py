import pytest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Never write application/test history into a developer's database."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "requests.db"))
