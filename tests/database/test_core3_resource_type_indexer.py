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

# Spawn classifications deliberately contain no stat range evidence.
def _spawn_lua(key="test_milk_hoth", label="Hoth Milk", parent="test_milk"):
    return ('resources = {{type = "' + key + '", classes = {'
            '{"Organic", "test_organic"}, {"Creature Resources", "test_creature"}, '
            '{"Milk", "' + parent + '"}, {"' + label + '", "' + key + '"}'
            '}, attributes = {{"res_quality", 500}}}}')


def _custom_index(source=None):
    from dataclasses import replace
    from app.indexing.core3.spawn_classifications import extend_classifications
    index = _index_for_loader()
    fingerprint = SourceFileFingerprint("resource_manager_spawns.lua", "spawn-sha", 100)
    types = extend_classifications(index.resource_types, source or _spawn_lua(), fingerprint, index.validation)
    edges = build_direct_edges(types)
    return replace(index, resource_types=types, direct_edges=edges,
                   closure=build_closure({item.source_type_id for item in types}, edges),
                   source_files=(*index.source_files, fingerprint))


def test_spawn_complete_hierarchy_and_baseline_preservation():
    index = _custom_index()
    assert not index.validation.errors
    by_id = {item.source_type_id: item for item in index.resource_types}
    assert by_id['test_milk_hoth'].stf_class_path == ('resource', 'test_organic', 'test_creature', 'test_milk', 'test_milk_hoth')
    assert by_id['test_milk_hoth'].class_path[-1] == 'Hoth Milk'
    assert by_id['test_milk_hoth'].stat_ranges == ()
    assert by_id['test_milk_hoth'].source_fingerprint == 'spawn-sha'
    for item in _index_for_loader().resource_types:
        assert by_id[item.source_type_id] == item


def test_spawn_repeated_entries_deduplicate():
    from app.indexing.core3.spawn_classifications import parse_spawn_classifications
    source = _spawn_lua()
    entry = source[len('resources = {'):-1]
    assert len(parse_spawn_classifications('resources = {' + entry + ',' + entry + '}')) == 1


def test_spawn_conflicting_duplicates_rejected():
    from app.indexing.core3.spawn_classifications import parse_spawn_classifications
    entry = _spawn_lua()[len('resources = {'):-1]
    with pytest.raises(ValueError, match='conflicting-definition'):
        parse_spawn_classifications('resources = {' + entry + ',' + entry.replace('Hoth Milk', 'Other') + '}')


@pytest.mark.parametrize('source', [
    'resources = {', 'resources = {}', 'resources = makeResources()',
    'resources = {{type="x", type="y", classes={}}}',
    'resources = {{type="x", classes={{"X", "y"}}}}',
    'resources = {{type="x", classes={{"X", "x"}}}}; run()',
])
def test_spawn_malformed_inputs_fail_closed(source):
    from app.indexing.core3.spawn_classifications import parse_spawn_classifications
    with pytest.raises(ValueError):
        parse_spawn_classifications(source)


def test_spawn_unresolved_parent_blocks_load():
    index = _custom_index(_spawn_lua(parent='missing'))
    assert 'missing-parent:test_milk_hoth:missing' in index.validation.errors
    with pytest.raises(ValueError, match='validation errors'):
        load_index(None, index)


def test_spawn_conflicting_baseline_definition():
    index = _custom_index(_spawn_lua().replace('"Milk",', '"Different Milk",'))
    assert any(error.startswith('conflicting-classification:') for error in index.validation.errors)


def test_spawn_cycle_rejected():
    index = _custom_index(_spawn_lua(parent='test_milk_hoth'))
    assert 'cycle:test_milk_hoth' in index.validation.errors


def test_optional_spawn_missing_file_is_actionable(tmp_path):
    sql = tmp_path / 'MMOCoreORB/sql/datatables.sql'
    sql.parent.mkdir(parents=True)
    sql.write_text(_fixture_sql())
    with pytest.raises(FileNotFoundError, match='Optional spawn file not found'):
        resource_type_cli.build_index(tmp_path, Path('missing.lua'))
    assert resource_type_cli.build_index(tmp_path).validation.errors == []


def test_custom_load_preserves_metadata_ranges_and_mapping(db):
    baseline = _index_for_loader()
    load_index(db, baseline)
    db.execute(text("UPDATE resource_types SET metadata = '{\"keep\": true}', display_name = 'Keep Me' WHERE slug = 'test_milk'"))
    before = db.execute(text("SELECT * FROM resource_type_stat_ranges ORDER BY id")).all()
    milk_id = db.execute(text("SELECT id FROM resource_types WHERE slug = 'test_milk'")).scalar_one()
    # An authoritative alias must continue to target its existing canonical ID.
    db.execute(text("INSERT INTO source_resource_types (source_instance_id, source_type_key, canonical_type_id, mapping_status, mapping_confidence) SELECT id, 'test_milk_hoth', :type_id, 'mapped', 'authoritative' FROM source_instances WHERE code = 'bellum-gero-live'"), {'type_id': milk_id})
    index = _custom_index()
    load_index(db, index)
    load_index(db, index)
    assert db.execute(text("SELECT canonical_type_id FROM source_resource_types WHERE source_type_key = 'test_milk_hoth'")).scalar_one() == milk_id
    assert db.execute(text("SELECT metadata FROM resource_types WHERE id = :id"), {'id': milk_id}).scalar_one() == {'keep': True}
    assert db.execute(text("SELECT * FROM resource_type_stat_ranges ORDER BY id")).all() == before
    assert db.execute(text("SELECT count(*) FROM resource_types WHERE slug = 'test_milk_hoth'")).scalar_one() == 0


def test_custom_mapping_enables_strict_snapshot_reconciliation(db):
    from datetime import datetime, timezone
    from app.importing.core3_live.snapshot import Core3ResourceSnapshot, Core3ResourceSnapshotItem
    from app.importing.core3_live.reconcile import reconcile_snapshot
    now = datetime.now(timezone.utc)
    item = Core3ResourceSnapshotItem('123', 'test', 'test_milk_hoth', (), {'OQ': 500}, None, None, None, {})
    snapshot = Core3ResourceSnapshot(1, 'core3', 'bellum-gero-live', True, now, (item,), '', 0, {})
    with pytest.raises(ValueError, match='Unknown Core3 resource types'):
        reconcile_snapshot(db, snapshot, mutate_source_planets=False)
    index = _custom_index()
    load_index(db, index)
    result = reconcile_snapshot(db, snapshot, mutate_source_planets=False)
    assert 'test_milk_hoth' in result.canonical_type_ids
    assert load_index(db, index) == load_index(db, index)


def test_database_plan_is_read_only(db):
    from app.indexing.core3.resource_types import inspect_database
    before = db.execute(text('SELECT count(*) FROM resource_types')).scalar_one()
    plan = inspect_database(db, _custom_index())
    assert 'test_milk_hoth' in plan['proposed_resource_type_inserts']
    assert db.execute(text('SELECT count(*) FROM resource_types')).scalar_one() == before


def test_custom_existing_canonical_record_is_unchanged(db):
    load_index(db, _index_for_loader())
    db.execute(text("INSERT INTO resource_types (slug, display_name, kind, metadata, container_type) VALUES ('test_milk_hoth', 'Existing Hoth', 'canonical', '{\"custom\": true}', 'keep.iff')"))
    before = db.execute(text("SELECT * FROM resource_types WHERE slug = 'test_milk_hoth'")).one()
    load_index(db, _custom_index())
    assert db.execute(text("SELECT * FROM resource_types WHERE slug = 'test_milk_hoth'")).one() == before
    assert db.execute(text("SELECT count(*) FROM resource_type_stat_ranges WHERE resource_type_id = :id"), {'id': before.id}).scalar_one() == 0


def test_unknown_existing_mapping_blocks_all_writes(db):
    db.execute(text("INSERT INTO source_resource_types (source_instance_id, source_type_key, mapping_status, mapping_confidence) SELECT id, 'test_milk_hoth', 'unmapped', 'unknown' FROM source_instances WHERE code = 'bellum-gero-live'"))
    before = db.execute(text('SELECT count(*) FROM resource_types')).scalar_one()
    with pytest.raises(ValueError, match='unresolved-existing-mapping'):
        load_index(db, _custom_index())
    assert db.execute(text('SELECT count(*) FROM resource_types')).scalar_one() == before


def _verified_hoth_index():
    """Portable classification evidence; production spawn observations are excluded."""
    import json
    from dataclasses import replace
    from app.indexing.core3.spawn_classifications import extend_classifications

    root = Path(__file__).resolve().parents[2]
    report = json.loads((root / 'docs/core3-hoth-classifications.json').read_text())
    baseline = {}
    for item in report['classifications']:
        for level, identifier in enumerate(item['stf_class_path'][:-1]):
            ids = tuple(item['stf_class_path'][:level + 1])
            names = tuple(item['class_path'][:level + 1])
            node = replace(_minimal_type(identifier, ids[-2:-1]),
                           display_name=names[-1], class_path=names,
                           stf_class_path=ids, ancestors=ids[:-1])
            assert identifier not in baseline or baseline[identifier] == node
            baseline[identifier] = node
    original = tuple(sorted(baseline.values(), key=lambda item: item.source_type_id))
    fingerprint = SourceFileFingerprint('tests/fixtures/core3_hoth_classifications.lua', 'fixture', 0)
    validation = ValidationResult()
    source = (root / fingerprint.relative_path).read_text()
    types = extend_classifications(original, source, fingerprint, validation)
    edges = build_direct_edges(types)
    index = replace(_index_for_loader(), resource_types=types, validation=validation,
                    direct_edges=edges, closure=build_closure(set(baseline) | {item.source_type_id for item in types}, edges))
    return index, report, original


def test_verified_42_hoth_paths_merge_and_preserve_sql_parents():
    index, report, original = _verified_hoth_index()
    assert index.validation.errors == []
    custom = {item.source_type_id: item for item in index.resource_types if item.source_type_id.endswith('_hoth')}
    assert len(custom) == 42
    for expected in report['classifications']:
        actual = custom[expected['source_type_id']]
        assert actual.display_name == expected['display_name']
        assert actual.class_path == tuple(expected['class_path'])
        assert actual.stf_class_path == tuple(expected['stf_class_path'])
        assert actual.direct_parents == tuple(expected['direct_parents'])
        assert not actual.stat_ranges
    assert all(item in index.resource_types for item in original)
    # Core3 identifiers are authoritative even when their spelling differs.
    assert custom['meat_reptilian_hoth'].direct_parents == ('meat_reptillian',)
    assert custom['softwood_conifer_hoth'].direct_parents == ('softwood',)


def test_verified_42_hoth_types_reconcile_strictly_and_idempotently(db):
    from datetime import datetime, timezone
    from app.importing.core3_live.snapshot import Core3ResourceSnapshot, Core3ResourceSnapshotItem
    from app.importing.core3_live.reconcile import reconcile_snapshot

    index, report, _ = _verified_hoth_index()
    resources = tuple(Core3ResourceSnapshotItem(str(10000 + ordinal), 'fixture', item['source_type_id'], (), {'OQ': 500}, None, None, None, {})
                      for ordinal, item in enumerate(report['classifications']))
    snapshot = Core3ResourceSnapshot(1, 'core3', 'bellum-gero-live', True, datetime.now(timezone.utc), resources, '', 0, {})
    with pytest.raises(ValueError, match='Unknown Core3 resource types'):
        reconcile_snapshot(db, snapshot, mutate_source_planets=False)
    load_index(db, index)
    ids_before = db.execute(text('SELECT id, slug FROM resource_types ORDER BY id')).all()
    mappings_before = db.execute(text('SELECT * FROM source_resource_types ORDER BY id')).all()
    ranges_before = db.execute(text('SELECT * FROM resource_type_stat_ranges ORDER BY id')).all()
    result = reconcile_snapshot(db, snapshot, mutate_source_planets=False)
    assert len(result.canonical_type_ids) == 42
    load_index(db, index)
    assert db.execute(text('SELECT id, slug FROM resource_types ORDER BY id')).all() == ids_before
    assert db.execute(text('SELECT * FROM source_resource_types ORDER BY id')).all() == mappings_before
    assert db.execute(text('SELECT * FROM resource_type_stat_ranges ORDER BY id')).all() == ranges_before


def test_taxonomy_extension_preserves_populated_history_and_gh_data(db, tmp_path):
    import json
    from app.importing.core3_live.importer import import_snapshot
    from tests.importing.test_core3_live_importer import write_snapshot, resource_payload
    from tests.database.conftest import create_source_resource

    load_index(db, _index_for_loader())
    path = write_snapshot(tmp_path / 'history.json', resources=[resource_payload(resource_type='test_milk')])
    import_snapshot(db, path)
    gh_id = db.execute(text("SELECT id FROM source_instances WHERE code = 'galaxy-153'")).scalar_one()
    create_source_resource(db, gh_id, 'historical-gh-resource', 'Historical GH Resource')
    milk_id = db.execute(text("SELECT id FROM resource_types WHERE slug = 'test_milk'")).scalar_one()
    gh_type_id = db.execute(text("INSERT INTO source_resource_types (source_instance_id, source_type_key, canonical_type_id, mapping_status, mapping_confidence, metadata) VALUES (:source_id, 'gh-history-type', :type_id, 'mapped', 'reviewed', '{\"keep\": true}') RETURNING id"), {'source_id': gh_id, 'type_id': milk_id}).scalar_one()
    db.execute(text("INSERT INTO resource_type_stat_ranges (source_resource_type_id, stat_code, min_value, max_value, is_applicable) VALUES (:id, 'OQ', 100, 800, true)"), {'id': gh_type_id})
    taxonomy_tables = {'resource_types', 'source_resource_types', 'resource_type_edges', 'resource_type_closure'}
    tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")).scalars().all()

    def rows(table):
        # Identifiers originate exclusively from PostgreSQL's own catalog.
        quoted = db.dialect.identifier_preparer.quote(table)
        return sorted(json.dumps(value, sort_keys=True) for value in db.execute(text(f'SELECT to_jsonb(t) FROM {quoted} t')).scalars())

    before = {table: rows(table) for table in tables if table not in taxonomy_tables}
    canonical_before = rows('resource_types')
    mappings_before = rows('source_resource_types')
    load_index(db, _custom_index())
    load_index(db, _custom_index())
    assert {table: rows(table) for table in before} == before
    assert set(canonical_before) <= set(rows('resource_types'))
    assert set(mappings_before) <= set(rows('source_resource_types'))


def test_database_cycle_conflict_is_reported_before_writes(db):
    from app.indexing.core3.resource_types import inspect_database
    load_index(db, _index_for_loader())
    milk_id = db.execute(text("SELECT id FROM resource_types WHERE slug = 'test_milk'")).scalar_one()
    root_id = db.execute(text("SELECT id FROM resource_types WHERE slug = 'resource'")).scalar_one()
    db.execute(text("INSERT INTO resource_type_edges (parent_type_id, child_type_id, source) VALUES (:parent, :child, 'test-conflict')"), {'parent': milk_id, 'child': root_id})
    before = db.execute(text('SELECT count(*) FROM resource_types')).scalar_one()
    assert any(item.startswith('cycle:') for item in inspect_database(db, _custom_index())['conflicts'])
    with pytest.raises(ValueError, match='cycle:'):
        load_index(db, _custom_index())
    assert db.execute(text('SELECT count(*) FROM resource_types')).scalar_one() == before
