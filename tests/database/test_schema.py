from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.database.conftest import (
    create_import_batch,
    create_resource,
    create_resource_type,
    create_source_resource,
)


def test_alembic_upgrade_downgrade_reupgrade(alembic_config, engine):
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM stat_definitions")).scalar_one() == 11


def test_required_reference_data_exists(db):
    assert db.execute(text("SELECT count(*) FROM stat_definitions")).scalar_one() == 11
    assert db.execute(text("SELECT core3_attribute_name FROM stat_definitions WHERE code = 'ER'")).scalar_one() is None
    assert db.execute(text("SELECT count(*) FROM planets")).scalar_one() == 10
    assert db.execute(text("SELECT count(*) FROM source_systems WHERE code IN ('galaxy_harvester', 'core3')")).scalar_one() == 2
    assert db.execute(text("SELECT external_id FROM source_instances WHERE code = 'galaxy-153'")).scalar_one() == "153"


def test_duplicate_gh_source_identity_with_null_epoch_is_rejected(engine, ids):
    with engine.begin() as conn:
        create_source_resource(conn, ids["gh_instance"], "12345", "alpha")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            create_source_resource(conn, ids["gh_instance"], "12345", "beta")


def test_same_source_resource_id_allowed_in_different_source_instances(db, ids):
    left = create_source_resource(db, ids["gh_instance"], "shared-identity", "ghname")
    right = create_source_resource(db, ids["core3_instance"], "shared-identity", "core3name")
    assert left != right


def test_explicit_zero_stat_is_valid(db, ids):
    batch_id = create_import_batch(db, ids["gh_instance"], "stat-zero")
    source_resource_id = create_source_resource(db, ids["gh_instance"], "zero-stat", "zeronium")
    inserted = db.execute(
        text(
            "INSERT INTO resource_stat_observations "
            "(source_resource_id, stat_code, value, is_present, import_batch_id) "
            "VALUES (:source_resource_id, 'OQ', 0, true, :batch_id) RETURNING id"
        ),
        {"source_resource_id": source_resource_id, "batch_id": batch_id},
    ).scalar_one()
    assert inserted > 0


def test_missing_stat_is_valid(db, ids):
    batch_id = create_import_batch(db, ids["gh_instance"], "stat-missing")
    source_resource_id = create_source_resource(db, ids["gh_instance"], "missing-stat", "nullium")
    inserted = db.execute(
        text(
            "INSERT INTO resource_stat_observations "
            "(source_resource_id, stat_code, value, is_present, import_batch_id) "
            "VALUES (:source_resource_id, 'ER', NULL, false, :batch_id) RETURNING id"
        ),
        {"source_resource_id": source_resource_id, "batch_id": batch_id},
    ).scalar_one()
    assert inserted > 0


def test_present_stat_requires_value(engine, ids):
    with engine.begin() as conn:
        batch_id = create_import_batch(conn, ids["gh_instance"], "stat-invalid-present")
        source_resource_id = create_source_resource(conn, ids["gh_instance"], "invalid-present", "badium")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_stat_observations "
                    "(source_resource_id, stat_code, value, is_present, import_batch_id) "
                    "VALUES (:source_resource_id, 'OQ', NULL, true, :batch_id)"
                ),
                {"source_resource_id": source_resource_id, "batch_id": batch_id},
            )


def test_missing_stat_requires_null_value(engine, ids):
    with engine.begin() as conn:
        batch_id = create_import_batch(conn, ids["gh_instance"], "stat-invalid-missing")
        source_resource_id = create_source_resource(conn, ids["gh_instance"], "invalid-missing", "badium")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_stat_observations "
                    "(source_resource_id, stat_code, value, is_present, import_batch_id) "
                    "VALUES (:source_resource_id, 'OQ', 1, false, :batch_id)"
                ),
                {"source_resource_id": source_resource_id, "batch_id": batch_id},
            )


def test_resource_type_self_edge_is_rejected(engine):
    with engine.begin() as conn:
        type_id = create_resource_type(conn, "test_self_edge")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_type_edges (parent_type_id, child_type_id, source) "
                    "VALUES (:type_id, :type_id, 'test')"
                ),
                {"type_id": type_id},
            )


def test_closure_depth_zero_self_membership_is_allowed(db):
    type_id = create_resource_type(db, "test_closure_self")
    inserted = db.execute(
        text(
            "INSERT INTO resource_type_closure (ancestor_type_id, descendant_type_id, depth) "
            "VALUES (:type_id, :type_id, 0) RETURNING depth"
        ),
        {"type_id": type_id},
    ).scalar_one()
    assert inserted == 0


def test_invalid_resource_type_stat_ranges_are_rejected(engine):
    with engine.begin() as conn:
        type_id = create_resource_type(conn, "test_invalid_range")

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_type_stat_ranges "
                    "(resource_type_id, stat_code, is_applicable, min_value, max_value) "
                    "VALUES (:type_id, 'OQ', false, 0, NULL)"
                ),
                {"type_id": type_id},
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_type_stat_ranges "
                    "(resource_type_id, stat_code, is_applicable, min_value, max_value) "
                    "VALUES (:type_id, 'OQ', true, 900, 100)"
                ),
                {"type_id": type_id},
            )


def test_canonical_resource_has_public_uuid_distinct_from_bigint_id(db):
    resource_id, public_id = create_resource(db, "canonical-test")
    parsed = uuid.UUID(public_id)
    assert resource_id > 0
    assert str(parsed) == public_id
    assert str(resource_id) != public_id


def test_one_source_resource_cannot_be_confirmed_to_multiple_canonical_resources(engine, ids):
    with engine.begin() as conn:
        source_resource_id = create_source_resource(conn, ids["gh_instance"], "confirmed-source", "confirmium")
        first_resource_id, _ = create_resource(conn, "canonical-one")
        second_resource_id, _ = create_resource(conn, "canonical-two")
        conn.execute(
            text(
                "INSERT INTO resource_identity_links "
                "(canonical_resource_id, source_resource_id, link_status, confirmed_by) "
                "VALUES (:resource_id, :source_resource_id, 'confirmed', 'test')"
            ),
            {"resource_id": first_resource_id, "source_resource_id": source_resource_id},
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_identity_links "
                    "(canonical_resource_id, source_resource_id, link_status, confirmed_by) "
                    "VALUES (:resource_id, :source_resource_id, 'confirmed', 'test')"
                ),
                {"resource_id": second_resource_id, "source_resource_id": source_resource_id},
            )


def test_unresolved_source_resource_can_exist_without_resource_identity(db, ids):
    batch_id = create_import_batch(db, ids["gh_instance"], "unresolved")
    inserted = db.execute(
        text(
            "INSERT INTO unresolved_source_resources "
            "(source_instance_id, import_batch_id, source_name, normalized_name, result_status, http_status, source_result_text) "
            "VALUES (:source_instance_id, :batch_id, 'dweina', 'dweina', 'unresolved_new_result', 200, 'new') "
            "RETURNING id"
        ),
        {"source_instance_id": ids["gh_instance"], "batch_id": batch_id},
    ).scalar_one()
    assert inserted > 0


def test_source_values_outside_conventional_stat_ranges_can_be_stored(db, ids):
    batch_id = create_import_batch(db, ids["gh_instance"], "anomaly-value")
    source_resource_id = create_source_resource(db, ids["gh_instance"], "outside-range", "bipa-like")
    inserted = db.execute(
        text(
            "INSERT INTO resource_stat_observations "
            "(source_resource_id, stat_code, value, is_present, import_batch_id) "
            "VALUES (:source_resource_id, 'PE', 5000, true, :batch_id) RETURNING id"
        ),
        {"source_resource_id": source_resource_id, "batch_id": batch_id},
    ).scalar_one()
    assert inserted > 0


def test_core3_live_snapshot_ledger_rejects_same_capture_with_different_hash(engine, ids):
    with engine.begin() as conn:
        first_batch_id = create_import_batch(conn, ids["core3_instance"], "core3-ledger-a")
        second_batch_id = create_import_batch(conn, ids["core3_instance"], "core3-ledger-b")
        first_snapshot_id = conn.execute(
            text(
                """
                INSERT INTO source_snapshots
                    (import_batch_id, snapshot_kind, external_path, content_sha256, observed_at)
                VALUES
                    (:batch_id, 'core3_live_resource_snapshot', 'a.json', 'hash-a', '2026-09-29T12:00:00+00:00')
                RETURNING id
                """
            ),
            {"batch_id": first_batch_id},
        ).scalar_one()
        second_snapshot_id = conn.execute(
            text(
                """
                INSERT INTO source_snapshots
                    (import_batch_id, snapshot_kind, external_path, content_sha256, observed_at)
                VALUES
                    (:batch_id, 'core3_live_resource_snapshot', 'b.json', 'hash-b', '2026-09-29T12:00:00+00:00')
                RETURNING id
                """
            ),
            {"batch_id": second_batch_id},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO core3_live_snapshot_imports
                    (source_instance_id, source_snapshot_id, captured_at, content_sha256, complete, status)
                VALUES
                    (:source_instance_id, :snapshot_id, '2026-09-29T12:00:00+00:00', 'hash-a', true, 'complete')
                """
            ),
            {"source_instance_id": ids["core3_instance"], "snapshot_id": first_snapshot_id},
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO core3_live_snapshot_imports
                        (source_instance_id, source_snapshot_id, captured_at, content_sha256, complete, status)
                    VALUES
                        (:source_instance_id, :snapshot_id, '2026-09-29T12:00:00+00:00', 'hash-b', true, 'complete')
                    """
                ),
                {"source_instance_id": ids["core3_instance"], "snapshot_id": second_snapshot_id},
            )
