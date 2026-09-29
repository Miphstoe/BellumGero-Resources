from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from app.importing.galaxy_harvester.archive import STAT_CODES, read_archive
from app.importing.galaxy_harvester.importer import dry_run, find_anomalies, import_archive
from app.importing.galaxy_harvester.reconcile import reconcile_planets, reconcile_types


requires_test_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for database integration tests",
)


def get_or_create_resource_type(db, slug: str, display_name: str = "Duralloy Steel") -> int:
    existing = db.execute(text("SELECT id FROM resource_types WHERE slug = :slug"), {"slug": slug}).scalar_one_or_none()
    if existing is not None:
        return existing
    return db.execute(
        text(
            """
            INSERT INTO resource_types (slug, display_name, kind)
            VALUES (:slug, :display_name, 'core3')
            RETURNING id
            """
        ),
        {"slug": slug, "display_name": display_name},
    ).scalar_one()


def phase3d_counts(db, source_resource_key: str) -> dict[str, int]:
    row = db.execute(
        text(
            """
            SELECT id, canonical_resource_id
            FROM source_resources
            WHERE source_resource_id = :source_resource_key
            """
        ),
        {"source_resource_key": source_resource_key},
    ).first()
    source_resource_id = row[0] if row else None
    canonical_resource_id = row[1] if row else None
    return {
        "canonical_resources": db.execute(text("SELECT count(*) FROM resources WHERE id = :id"), {"id": canonical_resource_id}).scalar_one() if canonical_resource_id else 0,
        "source_resources": db.execute(text("SELECT count(*) FROM source_resources WHERE source_resource_id = :key"), {"key": source_resource_key}).scalar_one(),
        "resource_names": db.execute(text("SELECT count(*) FROM resource_names WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "stat_observations": db.execute(text("SELECT count(*) FROM resource_stat_observations WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "type_memberships": db.execute(text("SELECT count(*) FROM resource_type_memberships WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "resource_observations": db.execute(text("SELECT count(*) FROM resource_observations WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "planet_observations": db.execute(text("SELECT count(*) FROM resource_planet_observations WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "lifecycle_events": db.execute(text("SELECT count(*) FROM resource_lifecycle_events WHERE source_resource_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "source_records": db.execute(text("SELECT count(*) FROM source_records WHERE record_type = 'gh_resource_exact' AND source_key = :key"), {"key": source_resource_key}).scalar_one(),
        "anomalies": db.execute(text("SELECT count(*) FROM anomalies WHERE entity_kind = 'source_resource' AND entity_id = :id"), {"id": source_resource_id}).scalar_one() if source_resource_id else 0,
        "unresolved_evidence": db.execute(text("SELECT count(*) FROM unresolved_source_resources")).scalar_one(),
    }


def membership_rows(db, source_resource_key: str):
    return db.execute(
        text(
            """
            SELECT rt.slug, rtm.membership_kind, srt.source_type_key, srt.mapping_confidence
            FROM source_resources sr
            JOIN resource_type_memberships rtm ON rtm.source_resource_id = sr.id
            JOIN resource_types rt ON rt.id = rtm.resource_type_id
            LEFT JOIN source_resource_types srt ON srt.id = sr.source_resource_type_id
            WHERE sr.source_resource_id = :key
            ORDER BY rt.slug
            """
        ),
        {"key": source_resource_key},
    ).all()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def make_archive(root: Path, resources: dict[str, dict], unresolved: list[dict] | None = None, extra_raw: bool = False) -> Path:
    identities = []
    names = {}
    source_types = {}
    planets = {
        "1": {"planet_id": 1, "planet_name": "Corellia"},
        "10": {"planet_id": 10, "planet_name": "Yavin 4"},
    }
    checksums = []
    for sid, payload in sorted(resources.items(), key=lambda item: int(item[0])):
        exact = payload["exact"]
        identities.append(
            {
                "galaxy_id": 153,
                "spawn_id": int(sid),
                "spawn_name": exact["name"],
                "resource_type": exact["resource_type"],
                "resource_type_name": exact["resource_type_name"],
                "entered": exact.get("entered"),
                "unavailable": exact.get("unavailable"),
                "raw_source_file": f"raw/http/get-resources-spawnid-pages/test/page.json",
                "raw_source_sha256": f"sha-{sid}",
            }
        )
        names[exact["name"]] = [int(sid)]
        source_types[exact["resource_type"]] = {"resource_type": exact["resource_type"], "resource_type_name": exact["resource_type_name"]}
        checksums.append(f"sha-{sid}  {payload['exact_source_path']}\n")
    frozen = {
        "galaxy_id": 153,
        "identities": identities,
        "unique_spawn_id_count": len(identities),
        "unique_name_count": len(identities),
        "duplicate_spawn_id_count": 0,
        "conflicting_spawn_id_count": 0,
        "duplicate_name_count": 0,
        "names_mapping_to_multiple_spawn_ids": {},
        "no_unresolved_identity_conflicts": True,
        "source_count_reconciles": True,
        "source_total_results_start": len(identities),
        "source_total_results_end": len(identities),
    }
    write_json(root / "normalized" / "frozen-identities.json", frozen)
    write_json(root / "normalized" / "resources-index.json", {"resources": resources})
    write_json(root / "normalized" / "names.json", {"names": names})
    write_json(root / "normalized" / "planets.json", {"planets": planets})
    write_json(root / "normalized" / "source-types.json", {"source_types": source_types})
    write_json(root / "normalized" / "unresolved.json", {"unresolved": unresolved or []})
    (root / "checksums").mkdir(parents=True, exist_ok=True)
    (root / "checksums" / "sha256sums.txt").write_text("".join(checksums), encoding="utf-8")
    if extra_raw:
        raw = root / "raw" / "http" / "get-resource-by-name" / "999999.xml"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("<result><spawnName>extra</spawnName><spawnID>999999</spawnID><resultText>found</resultText></result>", encoding="utf-8")
    return root


def resource_payload(
    spawn_id: int = 1,
    name: str = "alpha",
    resource_type: str = "steel_duralloy",
    stats: dict[str, int] | None = None,
    stat_ranges: dict | None = None,
    planets: list[dict] | None = None,
) -> dict:
    exact = {
        "found": True,
        "spawn_id": spawn_id,
        "name": name,
        "resource_type": resource_type,
        "resource_type_name": resource_type.replace("_", " ").title(),
        "container_type": "solid",
        "entered": "2025-01-02 03:04:05",
        "unavailable": "2025-02-03 04:05:06",
        "stats": stats if stats is not None else {"OQ": 0, "DR": 500},
        "stat_ranges": stat_ranges if stat_ranges is not None else {"OQ": {"min": 1, "max": 1000}, "DR": {"min": 1, "max": 1000}},
        "planets": planets
        if planets is not None
        else [{"id": 10, "name": "Yavin 4", "entered": "2025-01-02 03:04:05", "entered_by": None, "unavailable": None, "unavailable_by": None}],
    }
    return {"exact": exact, "exact_source_path": f"raw/http/get-resource-by-name/{spawn_id}.xml", "spawn_name": name}


def test_archive_reader_parses_resource_and_lifecycle(tmp_path):
    archive = read_archive(make_archive(tmp_path, {"1": resource_payload()}))
    resource = archive.resources[0]
    assert resource.name == "alpha"
    assert resource.spawn_id == 1
    assert resource.entered.isoformat() == "2025-01-02T03:04:05"
    assert resource.unavailable.isoformat() == "2025-02-03T04:05:06"
    assert resource.planets[0]["name"] == "Yavin 4"


def test_missing_stat_absent_and_explicit_zero_preserved(tmp_path):
    archive = read_archive(make_archive(tmp_path, {"1": resource_payload(stats={"OQ": 0})}))
    resource = archive.resources[0]
    assert resource.stats["OQ"] == 0
    assert "DR" not in resource.stats


def test_all_eleven_gh_stat_codes_are_recognized(tmp_path):
    stats = {code: idx for idx, code in enumerate(STAT_CODES)}
    ranges = {code: {"min": 0, "max": 1000} for code in STAT_CODES}
    archive = read_archive(make_archive(tmp_path, {"1": resource_payload(stats=stats, stat_ranges=ranges)}))
    assert set(archive.resources[0].stats) == set(STAT_CODES)


def test_resource_identity_uses_gh_spawn_id_source_identity(tmp_path):
    archive = read_archive(make_archive(tmp_path, {"123": resource_payload(spawn_id=123)}))
    assert archive.resources[0].spawn_id == 123
    assert archive.frozen_manifest["identities"][0]["spawn_id"] == 123


@requires_test_db
def test_type_reconciliation_exact_and_unknown(db, ids):
    existing = get_or_create_resource_type(db, "steel_duralloy")
    rec = reconcile_types(
        db,
        {
            "steel_duralloy": {"resource_type_name": "Duralloy Steel"},
            "unknown_gh_type": {"resource_type_name": "Unknown"},
        },
        ids["gh_instance"],
    )
    assert rec.mapping_by_source_type["steel_duralloy"] == existing
    assert rec.mapping_by_source_type["unknown_gh_type"] is None
    assert rec.exact_canonical_matches == 1
    assert rec.unresolved_types == ("unknown_gh_type",)


@requires_test_db
def test_type_reconciliation_maps_reptillian_gh_alias_to_core3_reptilian(db, ids):
    core3_type_id = get_or_create_resource_type(db, "meat_reptilian_corellia", "Corellian Reptillian Meat")
    rec = reconcile_types(
        db,
        {
            "meat_reptillian_corellia": {"resource_type_name": "Corellian Reptillian Meat"},
        },
        ids["gh_instance"],
    )
    assert rec.mapping_by_source_type["meat_reptillian_corellia"] == core3_type_id
    assert rec.mapping_confidence_by_source_type["meat_reptillian_corellia"] == "explicit_historical_alias"
    assert rec.aliased_canonical_matches == 1
    assert rec.unresolved_types == ()


@requires_test_db
def test_planet_reconciliation_maps_yavin4(db):
    rec = reconcile_planets(db, {"10": {"planet_name": "Yavin 4"}})
    assert rec.unresolved_planets == ()
    assert rec.slug_by_source_planet_id["10"] == "yavin4"


def test_bipa_values_preserved_and_anomalies_detected(tmp_path):
    bipa = resource_payload(
        spawn_id=2411471,
        name="bipa",
        resource_type="radioactive_type1",
        stats={"DR": 154, "FL": 217, "PE": 880, "OQ": 384},
        stat_ranges={"DR": {"min": 400, "max": 474}, "FL": {"min": 0, "max": 0}, "PE": {"min": 500, "max": 593}, "OQ": {"min": 1, "max": 1000}},
    )
    archive = read_archive(make_archive(tmp_path, {"2411471": bipa}))
    resource = archive.resources[0]
    assert resource.stats == {"DR": 154, "FL": 217, "OQ": 384, "PE": 880}
    anomalies = find_anomalies(archive.resources)
    assert sorted(item["stat_code"] for item in anomalies) == ["DR", "FL", "PE"]


def test_dweina_unresolved_does_not_create_resource_in_reader(tmp_path):
    archive = read_archive(make_archive(tmp_path, {"1": resource_payload()}, unresolved=[{"found": False, "name": "dweina", "result_text": "new", "source_path": "dweina.xml"}]))
    assert len(archive.resources) == 1
    assert archive.unresolved[0]["name"] == "dweina"


def test_import_population_ignores_extra_raw_xml(tmp_path):
    archive = read_archive(make_archive(tmp_path, {"1": resource_payload()}, extra_raw=True))
    assert len(archive.resources) == 1


def test_malformed_conflicting_source_identity_blocks(tmp_path):
    payload = resource_payload(spawn_id=2)
    payload["exact"]["spawn_id"] = 99
    try:
        read_archive(make_archive(tmp_path, {"2": payload}))
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("expected source identity mismatch to fail")


@requires_test_db
def test_dry_run_writes_nothing(tmp_path, db):
    archive_root = make_archive(tmp_path, {"1": resource_payload(resource_type="unknown_gh_type")})
    before = db.execute(text("SELECT count(*) FROM source_resources")).scalar_one()
    result = dry_run(db, archive_root)
    after = db.execute(text("SELECT count(*) FROM source_resources")).scalar_one()
    assert before == after
    assert result["dry_run"] is True
    assert result["archive"]["normalized_resources"] == 1


@requires_test_db
def test_import_is_idempotent_for_small_archive(tmp_path, db):
    type_id = get_or_create_resource_type(db, "steel_duralloy")
    source_key = "930000001"
    archive_root = make_archive(tmp_path, {source_key: resource_payload(spawn_id=int(source_key), resource_type="steel_duralloy")})
    first = import_archive(db, archive_root)
    first_counts = phase3d_counts(db, source_key)
    canonical_id_after_first = db.execute(
        text("SELECT canonical_resource_id FROM source_resources WHERE source_resource_id = :key"),
        {"key": source_key},
    ).scalar_one()
    second = import_archive(db, archive_root)
    second_counts = phase3d_counts(db, source_key)
    canonical_id_after_second = db.execute(
        text("SELECT canonical_resource_id FROM source_resources WHERE source_resource_id = :key"),
        {"key": source_key},
    ).scalar_one()

    assert first["source_resources_inserted"] == 1
    assert second["source_resources_reused"] == 1
    assert second_counts == first_counts
    assert canonical_id_after_second == canonical_id_after_first
    assert first_counts["canonical_resources"] == 1
    assert first_counts["source_resources"] == 1
    assert first_counts["resource_names"] == 1
    assert first_counts["stat_observations"] == len(STAT_CODES)
    assert first_counts["type_memberships"] == 1
    assert first_counts["resource_observations"] == 1
    assert first_counts["planet_observations"] == 1
    assert first_counts["lifecycle_events"] == 2
    assert first_counts["source_records"] == 1
    assert db.execute(text("SELECT canonical_type_id FROM resources WHERE id = :id"), {"id": canonical_id_after_first}).scalar_one() == type_id
    assert membership_rows(db, source_key) == [("steel_duralloy", "canonical_final_type", "steel_duralloy", "exact_identifier")]


@requires_test_db
def test_import_alias_type_creates_canonical_membership_and_preserves_source_type(tmp_path, db):
    canonical_type_id = get_or_create_resource_type(db, "meat_reptilian_corellia", "Corellian Reptillian Meat")
    source_key = "930000003"
    archive_root = make_archive(
        tmp_path,
        {
            source_key: resource_payload(
                spawn_id=int(source_key),
                resource_type="meat_reptillian_corellia",
            )
        },
    )

    first = import_archive(db, archive_root)
    second = import_archive(db, archive_root)
    counts = phase3d_counts(db, source_key)

    assert first["type_memberships_inserted"] == 1
    assert second["type_memberships_reused"] == 1
    assert counts["type_memberships"] == 1
    assert membership_rows(db, source_key) == [
        ("meat_reptilian_corellia", "canonical_final_type", "meat_reptillian_corellia", "explicit_historical_alias")
    ]
    assert db.execute(
        text(
            """
            SELECT srt.canonical_type_id
            FROM source_resources sr
            JOIN source_resource_types srt ON srt.id = sr.source_resource_type_id
            WHERE sr.source_resource_id = :key
            """
        ),
        {"key": source_key},
    ).scalar_one() == canonical_type_id


@requires_test_db
def test_rerun_repairs_missing_type_membership_without_duplicate_rows(tmp_path, db):
    get_or_create_resource_type(db, "steel_duralloy")
    source_key = "930000004"
    archive_root = make_archive(tmp_path, {source_key: resource_payload(spawn_id=int(source_key), resource_type="steel_duralloy")})
    import_archive(db, archive_root)
    before = phase3d_counts(db, source_key)
    source_resource_id = db.execute(text("SELECT id FROM source_resources WHERE source_resource_id = :key"), {"key": source_key}).scalar_one()
    db.execute(text("DELETE FROM resource_type_memberships WHERE source_resource_id = :id"), {"id": source_resource_id})
    missing = phase3d_counts(db, source_key)

    repaired = import_archive(db, archive_root)
    after = phase3d_counts(db, source_key)

    assert before["type_memberships"] == 1
    assert missing == {**before, "type_memberships": 0}
    assert repaired["type_memberships_inserted"] == 1
    assert after == before


@requires_test_db
def test_unresolved_type_does_not_create_membership(tmp_path, db):
    source_key = "930000005"
    archive_root = make_archive(tmp_path, {source_key: resource_payload(spawn_id=int(source_key), resource_type="unknown_gh_type")})

    result = import_archive(db, archive_root)
    counts = phase3d_counts(db, source_key)

    assert result["type_memberships_skipped_unresolved"] == 1
    assert counts["type_memberships"] == 0
    assert membership_rows(db, source_key) == []


@requires_test_db
def test_ambiguous_existing_type_mapping_does_not_create_membership(tmp_path, db, ids):
    expected_type_id = get_or_create_resource_type(db, "test_exact_membership_type")
    conflicting_type_id = get_or_create_resource_type(db, "test_conflicting_membership_type")
    db.execute(
        text(
            """
            INSERT INTO source_resource_types
                (source_instance_id, source_type_key, source_type_name, canonical_type_id, mapping_status, mapping_confidence)
            VALUES
                (:source_instance_id, 'test_exact_membership_type', 'Test Exact Membership Type', :canonical_type_id, 'mapped', 'test_conflict')
            """
        ),
        {"source_instance_id": ids["gh_instance"], "canonical_type_id": conflicting_type_id},
    )
    source_key = "930000006"
    archive_root = make_archive(tmp_path, {source_key: resource_payload(spawn_id=int(source_key), resource_type="test_exact_membership_type")})

    result = dry_run(db, archive_root)
    import_result = import_archive(db, archive_root)

    assert expected_type_id != conflicting_type_id
    assert result["type_reconciliation"]["ambiguous_conflicting"] == 1
    assert result["expected_database_mutations"]["type_memberships_expected"] == 0
    assert import_result["type_memberships_skipped_unresolved"] == 1
    assert phase3d_counts(db, source_key)["type_memberships"] == 0


@requires_test_db
def test_import_stat_presence_distinguishes_missing_zero_and_nonzero_idempotently(tmp_path, db):
    source_key = "930000002"
    archive_root = make_archive(
        tmp_path,
        {
            source_key: resource_payload(
                spawn_id=int(source_key),
                stats={"OQ": 0, "DR": 500},
                stat_ranges={"OQ": {"min": 0, "max": 1000}, "DR": {"min": 1, "max": 1000}},
            )
        },
    )
    import_archive(db, archive_root)
    import_archive(db, archive_root)
    source_resource_id = db.execute(
        text("SELECT id FROM source_resources WHERE source_resource_id = :key"),
        {"key": source_key},
    ).scalar_one()

    rows = {
        row.stat_code: (row.value, row.is_present)
        for row in db.execute(
            text(
                """
                SELECT stat_code, value, is_present
                FROM resource_stat_observations
                WHERE source_resource_id = :source_resource_id
                """
            ),
            {"source_resource_id": source_resource_id},
        )
    }

    assert len(rows) == len(STAT_CODES)
    assert rows["OQ"] == (0, True)
    assert rows["DR"] == (500, True)
    assert rows["ER"] == (None, False)
    assert all(rows[code] == (None, False) for code in set(STAT_CODES) - {"OQ", "DR"})
