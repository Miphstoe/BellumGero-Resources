"""Offline, bounded parsing of private Galaxy Harvester evidence."""
from __future__ import annotations

import re
from pathlib import Path
from xml.parsers import expat
from xml.etree import ElementTree as ET

from app.importing.galaxy_harvester.archive import STAT_CODES, parse_source_datetime

MAX_XML_BYTES = 1024 * 1024


def read_xml_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_XML_BYTES + 1)
    if len(content) > MAX_XML_BYTES:
        raise ValueError(f"{path}: XML exceeds size limit")
    return content


def integer(value: str | None, label: str, *, optional=False, signed=False) -> int | None:
    if optional and value in (None, "", "None"):
        return None
    if value is None or not re.fullmatch(r"-?[0-9]+" if signed else r"[0-9]+", value):
        raise ValueError(f"{label}: expected {'signed' if signed else 'nonnegative'} integer")
    result = int(value)
    if (signed and not -2147483648 <= result <= 2147483647) or result > 9223372036854775807:
        raise ValueError(f"{label}: integer exceeds database range")
    return result


def validate_planet_summary(summary: str, planets: list[dict], path: str) -> None:
    """Check plain names and comma/semicolon/pipe/newline-separated summaries.

    Case and whitespace are insignificant. Undelimited multi-name prose,
    annotations, brackets, and other separators are ambiguous and remain private
    raw metadata; they never establish associations or override structured XML.
    """
    normalize = lambda name: " ".join(name.split()).casefold()
    text = summary.strip()
    if not text or text == "None":
        return
    structured = {normalize(planet["name"]) for planet in planets}
    delimited = bool(re.search(r"[,;|\r\n]", text))
    parts = [normalize(part) for part in re.split(r"[,;|\r\n]+", text) if part.strip()]
    # A name consists of words and optional numeric components (e.g. Yavin 4).
    if not parts or any(not re.fullmatch(r"[^\W\d_]+(?: [^\W\d_]+| [0-9]+)*", part) for part in parts):
        return
    if not delimited and len(planets) > 1 and parts[0] not in structured:
        return
    if set(parts) != structured:
        raise ValueError(f"{path}: Planets summary disagrees with structured planet records")


def parse_xml(content: bytes, path: str) -> dict:
    if len(content) > MAX_XML_BYTES:
        raise ValueError(f"{path}: XML exceeds size limit")
    parser = expat.ParserCreate()

    def forbidden(*args):
        raise ValueError(f"{path}: DTD and entities are forbidden")

    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    depth = 0
    def start(*args):
        nonlocal depth
        depth += 1
        if depth > 2:
            raise ValueError(f"{path}: nested XML is forbidden")
    def end(*args):
        nonlocal depth
        depth -= 1
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(content, True)
        root = ET.fromstring(content)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError(f"{path}: invalid XML: {exc}") from exc
    if root.tag != "result" or root.attrib:
        raise ValueError(f"{path}: expected result root")
    if (root.text or "").strip():
        raise ValueError(f"{path}: unexpected text in result root")
    fields = {}
    planets = []
    allowed = {"spawnName", "spawnID", "resourceType", "resourceTypeName", "containerType",
               "entered", "unavailable", "enteredBy", "unavailableBy", "resultText", "Planets", "serverTime",
               "maxWaypointConc", "verified", "verifiedBy"}
    allowed |= {code + suffix for code in STAT_CODES for suffix in ("", "min", "max")}
    for child in root:
        if (child.tail or "").strip():
            raise ValueError(f"{path}: unexpected mixed XML content")
        if len(child):
            raise ValueError(f"{path}: nested XML field {child.tag}")
        if child.tag == "planet":
            if set(child.attrib) - {"id", "entered", "unavailable", "enteredBy", "unavailableBy"}:
                raise ValueError(f"{path}: unknown planet attributes")
            pid = integer(child.get("id"), f"{path}: planet id")
            name = child.text or ""
            if not name.strip() or pid == 0:
                raise ValueError(f"{path}: invalid planet")
            planet = {"id": pid, "name": name}
            for key in ("entered", "unavailable"):
                value = child.get(key)
                parse_source_datetime(value)
                planet[key] = value if value not in (None, "", "None") else None
            planets.append(planet)
        else:
            if child.attrib:
                raise ValueError(f"{path}: unexpected attributes on {child.tag}")
            if child.tag not in allowed or child.tag in fields:
                raise ValueError(f"{path}: unknown or duplicate XML field {child.tag}")
            fields[child.tag] = child.text or ""
    if "serverTime" in fields:
        try:
            timestamp = parse_source_datetime(fields["serverTime"])
        except ValueError as exc:
            raise ValueError(f"{path}: invalid serverTime timestamp: {fields['serverTime']!r}") from exc
        if timestamp is None:
            raise ValueError(f"{path}: invalid serverTime timestamp")
    if "Planets" in fields:
        validate_planet_summary(fields["Planets"], planets, path)
    supplemental = {}
    if "maxWaypointConc" in fields:
        concentration = integer(fields["maxWaypointConc"], f"{path}: maxWaypointConc")
        if concentration > 2147483647:
            raise ValueError(f"{path}: maxWaypointConc exceeds database integer range")
        supplemental["max_waypoint_conc"] = concentration
    if "verified" in fields:
        try:
            verified = parse_source_datetime(fields["verified"])
        except ValueError as exc:
            raise ValueError(f"{path}: invalid verified timestamp") from exc
        if verified is None:
            raise ValueError(f"{path}: invalid verified timestamp")
        supplemental["verified_at"] = fields["verified"]
    # verifiedBy is recognized for structural validation only. Its value remains
    # exclusively in private source XML, like enteredBy and unavailableBy.
    name = fields.get("spawnName", "")
    if not name.strip():
        raise ValueError(f"{path}: missing spawnName")
    status = fields.get("resultText")
    if status == "new":
        if fields.get("spawnID") not in (None, "", "None") or fields.get("resourceType") not in (None, "", "None") or planets or any(
            fields.get(code) not in (None, "", "None") for code in STAT_CODES):
            raise ValueError(f"{path}: unresolved response contains resource identity or stats")
        return {"found": False, "name": name, "result_text": "new"}
    if status != "found":
        raise ValueError(f"{path}: unsupported resultText {status!r}")
    sid = integer(fields.get("spawnID"), f"{path}: spawnID")
    if not sid:
        raise ValueError(f"{path}: spawnID must be positive")
    if not fields.get("resourceType", "").strip():
        raise ValueError(f"{path}: missing resourceType")
    if len({p["id"] for p in planets}) != len(planets):
        raise ValueError(f"{path}: duplicate planet id")
    stats, ranges = {}, {}
    for code in STAT_CODES:
        value = integer(fields.get(code), f"{path}: {code}", optional=True, signed=True)
        if value is not None:
            stats[code] = value
        low = integer(fields.get(code + "min"), f"{path}: {code}min", optional=True, signed=True)
        high = integer(fields.get(code + "max"), f"{path}: {code}max", optional=True, signed=True)
        if low is not None or high is not None:
            if low is not None and high is not None and low > high:
                raise ValueError(f"{path}: inverted {code} range")
            ranges[code] = {"min": low, "max": high}
    result = {"found": True, "spawn_id": sid, "name": name,
              "resource_type": fields["resourceType"], "resource_type_name": fields.get("resourceTypeName") or None,
              "container_type": fields.get("containerType") or None, "stats": stats,
              "stat_ranges": ranges, "planets": sorted(planets, key=lambda p: p["id"])}
    if supplemental:
        result["supplemental_metadata"] = supplemental
    for key in ("entered", "unavailable"):
        value = fields.get(key)
        parse_source_datetime(value)
        result[key] = value if value not in (None, "", "None") else None
    return result
