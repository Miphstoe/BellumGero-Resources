from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from app.importing.core3_live.importer import dry_run, import_snapshot
from app.importing.core3_live.snapshot import STAT_CODES, SnapshotValidationError, read_snapshot


requires_test_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for database integration tests",
)


def write_snapshot(path: Path, *, captured_at: str = "2026-09-29T12:00:00+00:00", resources: list[dict] | None = None, **overrides) -> Path:
    payload = {
        "schema_version": 1,
        "source_system": "core3",
        "source_instance": "bellum-gero-live",
        "complete": True,
        "captured_at": captured_at,
        "core3_revision": "test-rev",
        "exporter_version": "test-exporter",
        "resources": resources if resources is not None else [resource_payload()],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def resource_payload(
    *,
    oid: str = "900719925474099312345",
    name: str = "core-testium",
    resource_type: str = "test_core3_resource_type",
    planets: list[str] | None = None,
    stats: dict[str, int] | None = None,
    expires_at: str | None = "2026-09-30T12:00:00+00:00",
    spawned_at: str | None = "2026-09-29T11:00:00+00:00",
    despawned_at: str | None = None,
) -> dict:
    return {
        "oid": oid,
        "name": name,
        "type": resource_type,
        "planets": ["yavin4"] if planets is None else planets,
        "stats": {"OQ": 0, "DR": 500} if stats is None else stats,
        "expires_at": expires_at,
        "spawned_at": spawned_at,
        "despawned_at": despawned_at,
    }


def ensure_core3_type(db, ids, slug: str = "test_core3_resource_type") -> int:
    type_id = db.execute(text("SELECT id FROM resource_types WHERE slug = :slug"), {"slug": slug}).scalar_one_or_none()
    if type_id is None:
        type_id = db.execute(
            text(
                """
                INSERT INTO resource_types (slug, display_name, kind, is_spawnable)
                VALUES (:slug, :name, 'core3', true)
                RETURNING id
                """
            ),
            {"slug": slug, "name": slug.replace("_", " ").title()},
        ).scalar_one()
    db.execute(
        text(
            """
            INSERT INTO source_resource_types
                (source_instance_id, source_type_key, source_type_name, canonical_type_id, mapping_status, mapping_confidence)
            VALUES
                (:source_instance_id, :slug, :name, :type_id, 'mapped', 'authoritative')
            ON CONFLICT (source_instance_id, source_type_key) DO UPDATE SET
                canonical_type_id = EXCLUDED.canonical_type_id,
                mapping_status = 'mapped',
                mapping_confidence = 'authoritative'
            """
        ),
        {"source_instance_id": ids["core3_instance"], "slug": slug, "name": slug.replace("_", " ").title(), "type_id": type_id},
    )
    return type_id


def current_row(db, oid: str):
    return db.execute(
        text(
            """
            SELECT cra.is_active, cra.last_seen_at, cra.current_as_of, cra.absent_as_of, sr.id, sr.canonical_resource_id
            FROM source_resources sr
            JOIN current_resource_availability cra ON cra.source_resource_id = sr.id
            WHERE sr.source_resource_id = :oid
              AND sr.source_instance_id = (SELECT id FROM source_instances WHERE code = 'bellum-gero-live')
            """
        ),
        {"oid": oid},
    ).first()


def source_resource_id(db, oid: str) -> int:
    return db.execute(
        text(
            """
            SELECT id FROM source_resources
            WHERE source_resource_id = :oid
              AND source_instance_id = (SELECT id FROM source_instances WHERE code = 'bellum-gero-live')
            """
        ),
        {"oid": oid},
    ).scalar_one()


def test_parser_rejects_non_authoritative_and_malformed_snapshots(tmp_path):
    with pytest.raises(SnapshotValidationError, match="complete=false"):
        read_snapshot(write_snapshot(tmp_path / "incomplete.json", complete=False))
    with pytest.raises(SnapshotValidationError, match="wrong source_system"):
        read_snapshot(write_snapshot(tmp_path / "wrong-source.json", source_system="galaxy_harvester"))
    with pytest.raises(SnapshotValidationError, match="unsupported schema_version"):
        read_snapshot(write_snapshot(tmp_path / "schema.json", schema_version=2))
    with pytest.raises(SnapshotValidationError, match="malformed OID"):
        read_snapshot(write_snapshot(tmp_path / "oid.json", resources=[resource_payload(oid="1.25")]))
    with pytest.raises(SnapshotValidationError, match="duplicate OID"):
        read_snapshot(write_snapshot(tmp_path / "dupe.json", resources=[resource_payload(oid="1"), resource_payload(oid="1", name="other")]))
    with pytest.raises(SnapshotValidationError, match="ER"):
        read_snapshot(write_snapshot(tmp_path / "er.json", resources=[resource_payload(stats={"ER": 1})]))
    with pytest.raises(SnapshotValidationError, match="despawned_at"):
        read_snapshot(write_snapshot(tmp_path / "despawned.json", resources=[resource_payload(despawned_at="2026-09-29T12:00:00+00:00")]))


@requires_test_db
def test_valid_first_snapshot_preserves_identity_name_type_stats_and_planets(tmp_path, db, ids):
    type_id = ensure_core3_type(db, ids)
    path = write_snapshot(tmp_path / "snapshot.json", resources=[resource_payload(planets=[], stats={"OQ": 0})])

    result = import_snapshot(db, path)
    oid = "900719925474099312345"
    sr_id = source_resource_id(db, oid)
    row = current_row(db, oid)

    assert result["advanced_current"] is True
    assert row.is_active is True
    assert db.execute(text("SELECT source_resource_id, source_resource_name FROM source_resources WHERE id = :id"), {"id": sr_id}).one() == (oid, "core-testium")
    assert db.execute(text("SELECT canonical_type_id FROM source_resource_types WHERE source_type_key = 'test_core3_resource_type'")).scalar_one() == type_id
    assert db.execute(text("SELECT count(*) FROM resource_planet_observations WHERE source_resource_id = :id"), {"id": sr_id}).scalar_one() == 0

    stats = {
        row.stat_code: (row.value, row.is_present)
        for row in db.execute(text("SELECT stat_code, value, is_present FROM resource_stat_observations WHERE source_resource_id = :id"), {"id": sr_id})
    }
    assert set(stats) == set(STAT_CODES)
    assert stats["OQ"] == (0, True)
    assert all(stats[code] == (None, False) for code in set(STAT_CODES) - {"OQ"})


@requires_test_db
def test_planet_changes_new_appearance_disappearance_reappearance_and_no_fake_despawn(tmp_path, db, ids):
    ensure_core3_type(db, ids)
    oid_a = "1001"
    oid_b = "1002"
    import_snapshot(db, write_snapshot(tmp_path / "a.json", captured_at="2026-09-29T12:00:00+00:00", resources=[resource_payload(oid=oid_a, planets=["yavin4"])]))
    import_snapshot(
        db,
        write_snapshot(
            tmp_path / "b.json",
            captured_at="2026-09-29T12:05:00+00:00",
            resources=[
                resource_payload(oid=oid_a, planets=["corellia"]),
                resource_payload(oid=oid_b, name="new-core-testium", planets=["corellia"]),
            ],
        ),
    )
    import_snapshot(db, write_snapshot(tmp_path / "c.json", captured_at="2026-09-29T12:10:00+00:00", resources=[resource_payload(oid=oid_b, name="new-core-testium")]))
    import_snapshot(db, write_snapshot(tmp_path / "d.json", captured_at="2026-09-29T12:15:00+00:00", resources=[resource_payload(oid=oid_a, planets=["yavin4"])]))

    assert current_row(db, oid_a).is_active is True
    assert current_row(db, oid_b).is_active is False
    assert current_row(db, oid_b).absent_as_of.isoformat().startswith("2026-09-29T12:15:00")
    assert db.execute(
        text("SELECT count(*) FROM resource_lifecycle_events WHERE source_resource_id = :id AND event_type = 'despawned'"),
        {"id": source_resource_id(db, oid_b)},
    ).scalar_one() == 0
    assert db.execute(
        text("SELECT count(DISTINCT planet_id) FROM resource_planet_observations WHERE source_resource_id = :id"),
        {"id": source_resource_id(db, oid_a)},
    ).scalar_one() == 2


@requires_test_db
def test_idempotency_conflict_and_out_of_order_current_guard(tmp_path, db, ids):
    ensure_core3_type(db, ids)
    first_path = write_snapshot(tmp_path / "first.json", captured_at="2026-09-29T12:00:00+00:00", resources=[resource_payload(oid="2001")])
    first = import_snapshot(db, first_path)
    duplicate = import_snapshot(db, first_path)
    before_counts = db.execute(text("SELECT count(*) FROM resource_stat_observations")).scalar_one()
    assert first["advanced_current"] is True
    assert duplicate["status"] == "duplicate"
    assert db.execute(text("SELECT count(*) FROM resource_stat_observations")).scalar_one() == before_counts

    conflict_path = write_snapshot(tmp_path / "conflict.json", captured_at="2026-09-29T12:00:00+00:00", resources=[resource_payload(oid="2001", name="changed")])
    with pytest.raises(ValueError, match="Snapshot conflict"):
        import_snapshot(db, conflict_path)

    older_path = write_snapshot(
        tmp_path / "older.json",
        captured_at="2026-09-29T11:55:00+00:00",
        resources=[resource_payload(oid="2001", name="older-name"), resource_payload(oid="2002")],
    )
    older = import_snapshot(db, older_path)
    assert older["advanced_current"] is False
    assert current_row(db, "2001").is_active is True
    assert current_row(db, "2002") is None
    assert db.execute(text("SELECT source_resource_name FROM source_resources WHERE source_resource_id = '2001'")).scalar_one() == "core-testium"


@requires_test_db
def test_unknown_db_references_reject_without_current_mutation_and_dry_run_is_read_only(tmp_path, db, ids):
    ensure_core3_type(db, ids)
    baseline = write_snapshot(tmp_path / "baseline.json", resources=[resource_payload(oid="3001")])
    import_snapshot(db, baseline)
    before = current_row(db, "3001")

    with pytest.raises(ValueError, match="Unknown Core3 resource types"):
        import_snapshot(db, write_snapshot(tmp_path / "unknown-type.json", captured_at="2026-09-29T12:05:00+00:00", resources=[resource_payload(oid="3002", resource_type="unknown_type")]))
    with pytest.raises(ValueError, match="Unknown Core3 planets"):
        import_snapshot(db, write_snapshot(tmp_path / "unknown-planet.json", captured_at="2026-09-29T12:06:00+00:00", resources=[resource_payload(oid="3003", planets=["unknown"])]) )

    dry_before = db.execute(text("SELECT count(*) FROM import_batches")).scalar_one()
    plan = dry_run(db, write_snapshot(tmp_path / "dry.json", captured_at="2026-09-29T12:07:00+00:00", resources=[resource_payload(oid="3004")]))
    assert plan["dry_run"] is True
    assert db.execute(text("SELECT count(*) FROM import_batches")).scalar_one() == dry_before
    assert current_row(db, "3001").current_as_of == before.current_as_of


@requires_test_db
def test_gh_resources_are_isolated_and_same_name_does_not_merge(tmp_path, db, ids):
    ensure_core3_type(db, ids)
    gh_canonical = db.execute(text("INSERT INTO resources (name, status) VALUES ('shared-name', 'historical') RETURNING id")).scalar_one()
    gh_source = db.execute(
        text(
            """
            INSERT INTO source_resources
                (source_instance_id, source_resource_id, source_resource_name, canonical_resource_id, identity_status, confidence)
            VALUES
                (:source_instance_id, 'gh-1', 'shared-name', :canonical_resource_id, 'linked', 'source_identity')
            RETURNING id
            """
        ),
        {"source_instance_id": ids["gh_instance"], "canonical_resource_id": gh_canonical},
    ).scalar_one()

    import_snapshot(db, write_snapshot(tmp_path / "core.json", resources=[resource_payload(oid="4001", name="shared-name")]))
    core_row = db.execute(
        text(
            """
            SELECT id, canonical_resource_id
            FROM source_resources
            WHERE source_instance_id = :source_instance_id
              AND source_resource_id = '4001'
            """
        ),
        {"source_instance_id": ids["core3_instance"]},
    ).one()

    assert core_row.id != gh_source
    assert core_row.canonical_resource_id != gh_canonical
    assert db.execute(text("SELECT canonical_resource_id FROM source_resources WHERE id = :id"), {"id": gh_source}).scalar_one() == gh_canonical
