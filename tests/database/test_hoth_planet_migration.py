"""Exercise real Alembic transitions in isolated PostgreSQL schemas."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.importing.core3_live.importer import import_snapshot
from tests.database.conftest import create_import_batch, create_source_resource
from tests.importing.test_core3_live_importer import ensure_core3_type, resource_payload, write_snapshot

PREVIOUS = '20261009_0003'
REVISION = '20261012_0004'


@pytest.fixture()
def migration_database(database_url):
    # database_url enforces an isolated loopback database ending in _test.
    # Each test has its own schema, so no shared suite data needs to be reset.
    schema = 'hoth_migration_' + uuid4().hex
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    url = make_url(database_url).update_query_dict({'options': '-csearch_path=' + schema + ',public'})
    engine = create_engine(url)
    # Shadow the shared public revision ledger while retaining access to
    # public pgcrypto functions required by the snapshot importer.
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'database/migrations'))
    config.set_main_option('sqlalchemy.url', url.render_as_string(hide_password=False).replace('%', '%%'))
    try:
        command.upgrade(config, PREVIOUS)
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def hoth(connection):
    return connection.execute(text("SELECT id, slug, display_name, core3_zone_name, active FROM planets WHERE slug = 'hoth'")).one()


def register_manual_hoth(connection):
    return connection.execute(text("INSERT INTO planets (slug, display_name, core3_zone_name, active) VALUES ('hoth', 'Manual Hoth', 'legacy-hoth', false) RETURNING id")).scalar_one()


def table_rows(connection):
    tables = connection.execute(text('SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename')).scalars()
    return {table: sorted(json.dumps(value, sort_keys=True) for value in connection.execute(text(
        'SELECT to_jsonb(t) FROM ' + connection.dialect.identifier_preparer.quote(table) + ' t'
    )).scalars()) for table in tables}


def test_hoth_revision_is_single_alembic_head(alembic_config):
    script = ScriptDirectory.from_config(alembic_config)
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def test_original_ten_planets_gain_canonical_hoth(migration_database):
    engine, config = migration_database
    with engine.connect() as connection:
        before = connection.execute(text('SELECT * FROM planets ORDER BY id')).all()
        assert len(before) == 10
        assert all(row.slug != 'hoth' for row in before)
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM planets')).scalar_one() == 11
        assert tuple(hoth(connection))[1:] == ('hoth', 'Hoth', 'hoth', True)
        assert connection.execute(text("SELECT * FROM planets WHERE slug <> 'hoth' ORDER BY id")).all() == before
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == REVISION


def test_existing_hoth_identity_and_source_mappings_survive(migration_database):
    engine, config = migration_database
    with engine.begin() as connection:
        planet_id = register_manual_hoth(connection)
        connection.execute(text("INSERT INTO source_planets (source_instance_id, source_planet_id, source_planet_name, planet_id, metadata) SELECT id, 'hoth-manual-key', 'Manual Source Name', :planet, '{\"keep\": true}' FROM source_instances WHERE code IN ('bellum-gero-live', 'galaxy-153')"), {'planet': planet_id})
        before = connection.execute(text('SELECT * FROM source_planets ORDER BY id')).all()
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        assert tuple(hoth(connection)) == (planet_id, 'hoth', 'Hoth', 'hoth', True)
        assert connection.execute(text('SELECT * FROM source_planets ORDER BY id')).all() == before


def test_repeated_safe_insertion_keeps_one_hoth(migration_database):
    engine, config = migration_database
    command.upgrade(config, REVISION)
    migration = ScriptDirectory.from_config(config).get_revision(REVISION).module
    with engine.begin() as connection:
        before = table_rows(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert table_rows(connection) == before
        assert connection.execute(text("SELECT count(*) FROM planets WHERE slug = 'hoth'")).scalar_one() == 1


def seed_history(connection, tmp_path, planet):
    core_id = connection.execute(text("SELECT id FROM source_instances WHERE code = 'bellum-gero-live'")).scalar_one()
    gh_id = connection.execute(text("SELECT id FROM source_instances WHERE code = 'galaxy-153'")).scalar_one()
    ensure_core3_type(connection, {'core3_instance': core_id}, 'meat_wild_hoth')
    path = write_snapshot(tmp_path / 'hoth-snapshot.json', resources=[resource_payload(resource_type='meat_wild_hoth', planets=[planet])])
    import_snapshot(connection, path)
    source_id = create_source_resource(connection, gh_id, 'gh-preserve-hoth', 'GH Historical Resource')
    batch_id = create_import_batch(connection, gh_id, 'gh-migration-history-fixture')
    connection.execute(text("INSERT INTO resource_stat_observations (source_resource_id, stat_code, value, is_present, import_batch_id) VALUES (:source, 'OQ', 700, true, :batch)"), {'source': source_id, 'batch': batch_id})
    planet_id = connection.execute(text('SELECT id FROM planets WHERE slug = :slug'), {'slug': planet}).scalar_one()
    connection.execute(text("UPDATE resource_types SET specific_planet_id = :id WHERE slug = 'meat_wild_hoth'"), {'id': planet_id})
    # Include a creature-catalog FK without changing creature functionality.
    revision_id = connection.execute(text("INSERT INTO creature_revisions (repository, revision, manifest_hash, status) VALUES ('fixture', 'hoth-migration-test', 'fixture-hash', 'complete') RETURNING id")).scalar_one()
    file_id = connection.execute(text("INSERT INTO creature_source_files (revision_id, path, sha256, role) VALUES (:revision, 'hoth.lua', 'fixture-hash', 'region') RETURNING id"), {'revision': revision_id}).scalar_one()
    connection.execute(text("INSERT INTO creature_spawn_nodes (revision_id, kind, key, source_file_id, line, planet_id) VALUES (:revision, 'region', 'hoth-region', :file, 1, :planet)"), {'revision': revision_id, 'file': file_id, 'planet': planet_id})


def test_upgrade_preserves_core3_gh_creature_and_classification_data(migration_database, tmp_path):
    engine, config = migration_database
    with engine.begin() as connection:
        seed_history(connection, tmp_path, 'yavin4')
        before = table_rows(connection)
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        after = table_rows(connection)
        for table in before.keys() - {'planets', 'alembic_version'}:
            assert after[table] == before[table], table
        assert set(before['planets']) < set(after['planets'])


def test_hoth_snapshot_resolves_after_upgrade(migration_database, tmp_path):
    engine, config = migration_database
    command.upgrade(config, REVISION)
    with engine.begin() as connection:
        seed_history(connection, tmp_path, 'hoth')
        planet_id = hoth(connection).id
        assert connection.execute(text('SELECT DISTINCT planet_id FROM resource_planet_observations')).scalars().all() == [planet_id]
        assert connection.execute(text("SELECT planet_id FROM source_planets WHERE source_planet_id = 'hoth'")).scalar_one() == planet_id


def test_downgrade_preserves_referenced_hoth_history(migration_database, tmp_path):
    engine, config = migration_database
    with engine.begin() as connection:
        planet_id = register_manual_hoth(connection)
        seed_history(connection, tmp_path, 'hoth')
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        before = table_rows(connection)
    command.downgrade(config, PREVIOUS)
    with engine.connect() as connection:
        after = table_rows(connection)
        assert hoth(connection).id == planet_id
        assert all(after[table] == before[table] for table in before.keys() - {'alembic_version'})
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == PREVIOUS
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        assert table_rows(connection) == before


def test_downgrade_retains_unreferenced_hoth(migration_database):
    engine, config = migration_database
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        before = tuple(hoth(connection))
    command.downgrade(config, PREVIOUS)
    with engine.connect() as connection:
        assert tuple(hoth(connection)) == before
