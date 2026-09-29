from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import make_engine
from app.importing.galaxy_harvester.archive import (
    SOURCE_GALAXY_ID,
    SOURCE_INSTANCE,
    STAT_CODES,
    ArchiveData,
    HistoricalResource,
    normalize_name,
    read_archive,
)
from app.importing.galaxy_harvester.reconcile import PlanetReconciliation, TypeReconciliation, reconcile_planets, reconcile_types


IMPORT_KIND = "galaxy_harvester_historical_archive"
TOOL_VERSION = "phase3d-gh-importer-v1"


@dataclass(frozen=True)
class ArchiveCounts:
    frozen_identities: int
    normalized_resources: int
    normalized_names: int
    source_types: int
    planets: int
    unresolved_evidence: int


def get_source_instance_id(connection) -> int:
    return connection.execute(text("SELECT id FROM source_instances WHERE code = :code"), {"code": SOURCE_INSTANCE}).scalar_one()


def archive_counts(archive: ArchiveData) -> ArchiveCounts:
    return ArchiveCounts(
        frozen_identities=archive.frozen_identity_count,
        normalized_resources=len(archive.resources),
        normalized_names=len(archive.names),
        source_types=len(archive.source_types),
        planets=len(archive.planets),
        unresolved_evidence=len(archive.unresolved),
    )


def find_anomalies(resources: tuple[HistoricalResource, ...]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for resource in resources:
        for stat_code, value in resource.stats.items():
            stat_range = resource.stat_ranges.get(stat_code)
            if not stat_range:
                continue
            low = stat_range.get("min")
            high = stat_range.get("max")
            if low is None or high is None:
                continue
            if value < low or value > high:
                anomalies.append(
                    {
                        "spawn_id": resource.spawn_id,
                        "name": resource.name,
                        "resource_type": resource.resource_type,
                        "stat_code": stat_code,
                        "observed": value,
                        "source_min": low,
                        "source_max": high,
                        "reason": "source_stat_outside_archived_source_range",
                    }
                )
    return anomalies


def bipa_report(resources: tuple[HistoricalResource, ...], anomalies: list[dict[str, Any]]) -> dict[str, Any]:
    bipa = next((item for item in resources if item.spawn_id == 2411471), None)
    if bipa is None:
        return {"found": False}
    expected = {"DR": 154, "FL": 217, "PE": 880, "OQ": 384}
    return {
        "found": True,
        "values": {code: bipa.stats.get(code) for code in expected},
        "values_preserved": {code: bipa.stats.get(code) == value for code, value in expected.items()},
        "range_anomalies": sorted(item["stat_code"] for item in anomalies if item["spawn_id"] == 2411471),
    }


def dweina_report(archive: ArchiveData) -> dict[str, Any]:
    dweina = next((item for item in archive.unresolved if item.get("name") == "dweina"), None)
    return {
        "found_unresolved_evidence": dweina is not None,
        "becomes_canonical_resource": False,
        "record": dweina,
    }


def existing_counts(connection, source_instance_id: int) -> dict[str, int]:
    return {
        "canonical_resources": connection.execute(text("SELECT count(*) FROM resources")).scalar_one(),
        "source_resources": connection.execute(text("SELECT count(*) FROM source_resources WHERE source_instance_id = :id"), {"id": source_instance_id}).scalar_one(),
        "source_resource_types": connection.execute(text("SELECT count(*) FROM source_resource_types WHERE source_instance_id = :id"), {"id": source_instance_id}).scalar_one(),
        "source_planets": connection.execute(text("SELECT count(*) FROM source_planets WHERE source_instance_id = :id"), {"id": source_instance_id}).scalar_one(),
    }


def plan_expected_mutations(connection, archive: ArchiveData, type_rec: TypeReconciliation, planet_rec: PlanetReconciliation, anomalies: list[dict[str, Any]]) -> dict[str, Any]:
    source_instance_id = get_source_instance_id(connection)
    existing_source_ids = {
        row[0]
        for row in connection.execute(
            text("SELECT source_resource_id FROM source_resources WHERE source_instance_id = :id"),
            {"id": source_instance_id},
        )
    }
    existing_source_types = {
        row[0]
        for row in connection.execute(
            text("SELECT source_type_key FROM source_resource_types WHERE source_instance_id = :id"),
            {"id": source_instance_id},
        )
    }
    existing_source_planets = {
        row[0]
        for row in connection.execute(
            text("SELECT source_planet_id FROM source_planets WHERE source_instance_id = :id"),
            {"id": source_instance_id},
        )
    }
    existing_unresolved = {
        row[0]
        for row in connection.execute(
            text("SELECT normalized_name FROM unresolved_source_resources WHERE source_instance_id = :id"),
            {"id": source_instance_id},
        )
    }
    incoming_ids = {str(resource.spawn_id) for resource in archive.resources}
    planet_observations = sum(len(resource.planets) for resource in archive.resources)
    stat_observations = len(archive.resources) * len(STAT_CODES)
    type_memberships = sum(1 for resource in archive.resources if type_rec.mapping_by_source_type.get(resource.resource_type) is not None)
    lifecycle_events = sum(1 for resource in archive.resources if resource.entered is not None) + sum(1 for resource in archive.resources if resource.unavailable is not None)
    return {
        "canonical_resources_insert": len(incoming_ids - existing_source_ids),
        "canonical_resources_reuse": len(incoming_ids & existing_source_ids),
        "source_resources_insert": len(incoming_ids - existing_source_ids),
        "source_resources_reuse": len(incoming_ids & existing_source_ids),
        "source_resource_types_insert": len(set(archive.source_types) - existing_source_types),
        "source_resource_types_reuse": len(set(archive.source_types) & existing_source_types),
        "source_planets_insert": len(set(archive.planets) - existing_source_planets),
        "source_planets_reuse": len(set(archive.planets) & existing_source_planets),
        "names_expected": len(archive.resources),
        "type_memberships_expected": type_memberships,
        "stat_observations_expected": stat_observations,
        "planet_observations_expected": planet_observations,
        "lifecycle_events_expected": lifecycle_events,
        "source_records_expected": len(archive.resources) + len(archive.unresolved),
        "anomalies_expected": len(anomalies),
        "unresolved_expected": len(archive.unresolved),
        "type_unresolved_blocks_canonical_links": len(type_rec.unresolved_types),
        "planet_unresolved_blocks_import": len(planet_rec.unresolved_planets),
        "unresolved_reuse_by_name": len({normalize_name(item.get("name", "")) for item in archive.unresolved} & existing_unresolved),
    }


def dry_run(connection, archive_root: Path) -> dict[str, Any]:
    archive = read_archive(archive_root)
    source_instance_id = get_source_instance_id(connection)
    type_rec = reconcile_types(connection, archive.source_types, source_instance_id)
    planet_rec = reconcile_planets(connection, archive.planets)
    anomalies = find_anomalies(archive.resources)
    expected = plan_expected_mutations(connection, archive, type_rec, planet_rec, anomalies)
    counts = archive_counts(archive)
    malformed: list[str] = []
    if counts.frozen_identities != 18627:
        malformed.append("unexpected frozen identity count")
    if counts.normalized_resources != counts.frozen_identities:
        malformed.append("normalized resources do not match frozen identities")
    if planet_rec.unresolved_planets:
        malformed.append("unresolved planets")
    return {
        "dry_run": True,
        "archive": counts.__dict__,
        "source_identity": {
            "source_system": "galaxy_harvester",
            "source_instance": SOURCE_INSTANCE,
            "source_galaxy_id": SOURCE_GALAXY_ID,
        },
        "database_existing": existing_counts(connection, source_instance_id),
        "type_reconciliation": type_rec.to_report(),
        "planet_reconciliation": planet_rec.to_report(),
        "expected_database_mutations": expected,
        "anomalies": {
            "total": len(anomalies),
            "by_stat": dict(Counter(item["stat_code"] for item in anomalies)),
            "by_reason": dict(Counter(item["reason"] for item in anomalies)),
        },
        "bipa": bipa_report(archive.resources, anomalies),
        "dweina": dweina_report(archive),
        "malformed_resources": malformed,
    }


def upsert_import_batch(connection, source_instance_id: int, archive_root: Path) -> int:
    row = connection.execute(
        text(
            """
            SELECT id FROM import_batches
            WHERE source_instance_id = :source_instance_id
              AND import_kind = :kind
              AND source_revision = :source_revision
            ORDER BY id
            LIMIT 1
            """
        ),
        {"source_instance_id": source_instance_id, "kind": IMPORT_KIND, "source_revision": str(archive_root)},
    ).first()
    if row:
        return row[0]
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
            "source_revision": str(archive_root),
            "parameters": json.dumps({"archive_root": str(archive_root)}, sort_keys=True),
        },
    ).scalar_one()


def import_archive(connection, archive_root: Path) -> dict[str, Any]:
    archive = read_archive(archive_root)
    source_instance_id = get_source_instance_id(connection)
    batch_id = upsert_import_batch(connection, source_instance_id, archive_root)
    type_rec = reconcile_types(connection, archive.source_types, source_instance_id)
    planet_rec = reconcile_planets(connection, archive.planets)
    if planet_rec.unresolved_planets:
        raise ValueError(f"Cannot import with unresolved planets: {planet_rec.unresolved_planets}")

    inserted = Counter()
    source_type_ids = upsert_source_types(connection, archive, source_instance_id, type_rec, inserted)
    source_planet_ids = upsert_source_planets(connection, archive, source_instance_id, planet_rec, inserted)
    for resource in archive.resources:
        source_record_id = upsert_source_record(connection, None, "gh_resource_exact", str(resource.spawn_id), resource.exact_source_path, archive.checksums.get(resource.exact_source_path), resource.payload, inserted)
        source_type_id = source_type_ids.get(resource.resource_type)
        canonical_type_id = type_rec.mapping_by_source_type.get(resource.resource_type)
        canonical_resource_id = upsert_canonical_resource(connection, resource, canonical_type_id, inserted)
        source_resource_id = upsert_source_resource(connection, source_instance_id, resource, source_type_id, canonical_resource_id, source_record_id, inserted)
        upsert_type_membership(connection, source_resource_id, canonical_type_id, source_record_id, inserted)
        upsert_name(connection, source_resource_id, canonical_resource_id, resource, source_record_id, inserted)
        upsert_stats(connection, source_resource_id, batch_id, resource, source_record_id, inserted)
        upsert_observation(connection, source_resource_id, resource, source_record_id, inserted)
        upsert_planets(connection, source_resource_id, batch_id, resource, planet_rec, source_planet_ids, source_record_id, inserted)
        upsert_lifecycle(connection, source_resource_id, batch_id, resource, source_record_id, inserted)
    anomalies = find_anomalies(archive.resources)
    source_resource_rows = {
        row[0]: {"id": row[1], "first_source_record_id": row[2]}
        for row in connection.execute(
            text("SELECT source_resource_id, id, first_source_record_id FROM source_resources WHERE source_instance_id = :id"),
            {"id": source_instance_id},
        ).all()
    }
    for anomaly in anomalies:
        source_row = source_resource_rows[str(anomaly["spawn_id"])]
        upsert_anomaly(connection, source_row["first_source_record_id"], source_row["id"], anomaly, inserted)
    for unresolved in archive.unresolved:
        source_record_id = upsert_source_record(connection, None, "gh_unresolved_exact", unresolved["name"], f"raw/http/get-resource-by-name/{unresolved['source_path']}", archive.checksums.get(f"raw/http/get-resource-by-name/{unresolved['source_path']}"), unresolved, inserted)
        upsert_unresolved(connection, source_instance_id, batch_id, unresolved, source_record_id, inserted)
    connection.execute(
        text("UPDATE import_batches SET status = 'complete', summary = CAST(:summary AS jsonb), finished_at = now() WHERE id = :id"),
        {"id": batch_id, "summary": json.dumps(dict(inserted), sort_keys=True)},
    )
    return dict(inserted)


def upsert_source_types(connection, archive: ArchiveData, source_instance_id: int, type_rec: TypeReconciliation, inserted: Counter) -> dict[str, int]:
    ids: dict[str, int] = {}
    for key, payload in archive.source_types.items():
        row = connection.execute(
            text(
                """
                INSERT INTO source_resource_types
                    (source_instance_id, source_type_key, source_type_name, canonical_type_id, mapping_status, mapping_confidence, metadata)
                VALUES
                    (:source_instance_id, :key, :name, :canonical_type_id, :status, :confidence, '{}'::jsonb)
                ON CONFLICT (source_instance_id, source_type_key) DO UPDATE SET
                    source_type_name = EXCLUDED.source_type_name,
                    canonical_type_id = COALESCE(source_resource_types.canonical_type_id, EXCLUDED.canonical_type_id),
                    mapping_status = EXCLUDED.mapping_status,
                    mapping_confidence = EXCLUDED.mapping_confidence
                RETURNING id, (xmax = 0) AS inserted
                """
            ),
            {
                "source_instance_id": source_instance_id,
                "key": key,
                "name": payload.get("resource_type_name"),
                "canonical_type_id": type_rec.mapping_by_source_type.get(key),
                "status": "mapped" if type_rec.mapping_by_source_type.get(key) else "unresolved",
                "confidence": type_rec.mapping_confidence_by_source_type.get(key, "unresolved"),
            },
        ).one()
        ids[key] = row[0]
        inserted["source_resource_types_inserted" if row[1] else "source_resource_types_reused"] += 1
    return ids


def upsert_source_planets(connection, archive: ArchiveData, source_instance_id: int, planet_rec: PlanetReconciliation, inserted: Counter) -> dict[str, int]:
    ids: dict[str, int] = {}
    for source_id, payload in archive.planets.items():
        row = connection.execute(
            text(
                """
                INSERT INTO source_planets
                    (source_instance_id, source_planet_id, source_planet_name, planet_id, metadata)
                VALUES
                    (:source_instance_id, :source_id, :name, :planet_id, '{}'::jsonb)
                ON CONFLICT (source_instance_id, source_planet_id) DO UPDATE SET
                    source_planet_name = EXCLUDED.source_planet_name,
                    planet_id = EXCLUDED.planet_id
                RETURNING id, (xmax = 0) AS inserted
                """
            ),
            {"source_instance_id": source_instance_id, "source_id": str(source_id), "name": payload["planet_name"], "planet_id": planet_rec.mapping_by_source_planet_id[str(source_id)]},
        ).one()
        ids[str(source_id)] = row[0]
        inserted["source_planets_inserted" if row[1] else "source_planets_reused"] += 1
    return ids


def upsert_source_record(connection, snapshot_id, record_type: str, key: str, payload_ref: str | None, payload_hash: str | None, payload: dict[str, Any], inserted: Counter) -> int:
    row = connection.execute(
        text(
            """
            INSERT INTO source_records
                (source_snapshot_id, record_type, source_key, parse_status, payload_ref, payload_hash, normalized_payload)
            VALUES
                (:snapshot_id, :record_type, :key, 'parsed', :payload_ref, :payload_hash, CAST(:payload AS jsonb))
            ON CONFLICT (source_snapshot_id, record_type, source_key) DO UPDATE SET
                payload_ref = EXCLUDED.payload_ref,
                payload_hash = EXCLUDED.payload_hash,
                normalized_payload = EXCLUDED.normalized_payload
            RETURNING id, (xmax = 0) AS inserted
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "record_type": record_type,
            "key": key,
            "payload_ref": payload_ref,
            "payload_hash": payload_hash,
            "payload": json.dumps(payload, sort_keys=True),
        },
    ).one()
    inserted["source_records_inserted" if row[1] else "source_records_reused"] += 1
    return row[0]


def upsert_canonical_resource(connection, resource: HistoricalResource, canonical_type_id: int | None, inserted: Counter) -> int:
    row = connection.execute(
        text("SELECT canonical_resource_id FROM source_resources WHERE source_instance_id = (SELECT id FROM source_instances WHERE code = :code) AND source_resource_id = :spawn_id"),
        {"code": SOURCE_INSTANCE, "spawn_id": str(resource.spawn_id)},
    ).first()
    if row and row[0]:
        inserted["canonical_resources_reused"] += 1
        return row[0]
    new_id = connection.execute(
        text(
            """
            INSERT INTO resources (name, canonical_type_id, status, notes)
            VALUES (:name, :canonical_type_id, 'historical', 'Imported from Galaxy Harvester historical archive')
            RETURNING id
            """
        ),
        {"name": resource.name, "canonical_type_id": canonical_type_id},
    ).scalar_one()
    inserted["canonical_resources_inserted"] += 1
    return new_id


def upsert_source_resource(connection, source_instance_id: int, resource: HistoricalResource, source_type_id: int | None, canonical_resource_id: int, source_record_id: int, inserted: Counter) -> int:
    row = connection.execute(
        text(
            """
            INSERT INTO source_resources
                (source_instance_id, source_resource_id, source_resource_name, source_resource_type_id,
                 canonical_resource_id, identity_status, confidence, first_source_record_id, last_source_record_id, metadata)
            VALUES
                (:source_instance_id, :spawn_id, :name, :source_type_id,
                 :canonical_resource_id, 'linked', 'source_identity', :source_record_id, :source_record_id, CAST(:metadata AS jsonb))
            ON CONFLICT (source_instance_id, server_epoch_id, source_resource_id) DO UPDATE SET
                source_resource_name = EXCLUDED.source_resource_name,
                source_resource_type_id = EXCLUDED.source_resource_type_id,
                canonical_resource_id = COALESCE(source_resources.canonical_resource_id, EXCLUDED.canonical_resource_id),
                last_source_record_id = EXCLUDED.last_source_record_id,
                metadata = EXCLUDED.metadata
            RETURNING id, (xmax = 0) AS inserted
            """
        ),
        {
            "source_instance_id": source_instance_id,
            "spawn_id": str(resource.spawn_id),
            "name": resource.name,
            "source_type_id": source_type_id,
            "canonical_resource_id": canonical_resource_id,
            "source_record_id": source_record_id,
            "metadata": json.dumps({"source_galaxy_id": SOURCE_GALAXY_ID}, sort_keys=True),
        },
    ).one()
    inserted["source_resources_inserted" if row[1] else "source_resources_reused"] += 1
    return row[0]


def upsert_type_membership(connection, source_resource_id: int, canonical_type_id: int | None, source_record_id: int, inserted: Counter) -> None:
    if canonical_type_id is None:
        inserted["type_memberships_skipped_unresolved"] += 1
        return
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
        {
            "source_resource_id": source_resource_id,
            "resource_type_id": canonical_type_id,
            "source_record_id": source_record_id,
        },
    ).one()
    inserted["type_memberships_inserted" if row[0] else "type_memberships_reused"] += 1


def upsert_name(connection, source_resource_id: int, canonical_resource_id: int, resource: HistoricalResource, source_record_id: int, inserted: Counter) -> None:
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


def upsert_stats(connection, source_resource_id: int, batch_id: int, resource: HistoricalResource, source_record_id: int, inserted: Counter) -> None:
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
                "observed_at": resource.entered,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
            },
        ).first()
        inserted["stat_observations_inserted" if row else "stat_observations_reused"] += 1


def upsert_observation(connection, source_resource_id: int, resource: HistoricalResource, source_record_id: int, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            SELECT id FROM resource_observations
            WHERE source_resource_id = :source_resource_id
              AND observation_kind = 'historical_exact'
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
                 source_entered_at, source_unavailable_at, confidence, details)
            VALUES
                (:source_resource_id, :source_record_id, 'historical_exact', :observed_at,
                 :entered, :unavailable, 'source_exact', '{}'::jsonb)
            """
        ),
        {"source_resource_id": source_resource_id, "source_record_id": source_record_id, "observed_at": resource.entered, "entered": resource.entered, "unavailable": resource.unavailable},
    )
    inserted["resource_observations_inserted"] += 1


def upsert_planets(connection, source_resource_id: int, batch_id: int, resource: HistoricalResource, planet_rec: PlanetReconciliation, source_planet_ids: dict[str, int], source_record_id: int, inserted: Counter) -> None:
    for planet in resource.planets:
        source_planet_id = str(planet["id"])
        row = connection.execute(
            text(
                """
                SELECT id FROM resource_planet_observations
                WHERE source_resource_id = :source_resource_id
                  AND planet_id = :planet_id
                  AND source_record_id = :source_record_id
                """
            ),
            {"source_resource_id": source_resource_id, "planet_id": planet_rec.mapping_by_source_planet_id[source_planet_id], "source_record_id": source_record_id},
        ).first()
        if row:
            inserted["planet_observations_reused"] += 1
            continue
        connection.execute(
            text(
                """
                INSERT INTO resource_planet_observations
                    (source_resource_id, planet_id, source_planet_id, state, observed_at,
                     source_entered_at, source_unavailable_at, confidence, source_record_id, import_batch_id, details)
                VALUES
                    (:source_resource_id, :planet_id, :source_planet_id, 'observed', :observed_at,
                     :entered, :unavailable, 'source_exact', :source_record_id, :batch_id, '{}'::jsonb)
                """
            ),
            {
                "source_resource_id": source_resource_id,
                "planet_id": planet_rec.mapping_by_source_planet_id[source_planet_id],
                "source_planet_id": source_planet_ids[source_planet_id],
                "observed_at": resource.entered,
                "entered": resource.entered,
                "unavailable": None,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
            },
        )
        inserted["planet_observations_inserted"] += 1


def upsert_lifecycle(connection, source_resource_id: int, batch_id: int, resource: HistoricalResource, source_record_id: int, inserted: Counter) -> None:
    for event_type, event_time in (("entered", resource.entered), ("unavailable", resource.unavailable)):
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
                    (:source_resource_id, :event_type, :event_time, 'source_exact',
                     :source_record_id, :batch_id, '{}'::jsonb)
                """
            ),
            {"source_resource_id": source_resource_id, "event_type": event_type, "event_time": event_time, "source_record_id": source_record_id, "batch_id": batch_id},
        )
        inserted["lifecycle_events_inserted"] += 1


def upsert_anomaly(connection, source_record_id: int | None, source_resource_id: int, anomaly: dict[str, Any], inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            INSERT INTO anomalies
                (source_record_id, entity_kind, entity_id, anomaly_code, severity, message, details)
            VALUES
                (:source_record_id, 'source_resource', :entity_id, :code, 'warning', :message, CAST(:details AS jsonb))
            ON CONFLICT (source_record_id, anomaly_code, entity_kind, entity_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "source_record_id": source_record_id,
            "entity_id": source_resource_id,
            "code": f"source_range_violation:{anomaly['stat_code']}",
            "message": f"{anomaly['name']} {anomaly['stat_code']}={anomaly['observed']} is outside archived source range {anomaly['source_min']}-{anomaly['source_max']}",
            "details": json.dumps(anomaly, sort_keys=True),
        },
    ).first()
    inserted["anomalies_inserted" if row else "anomalies_reused"] += 1


def upsert_unresolved(connection, source_instance_id: int, batch_id: int, unresolved: dict[str, Any], source_record_id: int, inserted: Counter) -> None:
    row = connection.execute(
        text(
            """
            INSERT INTO unresolved_source_resources
                (source_instance_id, import_batch_id, source_name, normalized_name, result_status, http_status, source_result_text, source_record_id, details)
            VALUES
                (:source_instance_id, :batch_id, :name, :normalized_name, 'unresolved_new_result', 200, :result_text, :source_record_id, CAST(:details AS jsonb))
            ON CONFLICT (source_instance_id, normalized_name, import_batch_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "source_instance_id": source_instance_id,
            "batch_id": batch_id,
            "name": unresolved["name"],
            "normalized_name": normalize_name(unresolved["name"]),
            "result_text": unresolved.get("result_text"),
            "source_record_id": source_record_id,
            "details": json.dumps(unresolved, sort_keys=True),
        },
    ).first()
    inserted["unresolved_inserted" if row else "unresolved_reused"] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Galaxy Harvester historical resources into Bellum Gero Resources.")
    parser.add_argument("command", choices=["import-history"])
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan without writing to the database.")
    args = parser.parse_args(argv)

    with make_engine().begin() as connection:
        if args.dry_run:
            result = dry_run(connection, args.archive)
        else:
            result = import_archive(connection, args.archive)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
