from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


STAT_CODES = ("CR", "CD", "DR", "FL", "HR", "MA", "PE", "OQ", "SR", "UT", "ER")
SOURCE_SYSTEM = "galaxy_harvester"
SOURCE_INSTANCE = "galaxy-153"
SOURCE_GALAXY_ID = 153
SOURCE_GALAXY_NAME = "SWG Bellum Gero"


@dataclass(frozen=True)
class HistoricalResource:
    spawn_id: int
    name: str
    resource_type: str
    resource_type_name: str | None
    entered: datetime | None
    unavailable: datetime | None
    stats: dict[str, int]
    stat_ranges: dict[str, dict[str, int | None]]
    planets: tuple[dict[str, Any], ...]
    exact_source_path: str
    raw_source_sha256: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArchiveData:
    root: Path
    frozen_manifest: dict[str, Any]
    resources: tuple[HistoricalResource, ...]
    names: dict[str, list[int]]
    planets: dict[str, dict[str, Any]]
    source_types: dict[str, dict[str, Any]]
    unresolved: tuple[dict[str, Any], ...]
    checksums: dict[str, str]

    @property
    def frozen_identity_count(self) -> int:
        return len(self.frozen_manifest["identities"])


def parse_source_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_checksums(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel_path = line.split("  ", 1)
        out[rel_path] = digest
    return out


def read_archive(root: Path) -> ArchiveData:
    normalized = root / "normalized"
    required = [
        normalized / "frozen-identities.json",
        normalized / "resources-index.json",
        normalized / "names.json",
        normalized / "planets.json",
        normalized / "source-types.json",
        normalized / "unresolved.json",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required archive files: " + ", ".join(str(path) for path in missing))

    frozen = load_json(normalized / "frozen-identities.json")
    resources_index = load_json(normalized / "resources-index.json")["resources"]
    names = load_json(normalized / "names.json")["names"]
    planets = load_json(normalized / "planets.json")["planets"]
    source_types = load_json(normalized / "source-types.json")["source_types"]
    unresolved = tuple(load_json(normalized / "unresolved.json")["unresolved"])
    checksums = load_checksums(root / "checksums" / "sha256sums.txt")

    validate_frozen_manifest(frozen, resources_index, names)
    resources = tuple(_resource_from_payload(spawn_id, payload, frozen) for spawn_id, payload in sorted(resources_index.items(), key=lambda item: int(item[0])))
    return ArchiveData(
        root=root,
        frozen_manifest=frozen,
        resources=resources,
        names=names,
        planets=planets,
        source_types=source_types,
        unresolved=unresolved,
        checksums=checksums,
    )


def validate_frozen_manifest(frozen: dict[str, Any], resources_index: dict[str, Any], names: dict[str, list[int]]) -> None:
    identities = frozen.get("identities")
    if frozen.get("galaxy_id") != SOURCE_GALAXY_ID:
        raise ValueError("frozen identity manifest is not for Galaxy Harvester galaxy 153")
    if not isinstance(identities, list) or not identities:
        raise ValueError("frozen identity manifest must contain identities")
    expected_count = len(identities)
    checks = {
        "unique_spawn_id_count": expected_count,
        "unique_name_count": expected_count,
        "duplicate_spawn_id_count": 0,
        "conflicting_spawn_id_count": 0,
        "duplicate_name_count": 0,
        "source_total_results_start": expected_count,
        "source_total_results_end": expected_count,
    }
    for key, expected in checks.items():
        if frozen.get(key) != expected:
            raise ValueError(f"frozen identity manifest {key}={frozen.get(key)!r}; expected {expected!r}")
    if frozen.get("names_mapping_to_multiple_spawn_ids") != {}:
        raise ValueError("frozen identity manifest contains duplicate historical names")
    if frozen.get("no_unresolved_identity_conflicts") is not True or frozen.get("source_count_reconciles") is not True:
        raise ValueError("frozen identity manifest is not reconciled")
    identity_ids = {str(item["spawn_id"]) for item in identities}
    if identity_ids != set(resources_index):
        raise ValueError("resources-index population does not match frozen identity set")
    if len(names) != expected_count:
        raise ValueError("names index count does not match frozen identity count")


def _resource_from_payload(spawn_id: str, payload: dict[str, Any], frozen: dict[str, Any]) -> HistoricalResource:
    exact = payload.get("exact") or {}
    if not exact.get("found"):
        raise ValueError(f"resource {spawn_id} does not contain a found exact payload")
    if int(exact["spawn_id"]) != int(spawn_id):
        raise ValueError(f"resource {spawn_id} exact payload spawn_id mismatch")
    identity = next(item for item in frozen["identities"] if int(item["spawn_id"]) == int(spawn_id))
    stats = {code: int(value) for code, value in (exact.get("stats") or {}).items()}
    unknown_stats = sorted(set(stats) - set(STAT_CODES))
    if unknown_stats:
        raise ValueError(f"resource {spawn_id} contains unknown stat codes: {unknown_stats}")
    return HistoricalResource(
        spawn_id=int(spawn_id),
        name=exact["name"],
        resource_type=exact["resource_type"],
        resource_type_name=exact.get("resource_type_name"),
        entered=parse_source_datetime(exact.get("entered")),
        unavailable=parse_source_datetime(exact.get("unavailable")),
        stats=stats,
        stat_ranges=exact.get("stat_ranges") or {},
        planets=tuple(exact.get("planets") or ()),
        exact_source_path=payload.get("exact_source_path") or f"raw/http/get-resource-by-name/{spawn_id}.xml",
        raw_source_sha256=identity.get("raw_source_sha256"),
        payload=payload,
    )


def normalize_name(value: str) -> str:
    return value.strip().casefold()

