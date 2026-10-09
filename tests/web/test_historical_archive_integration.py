"""Exercise committed archive imports through the public website and API."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.importing.galaxy_harvester.importer import import_archive
from tests.importing.test_core3_live_importer import resource_payload as core_payload
from tests.importing.test_galaxy_harvester_importer import (
    make_archive,
    phase3d_counts,
    resource_payload,
)
from tests.web.test_website import snapshot, upload


@pytest.fixture()
def historical_archive(tmp_path, web_engine):
    return make_archive(tmp_path, {
        "610001": resource_payload(
            spawn_id=610001, name="Archive Alpha", resource_type="test_core3_resource_type",
            stats={"OQ": 0, "DR": 500}),
        "610002": resource_payload(
            spawn_id=610002, name="Archive Beta", resource_type="test_core3_resource_type",
            stats={"OQ": 900}, planets=[{"id": 1, "name": "Corellia"}]),
    })


def committed_import(engine, archive):
    # Public requests use independent connections, so importer writes must commit.
    with engine.begin() as connection:
        return import_archive(connection, archive)


def results(client, **params):
    response = client.get("/api/resources", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_committed_archive_preserves_values_dates_and_presentation(
    client, web_engine, historical_archive,
):
    committed_import(web_engine, historical_archive)
    data = results(client)
    assert data["total"] == 2
    alpha, beta = data["items"]
    assert alpha["name"] == "Archive Alpha"
    assert alpha["source_system"] == "galaxy_harvester"
    assert alpha["source_instance"] == "galaxy-153"
    assert alpha["source_resource_id"] == "610001"
    assert alpha["type_slug"] == "test_core3_resource_type"
    assert alpha["resource_type"] == beta["resource_type"] == "Test Core3 Resource Type"
    assert alpha["planets"] == ["Yavin 4"]
    assert beta["planets"] == ["Corellia"]
    assert alpha["stats"]["OQ"] == 0
    assert alpha["stats"]["DR"] == 500
    assert alpha["stats"]["CR"] is None
    assert beta["stats"]["DR"] is None
    assert alpha["first_observed_at"].startswith("2025-01-02T03:04:05")
    assert all(item["availability"] == "historical" for item in data["items"])
    assert alpha["current_as_of"] is None and alpha["absent_as_of"] is None
    assert results(client, availability="current")["total"] == 0
    assert data["snapshot_status"]["captured_at"] is None

    response = client.get(f"/api/resources/{alpha['id']}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["observations"][0]["observation_kind"] == "historical_exact"
    assert detail["observations"][0]["confidence"] == "source_exact"
    events = {event["event_type"]: event for event in detail["lifecycle"]}
    assert set(events) == {"entered", "unavailable"}
    assert events["entered"]["event_time"].startswith("2025-01-02T03:04:05")
    assert events["unavailable"]["event_time"].startswith("2025-02-03T04:05:06")
    assert all(event["confidence"] == "source_exact" for event in events.values())
    for path in ("/resources", "/history", f"/resources/{alpha['id']}"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Archive Alpha" in page.text
        assert "Galaxy Harvester historical archive" in page.text
        assert "galaxy-153" in page.text
        assert 'class="badge historical"' in page.text
    assert "Historical records do not establish current availability." in client.get("/history").text
    assert "does not establish current availability" in client.get(f"/resources/{alpha['id']}").text


@pytest.mark.parametrize("filters, expected", [
    ({"name": "aRcHiVe aLp"}, ["Archive Alpha"]),
    ({"type": "test_core3_resource_type"}, ["Archive Alpha", "Archive Beta"]),
    ({"type": "nonexistent"}, []),
    ({"planet": "yavin4"}, ["Archive Alpha"]),
    ({"planet": "corellia"}, ["Archive Beta"]),
    ({"source": "galaxy_harvester"}, ["Archive Alpha", "Archive Beta"]),
    ({"source": "core3"}, []),
    ({"availability": "historical"}, ["Archive Alpha", "Archive Beta"]),
    ({"min_OQ": 0, "max_OQ": 0}, ["Archive Alpha"]),
    ({"min_OQ": 800, "max_OQ": 950}, ["Archive Beta"]),
    ({"min_DR": 0}, ["Archive Alpha"]),
    ({"max_DR": 0}, []),
    ({"sort": "OQ", "direction": "desc"}, ["Archive Beta", "Archive Alpha"]),
    ({"sort": "name", "direction": "desc"}, ["Archive Beta", "Archive Alpha"]),
])
def test_archive_search(client, web_engine, historical_archive, filters, expected):
    committed_import(web_engine, historical_archive)
    data = results(client, **filters)
    assert data["total"] == len(expected)
    assert [item["name"] for item in data["items"]] == expected


def test_archive_pagination(client, web_engine, historical_archive):
    committed_import(web_engine, historical_archive)
    pages = [results(client, sort="OQ", direction="desc", page_size=1, page=page)
             for page in (1, 2, 3)]
    assert all(page["total"] == 2 and page["pages"] == 2 for page in pages)
    assert [pages[i]["items"][0]["name"] for i in (0, 1)] == ["Archive Beta", "Archive Alpha"]
    assert pages[2]["items"] == []
    page = client.get("/history", params={"page_size": 1, "sort": "OQ", "direction": "desc"})
    assert page.status_code == 200 and "Archive Beta" in page.text
    assert "Archive Alpha" not in page.text
    assert "page=2" in page.text and "Next" in page.text


def core_state(engine):
    """Capture complete persisted Core3 rows, not just counts or API projections."""
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY to_jsonb(t)::text")).scalars().all()
            for table in ("current_resource_availability", "core3_live_snapshot_imports")
        } | {"resources": connection.execute(text("""
            SELECT to_jsonb(sr), to_jsonb(r) FROM source_resources sr
            JOIN source_instances si ON si.id = sr.source_instance_id
            JOIN resources r ON r.id = sr.canonical_resource_id
            WHERE si.code = 'bellum-gero-live' ORDER BY sr.id
        """)).all()}


@pytest.mark.parametrize("stale", [False, True])
def test_mixed_sources_idempotency_and_core3_authority(
    client, web_engine, historical_archive, stale,
):
    committed_import(web_engine, historical_archive)
    assert core_state(web_engine) == {
        "current_resource_availability": [], "core3_live_snapshot_imports": [], "resources": [],
    }
    captured = datetime.now(timezone.utc) - timedelta(days=3 if stale else 0)
    content = snapshot(captured_at=captured.isoformat(), resources=[
        core_payload(oid="620001", name="Archive Alpha", stats={"OQ": 750})])
    response = upload(client, content)
    assert response.status_code == 200, response.text
    before = core_state(web_engine)
    live = results(client, source="core3")
    historical = results(client, source="galaxy_harvester")
    assert live["total"] == 1 and historical["total"] == 2
    assert {r["source_system"] for r in live["items"]} == {"core3"}
    assert {r["source_system"] for r in historical["items"]} == {"galaxy_harvester"}
    assert results(client, source="all")["total"] == 3
    current = results(client, availability="current")
    assert current["items"] == live["items"]
    assert current["snapshot_status"]["stale"] is stale
    assert current["items"][0]["availability"] == ("stale" if stale else "current")
    assert current["items"][0]["source_instance"] == "bellum-gero-live"
    assert results(client, source="galaxy_harvester", availability="current")["total"] == 0
    assert results(client, availability="historical")["items"] == historical["items"]
    same_name = results(client, name="Archive Alpha")["items"]
    assert len(same_name) == 2 and len({r["id"] for r in same_name}) == 2
    assert {r["stats"]["OQ"] for r in same_name} == {0, 750}
    with web_engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT canonical_resource_id FROM source_resources
            WHERE source_resource_name = 'Archive Alpha'
        """)).scalars().all()
        assert len(set(rows)) == 2
        counts = {key: phase3d_counts(connection, key) for key in ("610001", "610002")}
    committed_import(web_engine, historical_archive)
    assert core_state(web_engine) == before
    assert results(client, source="core3") == live
    assert results(client, source="galaxy_harvester") == historical
    with web_engine.connect() as connection:
        for key in counts:
            assert phase3d_counts(connection, key) == counts[key]
            assert counts[key]["canonical_resources"] == counts[key]["source_resources"] == 1
            assert counts[key]["stat_observations"] == 11
            assert counts[key]["lifecycle_events"] == 2
            record = connection.execute(text("""
                SELECT rec.payload_hash, rec.payload_ref, rec.normalized_payload, si.code
                FROM source_resources sr JOIN source_records rec ON rec.id = sr.first_source_record_id
                JOIN source_snapshots snap ON snap.id = rec.source_snapshot_id
                JOIN import_batches batch ON batch.id = snap.import_batch_id
                JOIN source_instances si ON si.id = batch.source_instance_id
                WHERE sr.source_resource_id = :key
            """), {"key": key}).one()
            assert record.code == "galaxy-153"
            assert record.payload_hash == hashlib.sha256((historical_archive / record.payload_ref).read_bytes()).hexdigest()
            assert record.normalized_payload
    page = client.get("/resources")
    assert "Core3 authoritative snapshots" in page.text and "Galaxy Harvester historical archive" in page.text
    assert "bellum-gero-live" in page.text and "galaxy-153" in page.text
    detail = client.get(f"/resources/{live['items'][0]['id']}")
    assert "does not establish an exact despawn time" in detail.text
    assert "Archive Alpha" not in client.get("/history", params={"source": "core3"}).text


def test_snapshot_absence_stays_distinct_from_historical_unavailable_date(
    client, web_engine, historical_archive,
):
    committed_import(web_engine, historical_archive)
    captured = datetime.now(timezone.utc) - timedelta(hours=2)
    assert upload(client, snapshot(captured_at=captured.isoformat(), resources=[
        core_payload(oid="620002", name="Former Core3 Resource"),
    ])).status_code == 200
    absent_at = captured + timedelta(hours=1)
    assert upload(client, snapshot(captured_at=absent_at.isoformat(), resources=[])).status_code == 200
    assert results(client, availability="current")["total"] == 0
    historical = results(client, availability="historical")
    assert historical["total"] == 3
    core = next(item for item in historical["items"] if item["source_system"] == "core3")
    assert datetime.fromisoformat(core["absent_as_of"]) == absent_at
    detail = client.get(f"/api/resources/{core['id']}")
    assert detail.status_code == 200
    assert not any(event["event_type"] in {"despawned", "unavailable"}
                   for event in detail.json()["lifecycle"])
    history = client.get("/history")
    assert "Former Core3 Resource" in history.text and "Archive Alpha" in history.text
    assert "not an exact despawn time" in history.text
    page = client.get(f"/resources/{core['id']}")
    assert "Absent as of snapshot" in page.text
    assert "does not establish an exact despawn time" in page.text
    assert all(item["absent_as_of"] is None for item in historical["items"]
               if item["source_system"] == "galaxy_harvester")


def test_template_utf8_bytes_are_intact():
    template_dir = Path(__file__).resolve().parents[2] / "app/web/templates"
    for path in template_dir.glob("*.html"):
        content = path.read_bytes().decode("utf-8", errors="strict")
        assert "\ufffd" not in content, path
        assert "\u00e2\u20ac" not in content, path
        assert "\u00e2\u2020" not in content, path
