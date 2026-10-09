"""Conservative acquisition-log metadata policy, shared by conversion and validation.

Only hash-bound records with explicit source_path, name, sha256 are recognized.
Other exporter log schemas remain private evidence and confer no HTTP metadata.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.importing.galaxy_harvester.archive import parse_source_datetime, safe_path


def acquisition_metadata(root: Path, checksums: dict[str, str]) -> tuple[dict, int]:
    relative = "manifests/attempts.jsonl"
    path = safe_path(root, relative)
    if not path.is_file():
        return {}, 0
    if relative not in checksums:
        raise ValueError("missing checksum for acquisition manifest")
    result, unrecognized = {}, 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            def unique(pairs):
                record = {}
                for key, value in pairs:
                    if key in record:
                        raise ValueError(f"duplicate acquisition key at line {line_number}: {key}")
                    record[key] = value
                return record
            try:
                row = json.loads(line, object_pairs_hook=unique)
            except ValueError as exc:
                raise ValueError(f"invalid acquisition manifest line {line_number}: {exc}") from exc
            if not isinstance(row, dict) or not {"source_path", "name", "sha256"}.issubset(row):
                unrecognized += 1
                continue
            relative = row["source_path"]
            safe_path(root, relative)
            if checksums.get(relative) != row["sha256"]:
                # Earlier failed attempts may refer to superseded responses.
                unrecognized += 1
                continue
            if not isinstance(row["name"], str) or not row["name"].strip():
                raise ValueError(f"invalid acquisition name at line {line_number}")
            status, timestamp = row.get("http_status"), row.get("response_timestamp")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError(f"invalid acquisition HTTP status at line {line_number}")
            if timestamp is not None:
                if not isinstance(timestamp, str) or parse_source_datetime(timestamp) is None:
                    raise ValueError(f"invalid acquisition timestamp at line {line_number}")
            metadata = {"name": row["name"], "http_status": status, "response_timestamp": timestamp}
            if relative in result and result[relative] != metadata:
                raise ValueError(f"conflicting hash-bound acquisition metadata: {relative}")
            result[relative] = metadata
    return result, unrecognized
