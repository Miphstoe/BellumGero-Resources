from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import make_engine
from app.importing.core3_live.reconcile import Core3Reconciliation, reconcile_snapshot
from app.importing.core3_live.snapshot import Core3ResourceSnapshot, Core3ResourceSnapshotItem, SOURCE_INSTANCE, STAT_CODES, read_snapshot


IMPORT_KIND = "core3_live_resource_snapshot"
TOOL_VERSION = "phase4b-core3-live-importer-v1"


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def dry_run(connection, snapshot_path: Path) -> dict[str, Any]:
    snapshot = read_snapshot(snapshot_path)
    rec = reconcile_snapshot(connection, snapshot, mutate_source_planets=False)
    conflict = captured_at_conflict(connection, rec.source_instance_id, snapshot)
    duplicate = existing_snapshot_import(connection, rec.source_instance_id, snapshot) is not None
    last_current = latest_advanced_snapshot_time(connection, rec.source_instance_id)
    advances_current = not duplicate and not conflict and (last_current is None or snapshot.captured_at > last_current)
    return {
        "dry_run": True,
        "source_identity": {"source_system": "core3", "source_instance": SOURCE_INSTANCE},
        "snapshot": {
            "captured_at": snapshot.captured_at,
            "content_sha256": snapshot.content_sha256,
            "resources": len(snapshot.resources),
            "complete": snapshot.complete,
        },
        "validation": {
            "duplicate": duplicate,
            "captured_at_conflict": conflict,
            "advances_current": advances_current,
            "last_advanced_current_at": last_current,
        },
        "expected_observations": {
            "stat_observations": len(snapshot.resources) * len(STAT_CODES),
            "planet_observations": sum(len(resource.planets) for resource in snapshot.resources),
        },
    }


def import_snapshot(connection, snapshot_path: Path) -> dict[str, Any]:
    snapshot = read_snapshot(snapshot_path)
    rec = reconcile_snapshot(connection, snapshot)
    existing = existing_snapshot_import(connection, rec.source_instance_id, snapshot)
    if existing:
        return {"status": "duplicate", "ledger_id": existing["id"], "advanced_current": existing["advanced_current"]}
    if captured_at_conflict(connection, rec.source_instance_id, snapshot):
        raise ValueError(f"Snapshot conflict for {snapshot.captured_at.isoformat()}: same captured_at with different content hash")

    inserted = Counter()
    advances_current = should_advance_current(connection, rec.source_instance_id, snapshot)
    batch_id = insert_import_batch(connection, rec.source_instance_id, snapshot_path, snapshot)
    snapshot_id = insert_source_snapshot(connection, batch_id, snapshot_path, snapshot)

    present_source_resource_ids: set[int] = set()
    for resource in snapshot.resources:
        source_record_id = upsert_source_record(connection, snapshot_id, resource, inserted)
        canonical_type_id = rec.canonical_type_ids[resource.resource_type]
        canonical_resource_id = upsert_canonical_resource(connection, resource, canonical_type_id, inserted)
        source_resource_id = upsert_source_resource(connection, rec, resource, canonical_resource_id, source_record_id, advances_current, inserted)
        present_source_resource_ids.add(source_resource_id)
        upsert_type_membership(connection, source_resource_id, canonical_type_id, source_record_id, inserted)
        upsert_name(connection, source_resource_id, canonical_resource_id, resource, source_record_id, inserted)
        upsert_stats(connection, source_resource_id, batch_id, snapshot, resource, source_record_id, inserted)
        upsert_resource_observation(connection, source_resource_id, resource, source_record_id, snapshot, inserted)
        upsert_planet_observations(connection, source_resource_id, batch_id, snapshot, resource, rec, source_record_id, inserted)
        upsert_lifecycle(connection, source_resource_id, batch_id, resource, source_record_id, inserted)

    ledger_id = insert_snapshot_ledger(connection, rec.source_instance_id, snapshot_id, snapshot, advances_current)
    if advances_current:
        reconcile_current_availability(connection, rec.source_instance_id, snapshot_id, snapshot, present_source_resource_ids, inserted)

    summary = dict(inserted)
    summary.update({"ledger_id": ledger_id, "advanced_current": advances_current})
    connection.execute(
        text("UPDATE import_batches SET status = 'complete', summary = CAST(:summary AS jsonb), finished_at = now() WHERE id = :id"),
        {"id": batch_id, "summary": json.dumps(summary, sort_keys=True, default=str)},
    )
    return summary


def existing_snapshot_import(connection, source_instance_id: int, snapshot: Core3ResourceSnapshot):
    row = connection.execute(
        text(
            """
            SELECT id, advanced_current
            FROM core3_live_snapshot_imports
            WHERE source_instance_id = :source_instance_id
              AND captured_at = :captured_at
              AND content_sha256 = :content_sha256
            """
        ),
        {
            "source_instance_id": source_instance_id,
            "captured_at": snapshot.captured_at,
            "content_sha256": snapshot.content_sha256,
        },
    ).mappings().first()
    return row


def captured_at_conflict(connection, source_instance_id: int, snapshot: Core3ResourceSnapshot) -> bool:
    return (
        connection.execute(
            text(
                """
                SELECT 1
                FROM core3_live_snapshot_imports
                WHERE source_instance_id = :source_instance_id
                  AND captured_at = :captured_at
                  AND content_sha256 <> :content_sha256
                LIMIT 1
                """
            ),
            {
                "source_instance_id": source_instance_id,
                "captured_at": snapshot.captured_at,
                "content_sha256": snapshot.content_sha256,
            },
        ).first()
        is not None
    )


def latest_advanced_snapshot_time(connection, source_instance_id: int):
    return connection.execute(
        text(
            """
            SELECT max(captured_at)
            FROM core3_live_snapshot_imports
            WHERE source_instance_id = :source_instance_id
              AND complete = true
              AND status = 'complete'
              AND advanced_current = true
            """
        ),
        {"source_instance_id": source_instance_id},
    ).scalar_one()


def should_advance_current(connection, source_instance_id: int, snapshot: Core3ResourceSnapshot) -> bool:
    latest = latest_advanced_snapshot_time(connection, source_instance_id)
    return latest is None or snapshot.captured_at > latest


def insert_import_batch(connection, source_instance_id: int, snapshot_path: Path, snapshot: Core3ResourceSnapshot) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO import_batches
                (source_instance_id, import_kind, status, tool_version, source_revision, parameters)
            VALUES
                (:source_instance_id, :kind, 'running', :tool_version, :source_revision, CAST(:parameters AS jsonb))
            RETURNING id
            """
        ),
        {
            "source_instance_id": source_instance_id,
            "kind": IMPORT_KIND,
            "tool_version": TOOL_VERSION,
            "source_revision": snapshot.core3_revision,
            "parameters": json.dumps({"snapshot_path": str(snapshot_path), "content_sha256": snapshot.content_sha256}, sort_keys=True),
        },
    ).scalar_one()


def insert_source_snapshot(connection, batch_id: int, snapshot_path: Path, snapshot: Core3ResourceSnapshot) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO source_snapshots
                (import_batch_id, snapshot_kind, external_path, content_sha256, byte_size,
                 record_count, observed_at, metadata)
            VALUES
                (:batch_id, :kind, :path, :content_sha256, :byte_size,
                 :record_count, :observed_at, CAST(:metadata AS jsonb))
            RETURNING id
            """
        ),
        {
            "batch_id": batch_id,
            "kind": IMPORT_KIND,
            "path": str(snapshot_path),
            "content_sha256": snapshot.content_sha256,
            "byte_size": snapshot.byte_size,
            "record_count": len(snapshot.resources),
            "observed_at": snapshot.captured_at,
            "metadata": json.dumps(snapshot_metadata(snapshot), sort_keys=True),
        },
    ).scalar_one()


def snapshot_metadata(snapshot: Core3ResourceSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "source_system": snapshot.source_system,
        "source_instance": snapshot.source_instance,
        "complete": snapshot.complete,
        "core3_revision": snapshot.core3_revision,
        "exporter_version": snapshot.exporter_version,
    }


def upsert_source_record(connection, snapshot_id: int, resource: Core3ResourceSnapshotItem, inserted: Counter) -> int:
    payload = json.dumps(resource.payload, sort_keys=True)
    row = connection.execute(
        text(
            """
            INSERT INTO source_records
                (source_snapshot_id, record_type, source_key, parse_status, payload_hash, normalized_payload)
            VALUES
                (:snapshot_id, 'core3_live_resource', :source_key, 'parsed', encode(digest(:payload, 'sha256'), 'hex'), CAST(:payload AS jsonb))
            ON CONFLICT (source_snapshot_id, record_type, source_key) DO UPDATE SET
                payload_hash = EXCLUDED.payload_hash,
                normalized_payload = EXCLUDED.normalized_payload
            RETURNING id, (xmax = 0) AS inserted
            """
        ),
        {"snapshot_id": snapshot_id, "source_key": resource.oid, "payload": payload},
    ).one()
    inserted["source_records_inserted" if row[1] else "source_records_reused"] += 1
    return row[0]


def upsert_canonical_resource(connection, resource: Core3ResourceSnapshotItem, canonical_type_id: int, inserted: Counter) -> int:
    row = connection.execute(
        text(
            """
            SELECT canonical_resource_id
            FROM source_resources
            WHERE source_instance_id = (SELECT id FROM source_instances WHERE code = :source_instance)
              AND server_epoch_id IS NULL
              AND source_resource_id = :oid
            """
        ),
        {"source_instance": SOURCE_INSTANCE, "oid": resource.oid},
    ).first()
    if row and row[0]:
        inserted["canonical_resources_reused"] += 1
        return row[0]
    resource_id = connection.execute(
        text(
            """
            INSERT INTO resources (name, canonical_type_id, status, notes)
            VALUES (:name, :canonical_type_id, 'live', 'Imported from Core3 live resource snapshot')
            RETURNING id
            """
        ),
        {"name": resource.name, "canonical_type_id": canonical_type_id},
    ).scalar_one()
    inserted["canonical_resources_inserted"] += 1
    return resource_id


def upsert_source_resource(
    connection,
    rec: Core3Reconciliation,
    resource: Core3ResourceSnapshotItem,
    canonical_resource_id: int,
    source_record_id: int,
    update_latest_identity: bool,
    inserted: Counter,
) -> int:
    row = connection.execute(
        text(
            """
            INSERT INTO source_resources
                (source_instance_id, server_instance_id, source_resource_id, source_resource_name,
                 source_resource_type_id, canonical_resource_id, identity_status, confidence,
                 first_source_record_id, last_source_record_id, metadata)
            VALUES
                (:source_instance_id, :server_instance_id, :oid, :name,
                 :source_type_id, :canonical_resource_id, 'linked', 'source_identity',
                 :source_record_id, :source_record_id, CAST(:metadata AS jsonb))
            ON CONFLICT (source_instance_id, server_epoch_id, source_resource_id) DO UPDATE SET
                source_resource_name = CASE WHEN :update_latest_identity THEN EXCLUDED.source_resource_name ELSE source_resources.source_resource_name END,
                source_resource_type_id = CASE WHEN :update_latest_identity THEN EXCLUDED.source_resource_type_id ELSE source_resources.source_resource_type_id END,
                canonical_resource_id = COALESCE(source_resources.canonical_resource_id, EXCLUDED.canonical_resource_id),
                last_source_record_id = CASE WHEN :update_latest_identity THEN EXCLUDED.last_source_record_id ELSE source_resources.last_source_record_id END,
                metadata = CASE WHEN :update_latest_identity THEN EXCLUDED.metadata ELSE source_resources.metadata END
            RETURNING id, (xmax = 0) AS inserted
            """
        ),
        {
            "source_instance_id": rec.source_instance_id,
            "server_instance_id": rec.server_instance_id,
            "oid": resource.oid,
            "name": resource.name,
            "source_type_id": rec.source_type_ids[resource.resource_type],
            "canonical_resource_id": canonical_resource_id,
            "source_record_id": source_record_id,
            "update_latest_identity": update_latest_identity,
            "metadata": json.dumps({"core3_oid": resource.oid}, sort_keys=True),
        },
    ).one()
    inserted["source_resources_inserted" if row[1] else "source_resources_reused"] += 1
    return row[0]


def upsert_type_membership(connection, source_resource_id: int, canonical_type_id: int, source_record_id: int, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            INSERT INTO resource_type_memberships
                (source_resource_id, resource_type_id, membership_kind, source_record_id)
            VALUES
                (:source_resource_id, :resource_type_id, 'canonical_final_type', :source_record_id)
            ON CONFLICT (source_resource_id, resource_type_id) DO UPDATE SET
                membership_kind = EXCLUDED.membership_kind,
                source_record_id = COALESCE(resource_type_memberships.source_record_id, EXCLUDED.source_record_id)
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {"source_resource_id": source_resource_id, "resource_type_id": canonical_type_id, "source_record_id": source_record_id},
    ).one()
    inserted["type_memberships_inserted" if row[0] else "type_memberships_reused"] += 1


def upsert_name(connection, source_resource_id: int, canonical_resource_id: int, resource: Core3ResourceSnapshotItem, source_record_id: int, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            INSERT INTO resource_names (canonical_resource_id, source_resource_id, name, normalized_name, source_record_id)
            VALUES (:canonical_resource_id, :source_resource_id, :name, :normalized_name, :source_record_id)
            ON CONFLICT (source_resource_id, normalized_name) DO NOTHING
            RETURNING id
            """
        ),
        {
            "canonical_resource_id": canonical_resource_id,
            "source_resource_id": source_resource_id,
            "name": resource.name,
            "normalized_name": normalize_name(resource.name),
            "source_record_id": source_record_id,
        },
    ).first()
    inserted["names_inserted" if row else "names_reused"] += 1


def upsert_stats(connection, source_resource_id: int, batch_id: int, snapshot: Core3ResourceSnapshot, resource: Core3ResourceSnapshotItem, source_record_id: int, inserted: Counter) -> None:
    for code in STAT_CODES:
        present = code in resource.stats
        row = connection.execute(
            text(
                """
                INSERT INTO resource_stat_observations
                    (source_resource_id, stat_code, value, is_present, observed_at, source_record_id, import_batch_id)
                VALUES
                    (:source_resource_id, :stat_code, :value, :present, :observed_at, :source_record_id, :batch_id)
                ON CONFLICT (source_resource_id, stat_code, source_record_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "source_resource_id": source_resource_id,
                "stat_code": code,
                "value": resource.stats.get(code),
                "present": present,
                "observed_at": snapshot.captured_at,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
            },
        ).first()
        inserted["stat_observations_inserted" if row else "stat_observations_reused"] += 1


def upsert_resource_observation(connection, source_resource_id: int, resource: Core3ResourceSnapshotItem, source_record_id: int, snapshot: Core3ResourceSnapshot, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            SELECT id FROM resource_observations
            WHERE source_resource_id = :source_resource_id
              AND observation_kind = 'core3_snapshot_resource'
              AND source_record_id = :source_record_id
            """
        ),
        {"source_resource_id": source_resource_id, "source_record_id": source_record_id},
    ).first()
    if row:
        inserted["resource_observations_reused"] += 1
        return
    connection.execute(
        text(
            """
            INSERT INTO resource_observations
                (source_resource_id, source_record_id, observation_kind, observed_at,
                 confidence, details)
            VALUES
                (:source_resource_id, :source_record_id, 'core3_snapshot_resource', :observed_at,
                 'core3_authoritative_snapshot', CAST(:details AS jsonb))
            """
        ),
        {
            "source_resource_id": source_resource_id,
            "source_record_id": source_record_id,
            "observed_at": snapshot.captured_at,
            "details": json.dumps({"expires_at": resource.expires_at.isoformat() if resource.expires_at else None}, sort_keys=True),
        },
    )
    inserted["resource_observations_inserted"] += 1


def upsert_planet_observations(connection, source_resource_id: int, batch_id: int, snapshot: Core3ResourceSnapshot, resource: Core3ResourceSnapshotItem, rec: Core3Reconciliation, source_record_id: int, inserted: Counter) -> None:
    for planet in resource.planets:
        planet_id = rec.planet_ids[planet]
        row = connection.execute(
            text(
                """
                SELECT id FROM resource_planet_observations
                WHERE source_resource_id = :source_resource_id
                  AND planet_id = :planet_id
                  AND source_record_id = :source_record_id
                """
            ),
            {"source_resource_id": source_resource_id, "planet_id": planet_id, "source_record_id": source_record_id},
        ).first()
        if row:
            inserted["planet_observations_reused"] += 1
            continue
        connection.execute(
            text(
                """
                INSERT INTO resource_planet_observations
                    (source_resource_id, planet_id, source_planet_id, state, observed_at,
                     confidence, source_record_id, import_batch_id, details)
                VALUES
                    (:source_resource_id, :planet_id, :source_planet_id, 'active', :observed_at,
                     'core3_authoritative_snapshot', :source_record_id, :batch_id, '{}'::jsonb)
                """
            ),
            {
                "source_resource_id": source_resource_id,
                "planet_id": planet_id,
                "source_planet_id": rec.source_planet_ids[planet],
                "observed_at": snapshot.captured_at,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
            },
        )
        inserted["planet_observations_inserted"] += 1


def upsert_lifecycle(connection, source_resource_id: int, batch_id: int, resource: Core3ResourceSnapshotItem, source_record_id: int, inserted: Counter) -> None:
    for event_type, event_time, confidence in (
        ("spawned", resource.spawned_at, "core3_source_exact"),
        ("expires_at", resource.expires_at, "core3_authoritative_deadline"),
    ):
        if event_time is None:
            continue
        row = connection.execute(
            text(
                """
                SELECT id FROM resource_lifecycle_events
                WHERE source_resource_id = :source_resource_id
                  AND event_type = :event_type
                  AND event_time = :event_time
                  AND source_record_id = :source_record_id
                """
            ),
            {"source_resource_id": source_resource_id, "event_type": event_type, "event_time": event_time, "source_record_id": source_record_id},
        ).first()
        if row:
            inserted["lifecycle_events_reused"] += 1
            continue
        connection.execute(
            text(
                """
                INSERT INTO resource_lifecycle_events
                    (source_resource_id, event_type, event_time, event_time_confidence,
                     source_record_id, import_batch_id, details)
                VALUES
                    (:source_resource_id, :event_type, :event_time, :confidence,
                     :source_record_id, :batch_id, '{}'::jsonb)
                """
            ),
            {
                "source_resource_id": source_resource_id,
                "event_type": event_type,
                "event_time": event_time,
                "confidence": confidence,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
            },
        )
        inserted["lifecycle_events_inserted"] += 1


def insert_snapshot_ledger(connection, source_instance_id: int, snapshot_id: int, snapshot: Core3ResourceSnapshot, advances_current: bool) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO core3_live_snapshot_imports
                (source_instance_id, source_snapshot_id, captured_at, content_sha256,
                 complete, status, advanced_current, metadata)
            VALUES
                (:source_instance_id, :source_snapshot_id, :captured_at, :content_sha256,
                 true, 'complete', :advanced_current, CAST(:metadata AS jsonb))
            RETURNING id
            """
        ),
        {
            "source_instance_id": source_instance_id,
            "source_snapshot_id": snapshot_id,
            "captured_at": snapshot.captured_at,
            "content_sha256": snapshot.content_sha256,
            "advanced_current": advances_current,
            "metadata": json.dumps(snapshot_metadata(snapshot), sort_keys=True),
        },
    ).scalar_one()


def reconcile_current_availability(connection, source_instance_id: int, snapshot_id: int, snapshot: Core3ResourceSnapshot, present_source_resource_ids: set[int], inserted: Counter) -> None:
    for source_resource_id in sorted(present_source_resource_ids):
        row = connection.execute(
            text(
                """
                INSERT INTO current_resource_availability
                    (source_resource_id, source_instance_id, is_active,
                     last_seen_source_snapshot_id, last_seen_at,
                     current_source_snapshot_id, current_as_of,
                     absent_source_snapshot_id, absent_as_of, details)
                VALUES
                    (:source_resource_id, :source_instance_id, true,
                     :snapshot_id, :captured_at,
                     :snapshot_id, :captured_at,
                     NULL, NULL, '{}'::jsonb)
                ON CONFLICT (source_resource_id) DO UPDATE SET
                    source_instance_id = EXCLUDED.source_instance_id,
                    is_active = true,
                    last_seen_source_snapshot_id = EXCLUDED.last_seen_source_snapshot_id,
                    last_seen_at = EXCLUDED.last_seen_at,
                    current_source_snapshot_id = EXCLUDED.current_source_snapshot_id,
                    current_as_of = EXCLUDED.current_as_of,
                    absent_source_snapshot_id = NULL,
                    absent_as_of = NULL,
                    updated_at = now(),
                    details = '{}'::jsonb
                RETURNING (xmax = 0) AS inserted
                """
            ),
            {
                "source_resource_id": source_resource_id,
                "source_instance_id": source_instance_id,
                "snapshot_id": snapshot_id,
                "captured_at": snapshot.captured_at,
            },
        ).one()
        inserted["current_availability_inserted" if row[0] else "current_availability_updated"] += 1

    absent_rows = connection.execute(
        text(
            """
            SELECT source_resource_id
            FROM current_resource_availability
            WHERE source_instance_id = :source_instance_id
              AND is_active = true
              AND NOT (source_resource_id = ANY(:present_ids))
            """
        ),
        {"source_instance_id": source_instance_id, "present_ids": list(present_source_resource_ids) or [-1]},
    ).all()
    for row in absent_rows:
        source_resource_id = row.source_resource_id
        connection.execute(
            text(
                """
                UPDATE current_resource_availability
                SET is_active = false,
                    current_source_snapshot_id = :snapshot_id,
                    current_as_of = :captured_at,
                    absent_source_snapshot_id = :snapshot_id,
                    absent_as_of = :captured_at,
                    updated_at = now(),
                    details = CAST(:details AS jsonb)
                WHERE source_resource_id = :source_resource_id
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "captured_at": snapshot.captured_at,
                "source_resource_id": source_resource_id,
                "details": json.dumps({"absence_semantics": "absent_as_of_snapshot_not_exact_despawn"}, sort_keys=True),
            },
        )
        upsert_absence_observation(connection, source_resource_id, snapshot_id, snapshot, inserted)
        inserted["current_availability_marked_absent"] += 1


def upsert_absence_observation(connection, source_resource_id: int, snapshot_id: int, snapshot: Core3ResourceSnapshot, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            SELECT id FROM resource_observations
            WHERE source_resource_id = :source_resource_id
              AND observation_kind = 'core3_snapshot_absent'
              AND observed_at = :observed_at
            """
        ),
        {"source_resource_id": source_resource_id, "observed_at": snapshot.captured_at},
    ).first()
    if row:
        inserted["absence_observations_reused"] += 1
        return
    connection.execute(
        text(
            """
            INSERT INTO resource_observations
                (source_resource_id, observation_kind, observed_at, confidence, details)
            VALUES
                (:source_resource_id, 'core3_snapshot_absent', :observed_at,
                 'snapshot_absence_observed', CAST(:details AS jsonb))
            """
        ),
        {
            "source_resource_id": source_resource_id,
            "observed_at": snapshot.captured_at,
            "details": json.dumps({"source_snapshot_id": snapshot_id, "absence_semantics": "as_of_not_exact_despawn"}, sort_keys=True),
        },
    )
    inserted["absence_observations_inserted"] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Core3 live resource snapshots into Bellum Gero Resources.")
    parser.add_argument("command", choices=["import-snapshot"])
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan without writing to the database.")
    args = parser.parse_args(argv)

    with make_engine().begin() as connection:
        if args.dry_run:
            result = dry_run(connection, args.snapshot)
        else:
            result = import_snapshot(connection, args.snapshot)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
