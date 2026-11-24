import pytest

from backend.app import create_app


def test_production_requires_long_admin_secret(monkeypatch):
    monkeypatch.setenv("MOTIONLAB_ENV", "production")
    monkeypatch.delenv("MOTIONLAB_ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="MOTIONLAB_ADMIN_TOKEN"):
        create_app(database_url="postgresql+psycopg://user:pass@localhost/motionlab")


def test_production_rejects_sqlite(monkeypatch):
    monkeypatch.setenv("MOTIONLAB_ENV", "production")
    monkeypatch.setenv("MOTIONLAB_ADMIN_TOKEN", "x" * 40)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        create_app(database_url="sqlite:///./must-not-be-created.db")
