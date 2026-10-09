from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path, PurePosixPath
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

    @cached_property
    def revision(self) -> str:
        payload = {"manifest": self.frozen_manifest, "resources": {str(r.spawn_id): r.payload for r in self.resources},
                   "names": self.names, "planets": self.planets, "types": self.source_types,
                   "unresolved": self.unresolved, "checksums": self.checksums}
        digest = hashlib.sha256()
        for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False).iterencode(payload):
            digest.update(chunk.encode("utf-8"))
        return digest.hexdigest()

    @property
    def frozen_identity_count(self) -> int:
        return len(self.frozen_manifest["identities"])


def parse_source_datetime(value: str | None) -> datetime | None:
    if value in (None, "", "None"):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", value):
        raise ValueError(f"invalid source timestamp: {value!r}")
    return datetime.fromisoformat(value)


def load_json(path: Path) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate JSON key {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"{path}: invalid JSON number {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative or any(ord(c) < 32 for c in relative):
        raise ValueError(f"invalid archive source path: {relative!r}")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in ("..", ".") for p in relative.split("/")) or "" in relative.split("/"):
        raise ValueError(f"invalid archive source path: {relative!r}")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"source path outside archive root: {relative}")
    current = root
    for part in parts.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink evidence is forbidden: {relative}")
    return result


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_checksums(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ValueError(f"missing required source checksums: {path}")
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "  " not in line:
            raise ValueError(f"malformed checksum line in {path}")
        digest, rel_path = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or rel_path in out:
            raise ValueError(f"invalid or duplicate checksum entry: {rel_path}")
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

    for path in required + [root / "checksums" / "sha256sums.txt"]:
        safe_path(root, path.relative_to(root).as_posix())

    frozen = load_json(normalized / "frozen-identities.json")
    def index(filename, key, kind=dict):
        envelope = load_json(normalized / filename)
        if not isinstance(envelope, dict) or not isinstance(envelope.get(key), kind):
            raise ValueError(f"{filename}: expected {key} {kind.__name__}")
        return envelope[key]
    if not isinstance(frozen, dict):
        raise ValueError("frozen identity manifest must be an object")
    resources_index = index("resources-index.json", "resources")
    names = index("names.json", "names")
    planets = index("planets.json", "planets")
    source_types = index("source-types.json", "source_types")
    unresolved = tuple(index("unresolved.json", "unresolved", list))
    if any(not isinstance(item, dict) for item in unresolved):
        raise ValueError("unresolved observations must be objects")
    for pid, planet in planets.items():
        if not re.fullmatch(r"[1-9][0-9]*", pid) or not isinstance(planet, dict) or type(planet.get("planet_id")) is not int or planet["planet_id"] != int(pid) or not isinstance(planet.get("planet_name"), str) or not planet["planet_name"].strip():
            raise ValueError(f"invalid planet index entry: {pid}")
    for sid, payload in resources_index.items():
        if not re.fullmatch(r"[1-9][0-9]*", sid) or not isinstance(payload, dict) or not isinstance(payload.get("exact"), dict):
            raise ValueError(f"invalid resource index entry: {sid}")
        exact = payload["exact"]
        if not isinstance(exact.get("stats"), dict) or not isinstance(exact.get("stat_ranges"), dict) or not isinstance(exact.get("planets"), list):
            raise ValueError(f"resource {sid}: stats, stat_ranges, and planets are required")
        if any(not isinstance(p, dict) or type(p.get("id")) is not int for p in exact["planets"]):
            raise ValueError(f"resource {sid}: invalid planet observation")
        if not isinstance(payload.get("exact_source_path"), str):
            raise ValueError(f"resource {sid}: missing source path")
    checksums = load_checksums(root / "checksums" / "sha256sums.txt")
    for path in required:
        if path.relative_to(root).as_posix() not in checksums:
            raise ValueError(f"missing normalized checksum: {path.name}")

    for relative, digest in checksums.items():
        path = safe_path(root, relative)
        if not path.is_file():
            raise ValueError(f"missing source evidence: {relative}")
        if file_sha256(path) != digest:
            raise ValueError(f"SHA-256 mismatch: {relative}")

    validate_frozen_manifest(frozen, resources_index, names, unresolved)
    identities = {str(item["spawn_id"]): item for item in frozen["identities"]}
    resources = tuple(_resource_from_payload(spawn_id, payload, identities) for spawn_id, payload in sorted(resources_index.items(), key=lambda item: int(item[0])))
    from app.importing.galaxy_harvester.xml_source import parse_xml, read_xml_bytes
    evidence = set()
    expected_planets, expected_types = {}, {}
    for resource in resources:
        relative = resource.exact_source_path
        if not isinstance(relative, str) or not relative.startswith(("raw/resources/", "raw/http/get-resource-by-name/")):
            raise ValueError(f"invalid resource source path: {relative!r}")
        if relative not in checksums:
            raise ValueError(f"missing checksum for source evidence: {relative}")
        if resource.raw_source_sha256 != checksums[relative]:
            raise ValueError(f"resource {resource.spawn_id}: manifest source hash mismatch")
        identity = identities[str(resource.spawn_id)]
        if identity.get("raw_source_file") != relative:
            raise ValueError(f"resource {resource.spawn_id}: manifest source path mismatch")
        parsed = parse_xml(read_xml_bytes(safe_path(root, relative)), relative)
        exact = resource.payload["exact"]
        # Older payloads may contain null contributor fields; never accept identities.
        for key in parsed:
            actual = exact.get(key)
            if key == "planets":
                actual = [{k: p.get(k) for k in ("id", "name", "entered", "unavailable")} for p in actual or []]
                actual.sort(key=lambda p: p["id"])
            if json.dumps(actual, sort_keys=True) != json.dumps(parsed[key], sort_keys=True):
                raise ValueError(f"resource {resource.spawn_id}: normalized {key} differs from source XML")
        _no_contributors(resource.payload)
        if relative in evidence:
            raise ValueError(f"duplicate source path: {relative}")
        evidence.add(relative)
        type_payload = {"resource_type": resource.resource_type, "resource_type_name": resource.resource_type_name}
        if expected_types.setdefault(resource.resource_type, type_payload) != type_payload:
            raise ValueError(f"conflicting type name: {resource.resource_type}")
        for planet in resource.planets:
            planet_payload = {"planet_id": planet["id"], "planet_name": planet["name"]}
            if expected_planets.setdefault(str(planet["id"]), planet_payload) != planet_payload:
                raise ValueError(f"conflicting planet name: {planet['id']}")
    if source_types != expected_types:
        raise ValueError("source-types index disagrees with verified resources")
    # Unused legacy planet entries are harmless, but every used mapping must agree.
    if any(planets.get(key) != value for key, value in expected_planets.items()):
        raise ValueError("planets index disagrees with verified resources")
    for item in unresolved:
        relative = item.get("source_path")
        if not isinstance(relative, str) or not relative.startswith(("raw/errors/", "raw/http/get-resource-by-name/")):
            raise ValueError(f"invalid unresolved source path: {relative!r}")
        if relative not in checksums:
            raise ValueError(f"missing checksum for unresolved evidence: {relative}")
        parsed = parse_xml(read_xml_bytes(safe_path(root, relative)), relative)
        if parsed != {"found": False, "name": item["name"], "result_text": "new"}:
            raise ValueError(f"unresolved evidence mismatch: {relative}")
        if item.get("raw_source_sha256") != checksums[relative]:
            raise ValueError(f"unresolved source hash mismatch: {relative}")
        _no_contributors(item)
        attempts = item.get("acquisition_attempts")
        if attempts is None:
            attempts = [{"source_path": relative, "raw_source_sha256": item["raw_source_sha256"]}]
        if not isinstance(attempts, list) or not attempts:
            raise ValueError(f"invalid unresolved acquisition attempts: {item['name']}")
        from app.importing.galaxy_harvester.unresolved import unresolved_attempt, reconcile_attempts
        verified_attempts = []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise ValueError("unresolved acquisition attempt must be an object")
            attempt_path = attempt.get("source_path")
            if not isinstance(attempt_path, str) or not attempt_path.startswith(("raw/errors/", "raw/http/get-resource-by-name/")):
                raise ValueError(f"invalid unresolved attempt source path: {attempt_path!r}")
            if attempt_path not in checksums or attempt.get("raw_source_sha256") != checksums[attempt_path]:
                raise ValueError(f"missing or mismatched unresolved attempt checksum: {attempt_path}")
            verified, signature = unresolved_attempt(read_xml_bytes(safe_path(root, attempt_path)), attempt_path)
            if signature.get("spawnName") != normalize_name(item["name"]):
                raise ValueError(f"unresolved acquisition name mismatch: {attempt_path}")
            if "acquisition_attempts" in item and verified != attempt:
                raise ValueError(f"unresolved acquisition metadata mismatch: {attempt_path}")
            if attempt_path in evidence:
                raise ValueError(f"duplicate source path: {attempt_path}")
            evidence.add(attempt_path)
            verified_attempts.append((verified, signature))
        canonical, ordered = reconcile_attempts(verified_attempts)
        if canonical["source_path"] != relative or canonical["raw_source_sha256"] != item["raw_source_sha256"]:
            raise ValueError(f"noncanonical unresolved response: {item['name']}")
        if "acquisition_attempts" in item and ordered != attempts:
            raise ValueError(f"unresolved acquisition attempts must be ordered by path: {item['name']}")
    from app.importing.galaxy_harvester.acquisition import acquisition_metadata
    metadata, _ = acquisition_metadata(root, checksums)
    for item in unresolved:
        for attempt in item.get("acquisition_attempts", []):
            trusted_attempt = metadata.get(attempt["source_path"])
            if trusted_attempt is not None and trusted_attempt["name"] != item["name"]:
                raise ValueError(f"acquisition name disagrees with unresolved XML: {attempt['source_path']}")
        trusted = metadata.get(item["source_path"])
        if trusted is not None and trusted["name"] != item["name"]:
            raise ValueError(f"acquisition name disagrees with unresolved XML: {item['name']}")
        expected = trusted or {"http_status": None, "response_timestamp": None}
        for key in ("http_status", "response_timestamp"):
            if item.get(key) != expected[key]:
                raise ValueError(f"unverified acquisition {key}: {item['name']}")
    raw_records = set()
    for directory in ("raw/resources", "raw/errors", "raw/http/get-resource-by-name"):
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)
            if path.is_file():
                raw_records.add(relative)
    if raw_records != evidence:
        raise ValueError(f"unexpected or missing source records: {sorted(raw_records ^ evidence)[:10]}")
    discovery_files = frozen.get("discovery_source_paths", [])
    if not isinstance(discovery_files, list) or not discovery_files or len(set(discovery_files)) != len(discovery_files):
        raise ValueError("missing or duplicate discovery source paths")
    if discovery_files:
        from app.importing.galaxy_harvester.converter import discovery_names
        discovered = None
        for relative in discovery_files:
            if relative not in checksums:
                raise ValueError(f"missing discovery checksum: {relative}")
            current = discovery_names(load_json(safe_path(root, relative)))
            if discovered is not None and current != discovered:
                raise ValueError("discovery snapshots disagree")
            discovered = current
        if discovered != set(frozen["discovered_names"]):
            raise ValueError("discovery evidence disagrees with frozen manifest")
    expected_raw = evidence | set(discovery_files)
    for path in (root / "raw").rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe_path(root, relative)
        if path.is_file() and relative not in expected_raw:
            raise ValueError(f"unexpected source evidence: {relative}")
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


def _no_contributors(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.replace("_", "").casefold() in {"enteredby", "unavailableby"} and value is not None:
                raise ValueError("contributor identities are forbidden in normalized payloads")
            _no_contributors(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _no_contributors(value)


def validate_frozen_manifest(frozen: dict[str, Any], resources_index: dict[str, Any], names: dict[str, list[int]], unresolved=()) -> None:
    identities = frozen.get("identities")
    if type(frozen.get("galaxy_id")) is not int or frozen.get("galaxy_id") != SOURCE_GALAXY_ID:
        raise ValueError("frozen identity manifest is not for Galaxy Harvester galaxy 153")
    if frozen.get("galaxy_name", SOURCE_GALAXY_NAME) != SOURCE_GALAXY_NAME:
        raise ValueError("incorrect frozen source galaxy name")
    if not isinstance(identities, list) or not identities:
        raise ValueError("frozen identity manifest must contain identities")
    if any(not isinstance(item, dict) or item.get("galaxy_id") != SOURCE_GALAXY_ID for item in identities):
        raise ValueError("invalid identity or identity source galaxy")
    expected_count = len(identities)
    ids = [item.get("spawn_id") for item in identities]
    identity_names = [item.get("spawn_name") for item in identities]
    if any(type(sid) is not int or sid <= 0 for sid in ids) or len(set(ids)) != expected_count:
        raise ValueError("duplicate or invalid frozen spawn IDs")
    if any(not isinstance(name, str) or not name.strip() for name in identity_names) or len({normalize_name(n) for n in identity_names}) != expected_count:
        raise ValueError("duplicate or invalid frozen resource names")
    unresolved_names = [item.get("name") for item in unresolved]
    if any(not isinstance(n, str) or not n.strip() for n in unresolved_names) or len({normalize_name(n) for n in unresolved_names}) != len(unresolved_names):
        raise ValueError("duplicate or invalid unresolved names")
    discovered = frozen.get("discovered_names")
    if discovered is None:
        raise ValueError("missing discovered-name reconciliation")
    if not isinstance(discovered, list) or any(not isinstance(n, str) or not n.strip() for n in discovered) or len(discovered) != len({normalize_name(n) for n in discovered}) or set(identity_names) & set(unresolved_names) or set(discovered) != set(identity_names) | set(unresolved_names):
        raise ValueError("discovered names do not reconcile with resolved and unresolved names")
    if frozen.get("unresolved_names") != sorted(unresolved_names) or type(frozen.get("discovered_name_count")) is not int or frozen.get("discovered_name_count") != len(discovered):
        raise ValueError("unresolved or discovered name manifest mismatch")
    checks = {
        "unique_spawn_id_count": expected_count,
        "unique_name_count": expected_count,
        "duplicate_spawn_id_count": 0,
        "conflicting_spawn_id_count": 0,
        "duplicate_name_count": 0,
        "source_total_results_start": len(discovered),
        "source_total_results_end": len(discovered),
    }
    for key, expected in checks.items():
        if type(frozen.get(key)) is not int or frozen.get(key) != expected:
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
    if json.dumps(names, sort_keys=True) != json.dumps({item["spawn_name"]: [item["spawn_id"]] for item in identities}, sort_keys=True):
        raise ValueError("names index does not match frozen identities")
    for item in identities:
        exact = resources_index[str(item["spawn_id"])].get("exact", {})
        if exact.get("name") != item["spawn_name"] or exact.get("resource_type") != item.get("resource_type"):
            raise ValueError(f"resource {item['spawn_id']}: identity name/type mismatch")


def _resource_from_payload(spawn_id: str, payload: dict[str, Any], identities: dict[str, Any]) -> HistoricalResource:
    exact = payload.get("exact") or {}
    if not exact.get("found"):
        raise ValueError(f"resource {spawn_id} does not contain a found exact payload")
    if type(exact.get("spawn_id")) is not int or exact["spawn_id"] != int(spawn_id):
        raise ValueError(f"resource {spawn_id} exact payload spawn_id mismatch")
    identity = identities[spawn_id]
    stats = exact.get("stats") or {}
    if any(type(value) is not int or not -2147483648 <= value <= 2147483647 for value in stats.values()):
        raise ValueError(f"resource {spawn_id}: invalid statistic value")
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
        exact_source_path=payload["exact_source_path"],
        raw_source_sha256=identity.get("raw_source_sha256"),
        payload=payload,
    )


def normalize_name(value: str) -> str:
    return value.strip().casefold()
