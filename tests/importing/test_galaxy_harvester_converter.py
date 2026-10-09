from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.importing.galaxy_harvester.archive import read_archive, safe_path
from app.importing.galaxy_harvester.converter import AUDITED_UNRESOLVED, convert_archive, discovery_names, main
from app.importing.galaxy_harvester.importer import import_archive, find_anomalies
from app.importing.galaxy_harvester.xml_source import parse_xml
from tests.importing.test_galaxy_harvester_importer import requires_test_db, phase3d_counts


XML = b'''<result><spawnName>archta</spawnName><spawnID>2606250</spawnID>
<resourceType>bone_horn_lok</resourceType><resourceTypeName>Lokian Horn</resourceTypeName>
<DR>922</DR><DRmin>200</DRmin><DRmax>1000</DRmax><OQ>0</OQ><MA>None</MA>
<entered>2026-06-03 23:57:03</entered><unavailable>2026-06-24 03:00:56</unavailable>
<enteredBy>private-person</enteredBy><unavailableBy>private-person</unavailableBy>
<planet id="5" entered="2026-06-03 23:57:03" enteredBy="private-person">Lok</planet>
<planet id="1" entered="2026-06-04 00:00:00" unavailable="2026-06-20 00:00:00">Corellia</planet>
<resultText>found</resultText></result>'''


def test_original_suggestions_snapshot_ignores_empty_placeholders():
    assert discovery_names({"query": "", "suggestions": ["", "aba", "abafo", "  ", "\t\n"]}) == {"aba", "abafo"}


@pytest.mark.parametrize("concentration", [0, 64, 69, 79, 85, 92, 2147483647])
def test_audited_optional_metadata_preserves_values_without_stats_or_contributors(concentration):
    content = XML.replace(b"</result>", (
        f"<maxWaypointConc>{concentration}</maxWaypointConc><verified>2026-06-25 01:02:03</verified>"
        "<verifiedBy>private-verifier-identity</verifiedBy></result>").encode())
    parsed = parse_xml(content, "audited.xml")
    assert parsed.pop("supplemental_metadata") == {"max_waypoint_conc": concentration, "verified_at": "2026-06-25 01:02:03"}
    assert parsed == parse_xml(XML, "original.xml")
    assert "private-verifier-identity" not in json.dumps(parsed)


@pytest.mark.parametrize("value", ["-1", "1.5", "NaN", "None", "", " 64 ", "2147483648", "9223372036854775808"])
def test_invalid_max_waypoint_concentration_rejected(value):
    with pytest.raises(ValueError, match="maxWaypointConc"):
        parse_xml(XML.replace(b"</result>", f"<maxWaypointConc>{value}</maxWaypointConc></result>".encode()), "invalid.xml")


@pytest.mark.parametrize("value", ["bad-time", "2026-02-30 00:00:00", "2026-06-25", "", "None"])
def test_invalid_verification_timestamp_rejected(value):
    with pytest.raises(ValueError, match="invalid verified timestamp"):
        parse_xml(XML.replace(b"</result>", f"<verified>{value}</verified></result>".encode()), "invalid.xml")


@pytest.mark.parametrize("field,value", [("maxWaypointConc", "64"), ("verified", "2026-06-25 01:02:03"), ("verifiedBy", "private-verifier-identity")])
def test_audited_optional_fields_reject_duplicates_and_attributes_without_identity_leak(field, value):
    element = f"<{field}>{value}</{field}>".encode()
    for extra in (element + element, f'<{field} unexpected="true">{value}</{field}>'.encode()):
        with pytest.raises(ValueError, match="duplicate XML field|unexpected attributes") as error:
            parse_xml(XML.replace(b"</result>", extra + b"</result>"), "strict.xml")
        assert "private-verifier-identity" not in str(error.value)


def test_audited_optional_metadata_conversion_privacy_and_revalidation(tmp_path, capsys):
    source = source_archive(tmp_path / "source")
    original = XML.replace(b"</result>", b"<maxWaypointConc>79</maxWaypointConc><verified>2026-06-25 01:02:03</verified><verifiedBy>private-verifier-identity</verifiedBy></result>")
    (source / "raw/resources/archta.xml").write_bytes(original)
    output = tmp_path / "output"
    assert main(["--source", str(source), "--output", str(output), "--dry-run"]) == 0
    report = convert_archive(source, output)
    archive = read_archive(output)
    exact = archive.resources[0].payload["exact"]
    assert exact["supplemental_metadata"] == {"max_waypoint_conc": 79, "verified_at": "2026-06-25 01:02:03"}
    assert exact["stats"] == {"DR": 922, "OQ": 0}
    assert (output / "raw/resources/archta.xml").read_bytes() == original
    for path in (output / "normalized").glob("*.json"):
        assert "private-verifier-identity" not in path.read_text()
        assert "verifiedBy" not in path.read_text()
    assert "private-verifier-identity" not in json.dumps(report)
    assert "private-verifier-identity" not in (output / "validation-report.json").read_text()
    captured = capsys.readouterr()
    assert "private-verifier-identity" not in captured.out + captured.err
    alter_json(output, "normalized/resources-index.json", lambda p: p["resources"]["2606250"]["exact"]["supplemental_metadata"].update(max_waypoint_conc=85))
    with pytest.raises(ValueError, match="supplemental_metadata differs from source XML"):
        read_archive(output)


def test_unresolved_optional_fields_preserve_existing_payload_contract():
    content = b"<result><spawnName>dweina</spawnName><resultText>new</resultText><serverTime>2026-06-25 01:02:03</serverTime><maxWaypointConc>0</maxWaypointConc><verified>2026-06-25 01:02:03</verified><verifiedBy>private-verifier-identity</verifiedBy></result>"
    assert parse_xml(content, "unresolved.bin") == {"found": False, "name": "dweina", "result_text": "new"}


@pytest.mark.parametrize("summary", ["Lok, Corellia", " corellia ; LOK ", "Lok|Corellia", "Lok\nCorellia",
    "Lok, Corellia, Lok", "Lok Corellia", "[Lok, Corellia]", "Lok / Corellia", "Lok (5), Corellia (1)"])
def test_historical_supplemental_fields_preserve_structured_planets(summary):
    historical = XML.replace(b"</result>", (
        f"<Planets>{summary}</Planets><serverTime>2026-06-25 01:02:03</serverTime></result>").encode())
    parsed = parse_xml(historical, "historical.xml")
    assert parsed == parse_xml(XML, "original.xml")
    assert [planet["id"] for planet in parsed["planets"]] == [1, 5]
    assert parsed["planets"][0]["unavailable"] == "2026-06-20 00:00:00"


@pytest.mark.parametrize("summary", ["Lok, Naboo", "Lok", "Lok;Corellia;Naboo"])
def test_unambiguous_planet_summary_mismatch_rejected(summary):
    content = XML.replace(b"</result>", f"<Planets>{summary}</Planets></result>".encode())
    with pytest.raises(ValueError, match="Planets summary disagrees"):
        parse_xml(content, "historical.xml")


def test_single_planet_summary_checks_names_without_inventing_ids():
    content = b'<result><spawnName>x</spawnName><spawnID>1</spawnID><resourceType>x</resourceType><planet id="10">Yavin 4</planet><Planets> yAvIn   4 </Planets><resultText>found</resultText></result>'
    parsed = parse_xml(content, "single.xml")
    assert parsed["planets"] == [{"id": 10, "name": "Yavin 4", "entered": None, "unavailable": None}]
    with pytest.raises(ValueError, match="Planets summary disagrees"):
        parse_xml(content.replace(b" yAvIn   4 ", b"Naboo"), "single.xml")


@pytest.mark.parametrize("status", ["found", "new"])
@pytest.mark.parametrize("timestamp", ["bad-time", "2026-02-30 00:00:00", "2026-06-25", "", "None"])
def test_invalid_server_time_rejected_for_resolved_and_unresolved(status, timestamp):
    content = XML if status == "found" else b"<result><spawnName>dweina</spawnName><resultText>new</resultText></result>"
    content = content.replace(b"</result>", f"<serverTime>{timestamp}</serverTime></result>".encode())
    with pytest.raises(ValueError, match="timestamp|day is out of range"):
        parse_xml(content, "invalid-time.xml")


def test_unresolved_server_time_is_supplemental_metadata():
    content = b"<result><spawnName>dweina</spawnName><resultText>new</resultText><serverTime>2026-06-25T01:02:03+00:00</serverTime></result>"
    assert parse_xml(content, "unresolved.bin") == {"found": False, "name": "dweina", "result_text": "new"}


@pytest.mark.parametrize("extra", [b"<Planets>Lok, Corellia</Planets><Planets>Lok, Corellia</Planets>",
    b"<serverTime>2026-06-25 01:02:03</serverTime><serverTime>2026-06-25 01:02:03</serverTime>",
    b"<unknown>value</unknown>"])
def test_supplemental_support_keeps_duplicate_and_unknown_fields_strict(extra):
    with pytest.raises(ValueError, match="unknown or duplicate XML field"):
        parse_xml(XML.replace(b"</result>", extra + b"</result>"), "strict.xml")


def test_supplemental_fields_convert_and_revalidate_without_duplicate_planets(tmp_path):
    source = source_archive(tmp_path / "source")
    original = XML.replace(b"</result>", b"<Planets>Lok, Corellia</Planets><serverTime>2026-06-25 01:02:03</serverTime></result>")
    (source / "raw/resources/archta.xml").write_bytes(original)
    unresolved = source / "raw/errors/dweina.bin"
    unresolved.write_bytes(unresolved.read_bytes().replace(b"</result>", b"<serverTime>2026-06-25 01:02:03</serverTime></result>"))
    output = tmp_path / "output"
    convert_archive(source, output)
    archive = read_archive(output)
    assert len(archive.resources[0].planets) == 2
    assert (output / "raw/resources/archta.xml").read_bytes() == original
    assert archive.unresolved[0]["name"] == "dweina"


@pytest.mark.parametrize("field", ["names", "resource_names", "suggestions"])
@pytest.mark.parametrize("entries", [["aba", "aba"], ["aba", "ABA"], ["aba", " aba "]])
def test_discovery_formats_reject_duplicate_names(field, entries):
    with pytest.raises(ValueError, match="duplicate discovery names"):
        discovery_names({field: entries})


@pytest.mark.parametrize("fields", [("names", "resource_names"), ("names", "suggestions"),
    ("resource_names", "suggestions"), ("names", "resource_names", "suggestions")])
def test_discovery_rejects_ambiguous_identity_fields(fields):
    with pytest.raises(ValueError, match="exactly one"):
        discovery_names({field: ["aba"] for field in fields})


@pytest.mark.parametrize("field", ["names", "resource_names"])
@pytest.mark.parametrize("empty", ["", "  ", "\t\n"])
def test_existing_discovery_formats_keep_strict_empty_validation(field, empty):
    with pytest.raises(ValueError, match="invalid discovery name"):
        discovery_names({field: [empty, "aba"]})


@pytest.mark.parametrize("payload", [["aba", "abafo"], {"names": ["aba", "abafo"]},
    {"resource_names": [{"name": "aba"}, {"spawnName": "abafo"}]},
    {"galaxy_id": 153, "suggestions": ["", "aba", "abafo"]}])
def test_existing_discovery_formats_and_galaxy_identity(payload):
    assert discovery_names(payload) == {"aba", "abafo"}


@pytest.mark.parametrize("field", ["names", "resource_names", "suggestions"])
def test_discovery_formats_reject_wrong_galaxy(field):
    with pytest.raises(ValueError, match="incorrect galaxy_id"):
        discovery_names({"galaxy_id": 154, field: ["aba"]})


@pytest.mark.parametrize("entries", [[], ["", "  ", "\t"], [None, "aba"], [0, "aba"], [{"name": ""}, "aba"]])
def test_suggestions_require_nonempty_names_and_reject_invalid_entries(entries):
    with pytest.raises(ValueError):
        discovery_names({"suggestions": entries})


def test_suggestions_recover_18593_nonempty_unique_names():
    expected = {f"resource-{index:05d}" for index in range(18593)}
    snapshot = {"query": "", "suggestions": ["", *sorted(expected), " ", "\t"]}
    recovered = discovery_names(snapshot)
    assert len(recovered) == 18593 and recovered == expected


def test_suggestions_conversion_reconciles_and_reader_verifies_discovery(tmp_path):
    source = source_archive(tmp_path / "source")
    (source / "raw/names/names-frozen.json").write_text(json.dumps(
        {"query": "", "suggestions": ["", "archta", *AUDITED_UNRESOLVED, "  "]}))
    output = tmp_path / "output"
    convert_archive(source, output)
    archive = read_archive(output)
    assert archive.frozen_manifest["discovered_name_count"] == 8
    assert archive.frozen_manifest["discovered_names"] == sorted(["archta", *AUDITED_UNRESOLVED])


def source_archive(root: Path, *, unresolved=AUDITED_UNRESOLVED):
    (root / "raw/resources").mkdir(parents=True)
    (root / "raw/errors").mkdir()
    (root / "raw/names").mkdir()
    (root / "raw/resources/archta.xml").write_bytes(XML)
    for name in unresolved:
        (root / f"raw/errors/{name}.bin").write_text(f"<result><spawnName>{name}</spawnName><resultText>new</resultText></result>")
    (root / "raw/names/names-frozen.json").write_text(json.dumps({"galaxy_id": 153, "names": ["archta", *unresolved]}))
    return root


def repeated_unresolved_source(root):
    source = source_archive(root)
    for name in AUDITED_UNRESOLVED:
        (source / f"raw/errors/{name}.bin").unlink()
        # The older response has the lexically later path: chronology must win.
        for prefix, time in (("z-old", "2026-06-25 01:02:03"), ("a-new", "2026-06-26 01:02:03")):
            (source / f"raw/errors/{prefix}-{name}.bin").write_text(
                f"<result><spawnName>{name}</spawnName><resultText>new</resultText><serverTime>{time}</serverTime></result>")
    return source


def test_fourteen_unresolved_responses_become_seven_observations_with_all_evidence(tmp_path):
    source = repeated_unresolved_source(tmp_path / "source")
    output = tmp_path / "output"
    report = convert_archive(source, output)
    archive = read_archive(output)
    assert len(archive.resources) == 1 and len(archive.unresolved) == 7
    assert report["discovered_names"] == 8 and report["unresolved_count"] == 7
    assert report["unresolved_response_count"] == 14
    assert tuple(item["name"] for item in archive.unresolved) == AUDITED_UNRESOLVED
    assert len(report["unresolved_acquisitions"]) == 7
    for item in archive.unresolved:
        assert item["source_path"] == f"raw/errors/a-new-{item['name']}.bin"
        assert len(item["acquisition_attempts"]) == 2
        for attempt in item["acquisition_attempts"]:
            relative = attempt["source_path"]
            assert (output / relative).read_bytes() == (source / relative).read_bytes()
            assert attempt["raw_source_sha256"] == archive.checksums[relative] == hashlib.sha256((source / relative).read_bytes()).hexdigest()


def test_unresolved_canonical_tie_breaker_and_determinism(tmp_path):
    source = repeated_unresolved_source(tmp_path / "source")
    path = source / "raw/errors/z-old-dweina.bin"
    path.write_bytes(path.read_bytes().replace(b"2026-06-25", b"2026-06-26"))
    for folder in ("first", "second"):
        convert_archive(source, tmp_path / folder)
    first, second = tmp_path / "first", tmp_path / "second"
    assert read_archive(first).unresolved[0]["source_path"] == "raw/errors/z-old-dweina.bin"
    assert {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()} == {p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation,match", [("invalid_xml", "invalid XML"), ("unexpected_name", "unexpected XML name"),
    ("spawn_id", "unresolved response contains"), ("found", "found response must"),
    ("conflicting_contents", "conflicting unresolved acquisition contents"), ("mixed_timezone", "mixes naive")])
def test_conflicting_unresolved_acquisition_evidence_rejected(tmp_path, mutation, match):
    source = repeated_unresolved_source(tmp_path / "source")
    path = source / "raw/errors/z-old-dweina.bin"
    content = path.read_bytes()
    if mutation == "invalid_xml":
        content = b"<result>"
    elif mutation == "unexpected_name":
        content = content.replace(b"dweina", b"not-discovered")
    elif mutation == "spawn_id":
        content = content.replace(b"</result>", b"<spawnID>123</spawnID></result>")
    elif mutation == "found":
        content = content.replace(b"new", b"found").replace(b"</result>", b"<spawnID>123</spawnID><resourceType>x</resourceType></result>")
    elif mutation == "conflicting_contents":
        content = content.replace(b"</result>", b"<entered>2026-06-25 00:00:00</entered></result>")
    else:
        content = content.replace(b"2026-06-25 01:02:03", b"2026-06-25T01:02:03+00:00")
    path.write_bytes(content)
    with pytest.raises(ValueError, match=match):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mutation,match", [("tamper", "SHA-256 mismatch"), ("missing", "missing source evidence"),
    ("hash", "attempt checksum"), ("canonical", "noncanonical"), ("time", "acquisition metadata mismatch"),
    ("omit", "unexpected or missing source records"), ("name", "acquisition name mismatch")])
def test_reader_verifies_every_unresolved_attempt_and_canonical_choice(tmp_path, mutation, match):
    source = repeated_unresolved_source(tmp_path / "source")
    output = tmp_path / "output"
    convert_archive(source, output)
    relative = "raw/errors/z-old-dweina.bin"
    if mutation == "tamper":
        (output / relative).write_bytes((output / relative).read_bytes() + b" ")
    elif mutation == "missing":
        (output / relative).unlink()
    elif mutation == "name":
        (output / relative).write_bytes((output / relative).read_bytes().replace(b"dweina", b"safe"))
        rehash(output, relative)
        def wrong_name(p):
            p["unresolved"][0]["acquisition_attempts"][1]["raw_source_sha256"] = hashlib.sha256((output / relative).read_bytes()).hexdigest()
        alter_json(output, "normalized/unresolved.json", wrong_name)
    else:
        def change(p):
            item = p["unresolved"][0]
            if mutation == "hash":
                item["acquisition_attempts"][1]["raw_source_sha256"] = "a" * 64
            elif mutation == "canonical":
                item["source_path"] = relative
                item["raw_source_sha256"] = item["acquisition_attempts"][1]["raw_source_sha256"]
            elif mutation == "time":
                item["acquisition_attempts"][1]["server_time"] = "2030-01-01 00:00:00"
            else:
                item["acquisition_attempts"].pop()
        alter_json(output, "normalized/unresolved.json", change)
    with pytest.raises(ValueError, match=match):
        read_archive(output)


@requires_test_db
def test_repeated_unresolved_import_retains_all_paths_and_is_idempotent(tmp_path, db):
    source = repeated_unresolved_source(tmp_path / "source")
    output = tmp_path / "output"
    convert_archive(source, output)
    import_archive(db, output)
    import_archive(db, output)
    rows = db.execute(text("SELECT source_name, details FROM unresolved_source_resources ORDER BY source_name")).all()
    assert len(rows) == 7
    for name, details in rows:
        assert details["source_path"] == f"raw/errors/a-new-{name}.bin"
        assert len(details["acquisition_attempts"]) == 2
    assert db.execute(text("SELECT count(*) FROM source_resources WHERE source_resource_name IN ('dweina', 'eloate', 'fopo', 'golifo', 'ileciium', 'safe', 'seekeheite')")).scalar_one() == 0
    refs = db.execute(text("SELECT payload_ref, normalized_payload FROM source_records WHERE record_type = 'gh_unresolved_exact'")).all()
    assert len(refs) == 7 and all(ref.startswith("raw/errors/a-new-") and len(payload["acquisition_attempts"]) == 2 for ref, payload in refs)


def converted(tmp_path):
    source = source_archive(tmp_path / "source")
    output = tmp_path / "output"
    report = convert_archive(source, output)
    return source, output, report


def rehash(output, relative):
    path = output / "checksums/sha256sums.txt"
    lines = path.read_text().splitlines()
    digest = hashlib.sha256((output / relative).read_bytes()).hexdigest()
    path.write_text("\n".join(f"{digest}  {relative}" if line.split("  ", 1)[1] == relative else line for line in lines) + "\n")


def alter_json(output, relative, mutate):
    path = output / relative
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload))
    rehash(output, relative)


def test_conversion_fidelity_seven_unresolved_provenance_and_privacy(tmp_path):
    source, output, report = converted(tmp_path)
    archive = read_archive(output)
    resource = archive.resources[0]
    assert resource.spawn_id == 2606250 and resource.stats == {"DR": 922, "OQ": 0}
    assert resource.entered.isoformat() == "2026-06-03T23:57:03"
    assert resource.unavailable.isoformat() == "2026-06-24T03:00:56"
    assert len(resource.planets) == 2 and resource.planets[0]["unavailable"] == "2026-06-20 00:00:00"
    assert resource.exact_source_path == "raw/resources/archta.xml"
    assert resource.raw_source_sha256 == hashlib.sha256(XML).hexdigest()
    assert tuple(item["name"] for item in archive.unresolved) == AUDITED_UNRESOLVED
    assert all(item["http_status"] is None for item in archive.unresolved)
    assert report["discovered_names"] == 8 and report["resolved_resources"] == 1 and report["unresolved_count"] == 7
    assert not report["audited_counts_match"]
    assert "private-person" not in (output / "normalized/resources-index.json").read_text()
    assert (output / "raw/resources/archta.xml").read_bytes() == (source / "raw/resources/archta.xml").read_bytes() == XML


def test_all_stats_none_zero_ranges_and_anomaly_preserved():
    from app.importing.galaxy_harvester.archive import STAT_CODES
    xml = XML.replace(b"<DR>922</DR>", b"<DR>154</DR>")
    parsed = parse_xml(xml, "fixture")
    assert parsed["stats"]["DR"] == 154 and parsed["stat_ranges"]["DR"]["min"] == 200
    for code in STAT_CODES:
        sample = b"<result><spawnName>x</spawnName><spawnID>1</spawnID><resourceType>x</resourceType><" + code.encode() + b">0</" + code.encode() + b"><resultText>found</resultText></result>"
        assert parse_xml(sample, "fixture")["stats"] == {code: 0}
        assert parse_xml(sample.replace(b">0<", b">None<"), "fixture")["stats"] == {}


def test_anomalies_survive_conversion(tmp_path):
    source = source_archive(tmp_path / "source")
    (source / "raw/resources/archta.xml").write_bytes(XML.replace(b"<DR>922</DR>", b"<DR>154</DR>"))
    report = convert_archive(source, tmp_path / "output")
    archive = read_archive(tmp_path / "output")
    assert archive.resources[0].stats["DR"] == 154
    assert report["anomalies"][0]["observed"] == 154
    assert find_anomalies(archive.resources)[0]["observed"] == 154


@pytest.mark.parametrize("observed", [-5, 2000])
def test_source_stats_outside_normal_game_bounds_are_not_clamped(tmp_path, observed):
    source = source_archive(tmp_path / "source")
    (source / "raw/resources/archta.xml").write_bytes(XML.replace(b"<DR>922</DR>", f"<DR>{observed}</DR>".encode()))
    output = tmp_path / "output"
    convert_archive(source, output)
    archive = read_archive(output)
    assert archive.resources[0].stats["DR"] == observed
    assert find_anomalies(archive.resources)[0]["observed"] == observed


def test_deterministic_output_revision_and_no_write_dry_run(tmp_path):
    source = source_archive(tmp_path / "source")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    missing = tmp_path / "missing-parent/output"
    convert_archive(source, missing, dry_run=True)
    assert not missing.parent.exists()
    for name in ("one", "two"):
        convert_archive(source, tmp_path / name)
    one, two = tmp_path / "one", tmp_path / "two"
    assert {p.relative_to(one): p.read_bytes() for p in one.rglob("*") if p.is_file()} == {p.relative_to(two): p.read_bytes() for p in two.rglob("*") if p.is_file()}
    assert read_archive(one).revision == read_archive(two).revision
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="already exists"):
        convert_archive(source, one)
    with pytest.raises(ValueError, match="audited"):
        convert_archive(source, tmp_path / "audited", verify_audited_counts=True)
    assert not (tmp_path / "audited").exists()


@pytest.mark.parametrize("content", [b"<result>", b'<!DOCTYPE result [<!ENTITY x "a">]><result>&x;</result>',
    b'<!DOCTYPE result SYSTEM "file:///etc/passwd"><result/>',
    XML.replace(b"<spawnID>2606250</spawnID>", b"<spawnID>-1</spawnID>"),
    XML.replace(b"<OQ>0</OQ>", b"<OQ>0</OQ><OQ>1</OQ>"),
    XML.replace(b"2026-06-24 03:00:56", b"bad-time"), XML.replace(b"<OQ>0</OQ>", b"<OQ>1.5</OQ>"),
    XML.replace(b"<DRmin>200</DRmin>", b"<DRmin>1001</DRmin>")])
def test_invalid_xml_rejected(content):
    with pytest.raises(ValueError):
        parse_xml(content, "fixture")


@pytest.mark.parametrize("mutation,match", [("duplicate_id", "duplicate spawn"), ("duplicate_name", "duplicate resource name"),
    ("missing", "missing discovered"), ("unexpected", "unexpected XML"), ("discovery_duplicate", "duplicate discovery")])
def test_identity_reconciliation_rejects_inconsistent_sources(tmp_path, mutation, match):
    source = source_archive(tmp_path / "source")
    if mutation in ("duplicate_id", "duplicate_name"):
        xml = XML.replace(b"archta", b"other") if mutation == "duplicate_id" else XML.replace(b"2606250", b"2606251")
        (source / "raw/resources/other.xml").write_bytes(xml)
        if mutation == "duplicate_id":
            (source / "raw/names/names-frozen.json").write_text(json.dumps(["archta", "other", *AUDITED_UNRESOLVED]))
    elif mutation == "missing":
        (source / "raw/errors/dweina.bin").unlink()
    elif mutation == "unexpected":
        (source / "raw/resources/archta.xml").write_bytes(XML.replace(b"archta", b"unexpected"))
    else:
        (source / "raw/names/names-frozen.json").write_text('["archta", "archta"]')
    with pytest.raises(ValueError, match=match):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("relative", ["../outside", "/etc/passwd", "C:/secret", "raw/../../secret", "raw\\secret", "raw//secret", "raw/new\nline.xml"])
def test_path_traversal_rejected(tmp_path, relative):
    with pytest.raises(ValueError, match="path"):
        safe_path(tmp_path, relative)


def test_oversized_xml_cannot_publish_output(tmp_path):
    source = source_archive(tmp_path / "source")
    with (source / "raw/resources/archta.xml").open("ab") as stream:
        stream.write(b" " * (1024 * 1024))
    with pytest.raises(ValueError, match="size limit"):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mutation,match", [("missing", "missing source evidence"), ("tamper", "SHA-256 mismatch"),
    ("missing_checksum", "missing checksum"), ("names", "names index"), ("identity", "duplicate or invalid frozen spawn"),
    ("discovery", "discovered names"), ("normalized", "differs from source XML"), ("path", "path"),
    ("extra", "unexpected or missing source records"), ("planet", "planets index"), ("type", "source-types index")])
def test_reader_fail_closed(tmp_path, mutation, match):
    _, output, _ = converted(tmp_path)
    if mutation == "missing":
        (output / "raw/resources/archta.xml").unlink()
    elif mutation == "tamper":
        (output / "raw/resources/archta.xml").write_bytes(XML + b" ")
    elif mutation == "missing_checksum":
        path = output / "checksums/sha256sums.txt"
        path.write_text("\n".join(line for line in path.read_text().splitlines() if not line.endswith("raw/resources/archta.xml")) + "\n")
    elif mutation == "names":
        alter_json(output, "normalized/names.json", lambda p: p["names"].update(archta=[42]))
    elif mutation == "identity":
        alter_json(output, "normalized/frozen-identities.json", lambda p: p["identities"].append(p["identities"][0]))
    elif mutation == "discovery":
        alter_json(output, "normalized/frozen-identities.json", lambda p: p["discovered_names"].remove("safe"))
    elif mutation == "normalized":
        alter_json(output, "normalized/resources-index.json", lambda p: p["resources"]["2606250"]["exact"]["stats"].update(DR=500))
    elif mutation == "path":
        path = output / "checksums/sha256sums.txt"
        with path.open("a") as stream:
            stream.write("a" * 64 + "  ../outside\n")
    elif mutation == "extra":
        (output / "raw/resources/extra.xml").write_bytes(XML)
    elif mutation == "planet":
        alter_json(output, "normalized/planets.json", lambda p: p["planets"]["5"].update(planet_name="other"))
    else:
        alter_json(output, "normalized/source-types.json", lambda p: p["source_types"].clear())
    with pytest.raises(ValueError, match=match):
        read_archive(output)


def test_import_invalid_archive_never_touches_connection(tmp_path):
    _, output, _ = converted(tmp_path)
    (output / "raw/resources/archta.xml").unlink()
    class NoDatabaseAccess:
        def __getattr__(self, name):
            raise AssertionError(f"database accessed before validation: {name}")
    with pytest.raises(ValueError, match="missing source evidence"):
        import_archive(NoDatabaseAccess(), output)


@requires_test_db
def test_relocated_repeat_import_is_idempotent_and_unknown_http_stays_null(tmp_path, db):
    _, output, _ = converted(tmp_path)
    first = import_archive(db, output)
    before = phase3d_counts(db, "2606250")
    batches = db.execute(text("SELECT count(*) FROM import_batches")).scalar_one()
    moved = tmp_path / "relocated"
    shutil.copytree(output, moved)
    second = import_archive(db, moved)
    assert first["source_resources_inserted"] == 1 and second["source_resources_reused"] == 1
    assert phase3d_counts(db, "2606250") == before
    assert db.execute(text("SELECT count(*) FROM import_batches")).scalar_one() == batches
    assert db.execute(text("SELECT count(*) FROM unresolved_source_resources WHERE http_status IS NOT NULL")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM source_records WHERE record_type LIKE 'gh_%' AND source_snapshot_id IS NULL")).scalar_one() == 0


@requires_test_db
def test_unmapped_planet_rejected_without_any_mutation(tmp_path, db):
    source = source_archive(tmp_path / "source")
    (source / "raw/resources/archta.xml").write_bytes(XML.replace(b">Lok<", b">Unknown Planet<"))
    output = tmp_path / "output"
    convert_archive(source, output)
    before = db.execute(text("SELECT count(*) FROM import_batches")).scalar_one()
    with pytest.raises(ValueError, match="unresolved planets"):
        import_archive(db, output)
    assert db.execute(text("SELECT count(*) FROM import_batches")).scalar_one() == before


def test_cli_requires_explicit_mode_and_dry_run_writes_nothing(tmp_path, capsys):
    source = source_archive(tmp_path / "source")
    output = tmp_path / "output"
    with pytest.raises(SystemExit):
        main(["--source", str(source), "--output", str(output)])
    assert main(["--source", str(source), "--output", str(output), "--dry-run"]) == 0
    assert not output.exists()


def add_acquisition(source, **changes):
    row = {"source_path": "raw/errors/dweina.bin", "name": "dweina",
           "sha256": hashlib.sha256((source / "raw/errors/dweina.bin").read_bytes()).hexdigest(),
           "http_status": 200, "response_timestamp": "2026-06-25T01:02:03+00:00"}
    row.update(changes)
    (source / "manifests").mkdir(exist_ok=True)
    (source / "manifests/attempts.jsonl").write_text(json.dumps(row) + "\n")


def test_hash_bound_acquisition_metadata_and_unrecognized_logs(tmp_path):
    source = source_archive(tmp_path / "source")
    add_acquisition(source)
    with (source / "manifests/attempts.jsonl").open("a") as stream:
        stream.write('{"different_exporter_schema": true}\n')
    output = tmp_path / "output"
    report = convert_archive(source, output)
    archive = read_archive(output)
    assert archive.unresolved[0]["http_status"] == 200
    assert archive.unresolved[0]["response_timestamp"] == "2026-06-25T01:02:03+00:00"
    assert report["unrecognized_acquisition_records"] == 1
    assert (output / "manifests/attempts.jsonl").read_bytes() == (source / "manifests/attempts.jsonl").read_bytes()


def test_unbound_acquisition_never_fabricates_metadata(tmp_path):
    source = source_archive(tmp_path / "source")
    add_acquisition(source, sha256="a" * 64)
    output = tmp_path / "output"
    convert_archive(source, output)
    assert read_archive(output).unresolved[0]["http_status"] is None
    alter_json(output, "normalized/unresolved.json", lambda p: p["unresolved"][0].update(http_status=200))
    with pytest.raises(ValueError, match="unverified acquisition http_status"):
        read_archive(output)


@pytest.mark.parametrize("changes", [{"http_status": True}, {"http_status": 999}, {"response_timestamp": "bad"}, {"name": "wrong"}, {"source_path": "../outside"}])
def test_invalid_acquisition_fails_closed(tmp_path, changes):
    source = source_archive(tmp_path / "source")
    add_acquisition(source, **changes)
    with pytest.raises(ValueError):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_staging_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    source = source_archive(tmp_path / "source")
    def failed_copy(*args):
        raise OSError("simulated disk write failure")
    monkeypatch.setattr("app.importing.galaxy_harvester.converter.shutil.copyfile", failed_copy)
    with pytest.raises(OSError, match="disk write"):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output-staging-*"))
    assert (source / "raw/resources/archta.xml").read_bytes() == XML


def test_source_change_during_publication_fails_closed(tmp_path, monkeypatch):
    source = source_archive(tmp_path / "source")
    original_copy = shutil.copyfile
    def changed_copy(src, dst):
        result = original_copy(src, dst)
        Path(dst).write_bytes(Path(dst).read_bytes() + b" ")
        return result
    monkeypatch.setattr("app.importing.galaxy_harvester.converter.shutil.copyfile", changed_copy)
    with pytest.raises(ValueError, match="source changed"):
        convert_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_symlink_evidence_rejected(tmp_path):
    outside = tmp_path / "outside.xml"
    outside.write_bytes(XML)
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "evidence.xml").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation requires Windows developer mode or privilege")
    with pytest.raises(ValueError, match="outside archive|symlink"):
        safe_path(root, "evidence.xml")


@requires_test_db
def test_acquisition_http_status_is_imported_only_with_evidence(tmp_path, db):
    source = source_archive(tmp_path / "source")
    add_acquisition(source)
    output = tmp_path / "output"
    convert_archive(source, output)
    import_archive(db, output)
    assert db.execute(text("SELECT http_status FROM unresolved_source_resources WHERE source_name = 'dweina'")).scalar_one() == 200


@requires_test_db
def test_database_failure_rolls_back_even_if_caller_catches_it(tmp_path, db, monkeypatch):
    _, output, _ = converted(tmp_path)
    before = {table: db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in
              ("resources", "source_resources", "source_records", "import_batches", "source_snapshots")}
    def fail_after_writes(*args):
        raise RuntimeError("injected database-stage failure")
    monkeypatch.setattr("app.importing.galaxy_harvester.importer.upsert_stats", fail_after_writes)
    with pytest.raises(RuntimeError, match="injected"):
        import_archive(db, output)
    assert before == {table: db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in before}


@requires_test_db
def test_unchanged_legacy_null_snapshot_provenance_is_adopted(tmp_path, db):
    _, output, _ = converted(tmp_path)
    import_archive(db, output)
    before = phase3d_counts(db, "2606250")
    batches_before = db.execute(text("SELECT count(*) FROM import_batches")).scalar_one()
    # Model the old importer's global NULL-snapshot records without destroying
    # existing observation references. The new importer attaches matching hashes.
    db.execute(text("UPDATE source_records SET source_snapshot_id = NULL WHERE record_type LIKE 'gh_%'"))
    db.execute(text("DELETE FROM source_snapshots WHERE snapshot_kind = 'galaxy_harvester_historical_archive'"))
    db.execute(text("UPDATE import_batches SET source_revision = '/old/archive/location' WHERE import_kind = 'galaxy_harvester_historical_archive'"))
    relocated = tmp_path / "relocated"
    shutil.copytree(output, relocated)
    import_archive(db, relocated)
    assert phase3d_counts(db, "2606250") == before
    assert db.execute(text("SELECT count(*) FROM import_batches")).scalar_one() == batches_before
    assert db.execute(text("SELECT count(*) FROM source_records WHERE record_type LIKE 'gh_%' AND source_snapshot_id IS NULL")).scalar_one() == 0


@requires_test_db
def test_importer_cli_dry_run_enforces_read_only_transaction(tmp_path, engine, monkeypatch):
    from app.importing.galaxy_harvester import importer
    _, output, _ = converted(tmp_path)
    original = importer.dry_run
    def checked(connection, root):
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        return original(connection, root)
    monkeypatch.setattr(importer, "make_engine", lambda: engine)
    monkeypatch.setattr(importer, "dry_run", checked)
    assert importer.main(["import-history", "--archive", str(output), "--dry-run"]) == 0
