import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.main import create_app
from app.web.settings import WebSettings
from tests.importing.test_core3_live_importer import resource_payload

BEARER = {"Authorization": "Bearer test-api-token"}
BASIC = ("test-admin", "test-password")


def snapshot(*, captured_at=None, resources=None, **kwargs):
    payload = {"schema_version": 1, "source_system": "core3", "source_instance": "bellum-gero-live",
               "complete": True, "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
               "resources": resources if resources is not None else [resource_payload()]}
    payload.update(kwargs)
    return json.dumps(payload).encode()


def upload(client, content, mode="import", **kwargs):
    return client.post("/api/admin/snapshots", headers=BEARER,
                       files={"snapshot": ("snapshot.json", content, "application/json")}, data={"mode": mode}, **kwargs)


def counts(engine):
    with engine.connect() as connection:
        return {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("source_resources", "resources", "import_batches", "source_planets", "resource_stat_observations", "core3_live_snapshot_imports")}


def test_empty_database_pages_and_api_contract(client):
    for path in ("/", "/resources", "/history"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Bellum Gero" in response.text
        assert "No resources" in response.text or "No historical" in response.text
    data = client.get("/api/resources").json()
    assert data["items"] == [] and data["total"] == 0 and data["page_size"] == 25
    assert data["snapshot_status"]["captured_at"] is None and data["snapshot_status"]["stale"]
    assert len(client.get("/api/planets").json()["items"]) == 10
    assert "items" in client.get("/api/resource-types").json()
    assert client.get("/api/resources/99999999").status_code == 404
    assert client.get("/api/resources/99999999999999999999999999").status_code == 422
    assert client.get("/api/resources/0").status_code == 422
    assert client.get("/resources/99999999").status_code == 404
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200


def test_dry_run_and_success_duplicate(client, web_engine):
    content = snapshot()
    before = counts(web_engine)
    response = upload(client, content, "validate")
    assert response.status_code == 200 and response.json()["summary"]["dry_run"]
    assert counts(web_engine) == before
    response = upload(client, content)
    assert response.status_code == 200 and response.json()["summary"]["advanced_current"]
    first = counts(web_engine)
    duplicate = upload(client, content)
    assert duplicate.status_code == 200 and duplicate.json()["summary"]["status"] == "duplicate"
    second = counts(web_engine)
    assert first["source_resources"] == second["source_resources"] == 1
    assert first["resource_stat_observations"] == second["resource_stat_observations"] == 10
    assert first["core3_live_snapshot_imports"] == second["core3_live_snapshot_imports"] == 1
    assert len(client.get("/api/admin/imports", headers=BEARER).json()["items"]) == 2
    with web_engine.connect() as connection:
        assert connection.execute(text("SELECT external_path FROM source_snapshots")).scalar_one() is None
        assert "snapshot_path" not in connection.execute(text("SELECT parameters FROM import_batches WHERE import_kind='core3_live_resource_snapshot'")).scalar_one()


@pytest.mark.parametrize("mode", ["validate", "import"])
def test_out_of_order_conflict(client, web_engine, mode):
    captured = "2026-10-08T12:00:00Z"
    assert upload(client, snapshot(captured_at=captured)).status_code == 200
    older = upload(client, snapshot(captured_at="2026-10-08T11:00:00Z"), mode)
    assert older.status_code == 409 and older.json()["errors"][0]["code"] == "out_of_order"
    conflict = upload(client, snapshot(captured_at=captured, resources=[]), mode)
    assert conflict.status_code == 409 and conflict.json()["errors"][0]["code"] == "snapshot_conflict"
    assert counts(web_engine)["core3_live_snapshot_imports"] == 1


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"complete": False}, {"source_system": "other"},
    {"source_instance": "other"}, {"captured_at": "no-time"},
    {"resources": [resource_payload(oid="bad")]}, {"resources": [resource_payload(stats={"ER": 1})]},
    {"resources": [resource_payload(stats={"OQ": True})]},
    {"resources": [resource_payload(resource_type="unknown")]},
    {"resources": [resource_payload(planets=["unknown"])]},
])
def test_validation_rules(client, web_engine, changes):
    before = counts(web_engine)
    response = upload(client, snapshot(**changes), "validate")
    assert response.status_code == 422 and response.json()["errors"][0]["code"] == "invalid_snapshot"
    assert counts(web_engine) == before


@pytest.mark.parametrize("content", [b"{broken", b"\xff", b"[]", b"", b'{"schema_version":NaN}'])
def test_invalid_json_and_failure_history(client, web_engine, content):
    response = upload(client, content)
    assert response.status_code == 422
    assert counts(web_engine)["source_resources"] == 0
    history = client.get("/api/admin/imports", headers=BEARER).json()["items"]
    assert history[0]["status"] == "failed"


def test_oversized_file_body_and_chunked_body(client, web_engine):
    response = upload(client, b"x" * 4097)
    assert response.status_code == 413
    response = upload(client, b"x" * 80000)
    assert response.status_code == 413
    response = client.post("/api/admin/snapshots", headers={**BEARER, "Content-Type": "multipart/form-data; boundary=test"},
                           content=iter([b"x" * 40000, b"x" * 40000]))
    assert response.status_code == 413
    assert counts(web_engine)["source_resources"] == 0


def test_auth_all_admin_routes_and_fail_closed(client, web_engine):
    for path in ("/admin", "/api/admin/imports"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Basic malformed"}).status_code == 401
    for path in ("/admin/snapshots", "/api/admin/snapshots"):
        assert client.post(path, content=b"x" * 100000).status_code == 401
    assert client.get("/admin", auth=("test-admin", "wrong")).status_code == 401
    assert client.get("/admin", headers=BEARER).status_code == 401
    assert client.get("/api/admin/imports", headers={"Authorization": "Bearer wrong"}).status_code == 401
    with TestClient(create_app(engine=web_engine, settings=WebSettings())) as disabled:
        assert disabled.get("/admin", auth=BASIC).status_code == 401
        assert disabled.get("/api/admin/imports", headers=BEARER).status_code == 401


def test_csrf_browser_and_basic_api(client, web_engine):
    page = client.get("/admin", auth=BASIC)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    files = {"snapshot": ("snapshot.json", snapshot())}
    for path in ("/admin/snapshots", "/api/admin/snapshots"):
        assert client.post(path, auth=BASIC, files=files, data={"mode": "import"}).status_code == 403
        assert client.post(path, auth=BASIC, files=files, data={"csrf_token": "bad", "mode": "import"}).status_code == 403
    before = counts(web_engine)
    response = client.post("/admin/snapshots", auth=BASIC, files=files,
                           data={"mode": "validate", "csrf_token": token})
    assert response.status_code == 200 and counts(web_engine) == before
    token = client.cookies.get("bellum_csrf")
    assert client.post("/api/admin/snapshots", auth=BASIC, files=files,
                       headers={"Origin": "https://evil.example"}, data={"csrf_token": token}).status_code == 403
    response = client.post("/admin/snapshots", auth=BASIC, files=files,
                           data={"mode": "import", "csrf_token": token})
    assert response.status_code == 200
    assert counts(web_engine)["source_resources"] == 1


def test_database_rollback_on_partial_import(client, web_engine, monkeypatch):
    from app.web import ingestion
    original = ingestion.importer.import_snapshot

    def partial(connection, path):
        original(connection, path)
        raise SQLAlchemyError("synthetic private /server/path credential failure")

    monkeypatch.setattr(ingestion.importer, "import_snapshot", partial)
    response = upload(client, snapshot())
    assert response.status_code == 503
    assert "private" not in response.text and "/server" not in response.text
    after = counts(web_engine)
    assert after["resources"] == after["source_resources"] == after["core3_live_snapshot_imports"] == 0
    assert after["resource_stat_observations"] == 0
    assert client.get("/api/admin/imports", headers=BEARER).json()["items"][0]["status"] == "failed"


def test_public_filtering_sort_pagination_and_rendering(client):
    resources = [resource_payload(oid="101", name="Alpha", stats={"OQ": 0}, planets=["yavin4"]),
                 resource_payload(oid="102", name="Beta", stats={"OQ": 900}, planets=["corellia"])]
    assert upload(client, snapshot(resources=resources)).status_code == 200
    data = client.get("/api/resources", params={"page_size": 1, "sort": "OQ", "direction": "desc"}).json()
    assert data["total"] == 2 and data["pages"] == 2 and data["items"][0]["name"] == "Beta"
    assert client.get("/api/resources", params={"page_size": 1, "page": 2, "sort": "OQ", "direction": "desc"}).json()["items"][0]["name"] == "Alpha"
    for params in ({"name": "alpha"}, {"planet": "yavin4"}, {"max_OQ": 0}, {"min_OQ": 0, "max_OQ": 0}):
        result = client.get("/api/resources", params=params).json()
        assert result["total"] == 1 and result["items"][0]["name"] == "Alpha"
    assert client.get("/api/resources", params={"type": "test_core3_resource_type", "source": "core3", "availability": "current"}).json()["total"] == 2
    assert client.get("/api/resources", params={"min_DR": 0}).json()["total"] == 0
    assert client.get("/api/resources", params={"name": "' OR 1=1 --"}).json()["total"] == 0
    for params in ({"page": 0}, {"page_size": 101}, {"min_OQ": "bad"}, {"sort": "drop table"}, {"unknown": "x"}, {"min_OQ": 10, "max_OQ": 1}):
        assert client.get("/api/resources", params=params).status_code == 422
    resource = client.get("/api/resources", params={"name": "Alpha"}).json()["items"][0]
    assert resource["stats"]["OQ"] == 0 and resource["stats"]["CR"] is None
    assert resource["availability"] == "current"
    assert resource["source_resource_id"] == "101"
    detail = client.get(f"/api/resources/{resource['id']}")
    assert detail.status_code == 200 and detail.json()["observations"]
    for path in ("/", "/resources", f"/resources/{resource['id']}"):
        response = client.get(path)
        assert response.status_code == 200 and "Alpha" in response.text


def test_stale_history_provenance_and_planet_changes(client, web_engine):
    first = datetime.now(timezone.utc) - timedelta(days=3)
    second = first + timedelta(hours=1)
    assert upload(client, snapshot(captured_at=first.isoformat(), resources=[resource_payload(oid="101", planets=["yavin4"]), resource_payload(oid="102")])).status_code == 200
    assert upload(client, snapshot(captured_at=second.isoformat(), resources=[resource_payload(oid="101", planets=["corellia"], stats={"DR": 22})])).status_code == 200
    with web_engine.begin() as conn:
        conn.execute(text("""INSERT INTO source_resources
            (source_instance_id, source_resource_id, source_resource_name, identity_status, confidence)
            SELECT id, 'gh-history', 'Archive Test', 'source_only', 'historical'
            FROM source_instances WHERE code='galaxy-153'"""))
    active = client.get("/api/resources", params={"availability": "current"}).json()["items"]
    assert len(active) == 1 and active[0]["availability"] == "stale"
    assert active[0]["planets"] == ["Corellia"]
    assert active[0]["stats"]["OQ"] is None
    assert client.get("/api/resources", params={"availability": "current", "planet": "yavin4"}).json()["total"] == 0
    historical = client.get("/api/resources", params={"availability": "historical"}).json()["items"]
    assert len(historical) == 2
    gh = next(r for r in historical if r["source_system"] == "galaxy_harvester")
    assert gh["first_observed_at"] is None and gh["availability"] == "historical"
    core = next(r for r in historical if r["source_system"] == "core3")
    detail = client.get(f"/api/resources/{core['id']}").json()
    assert not any(event["event_type"] == "despawned" for event in detail["lifecycle"])
    assert "Archive Test" in client.get("/history").text
    assert "Galaxy Harvester" in client.get(f"/resources/{gh['id']}").text


def test_xss_escape(client):
    assert upload(client, snapshot(resources=[resource_payload(name='<script>alert(1)</script>')])).status_code == 200
    assert "<script>alert(1)</script>" not in client.get("/resources").text
    assert "&lt;script&gt;" in client.get("/resources").text


def test_configuration_validation():
    with pytest.raises(ValueError):
        WebSettings(admin_username="admin")
    with pytest.raises(ValueError):
        WebSettings(admin_username="admin", admin_password="secret", csrf_secret="short")
    with pytest.raises(ValueError):
        WebSettings(max_upload_bytes=0)


def test_simultaneous_duplicate_uploads_are_serialized(client, web_engine):
    from concurrent.futures import ThreadPoolExecutor
    content = snapshot()
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: upload(client, content), range(2)))
    assert all(response.status_code == 200 for response in responses)
    summaries = [response.json()["summary"] for response in responses]
    assert sum(summary.get("status") == "duplicate" for summary in summaries) == 1
    assert counts(web_engine)["core3_live_snapshot_imports"] == 1
    assert counts(web_engine)["resource_stat_observations"] == 10


def test_expired_csrf_is_rejected(client):
    import time
    from itsdangerous import TimestampSigner, URLSafeTimedSerializer

    class OldSigner(TimestampSigner):
        def get_timestamp(self):
            return int(time.time()) - 7200

    client.get("/admin", auth=BASIC)
    expired = URLSafeTimedSerializer("s" * 40, salt="snapshot-csrf", signer=OldSigner).dumps("old-token")
    client.cookies.set("bellum_csrf", expired, domain="testserver.local", path="/")
    response = client.post("/admin/snapshots", auth=BASIC,
        files={"snapshot": ("snapshot.json", snapshot())}, data={"mode": "import", "csrf_token": expired})
    assert response.status_code == 403


def test_generic_import_failure_is_audited_and_sanitized(client, web_engine, monkeypatch):
    from app.web import ingestion

    def broken(connection, path):
        raise RuntimeError("secret host credential /internal/path")

    monkeypatch.setattr(ingestion.importer, "import_snapshot", broken)
    response = upload(client, snapshot())
    assert response.status_code == 500 and "secret" not in response.text
    assert counts(web_engine)["source_resources"] == 0
    assert client.get("/api/admin/imports", headers=BEARER).json()["items"][0]["status"] == "failed"
