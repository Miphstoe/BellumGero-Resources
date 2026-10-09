"""Deterministic reconciliation of private unresolved acquisition evidence."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from app.importing.galaxy_harvester.archive import normalize_name, parse_source_datetime
from app.importing.galaxy_harvester.xml_source import parse_xml


def unresolved_attempt(content: bytes, path: str) -> tuple[dict, dict]:
    parsed = parse_xml(content, path)
    if parsed.get("found") is not False:
        raise ValueError(f"conflicting unresolved acquisition identity: {path}")
    # parse_xml has already applied the bounded, DTD-free schema validation.
    root = ET.fromstring(content)
    fields = {child.tag: child.text or "" for child in root}
    signature = {key: value for key, value in fields.items()
                 if key not in {"serverTime", "enteredBy", "unavailableBy", "verifiedBy"}
                 and value not in ("", "None")}
    signature["spawnName"] = normalize_name(parsed["name"])
    attempt = {"source_path": path, "raw_source_sha256": hashlib.sha256(content).hexdigest(),
               "server_time": fields.get("serverTime")}
    return attempt, signature


def reconcile_attempts(evidence: list[tuple[dict, dict]]) -> tuple[dict, list[dict]]:
    if not evidence:
        raise ValueError("unresolved observation has no acquisition evidence")
    if any(signature != evidence[0][1] for _, signature in evidence):
        raise ValueError("conflicting unresolved acquisition contents")
    attempts = sorted((attempt for attempt, _ in evidence), key=lambda a: a["source_path"])
    if len({a["source_path"] for a in attempts}) != len(attempts):
        raise ValueError("duplicate unresolved acquisition source path")
    times = [parse_source_datetime(a["server_time"]) for a in attempts]
    awareness = {time.tzinfo is not None for time in times if time is not None}
    if len(awareness) > 1:
        raise ValueError("unresolved serverTime mixes naive and timezone-aware timestamps")
    def key(attempt):
        time = parse_source_datetime(attempt["server_time"])
        if time is None:
            return (False, datetime.min, attempt["source_path"])
        if time.tzinfo is not None:
            time = time.astimezone(timezone.utc).replace(tzinfo=None)
        return (True, time, attempt["source_path"])
    return max(attempts, key=key), attempts
