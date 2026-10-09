"""Pure compatibility boundary for the native Core3 resource exporter.

Verified against BellumGero-Live Main d3b9a7fc6f271ffbb8e9576f8928445fdafb71b5.
The existing Phase 4B parser remains the final resource validator.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.importing.core3_live.snapshot import (
    SCHEMA_VERSION, SOURCE_INSTANCE, SOURCE_SYSTEM, SnapshotValidationError, parse_snapshot,
)

NATIVE_MARKERS = frozenset({"generated_at", "capture_started_at", "capture_completed_at",
                          "consistency", "selection", "resource_count"})
NATIVE_RESOURCE_MARKERS = frozenset({"source_resource_id", "source_type_id", "lifecycle", "active", "spawn_map_state"})
NATIVE_ENVELOPE_MARKERS = NATIVE_MARKERS | {"galaxy", "build", "native_exporter_metadata", "native_exporter_resource_metadata"}
LEGACY_RESOURCE_MARKERS = frozenset({"oid", "type", "spawned_at", "expires_at", "despawned_at"})


def _reject(message: str):
    raise SnapshotValidationError(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(content: bytes) -> Any:
    """Strict native JSON decoding: no ambiguous keys or non-finite numbers."""
    try:
        return json.loads(content, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: _reject(f"invalid JSON number: {value}"))
    except SnapshotValidationError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SnapshotValidationError("malformed snapshot JSON") from exc


def is_native_snapshot(payload: Any) -> bool:
    """Classify complete native structure; reject tainted or mixed legacy shapes.

    One reserved field is evidence of ambiguity, not enough to accept a native
    document. Incomplete native structure never falls back to legacy validation.
    """
    if not isinstance(payload, dict):
        return False
    records = payload.get("resources")
    records = records if isinstance(records, list) else []
    native_hint = bool(NATIVE_ENVELOPE_MARKERS.intersection(payload)) or any(
        isinstance(record, dict) and NATIVE_RESOURCE_MARKERS.intersection(record) for record in records)
    if not native_hint:
        return False
    if ({"core3_revision", "exporter_version"} | LEGACY_RESOURCE_MARKERS).intersection(payload) or any(
        isinstance(record, dict) and LEGACY_RESOURCE_MARKERS.intersection(record) for record in records):
        _reject("ambiguous snapshot mixes native and website fields")
    if {"native_exporter_metadata", "native_exporter_resource_metadata"}.intersection(payload):
        _reject("internal normalized snapshot envelopes are not upload formats")
    if not NATIVE_MARKERS.issubset(payload):
        _reject("incomplete native snapshot metadata; legacy fallback is forbidden")
    return True


def unix_seconds(value: Any, label: str) -> str:
    # Integer-only: floats/booleans/millisecond epochs/strings are not native seconds.
    if type(value) is not int or not 0 <= value <= 253402300799:
        _reject(f"{label} must be a valid nonnegative integer Unix-second timestamp")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise SnapshotValidationError(f"{label} is outside the supported timestamp range") from exc


def adapt_exporter_snapshot(snapshot: dict | bytes) -> dict:
    """Convert a native export without mutating input or fabricating source values."""
    native = decode_json(snapshot) if isinstance(snapshot, bytes) else deepcopy(snapshot)
    if not isinstance(native, dict):
        _reject("native snapshot root must be an object")
    if not is_native_snapshot(native):
        _reject("adapt_exporter_snapshot requires a native Core3 snapshot")
    required = {"schema_version", "source_system", "source_instance", "complete", "captured_at", "resources", *NATIVE_MARKERS}
    missing = sorted(required - native.keys())
    if missing:
        _reject(f"missing native snapshot fields: {', '.join(missing)}")
    if type(native["schema_version"]) is not int or native["schema_version"] != SCHEMA_VERSION:
        _reject("unsupported native schema_version")
    if native["source_system"] != SOURCE_SYSTEM or native["source_instance"] != SOURCE_INSTANCE:
        _reject("incorrect native source identity")
    if native["complete"] is not True:
        _reject("native snapshot must have complete=true")
    if native["selection"] != "core3_in_shift":
        _reject("unsupported native selection; expected core3_in_shift")
    if native["consistency"] != "interval":
        _reject("unsupported native consistency; expected interval")
    times = {key: unix_seconds(native[key], key) for key in
             ("captured_at", "capture_started_at", "capture_completed_at", "generated_at")}
    if not native["capture_started_at"] <= native["captured_at"] <= native["capture_completed_at"]:
        _reject("captured_at must be within the ordered capture interval")
    if native["captured_at"] != native["capture_started_at"]:
        _reject("captured_at must equal capture_started_at selection cutoff")
    if native["generated_at"] < native["capture_completed_at"]:
        _reject("generated_at must not precede capture completion")
    resources = native["resources"]
    count = native["resource_count"]
    if not isinstance(resources, list) or type(count) is not int or count < 0 or count != len(resources):
        _reject("resource_count must match the resource array length")
    converted = []
    for index, record in enumerate(resources):
        label = f"resources[{index}]"
        if not isinstance(record, dict):
            _reject(f"{label} must be an object")
        fields = {"source_resource_id", "source_type_id", "name", "stats", "planets", "spawn_map_state", "lifecycle", "active"}
        missing = sorted(fields - record.keys())
        if missing:
            _reject(f"{label} missing fields: {', '.join(missing)}")
        if {"oid", "type", "spawned_at", "expires_at", "despawned_at"}.intersection(record):
            _reject(f"{label} mixes native and website resource fields")
        if record["active"] is not True:
            _reject(f"{label} must be active=true")
        planets = record["planets"]
        if not isinstance(planets, list):
            _reject(f"{label}.planets must be an array")
        expected_map_state = "present" if planets else "empty"
        if record["spawn_map_state"] != expected_map_state:
            _reject(f"{label} spawn_map_state must match planet keys ({expected_map_state})")
        lifecycle = record["lifecycle"]
        if not isinstance(lifecycle, dict):
            _reject(f"{label}.lifecycle must be an object")
        if lifecycle.get("despawned_at") is not None:
            _reject(f"{label} active resources cannot have despawned_at")
        if lifecycle.get("spawned_at") is not None:
            _reject(f"{label} native spawned_at must be null; creation time is not established")
        if lifecycle.get("expires_at") is None:
            _reject(f"{label} native expires_at is required for in-shift selection")
        flattened = {}
        for key in ("spawned_at", "expires_at", "despawned_at"):
            value = lifecycle.get(key)
            flattened[key] = None if value is None else unix_seconds(value, f"{label}.lifecycle.{key}")
        if lifecycle["expires_at"] <= native["captured_at"]:
            _reject(f"{label} expires_at must be strictly after captured_at")
        converted.append({"oid": record["source_resource_id"], "type": record["source_type_id"],
                          "name": record["name"], "stats": record["stats"], "planets": record["planets"], **flattened})
    result = {"schema_version": native["schema_version"], "source_system": native["source_system"],
              "source_instance": native["source_instance"], "complete": True,
              "captured_at": times["captured_at"], "resources": converted}
    # Do not infer revision from commit or fabricate exporter-version metadata.
    build = native.get("build")
    if build is not None:
        if not isinstance(build, dict):
            _reject("build must be an object when present")
        for field in ("revision", "commit"):
            value = build.get(field)
            if value is not None and (not isinstance(value, str) or not value):
                _reject(f"build.{field} must be a non-empty string or null")
        if build.get("commit") is not None:
            _reject("native build.commit must be null; no verified commit is supplied")
        if build.get("revision") is not None:
            result["core3_revision"] = build["revision"]
    galaxy = native.get("galaxy")
    if galaxy is not None:
        if not isinstance(galaxy, dict) or type(galaxy.get("id")) is not int or not isinstance(galaxy.get("name"), str):
            _reject("galaxy must contain an integer id and string name when present")
    parse_snapshot(result)  # Phase 4B owns OID, stat, planet and timestamp validation.
    for record in converted:
        decimal = record["oid"].lstrip("0") or "0"
        if record["oid"] != decimal:
            _reject("native source_resource_id must be the canonical decimal object identity (no leading zeroes)")
        if len(decimal) > 20 or (len(decimal) == 20 and decimal > "18446744073709551615"):
            _reject("native source_resource_id exceeds unsigned 64-bit object identity")
    return result


@dataclass(frozen=True)
class PreparedSnapshot:
    content: bytes
    native: bool
    audit: dict


def prepare_snapshot(content: bytes) -> PreparedSnapshot:
    """Website bytes retain legacy hashing; native JSON is deterministically encoded."""
    # Decode once, before classification: duplicate keys must not erase native
    # markers or replace a native resource array with an apparently legacy array.
    payload = decode_json(content)
    raw_hash = hashlib.sha256(content).hexdigest()
    if not is_native_snapshot(payload):
        parse_snapshot(payload)
        return PreparedSnapshot(content, False, {"input_format": "website", "input_sha256": raw_hash,
                                                  "input_byte_size": len(content)})
    native = payload
    normalized = adapt_exporter_snapshot(native)
    # Include native metadata in the canonical fingerprint: same-capture content
    # changes remain conflicts; only object-key order/whitespace are nonsemantic.
    normalized["native_exporter_metadata"] = {key: value for key, value in native.items() if key != "resources"}
    normalized["native_exporter_resource_metadata"] = [
        {key: value for key, value in record.items() if key not in {"source_resource_id", "source_type_id", "name", "stats", "planets", "lifecycle"}}
        for record in native["resources"]
    ]
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    audit = {"input_format": "native_core3", "input_sha256": raw_hash, "input_byte_size": len(content),
             "normalized_sha256": hashlib.sha256(canonical).hexdigest(),
             "native_metadata": normalized["native_exporter_metadata"]}
    return PreparedSnapshot(canonical, True, audit)


@dataclass(frozen=True)
class CountSafetyPolicy:
    max_reduction_fraction: float = 0.5
    sustained_max_reduction_fraction: float | None = None

    def __post_init__(self):
        for index, fraction in enumerate((self.max_reduction_fraction, self.sustained_max_reduction_fraction)):
            if index == 1 and fraction is None:
                continue
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
                raise ValueError("Native count reduction fractions must be between 0 and 1")


def assess_count_safety(resource_count: int, previous_count: int | None, policy: CountSafetyPolicy,
                        *, sustained_baseline_count: int | None = None) -> dict:
    """Check the immediate drop and sustained decline from a trusted high water."""
    if any(value is not None and (type(value) is not int or value < 0)
           for value in (resource_count, previous_count, sustained_baseline_count)) or resource_count is None:
        raise ValueError("Resource counts must be nonnegative integers")
    baseline_values = [value for value in (previous_count, sustained_baseline_count) if value is not None]
    baseline = max(baseline_values) if baseline_values else None
    reduction = (previous_count - resource_count) / previous_count if previous_count else 0.0
    sustained_reduction = (baseline - resource_count) / baseline if baseline else 0.0
    sustained_limit = policy.sustained_max_reduction_fraction
    if sustained_limit is None:
        sustained_limit = policy.max_reduction_fraction
    reasons = []
    if resource_count == 0 and baseline != 0:
        reasons.append("unexpected_empty")
    if reduction > policy.max_reduction_fraction:
        reasons.append("single_snapshot_reduction")
    if sustained_reduction > sustained_limit:
        reasons.append("sustained_reduction")
    return {"review_required": bool(reasons), "review_reasons": reasons, "resource_count": resource_count,
            "previous_resource_count": previous_count, "sustained_baseline_count": baseline,
            "reduction_fraction": max(0.0, reduction), "max_reduction_fraction": policy.max_reduction_fraction,
            "sustained_reduction_fraction": max(0.0, sustained_reduction), "sustained_max_reduction_fraction": sustained_limit}


def main(argv: list[str] | None = None) -> int:
    """Structural validation only. Never connects to a database or imports data."""
    parser = argparse.ArgumentParser(description="Validate native/website Core3 snapshots without importing")
    parser.add_argument("--snapshot", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        prepared = prepare_snapshot(args.snapshot.read_bytes())
        snapshot = parse_snapshot(json.loads(prepared.content))
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.exit(2, "Snapshot file could not be read or failed structural validation\n")
    print(json.dumps({"valid": True, "source_instance": snapshot.source_instance,
        "captured_at": snapshot.captured_at.isoformat(), "resources": len(snapshot.resources),
        "audit": prepared.audit, "database_checks": "not performed; use authenticated validate-only upload"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
