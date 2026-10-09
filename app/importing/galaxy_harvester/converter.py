"""Offline Galaxy Harvester XML archive conversion. No database or network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from app.importing.galaxy_harvester.archive import (
    SOURCE_GALAXY_ID, SOURCE_GALAXY_NAME, file_sha256, load_json, safe_path, read_archive,
)
from app.importing.galaxy_harvester.xml_source import parse_xml, read_xml_bytes
from app.importing.galaxy_harvester.unresolved import unresolved_attempt, reconcile_attempts

AUDITED_UNRESOLVED = ("dweina", "eloate", "fopo", "golifo", "ileciium", "safe", "seekeheite")


def discovery_names(payload) -> set[str]:
    """Accept a frozen list, or names/resource_names/suggestions envelope.

    Each entry is a string or an object with name/spawnName. Ambiguous schemas
    and duplicate names fail rather than guessing which fields are identities.
    """
    suggestions = False
    if isinstance(payload, dict):
        if payload.get("galaxy_id", SOURCE_GALAXY_ID) != SOURCE_GALAXY_ID:
            raise ValueError("discovery snapshot has incorrect galaxy_id")
        keys = [key for key in ("names", "resource_names", "suggestions") if key in payload]
        if len(keys) != 1:
            raise ValueError("discovery snapshot requires exactly one names, resource_names, or suggestions list")
        suggestions = keys[0] == "suggestions"
        payload = payload[keys[0]]
    if not isinstance(payload, list) or not payload:
        raise ValueError("discovery snapshot requires a nonempty name list")
    names = []
    for item in payload:
        if suggestions and isinstance(item, str) and not item.strip():
            continue
        if isinstance(item, dict):
            keys = [k for k in ("name", "spawnName") if k in item]
            if len(keys) != 1:
                raise ValueError("ambiguous discovery name entry")
            item = item[keys[0]]
        if not isinstance(item, str) or not item.strip():
            raise ValueError("invalid discovery name")
        names.append(item)
    if not names:
        raise ValueError("discovery snapshot requires a nonempty name list")
    if len(set(names)) != len(names) or len({n.strip().casefold() for n in names}) != len(names):
        raise ValueError("duplicate discovery names")
    return set(names)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def convert_archive(source: Path, output: Path, *, dry_run=False, verify_audited_counts=False) -> dict:
    source, output = source.resolve(), output.absolute()
    if not source.is_dir():
        raise ValueError(f"missing source archive: {source}")
    if output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
        raise ValueError("source and output must be separate, nonoverlapping directories")
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists; choose a new directory: {output}")
    paths = sorted(source.glob("raw/names/names-*.json"))
    if not paths:
        raise ValueError("missing frozen discovery snapshot: raw/names/names-*.json")
    discovered = None
    hashes = {}
    for path in paths:
        relative = path.relative_to(source).as_posix()
        safe_path(source, relative)
        current = discovery_names(load_json(path))
        if discovered is not None and discovered != current:
            raise ValueError("frozen discovery snapshots disagree; select the reconciled source archive")
        discovered = current
        hashes[relative] = file_sha256(path)
    for relative in ("manifests/attempts.jsonl", "reports/latest.json"):
        path = safe_path(source, relative)
        if path.is_file():
            hashes[relative] = file_sha256(path)
    resources, identities, names, planets, types, unresolved = {}, [], {}, {}, {}, []
    seen_names, seen_ids = set(), set()
    unresolved_groups = {}
    anomalies = []
    for directory, suffix in (("raw/resources", ".xml"), ("raw/errors", ".bin")):
        folder = source / directory
        if not folder.is_dir():
            raise ValueError(f"missing source directory: {directory}")
        for path in sorted(folder.iterdir()):
            relative = path.relative_to(source).as_posix()
            safe_path(source, relative)
            if not path.is_file() or path.suffix != suffix:
                raise ValueError(f"unexpected source record: {relative}")
            raw = read_xml_bytes(path)
            exact = parse_xml(raw, relative)
            digest = hashlib.sha256(raw).hexdigest()
            hashes[relative] = digest
            name = exact["name"]
            if name not in discovered:
                raise ValueError(f"unexpected XML name: {name}")
            if not exact["found"]:
                if directory != "raw/errors":
                    raise ValueError(f"unresolved response must be under raw/errors: {relative}")
                if name.strip().casefold() in seen_names:
                    raise ValueError(f"conflicting resolved and unresolved resource name: {name}")
                group = unresolved_groups.setdefault(name.strip().casefold(), {"name": name, "evidence": []})
                group["evidence"].append(unresolved_attempt(raw, relative))
                continue
            if name.strip().casefold() in seen_names:
                raise ValueError(f"duplicate resource name: {name}")
            seen_names.add(name.strip().casefold())
            if directory != "raw/resources":
                raise ValueError(f"found response must be under raw/resources: {relative}")
            sid = exact["spawn_id"]
            if sid in seen_ids:
                raise ValueError(f"duplicate spawn ID: {sid}")
            seen_ids.add(sid)
            resources[str(sid)] = {"exact": exact, "exact_source_path": relative, "spawn_name": name}
            identities.append({"galaxy_id": SOURCE_GALAXY_ID, "spawn_id": sid, "spawn_name": name,
                               "resource_type": exact["resource_type"], "resource_type_name": exact["resource_type_name"],
                               "entered": exact["entered"], "unavailable": exact["unavailable"],
                               "raw_source_file": relative, "raw_source_sha256": digest})
            names[name] = [sid]
            type_payload = {"resource_type": exact["resource_type"], "resource_type_name": exact["resource_type_name"]}
            old = types.setdefault(exact["resource_type"], type_payload)
            if old != type_payload:
                raise ValueError(f"conflicting resource type names: {exact['resource_type']}")
            for planet in exact["planets"]:
                planet_payload = {"planet_id": planet["id"], "planet_name": planet["name"]}
                if planets.setdefault(str(planet["id"]), planet_payload) != planet_payload:
                    raise ValueError(f"conflicting planet names: {planet['id']}")
            for code, value in exact["stats"].items():
                bounds = exact["stat_ranges"].get(code, {})
                low, high = bounds.get("min"), bounds.get("max")
                if low is not None and high is not None and not low <= value <= high:
                    anomalies.append({"spawn_id": sid, "name": name, "stat_code": code, "observed": value,
                                      "source_min": low, "source_max": high})
    for group in unresolved_groups.values():
        canonical, attempts = reconcile_attempts(group["evidence"])
        unresolved.append({"found": False, "name": group["name"], "result_text": "new",
                           "source_path": canonical["source_path"], "raw_source_sha256": canonical["raw_source_sha256"],
                           "acquisition_attempts": attempts, "response_timestamp": None, "http_status": None})
    recovered = set(names) | {item["name"] for item in unresolved}
    if recovered != discovered:
        raise ValueError(f"missing discovered-name evidence: {sorted(discovered - recovered)[:20]}")
    known_raw = {relative for relative in hashes if relative.startswith("raw/")}
    for path in (source / "raw").rglob("*"):
        relative = path.relative_to(source).as_posix()
        safe_path(source, relative)
        if path.is_file() and relative not in known_raw:
            raise ValueError(f"unexpected source evidence: {relative}")
    identities.sort(key=lambda item: item["spawn_id"])
    unresolved.sort(key=lambda item: item["name"])
    count = len(identities)
    if not count:
        raise ValueError("archive contains no verified identities")
    from app.importing.galaxy_harvester.acquisition import acquisition_metadata
    metadata, unrecognized = acquisition_metadata(source, hashes)
    for item in unresolved:
        for attempt in item["acquisition_attempts"]:
            trusted = metadata.get(attempt["source_path"])
            if trusted and trusted["name"] != item["name"]:
                raise ValueError(f"acquisition name disagrees with XML: {attempt['source_path']}")
        acquisition = metadata.get(item["source_path"])
        if acquisition:
            if acquisition["name"] != item["name"]:
                raise ValueError(f"acquisition name disagrees with XML: {item['source_path']}")
            item.update({key: acquisition[key] for key in ("http_status", "response_timestamp")})
    audited_matches = (len(discovered) == 18593 and count == 18586 and
                       tuple(item["name"] for item in unresolved) == AUDITED_UNRESOLVED and len(anomalies) == 3)
    if verify_audited_counts and not audited_matches:
        raise ValueError("archive does not match audited 18593 discovered / 18586 resolved / seven unresolved / three anomalies")
    frozen = {"galaxy_id": SOURCE_GALAXY_ID, "galaxy_name": SOURCE_GALAXY_NAME, "identities": identities,
              "discovered_names": sorted(discovered), "discovered_name_count": len(discovered),
              "unresolved_names": [item["name"] for item in unresolved],
              "discovery_source_paths": sorted(path.relative_to(source).as_posix() for path in paths),
              "unique_spawn_id_count": count, "unique_name_count": count,
              "duplicate_spawn_id_count": 0, "conflicting_spawn_id_count": 0, "duplicate_name_count": 0,
              "names_mapping_to_multiple_spawn_ids": {}, "no_unresolved_identity_conflicts": True,
              "source_count_reconciles": True, "source_total_results_start": len(discovered),
              "source_total_results_end": len(discovered)}
    report = {"format_version": 1, "galaxy_id": SOURCE_GALAXY_ID, "galaxy_name": SOURCE_GALAXY_NAME,
              "discovered_names": len(discovered), "resolved_resources": count, "unresolved_names": frozen["unresolved_names"],
              "unresolved_count": len(unresolved), "duplicate_spawn_ids": 0, "duplicate_names": 0,
              "unresolved_response_count": sum(len(item["acquisition_attempts"]) for item in unresolved),
              "unresolved_acquisitions": [{"name": item["name"], "canonical_source_path": item["source_path"],
                                           "attempts": item["acquisition_attempts"]} for item in unresolved],
              "unexpected_names": 0, "identity_conflicts": 0, "source_checksums_verified": len(hashes),
              "anomalies": sorted(anomalies, key=lambda a: (a["spawn_id"], a["stat_code"])),
              "audited_counts_match": audited_matches,
              "unrecognized_acquisition_records": unrecognized,
              "http_metadata_policy": "only explicit hash-bound acquisition records confer metadata; otherwise NULL"}
    if dry_run:
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent))
    # Raw evidence can contain contributor identities. Preserve it privately.
    stage.chmod(0o700)
    try:
        for relative, digest in sorted(hashes.items()):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(source, relative), destination)
            if file_sha256(destination) != digest:
                raise ValueError(f"source changed during conversion: {relative}")
        for filename, payload in (
            ("frozen-identities.json", frozen), ("resources-index.json", {"resources": resources}),
            ("names.json", {"names": names}), ("planets.json", {"planets": planets}),
            ("source-types.json", {"source_types": types}), ("unresolved.json", {"unresolved": unresolved})):
            path = stage / "normalized" / filename
            write_json(path, payload)
            hashes[path.relative_to(stage).as_posix()] = file_sha256(path)
        write_json(stage / "validation-report.json", report)
        hashes["validation-report.json"] = file_sha256(stage / "validation-report.json")
        (stage / "checksums").mkdir()
        (stage / "checksums" / "sha256sums.txt").write_text(
            "".join(f"{digest}  {relative}\n" for relative, digest in sorted(hashes.items())), encoding="utf-8", newline="\n")
        read_archive(stage)
        if output.exists():
            raise ValueError("output appeared during conversion; refusing overwrite")
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--convert", action="store_true")
    parser.add_argument("--verify-audited-counts", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = convert_archive(args.source, args.output, dry_run=args.dry_run, verify_audited_counts=args.verify_audited_counts)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Archive validation failed: {exc}\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
