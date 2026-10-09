from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app.importing.core3_live.exporter_adapter import (
    CountSafetyPolicy, adapt_exporter_snapshot, assess_count_safety, is_native_snapshot, prepare_snapshot,
)
from app.importing.core3_live.snapshot import SnapshotValidationError, parse_snapshot


def native_resource(**changes):
    record = {"source_resource_id": "18446744073709551615", "name": "  Exact Name Ω  ",
              "source_type_id": "test_core3_resource_type", "stats": {"CD": 800, "OQ": 0},
              "planets": ["corellia", "tatooine"], "spawn_map_state": "present",
              "lifecycle": {"spawned_at": None, "expires_at": 1800100000, "despawned_at": None}, "active": True}
    record.update(changes)
    return record


def native_snapshot(resources=None, **changes):
    records = [native_resource()] if resources is None else resources
    payload = {"schema_version": 1, "source_system": "core3", "source_instance": "bellum-gero-live",
               "generated_at": 1800000002, "captured_at": 1800000000, "capture_started_at": 1800000000,
               "capture_completed_at": 1800000001, "consistency": "interval", "complete": True,
               "selection": "core3_in_shift", "resource_count": len(records), "resources": records,
               "galaxy": {"id": 2, "name": "Bellum Gero"}, "build": {"revision": None, "commit": None}}
    payload.update(changes)
    return payload


def native_bytes(**changes):
    return json.dumps(native_snapshot(**changes)).encode()


def test_conversion_preserves_owned_values_and_does_not_mutate_input():
    original = native_snapshot()
    before = deepcopy(original)
    for source in (original, json.dumps(original).encode()):
        converted = adapt_exporter_snapshot(source)
        parsed = parse_snapshot(converted)
        assert parsed.captured_at == datetime.fromtimestamp(1800000000, timezone.utc)
        assert converted["captured_at"] == "2027-01-15T08:00:00+00:00"
        resource = parsed.resources[0]
        assert resource.oid == "18446744073709551615"
        assert resource.name == "  Exact Name Ω  "
        assert resource.resource_type == "test_core3_resource_type"
        assert resource.stats == {"CD": 800, "OQ": 0} and "CR" not in resource.stats
        assert resource.planets == ("corellia", "tatooine")
        assert resource.spawned_at is None and resource.despawned_at is None
        assert resource.expires_at == datetime.fromtimestamp(1800100000, timezone.utc)
    assert original == before


def test_empty_maps_missing_stats_and_optional_metadata():
    payload = native_snapshot(resources=[native_resource(planets=[], spawn_map_state="empty", stats={},
        lifecycle={"expires_at": 1800100000})])
    payload.pop("galaxy")
    payload.pop("build")
    result = adapt_exporter_snapshot(payload)
    assert result["resources"][0]["planets"] == [] and result["resources"][0]["stats"] == {}
    assert result["resources"][0]["spawned_at"] is None
    assert "core3_revision" not in result
    result = adapt_exporter_snapshot(native_snapshot(build={"revision": "known-revision", "commit": None}, galaxy={"id": 0, "name": ""}))
    assert result["core3_revision"] == "known-revision"


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": True}, {"source_instance": "other"}, {"source_system": "other"},
    {"complete": False}, {"complete": 1}, {"selection": "all"}, {"consistency": "instant"},
    {"resource_count": 2}, {"resource_count": True}, {"resource_count": -1}, {"resources": {}},
    {"capture_started_at": 1800000002}, {"capture_completed_at": 1799999999}, {"generated_at": 1799999999},
    {"captured_at": 1800000001}, {"galaxy": {"id": "2", "name": "wrong"}}, {"build": {"commit": "unverified"}},
])
def test_invalid_metadata(changes):
    with pytest.raises(SnapshotValidationError):
        adapt_exporter_snapshot(native_snapshot(**changes))


@pytest.mark.parametrize("field", ["generated_at", "captured_at", "capture_started_at", "capture_completed_at"])
@pytest.mark.parametrize("value", [None, True, 1800000000.0, "1800000000", -1, 1800000000000, 10**30])
def test_invalid_unix_seconds(field, value):
    with pytest.raises(SnapshotValidationError):
        adapt_exporter_snapshot(native_snapshot(**{field: value}))


@pytest.mark.parametrize("changes", [
    {"active": False}, {"active": 1}, {"source_resource_id": 123}, {"source_resource_id": "1.25"},
    {"source_resource_id": "18446744073709551616"}, {"source_resource_id": "001"},
    {"stats": {"ER": 1}}, {"stats": {"XX": 10}},
    {"stats": {"OQ": True}}, {"stats": {"OQ": 1.5}}, {"stats": {"OQ": None}},
    {"name": ""}, {"source_type_id": ""}, {"planets": ["corellia", "corellia"]}, {"planets": [42]},
    {"spawn_map_state": "empty"}, {"spawn_map_state": "unknown"}, {"planets": []},
    {"lifecycle": []}, {"lifecycle": {"expires_at": None}}, {"lifecycle": {}},
    {"lifecycle": {"expires_at": 1800000000}}, {"lifecycle": {"expires_at": 0}},
    {"lifecycle": {"expires_at": 1800100000, "spawned_at": 1799999999}},
    {"lifecycle": {"expires_at": 1800100000, "despawned_at": 1800000000}},
    {"lifecycle": {"expires_at": "1800100000"}}, {"oid": "123"},
])
def test_invalid_resources(changes):
    with pytest.raises(SnapshotValidationError):
        adapt_exporter_snapshot(native_snapshot(resources=[native_resource(**changes)]))


def test_duplicate_oid_and_malformed_records():
    for records in ([native_resource(), native_resource()], [None], ["resource"]):
        with pytest.raises(SnapshotValidationError):
            adapt_exporter_snapshot(native_snapshot(resources=records))


@pytest.mark.parametrize("field", ["schema_version", "source_system", "source_instance", "generated_at", "captured_at",
    "capture_started_at", "capture_completed_at", "consistency", "complete", "selection", "resource_count", "resources"])
def test_missing_required_metadata(field):
    payload = native_snapshot()
    del payload[field]
    with pytest.raises(SnapshotValidationError):
        adapt_exporter_snapshot(payload)


@pytest.mark.parametrize("field", ["source_resource_id", "source_type_id", "name", "stats", "planets", "spawn_map_state", "active", "lifecycle"])
def test_missing_required_resource_fields(field):
    record = native_resource()
    del record[field]
    with pytest.raises(SnapshotValidationError):
        adapt_exporter_snapshot(native_snapshot(resources=[record]))


def test_json_ambiguity_nonfinite_numbers_and_invalid_encoding():
    duplicate = native_bytes().replace(b'"complete": true', b'"complete": false, "complete": true')
    for content in (duplicate, b"{invalid", b"\xff", b"[]", native_bytes().replace(b'"OQ": 0', b'"OQ": NaN')):
        with pytest.raises(SnapshotValidationError):
            adapt_exporter_snapshot(content)


def test_native_recognition_does_not_guess_single_fields_and_rejects_partial_native():
    with pytest.raises(SnapshotValidationError):
        is_native_snapshot({"generated_at": 123, "resources": []})
    assert is_native_snapshot(native_snapshot(resources=[]))
    for missing in ("resource_count", "selection", "capture_completed_at"):
        payload = native_snapshot()
        del payload[missing]
        with pytest.raises(SnapshotValidationError):
            is_native_snapshot(payload)
        with pytest.raises(SnapshotValidationError):
            prepare_snapshot(json.dumps(payload).encode())


def test_stable_native_fingerprint_and_original_byte_audit():
    payload = native_snapshot()
    first_bytes = json.dumps(payload).encode()
    second_bytes = json.dumps(dict(reversed(list(payload.items()))), indent=4, sort_keys=True).encode()
    first, second = prepare_snapshot(first_bytes), prepare_snapshot(second_bytes)
    assert first.native and first.content == second.content
    assert first.audit["normalized_sha256"] == second.audit["normalized_sha256"]
    assert first.audit["input_sha256"] != second.audit["input_sha256"]
    assert first.audit["input_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert first.audit["native_metadata"]["consistency"] == "interval"
    assert parse_snapshot(json.loads(first.content)).resources[0].stats["OQ"] == 0
    changed = deepcopy(payload)
    changed["generated_at"] += 1
    assert prepare_snapshot(json.dumps(changed).encode()).content != first.content


@pytest.mark.parametrize("as_bytes", [False, True])
def test_direct_adapter_rejects_valid_legacy_snapshot(as_bytes):
    website = adapt_exporter_snapshot(native_snapshot())
    content = json.dumps(website, indent=2).encode()
    assert parse_snapshot(website).resources
    with pytest.raises(SnapshotValidationError, match="requires a native Core3 snapshot"):
        adapt_exporter_snapshot(content if as_bytes else website)
    prepared = prepare_snapshot(content)
    assert not prepared.native and prepared.content == content
    assert prepared.audit["input_sha256"] == hashlib.sha256(content).hexdigest()


def test_existing_website_format_keeps_original_bytes_and_hash():
    website = adapt_exporter_snapshot(native_snapshot())
    first_bytes = json.dumps(website, indent=2).encode()
    result = prepare_snapshot(first_bytes)
    assert not result.native and result.content == first_bytes
    assert result.audit["input_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert not is_native_snapshot(website)


@pytest.mark.parametrize("current,previous,review", [
    (0, None, True), (0, 100, True), (0, 0, False), (1, None, False),
    (49, 100, True), (50, 100, False), (70, 100, False), (110, 100, False),
])
def test_count_safeguard(current, previous, review):
    assert assess_count_safety(current, previous, CountSafetyPolicy())["review_required"] is review


def test_configurable_threshold_cannot_disable_unexpected_empty_review():
    assert assess_count_safety(1, 100, CountSafetyPolicy(1))["review_required"] is False
    assert assess_count_safety(0, 100, CountSafetyPolicy(1))["review_required"] is True
    for value in (-1, 2, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            CountSafetyPolicy(value)


def test_standalone_adapter_never_needs_a_database(monkeypatch):
    monkeypatch.delenv("BELLUM_DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert adapt_exporter_snapshot(native_bytes())["resources"]


def test_expiration_is_compared_with_cutoff_not_interval_end():
    payload = native_snapshot(capture_completed_at=1800000005, generated_at=1800000006,
        resources=[native_resource(lifecycle={"expires_at": 1800000001})])
    assert adapt_exporter_snapshot(payload)["resources"][0]["expires_at"] == "2027-01-15T08:00:01+00:00"


def test_pure_validation_cli_has_no_import_side_effects(tmp_path, capsys, monkeypatch):
    from app.importing.core3_live.exporter_adapter import main
    monkeypatch.delenv("BELLUM_DATABASE_URL", raising=False)
    path = tmp_path / "native.json"
    content = native_bytes()
    path.write_bytes(content)
    assert main(["--snapshot", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] and result["resources"] == 1
    assert "not performed" in result["database_checks"]
    assert path.read_bytes() == content
    path.write_bytes(b"{invalid")
    with pytest.raises(SystemExit) as failure:
        main(["--snapshot", str(path)])
    assert failure.value.code == 2


@pytest.mark.parametrize("marker", ["generated_at", "capture_started_at", "capture_completed_at", "consistency",
    "selection", "resource_count", "galaxy", "build", "native_exporter_metadata", "native_exporter_resource_metadata"])
def test_single_native_envelope_marker_cannot_taint_valid_legacy(marker):
    payload = adapt_exporter_snapshot(native_snapshot())
    payload[marker] = None
    with pytest.raises(SnapshotValidationError):
        prepare_snapshot(json.dumps(payload).encode())


@pytest.mark.parametrize("marker", ["source_resource_id", "source_type_id", "lifecycle", "active", "spawn_map_state"])
def test_single_native_resource_marker_cannot_hide_in_legacy_records(marker):
    payload = adapt_exporter_snapshot(native_snapshot())
    payload["resources"][0][marker] = None
    with pytest.raises(SnapshotValidationError, match="mixes"):
        prepare_snapshot(json.dumps(payload).encode())


def test_mixed_records_and_native_metadata_with_legacy_fields_are_rejected():
    legacy = adapt_exporter_snapshot(native_snapshot())
    mixtures = [native_snapshot(resources=[native_resource(), legacy["resources"][0]]),
                {**native_snapshot(), "resources": legacy["resources"]},
                {**native_snapshot(), "core3_revision": "legacy"},
                {**native_snapshot(), "exporter_version": "legacy"},
                {**native_snapshot(), "oid": "123"},
                {**native_snapshot(), "expires_at": "2027-01-16T00:00:00Z"},
                {**native_snapshot(), "captured_at": legacy["captured_at"]}]
    for payload in mixtures:
        with pytest.raises(SnapshotValidationError):
            prepare_snapshot(json.dumps(payload).encode())
        with pytest.raises(SnapshotValidationError):
            adapt_exporter_snapshot(payload)


def test_duplicate_keys_cannot_erase_native_identity_before_classification():
    legacy = adapt_exporter_snapshot(native_snapshot())
    legacy_bytes = json.dumps(legacy).encode()
    native_resources = json.dumps(native_snapshot()["resources"]).encode()
    # The old json.loads-first boundary discarded the first resource array.
    ambiguous = b'{"resources":' + native_resources + b"," + legacy_bytes[1:]
    with pytest.raises(SnapshotValidationError, match="duplicate JSON key"):
        prepare_snapshot(ambiguous)
    for content in (legacy_bytes[:-1], b"{", b'{"resources": [',
                    legacy_bytes.replace(b'"complete": true', b'"complete": false, "complete": true')):
        with pytest.raises(SnapshotValidationError):
            prepare_snapshot(content)


def test_genuine_legacy_extensions_empty_resources_and_original_hash_remain_valid():
    legacy = adapt_exporter_snapshot(native_snapshot())
    legacy.update(core3_revision="known", exporter_version="phase4", notes="optional annotation")
    for resources in (legacy["resources"], []):
        content = json.dumps({**legacy, "resources": resources}, indent=3).encode()
        prepared = prepare_snapshot(content)
        assert not prepared.native and prepared.content == content
        assert prepared.audit["input_sha256"] == hashlib.sha256(content).hexdigest()


def test_sustained_reduction_is_independent_of_immediate_reduction():
    policy = CountSafetyPolicy(0.5, 0.5)
    assert not assess_count_safety(6, 8, policy, sustained_baseline_count=8)["review_required"]
    assert not assess_count_safety(4, 6, policy, sustained_baseline_count=8)["review_required"]
    result = assess_count_safety(3, 4, policy, sustained_baseline_count=8)
    assert result["review_reasons"] == ["sustained_reduction"]
    assert result["reduction_fraction"] == 0.25 and result["sustained_reduction_fraction"] == 0.625
    assert not assess_count_safety(3, 4, CountSafetyPolicy(0.5, 0.75), sustained_baseline_count=8)["review_required"]


@pytest.mark.parametrize("value", [-1, 2, float("nan"), True, "0.5"])
def test_invalid_sustained_threshold(value):
    with pytest.raises(ValueError):
        CountSafetyPolicy(0.5, value)
