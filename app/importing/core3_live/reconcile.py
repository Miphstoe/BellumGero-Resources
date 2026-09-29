from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.importing.core3_live.snapshot import Core3ResourceSnapshot, SOURCE_INSTANCE, STAT_CODES


@dataclass(frozen=True)
class Core3Reconciliation:
    source_instance_id: int
    server_instance_id: int | None
    source_type_ids: dict[str, int]
    canonical_type_ids: dict[str, int]
    planet_ids: dict[str, int]
    source_planet_ids: dict[str, int]


def reconcile_snapshot(connection, snapshot: Core3ResourceSnapshot, *, mutate_source_planets: bool = True) -> Core3Reconciliation:
    source_instance_id = connection.execute(
        text("SELECT id FROM source_instances WHERE code = :code"),
        {"code": SOURCE_INSTANCE},
    ).scalar_one()
    server_instance_id = connection.execute(
        text("SELECT id FROM server_instances WHERE code = :code"),
        {"code": SOURCE_INSTANCE},
    ).scalar_one_or_none()

    type_keys = sorted({resource.resource_type for resource in snapshot.resources})
    source_type_ids: dict[str, int] = {}
    canonical_type_ids: dict[str, int] = {}
    if type_keys:
        rows = connection.execute(
            text(
                """
                SELECT srt.source_type_key, srt.id, srt.canonical_type_id
                FROM source_resource_types srt
                JOIN resource_types rt ON rt.id = srt.canonical_type_id
                WHERE srt.source_instance_id = :source_instance_id
                  AND srt.source_type_key = ANY(:type_keys)
                  AND srt.mapping_status = 'mapped'
                """
            ),
            {"source_instance_id": source_instance_id, "type_keys": type_keys},
        ).all()
        for row in rows:
            source_type_ids[row.source_type_key] = row.id
            canonical_type_ids[row.source_type_key] = row.canonical_type_id
    missing_types = sorted(set(type_keys) - set(source_type_ids))
    if missing_types:
        raise ValueError(f"Unknown Core3 resource types: {missing_types}")

    planet_keys = sorted({planet for resource in snapshot.resources for planet in resource.planets})
    planet_ids: dict[str, int] = {}
    if planet_keys:
        rows = connection.execute(
            text(
                """
                SELECT slug, core3_zone_name, id
                FROM planets
                WHERE slug = ANY(:planet_keys)
                   OR core3_zone_name = ANY(:planet_keys)
                """
            ),
            {"planet_keys": planet_keys},
        ).all()
        for row in rows:
            if row.slug in planet_keys:
                planet_ids[row.slug] = row.id
            if row.core3_zone_name in planet_keys:
                planet_ids[row.core3_zone_name] = row.id
    missing_planets = sorted(set(planet_keys) - set(planet_ids))
    if missing_planets:
        raise ValueError(f"Unknown Core3 planets: {missing_planets}")

    stat_rows = connection.execute(
        text("SELECT code FROM stat_definitions WHERE code = ANY(:codes)"),
        {"codes": list(STAT_CODES)},
    ).all()
    missing_stats = sorted(set(STAT_CODES) - {row.code for row in stat_rows})
    if missing_stats:
        raise ValueError(f"Missing stat definitions: {missing_stats}")

    source_planet_ids = _upsert_source_planets(connection, source_instance_id, planet_ids) if mutate_source_planets else {}
    return Core3Reconciliation(
        source_instance_id=source_instance_id,
        server_instance_id=server_instance_id,
        source_type_ids=source_type_ids,
        canonical_type_ids=canonical_type_ids,
        planet_ids=planet_ids,
        source_planet_ids=source_planet_ids,
    )


def _upsert_source_planets(connection, source_instance_id: int, planet_ids: dict[str, int]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for source_planet_id, planet_id in sorted(planet_ids.items()):
        row = connection.execute(
            text(
                """
                INSERT INTO source_planets
                    (source_instance_id, source_planet_id, source_planet_name, planet_id, metadata)
                VALUES
                    (:source_instance_id, :source_planet_id, :source_planet_id, :planet_id, '{}'::jsonb)
                ON CONFLICT (source_instance_id, source_planet_id) DO UPDATE SET
                    source_planet_name = EXCLUDED.source_planet_name,
                    planet_id = EXCLUDED.planet_id
                RETURNING id
                """
            ),
            {
                "source_instance_id": source_instance_id,
                "source_planet_id": source_planet_id,
                "planet_id": planet_id,
            },
        ).scalar_one()
        ids[source_planet_id] = row
    return ids
