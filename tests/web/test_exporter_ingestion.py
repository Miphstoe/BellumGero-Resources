from dataclasses import replace
import hashlib
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.main import create_app
from app.importing.core3_live.exporter_adapter import prepare_snapshot
from app.importing.core3_live.snapshot import SnapshotValidationError
from tests.importing.test_core3_exporter_adapter import native_bytes, native_resource, native_snapshot
from tests.web.test_website import BEARER, BASIC, counts, snapshot, upload


def native_upload(client, content, *, mode="import", **fields):
    return client.post("/api/admin/snapshots", headers=BEARER,
        files={"snapshot": ("native.json", content, "application/json")}, data={"mode": mode, **fields})


def content_with_count(count, *, timestamp=1800000000):
    return native_bytes(resources=[native_resource(source_resource_id=str(100 + i)) for i in range(count)],
        captured_at=timestamp, capture_started_at=timestamp, capture_completed_at=timestamp + 1, generated_at=timestamp + 2)


def test_native_import_dry_run_and_raw_audit(client, web_engine):
    content = native_bytes()
    before = counts(web_engine)
    plan = native_upload(client, content, mode="validate")
    assert plan.status_code == 200 and counts(web_engine) == before
    assert plan.json()["summary"]["adapter"]["input_sha256"] == hashlib.sha256(content).hexdigest()
    assert not plan.json()["summary"]["count_safety"]["review_required"]
    response = native_upload(client, content)
    assert response.status_code == 200 and response.json()["summary"]["advanced_current"]
    with web_engine.connect() as connection:
        assert connection.execute(text("SELECT source_resource_id FROM source_resources")).scalar_one() == "18446744073709551615"
        metadata = connection.execute(text("SELECT metadata FROM source_snapshots")).scalar_one()
        assert metadata["input_sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["native_metadata"]["captured_at"] == 1800000000
        assert metadata["native_metadata"]["consistency"] == "interval"
        assert connection.execute(text("SELECT content_sha256 FROM core3_live_snapshot_imports")).scalar_one() == metadata["normalized_sha256"]
    assert client.get("/api/resources").json()["items"][0]["stats"]["OQ"] == 0


def test_native_formatting_duplicates_and_conflicts(client, web_engine):
    payload = native_snapshot()
    first = json.dumps(payload).encode()
    second = json.dumps(payload, indent=4, sort_keys=True).encode()
    assert native_upload(client, first).status_code == 200
    response = native_upload(client, second)
    assert response.status_code == 200 and response.json()["summary"]["status"] == "duplicate"
    assert response.json()["summary"]["adapter"]["input_sha256"] == hashlib.sha256(second).hexdigest()
    assert counts(web_engine)["core3_live_snapshot_imports"] == 1
    assert counts(web_engine)["resource_stat_observations"] == 10
    changed = native_snapshot(resources=[native_resource(name="different")])
    assert native_upload(client, json.dumps(changed).encode()).status_code == 409
    assert counts(web_engine)["source_resources"] == 1


@pytest.mark.parametrize("new_count", [0, 1])
def test_count_anomalies_block_before_import_or_deactivation(client, web_engine, monkeypatch, new_count):
    assert native_upload(client, content_with_count(4)).status_code == 200
    content = content_with_count(new_count, timestamp=1800000300)
    before = counts(web_engine)
    validation = native_upload(client, content, mode="validate")
    assert validation.status_code == 200
    assert validation.json()["summary"]["count_safety"]["review_required"]
    assert counts(web_engine) == before
    from app.web import ingestion

    def must_not_import(*args, **kwargs):
        pytest.fail("Anomalous snapshot reached authoritative importer")

    monkeypatch.setattr(ingestion.importer, "import_snapshot", must_not_import)
    response = native_upload(client, content)
    assert response.status_code == 409 and response.json()["errors"][0]["code"] == "snapshot_review_required"
    assert counts(web_engine)["core3_live_snapshot_imports"] == 1
    with web_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM current_resource_availability WHERE is_active")).scalar_one() == 4
    assert client.get("/api/admin/imports", headers=BEARER).json()["items"][0]["status"] == "failed"


def test_initial_empty_requires_review(client):
    response = native_upload(client, native_bytes(resources=[]), mode="validate")
    assert response.status_code == 200 and response.json()["summary"]["count_safety"]["review_required"]
    assert native_upload(client, native_bytes(resources=[])).status_code == 409


def test_reduction_at_threshold_is_accepted(client):
    assert native_upload(client, content_with_count(4)).status_code == 200
    assert native_upload(client, content_with_count(2, timestamp=1800000300)).status_code == 200


def test_operator_override_requires_enabled_policy_exact_hash_and_reason(client, web_engine, web_settings):
    assert native_upload(client, content_with_count(4)).status_code == 200
    content = content_with_count(0, timestamp=1800000300)
    fingerprint = native_upload(client, content, mode="validate").json()["summary"]["adapter"]["normalized_sha256"]
    response = native_upload(client, content, review_sha256=fingerprint, review_reason="Verified legitimate full shift")
    assert response.status_code == 403
    with TestClient(create_app(engine=web_engine, settings=replace(web_settings, native_review_overrides=True))) as authorized:
        for fields in ({"review_sha256": "0" * 64, "review_reason": "reviewed"},
                       {"review_sha256": fingerprint}, {"review_reason": "reviewed"},
                       {"review_sha256": fingerprint, "review_reason": "x" * 501}):
            assert native_upload(authorized, content, **fields).status_code == 422
        response = native_upload(authorized, content, review_sha256=fingerprint, review_reason="Verified legitimate full shift")
        assert response.status_code == 200 and response.json()["summary"]["count_safety"]["override_applied"]
        with web_engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM current_resource_availability WHERE is_active")).scalar_one() == 0
            audit = connection.execute(text("SELECT summary FROM import_batches WHERE import_kind='core3_live_resource_snapshot' ORDER BY id DESC LIMIT 1")).scalar_one()
            assert audit["review_reason"] == "Verified legitimate full shift"
        # Identical retry is a duplicate without requiring another override.
        assert native_upload(authorized, content).json()["summary"]["status"] == "duplicate"
        assert native_upload(authorized, content_with_count(0, timestamp=1800000600)).status_code == 200


@pytest.mark.parametrize("changes", [{"complete": False}, {"source_instance": "wrong"}, {"resource_count": 5},
    {"resources": [native_resource(active=False)]}, {"resources": [native_resource(stats={"ER": 2})]},
    {"resources": [native_resource(source_type_id="unknown")]},
    {"resources": [native_resource(planets=["unknown"])]}])
def test_override_never_bypasses_validation(web_engine, web_settings, changes):
    with TestClient(create_app(engine=web_engine, settings=replace(web_settings, native_review_overrides=True))) as client:
        before = counts(web_engine)
        content = native_bytes(**changes)
        try:
            fingerprint = prepare_snapshot(content).audit["normalized_sha256"]
        except SnapshotValidationError:
            fingerprint = "0" * 64
        response = native_upload(client, content, review_sha256=fingerprint, review_reason="reviewed")
        assert response.status_code == 422 and response.json()["errors"][0]["code"] == "invalid_snapshot"
        after = counts(web_engine)
        assert after["source_resources"] == before["source_resources"] == 0
        assert after["core3_live_snapshot_imports"] == 0


def test_native_ordering_and_existing_format_safety_compatibility(client, web_engine):
    # Existing website complete-empty behavior is intentionally unchanged.
    assert upload(client, snapshot(captured_at="2027-01-15T07:50:00Z", resources=[])).status_code == 200
    assert native_upload(client, native_bytes()).status_code == 200
    older = content_with_count(0, timestamp=1799999990)
    assert native_upload(client, older).status_code == 409
    assert counts(web_engine)["core3_live_snapshot_imports"] == 2


def test_native_auth_and_csrf_unchanged(client):
    content = native_bytes()
    assert client.post("/api/admin/snapshots", files={"snapshot": ("snapshot.json", content)}).status_code == 401
    assert client.post("/admin/snapshots", auth=BASIC, files={"snapshot": ("snapshot.json", content)}, data={"mode": "import"}).status_code == 403


def test_native_rollback_including_audit_enrichment(client, web_engine, monkeypatch):
    from app.web import ingestion
    original = ingestion.importer.import_snapshot

    def fail(connection, path):
        original(connection, path)
        raise RuntimeError("synthetic interrupted import")

    monkeypatch.setattr(ingestion.importer, "import_snapshot", fail)
    assert native_upload(client, native_bytes()).status_code == 500
    assert counts(web_engine)["core3_live_snapshot_imports"] == counts(web_engine)["source_resources"] == 0
    audit = client.get("/api/admin/imports", headers=BEARER).json()["items"][0]["summary"]
    assert audit["adapter"]["input_sha256"] == hashlib.sha256(native_bytes()).hexdigest()


def test_environment_threshold_and_override_parsing(monkeypatch):
    from app.web.settings import WebSettings
    monkeypatch.setenv("BELLUM_NATIVE_MAX_REDUCTION_FRACTION", "0.75")
    monkeypatch.setenv("BELLUM_NATIVE_REVIEW_OVERRIDES", "true")
    settings = WebSettings.from_env()
    assert settings.native_review_overrides and settings.native_max_reduction_fraction == 0.75
    monkeypatch.setenv("BELLUM_NATIVE_SUSTAINED_MAX_REDUCTION_FRACTION", "0.8")
    assert WebSettings.from_env().native_sustained_max_reduction_fraction == 0.8
    monkeypatch.setenv("BELLUM_NATIVE_REVIEW_OVERRIDES", "typo")
    with pytest.raises(ValueError):
        WebSettings.from_env()


def test_cumulative_decline_blocks_before_reconciliation_and_keeps_baseline(client, web_engine):
    for count, timestamp in ((8, 1800000000), (6, 1800000300), (4, 1800000600)):
        assert native_upload(client, content_with_count(count, timestamp=timestamp)).status_code == 200
    content = content_with_count(3, timestamp=1800000900)
    before = counts(web_engine)
    plan = native_upload(client, content, mode="validate")
    assert plan.status_code == 200 and counts(web_engine) == before
    safety = plan.json()["summary"]["count_safety"]
    assert safety["previous_resource_count"] == 4 and safety["sustained_baseline_count"] == 8
    assert safety["review_reasons"] == ["sustained_reduction"]
    for _ in range(2):
        assert native_upload(client, content).status_code == 409
    assert counts(web_engine)["core3_live_snapshot_imports"] == 3
    with web_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM current_resource_availability WHERE is_active")).scalar_one() == 4
    failure = client.get("/api/admin/imports", headers=BEARER).json()["items"][0]
    assert failure["summary"]["count_safety"]["review_reasons"] == ["sustained_reduction"]


def test_reviewed_cumulative_decline_resets_baseline_and_allows_legitimate_recovery(web_engine, web_settings):
    settings = replace(web_settings, native_review_overrides=True)
    with TestClient(create_app(engine=web_engine, settings=settings)) as client:
        for count, timestamp in ((8, 1800000000), (6, 1800000300), (4, 1800000600)):
            assert native_upload(client, content_with_count(count, timestamp=timestamp)).status_code == 200
        reviewed = content_with_count(3, timestamp=1800000900)
        plan = native_upload(client, reviewed, mode="validate").json()["summary"]
        fingerprint = plan["adapter"]["normalized_sha256"]
        assert native_upload(client, reviewed, review_sha256="0" * 64, review_reason="Verified shift").status_code == 422
        assert native_upload(client, reviewed).status_code == 409
        response = native_upload(client, reviewed, review_sha256=fingerprint, review_reason="Verified gradual legitimate despawns")
        assert response.status_code == 200 and response.json()["summary"]["count_safety"]["override_applied"]
        assert native_upload(client, reviewed).json()["summary"]["status"] == "duplicate"
        next_content = content_with_count(2, timestamp=1800001200)
        next_plan = native_upload(client, next_content, mode="validate").json()["summary"]["count_safety"]
        assert next_plan["sustained_baseline_count"] == 3 and not next_plan["review_required"]
        assert next_plan["sustained_baseline_reset_at"] is not None
        assert native_upload(client, next_content).status_code == 200
        # Recovery raises the high-water mark again; an old duplicate cannot reset it.
        assert native_upload(client, content_with_count(6, timestamp=1800001500)).status_code == 200
        assert native_upload(client, reviewed, review_sha256=fingerprint, review_reason="Retry").json()["summary"]["status"] == "duplicate"
        assert native_upload(client, content_with_count(2, timestamp=1800001800)).status_code == 409


def test_sustained_baseline_does_not_expire_after_long_delivery_gap(client):
    assert native_upload(client, content_with_count(8)).status_code == 200
    assert native_upload(client, content_with_count(4, timestamp=1800000300)).status_code == 200
    later = content_with_count(3, timestamp=1800090000)  # More than a day later, still before fixture expiry.
    assert native_upload(client, later).status_code == 409


def test_cumulative_limit_can_be_configured(web_engine, web_settings):
    settings = replace(web_settings, native_sustained_max_reduction_fraction=0.75)
    with TestClient(create_app(engine=web_engine, settings=settings)) as client:
        for count, timestamp in ((8, 1800000000), (4, 1800000300), (2, 1800000600)):
            assert native_upload(client, content_with_count(count, timestamp=timestamp)).status_code == 200
        assert native_upload(client, content_with_count(1, timestamp=1800000900)).status_code == 409


def test_failed_review_cannot_reset_baseline(client, web_engine, web_settings, monkeypatch):
    for count, timestamp in ((8, 1800000000), (4, 1800000300)):
        assert native_upload(client, content_with_count(count, timestamp=timestamp)).status_code == 200
    content = content_with_count(3, timestamp=1800000600)
    fingerprint = native_upload(client, content, mode="validate").json()["summary"]["adapter"]["normalized_sha256"]
    from app.web import ingestion
    original = ingestion.importer.import_snapshot

    def fail(connection, path):
        original(connection, path)
        raise RuntimeError("interrupted reviewed import")

    with TestClient(create_app(engine=web_engine, settings=replace(web_settings, native_review_overrides=True))) as authorized:
        with monkeypatch.context() as patch:
            patch.setattr(ingestion.importer, "import_snapshot", fail)
            assert native_upload(authorized, content, review_sha256=fingerprint, review_reason="Verified").status_code == 500
        plan = native_upload(authorized, content, mode="validate").json()["summary"]["count_safety"]
        assert plan["sustained_baseline_count"] == 8 and plan["sustained_baseline_reset_at"] is None
        assert native_upload(authorized, content).status_code == 409


def test_mixed_legacy_payload_rejected_without_resource_updates(client, web_engine):
    payload = json.loads(snapshot())
    payload["resource_count"] = len(payload["resources"])
    before = counts(web_engine)
    response = native_upload(client, json.dumps(payload).encode())
    assert response.status_code == 422 and response.json()["errors"][0]["code"] == "invalid_snapshot"
    after = counts(web_engine)
    assert after["source_resources"] == before["source_resources"]
    assert after["core3_live_snapshot_imports"] == before["core3_live_snapshot_imports"]


def test_legacy_history_is_used_when_establishing_sustained_baseline(client):
    from tests.importing.test_core3_live_importer import resource_payload
    resources = [resource_payload(oid=str(100 + index)) for index in range(8)]
    assert upload(client, snapshot(captured_at="2027-01-15T08:00:00Z", resources=resources)).status_code == 200
    assert native_upload(client, content_with_count(4, timestamp=1800000300)).status_code == 200
    content = content_with_count(3, timestamp=1800000600)
    safety = native_upload(client, content, mode="validate").json()["summary"]["count_safety"]
    assert safety["sustained_baseline_count"] == 8
    assert native_upload(client, content).status_code == 409


def test_untrusted_snapshot_metadata_cannot_spoof_a_review_reset(client):
    assert native_upload(client, content_with_count(8)).status_code == 200
    payload = json.loads(content_with_count(4, timestamp=1800000300))
    payload["count_safety"] = {"override_applied": True}
    assert native_upload(client, json.dumps(payload).encode()).status_code == 200
    content = content_with_count(3, timestamp=1800000600)
    safety = native_upload(client, content, mode="validate").json()["summary"]["count_safety"]
    assert safety["sustained_baseline_count"] == 8 and safety["sustained_baseline_reset_at"] is None
    assert native_upload(client, content).status_code == 409
