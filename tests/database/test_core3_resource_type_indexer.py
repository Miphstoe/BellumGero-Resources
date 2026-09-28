from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import text

import app.indexing.core3.resource_types as resource_type_cli
from app.indexing.core3.resource_types import (
    CORE3_STAT_CODES,
    NormalizedResourceType,
    StatRange,
    build_closure,
    build_direct_edges,
    load_index,
    normalize_rows,
    parse_resource_tree_sql_text,
    stable_json_bytes,
    validate_normalized_types,
    NormalizedIndex,
    SourceFileFingerprint,
    ValidationResult,
)


def _values(
    index: int,
    enum: str,
    classes: list[str],
    attrs: list[tuple[str, int, int]] | None = None,
    container: str = "object/resource_container/test.iff",
    random_name_class: str = "plain_resource",
) -> str:
    row: list[object] = [index, enum]
    row.extend(classes + [" "] * (8 - len(classes)))
    row.extend([1, 1, 1, 1, 0, 0])
    attrs = attrs or []
    row.extend([attr[0] for attr in attrs] + [" "] * (11 - len(attrs)))
    for attr in attrs:
        row.extend([attr[1], attr[2]])
    row.extend([0, 0] * (11 - len(attrs)))
    row.extend([container, random_name_class])
    assert len(row) == 51
    encoded = []
    for value in row:
        if isinstance(value, str):
            encoded.append("'" + value.replace("'", "''") + "'")
        else:
            encoded.append(str(value))
    return "(" + ",".join(encoded) + ")"


def _fixture_sql(extra_rows: list[str] | None = None) -> str:
    rows = [
        _values(1, "resource", ["Resources"], [("res_quality", 1, 1000)]),
        _values(2, "test_organic", [" ", "Organic"], [("res_quality", 1, 1000)]),
        _values(3, "test_creature", [" ", " ", "Creature Resources"], [("res_quality", 1, 900)]),
        _values(4, "test_milk", [" ", " ", " ", "Milk"], [("res_quality", 0, 0), ("res_flavor", 1, 700)]),
    ]
    rows.extend(extra_rows or [])
    return "INSERT INTO `resource_resource_tree` VALUES " + ",".join(rows) + ";\n"


def _normalized(extra_rows: list[str] | None = None):
    rows = parse_resource_tree_sql_text(_fixture_sql(extra_rows))
    return normalize_rows(rows, "fixture/datatables.sql", "abc123")


def _index_for_loader() -> NormalizedIndex:
    resource_types, validation = _normalized()
    direct_edges = build_direct_edges(resource_types)
    closure = build_closure({item.source_type_id for item in resource_types}, direct_edges)
    return NormalizedIndex(
        core3_root="fixture",
        core3_commit_sha="fixture-sha",
        source_files=(SourceFileFingerprint("fixture/datatables.sql", "abc123", 1),),
        resource_types=resource_types,
        direct_edges=direct_edges,
        closure=closure,
        validation=validation,
        source_of_truth="fixture/datatables.sql",
        resource_tree_iff_exists=False,
        tre_extraction_required=False,
    )


def test_deterministic_parsing():
    assert parse_resource_tree_sql_text(_fixture_sql()) == parse_resource_tree_sql_text(_fixture_sql())


def test_deterministic_normalized_json():
    index = _index_for_loader()
    assert stable_json_bytes(index.to_dict()) == stable_json_bytes(index.to_dict())


def test_stable_sha256_fingerprinting_for_fixture_text():
    payload = _fixture_sql().encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_direct_hierarchy_extraction():
    resource_types, _ = _normalized()
    edges = build_direct_edges(resource_types)
    assert ("resource", "test_organic") in edges
    assert ("test_organic", "test_creature") in edges
    assert ("test_creature", "test_milk") in edges


def test_closure_self_rows_at_depth_zero():
    resource_types, _ = _normalized()
    closure = build_closure({item.source_type_id for item in resource_types}, build_direct_edges(resource_types))
    assert any(row.ancestor == "test_milk" and row.descendant == "test_milk" and row.depth == 0 for row in closure)


def test_direct_parent_closure_depth_one():
    resource_types, _ = _normalized()
    closure = build_closure({item.source_type_id for item in resource_types}, build_direct_edges(resource_types))
    assert any(row.ancestor == "test_creature" and row.descendant == "test_milk" and row.depth == 1 for row in closure)


def test_deeper_ancestor_minimum_depth():
    resource_types, _ = _normalized()
    closure = build_closure({item.source_type_id for item in resource_types}, build_direct_edges(resource_types))
    assert any(row.ancestor == "resource" and row.descendant == "test_milk" and row.depth == 3 for row in closure)


def test_multiple_parent_ancestry_if_format_permits_it():
    ids = {"a", "b", "c"}
    closure = build_closure(ids, (("a", "c"), ("b", "c")))
    assert any(row.ancestor == "a" and row.descendant == "c" and row.depth == 1 for row in closure)
    assert any(row.ancestor == "b" and row.descendant == "c" and row.depth == 1 for row in closure)


def test_cycle_detection():
    resource_types = (
        _minimal_type("a", ("b",)),
        _minimal_type("b", ("a",)),
    )
    result = validate_normalized_types(resource_types)
    assert any(error.startswith("cycle:") for error in result.errors)


def test_direct_self_edge_rejection():
    result = validate_normalized_types((_minimal_type("a", ("a",)),))
    assert "direct-self-edge:a" in result.errors


def test_missing_parent_detection():
    result = validate_normalized_types((_minimal_type("a", ("missing",)),))
    assert "missing-parent:a:missing" in result.warnings


def test_stat_applicability_preservation():
    resource_types, _ = _normalized()
    milk = next(item for item in resource_types if item.source_type_id == "test_milk")
    oq = next(stat for stat in milk.stat_ranges if stat.stat_code == "OQ")
    assert oq.is_applicable is True


def test_missing_stat_is_not_zero():
    resource_types, _ = _normalized()
    milk = next(item for item in resource_types if item.source_type_id == "test_milk")
    cr = next(stat for stat in milk.stat_ranges if stat.stat_code == "CR")
    assert cr.is_applicable is False
    assert cr.min_value is None
    assert cr.max_value is None


def test_explicit_zero_stat_range_is_preserved():
    resource_types, _ = _normalized()
    milk = next(item for item in resource_types if item.source_type_id == "test_milk")
    oq = next(stat for stat in milk.stat_ranges if stat.stat_code == "OQ")
    assert oq.is_applicable is True
    assert oq.min_value == 0
    assert oq.max_value == 0


def test_min_greater_than_max_is_rejected():
    bad = _values(5, "test_bad", [" ", " ", " ", "Bad"], [("res_quality", 20, 10)])
    _, validation = _normalized([bad])
    assert any(error.startswith("invalid-stat-range:test_bad:OQ") for error in validation.errors)


def test_er_is_not_invented_from_core3():
    resource_types, _ = _normalized()
    assert "ER" not in CORE3_STAT_CODES
    assert all(stat.stat_code != "ER" for item in resource_types for stat in item.stat_ranges)


def test_entangle_resistance_is_reported_unknown_and_not_mapped_to_er():
    entangle = _values(5, "test_gemstone", [" ", " ", " ", "Gemstone"], [("entangle_resistance", 1, 1000)])
    resource_types, validation = _normalized([entangle])
    gemstone = next(item for item in resource_types if item.source_type_id == "test_gemstone")

    assert "unknown-core3-attribute:test_gemstone:entangle_resistance" in validation.warnings
    assert "ER" not in CORE3_STAT_CODES
    assert all(stat.stat_code != "ER" for stat in gemstone.stat_ranges)
    assert all(not (stat.is_applicable and stat.min_value == 1 and stat.max_value == 1000) for stat in gemstone.stat_ranges)


def test_normalized_index_emits_one_range_slot_per_verified_core3_stat():
    resource_types, _ = _normalized()

    assert all(len(item.stat_ranges) == len(CORE3_STAT_CODES) for item in resource_types)
    assert sum(len(item.stat_ranges) for item in resource_types) == len(resource_types) * len(CORE3_STAT_CODES)


def test_duplicate_source_type_ids_are_diagnosed():
    duplicate = _values(5, "test_milk", [" ", " ", " ", "Duplicate"], [("res_quality", 1, 10)])
    _, validation = _normalized([duplicate])
    assert "duplicate-source-type-id:test_milk" in validation.errors


def test_idempotent_database_load(db):
    index = _index_for_loader()
    first = load_index(db, index)
    second = load_index(db, index)
    assert first == second


def test_repeated_load_does_not_duplicate_hierarchy_rows(db):
    index = _index_for_loader()
    load_index(db, index)
    load_index(db, index)
    resource_ids = db.execute(
        text("SELECT id FROM resource_types WHERE slug IN ('resource', 'test_organic', 'test_creature', 'test_milk')")
    ).scalars().all()
    edge_count = db.execute(
        text("SELECT count(*) FROM resource_type_edges WHERE source = 'core3_resource_tree' AND child_type_id = ANY(:ids)"),
        {"ids": resource_ids},
    ).scalar_one()
    assert edge_count == 3


def test_source_identity_remains_distinct_from_canonical_identity(db):
    index = _index_for_loader()
    load_index(db, index)
    row = db.execute(
        text(
            """
            SELECT srt.source_type_key, srt.canonical_type_id, rt.id, rt.slug
            FROM source_resource_types srt
            JOIN resource_types rt ON rt.id = srt.canonical_type_id
            WHERE srt.source_type_key = 'test_milk'
            """
        )
    ).one()
    assert row[0] == "test_milk"
    assert row[1] == row[2]
    assert row[3] == "test_milk"


def test_cli_load_uses_runtime_bellum_database_url(database_url, monkeypatch):
    monkeypatch.setenv("BELLUM_DATABASE_URL", database_url)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:1/unused")
    monkeypatch.setattr(resource_type_cli, "build_index", lambda _core3_root: _index_for_loader())

    assert resource_type_cli.main(["load", "--core3-root", "fixture"]) == 0


def test_cli_load_transaction_rolls_back_on_exception(database_url, monkeypatch):
    monkeypatch.setenv("BELLUM_DATABASE_URL", database_url)
    monkeypatch.setattr(resource_type_cli, "build_index", lambda _core3_root: _index_for_loader())

    def fail_after_write(connection, _index):
        connection.execute(
            text(
                "INSERT INTO resource_types (slug, display_name, kind) "
                "VALUES ('rollback_probe', 'Rollback Probe', 'test')"
            )
        )
        raise RuntimeError("forced loader failure")

    monkeypatch.setattr(resource_type_cli, "load_index", fail_after_write)

    with pytest.raises(RuntimeError, match="forced loader failure"):
        resource_type_cli.main(["load", "--core3-root", "fixture"])

    from app.db.session import make_engine

    engine = make_engine(database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM resource_types WHERE slug = 'rollback_probe'")
            ).scalar_one()
    finally:
        engine.dispose()

    assert count == 0


def _minimal_type(source_type_id: str, direct_parents: tuple[str, ...]) -> NormalizedResourceType:
    return NormalizedResourceType(
        source_type_id=source_type_id,
        internal_name=source_type_id,
        display_name=source_type_id,
        display_name_resolved=True,
        direct_parents=direct_parents,
        ancestors=(),
        class_path=(source_type_id,),
        stf_class_path=(source_type_id,),
        stat_ranges=tuple(StatRange(code, False) for code in CORE3_STAT_CODES),
        source_path="fixture",
        source_fingerprint="abc",
        recycled=False,
        permanent=False,
        container_type=None,
        random_name_class=None,
    )
