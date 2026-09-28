from __future__ import annotations

import pytest

from app.db.alembic_url import resolve_alembic_database_url


def test_explicit_alembic_config_url_wins_when_bellum_and_test_urls_differ(monkeypatch):
    bellum_url = "postgresql+psycopg://bellum:bellum@localhost:55432/bellum_resources"
    test_url = "postgresql+psycopg://bellum:bellum@localhost:55432/bellum_resources_test"

    monkeypatch.setenv("BELLUM_DATABASE_URL", bellum_url)
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)

    resolved = resolve_alembic_database_url(lambda key: test_url if key == "sqlalchemy.url" else None)

    assert resolved == test_url


def test_alembic_falls_back_to_bellum_url_for_normal_commands(monkeypatch):
    bellum_url = "postgresql+psycopg://bellum:bellum@localhost:55432/bellum_resources"
    test_url = "postgresql+psycopg://bellum:bellum@localhost:55432/bellum_resources_test"

    monkeypatch.setenv("BELLUM_DATABASE_URL", bellum_url)
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)

    resolved = resolve_alembic_database_url(lambda key: "driver://placeholder" if key == "sqlalchemy.url" else None)

    assert resolved == bellum_url


def test_alembic_requires_bellum_url_without_explicit_config(monkeypatch):
    monkeypatch.delenv("BELLUM_DATABASE_URL", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://bellum:bellum@localhost:55432/bellum_resources_test")

    with pytest.raises(RuntimeError, match="BELLUM_DATABASE_URL"):
        resolve_alembic_database_url(lambda key: "driver://placeholder" if key == "sqlalchemy.url" else None)
