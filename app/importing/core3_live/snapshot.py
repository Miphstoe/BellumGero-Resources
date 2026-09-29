from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SOURCE_SYSTEM = "core3"
SOURCE_INSTANCE = "bellum-gero-live"
SCHEMA_VERSION = 1
STAT_CODES = ("CR", "CD", "DR", "FL", "HR", "MA", "PE", "OQ", "SR", "UT")
STAT_CODE_SET = set(STAT_CODES)
OID_RE = re.compile(r"^[0-9]+$")


class SnapshotValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Core3ResourceSnapshotItem:
    oid: str
    name: str
    resource_type: str
    planets: tuple[str, ...]
    stats: dict[str, int]
    expires_at: datetime | None
    spawned_at: datetime | None
    despawned_at: datetime | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class Core3ResourceSnapshot:
    schema_version: int
    source_system: str
    source_instance: str
    complete: bool
    captured_at: datetime
    resources: tuple[Core3ResourceSnapshotItem, ...]
    content_sha256: str
    byte_size: int
    raw_payload: dict[str, Any]
    core3_revision: str | None = None
    exporter_version: str | None = None


def read_snapshot(path: Path) -> Core3ResourceSnapshot:
    content = path.read_bytes()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SnapshotValidationError(f"malformed JSON: {exc}") from exc
    return parse_snapshot(payload, content_sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content))


def parse_snapshot(payload: Any, *, content_sha256: str = "", byte_size: int = 0) -> Core3ResourceSnapshot:
    if not isinstance(payload, dict):
        raise SnapshotValidationError("snapshot root must be an object")

    schema_version = _required_int(payload, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SnapshotValidationError(f"unsupported schema_version: {schema_version}")

    source_system = _required_str(payload, "source_system")
    if source_system != SOURCE_SYSTEM:
        raise SnapshotValidationError(f"wrong source_system: {source_system}")

    source_instance = _required_str(payload, "source_instance")
    if source_instance != SOURCE_INSTANCE:
        raise SnapshotValidationError(f"wrong source_instance: {source_instance}")

    complete = _required_bool(payload, "complete")
    if complete is not True:
        raise SnapshotValidationError("complete=false snapshots are structurally valid but not authoritative")

    captured_at = _parse_required_timestamp(payload, "captured_at")
    resources_payload = payload.get("resources")
    if not isinstance(resources_payload, list):
        raise SnapshotValidationError("resources must be an array")

    resources: list[Core3ResourceSnapshotItem] = []
    seen_oids: set[str] = set()
    for idx, item in enumerate(resources_payload):
        if not isinstance(item, dict):
            raise SnapshotValidationError(f"resources[{idx}] must be an object")
        resource = _parse_resource(item, idx)
        if resource.oid in seen_oids:
            raise SnapshotValidationError(f"duplicate OID: {resource.oid}")
        seen_oids.add(resource.oid)
        resources.append(resource)

    return Core3ResourceSnapshot(
        schema_version=schema_version,
        source_system=source_system,
        source_instance=source_instance,
        complete=complete,
        captured_at=captured_at,
        resources=tuple(resources),
        content_sha256=content_sha256,
        byte_size=byte_size,
        raw_payload=payload,
        core3_revision=_optional_str(payload, "core3_revision"),
        exporter_version=_optional_str(payload, "exporter_version"),
    )


def _parse_resource(payload: dict[str, Any], idx: int) -> Core3ResourceSnapshotItem:
    oid = _required_str(payload, "oid", prefix=f"resources[{idx}]")
    if not OID_RE.fullmatch(oid):
        raise SnapshotValidationError(f"malformed OID: {oid}")
    name = _required_str(payload, "name", prefix=f"resources[{idx}]")
    resource_type = _required_str(payload, "type", prefix=f"resources[{idx}]")
    planets_payload = payload.get("planets")
    if not isinstance(planets_payload, list):
        raise SnapshotValidationError(f"resources[{idx}].planets must be an array")
    planets: list[str] = []
    seen_planets: set[str] = set()
    for planet in planets_payload:
        if not isinstance(planet, str) or not planet:
            raise SnapshotValidationError(f"resources[{idx}].planets contains a non-empty string requirement violation")
        if planet in seen_planets:
            raise SnapshotValidationError(f"resources[{idx}] has duplicate planet {planet}")
        seen_planets.add(planet)
        planets.append(planet)

    stats_payload = payload.get("stats")
    if not isinstance(stats_payload, dict):
        raise SnapshotValidationError(f"resources[{idx}].stats must be an object")
    stats: dict[str, int] = {}
    for code, value in stats_payload.items():
        if code == "ER":
            raise SnapshotValidationError("ER is not a supported Core3 stat")
        if code not in STAT_CODE_SET:
            raise SnapshotValidationError(f"unknown Core3 stat: {code}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise SnapshotValidationError(f"resources[{idx}].stats.{code} must be an integer")
        stats[code] = value

    expires_at = _parse_optional_timestamp(payload, "expires_at", prefix=f"resources[{idx}]")
    spawned_at = _parse_optional_timestamp(payload, "spawned_at", prefix=f"resources[{idx}]")
    despawned_at = _parse_optional_timestamp(payload, "despawned_at", prefix=f"resources[{idx}]")
    if despawned_at is not None:
        raise SnapshotValidationError(f"resources[{idx}].despawned_at is incompatible with an active resource snapshot")

    return Core3ResourceSnapshotItem(
        oid=oid,
        name=name,
        resource_type=resource_type,
        planets=tuple(planets),
        stats=stats,
        expires_at=expires_at,
        spawned_at=spawned_at,
        despawned_at=despawned_at,
        payload=payload,
    )


def _required_str(payload: dict[str, Any], key: str, *, prefix: str = "snapshot") -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SnapshotValidationError(f"{prefix}.{key} must be a non-empty string")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SnapshotValidationError(f"snapshot.{key} must be a non-empty string when present")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotValidationError(f"snapshot.{key} must be an integer")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise SnapshotValidationError(f"snapshot.{key} must be a boolean")
    return value


def _parse_required_timestamp(payload: dict[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SnapshotValidationError(f"snapshot.{key} must be a timestamp string")
    return _parse_timestamp(value, f"snapshot.{key}")


def _parse_optional_timestamp(payload: dict[str, Any], key: str, *, prefix: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SnapshotValidationError(f"{prefix}.{key} must be a timestamp string or null")
    return _parse_timestamp(value, f"{prefix}.{key}")


def _parse_timestamp(value: str, label: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SnapshotValidationError(f"malformed timestamp {label}: {value}") from exc
    if parsed.tzinfo is None:
        raise SnapshotValidationError(f"timestamp {label} must include a timezone")
    return parsed
