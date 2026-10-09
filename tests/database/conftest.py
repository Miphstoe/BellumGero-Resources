from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required for database integration tests")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or not (parsed.database or "").endswith("_test"):
        pytest.fail("Tests require a loopback PostgreSQL database whose name ends in _test")
    development = os.environ.get("BELLUM_DATABASE_URL")
    if development:
        other = make_url(development)
        if (parsed.host, parsed.port, parsed.database) == (other.host, other.port, other.database):
            pytest.fail("TEST_DATABASE_URL must not target BELLUM_DATABASE_URL")
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    os.environ["TEST_DATABASE_URL"] = database_url
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "database" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="session")
def engine(database_url: str, alembic_config: Config):
    engine = create_engine(database_url, future=True)
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture()
def ids(db):
    return {
        "gh_instance": db.execute(text("SELECT id FROM source_instances WHERE code = 'galaxy-153'")).scalar_one(),
        "core3_instance": db.execute(text("SELECT id FROM source_instances WHERE code = 'bellum-gero-live'")).scalar_one(),
    }


def create_import_batch(conn, source_instance_id: int, kind: str = "test") -> int:
    return conn.execute(
        text(
            "INSERT INTO import_batches (source_instance_id, import_kind, status) "
            "VALUES (:source_instance_id, :kind, 'running') RETURNING id"
        ),
        {"source_instance_id": source_instance_id, "kind": kind},
    ).scalar_one()


def create_source_resource(conn, source_instance_id: int, source_resource_id: str, name: str = "testium") -> int:
    return conn.execute(
        text(
            "INSERT INTO source_resources "
            "(source_instance_id, source_resource_id, source_resource_name, identity_status, confidence) "
            "VALUES (:source_instance_id, :source_resource_id, :name, 'source_only', 'imported') "
            "RETURNING id"
        ),
        {"source_instance_id": source_instance_id, "source_resource_id": source_resource_id, "name": name},
    ).scalar_one()


def create_resource_type(conn, slug: str) -> int:
    return conn.execute(
        text(
            "INSERT INTO resource_types (slug, display_name, kind) "
            "VALUES (:slug, :display_name, 'canonical') RETURNING id"
        ),
        {"slug": slug, "display_name": slug.replace("_", " ").title()},
    ).scalar_one()


def create_resource(conn, name: str) -> tuple[int, str]:
    row = conn.execute(
        text("INSERT INTO resources (name, status) VALUES (:name, 'draft') RETURNING id, public_id::text"),
        {"name": name},
    ).one()
    return row[0], row[1]
