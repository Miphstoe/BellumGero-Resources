"""Proxy middleware semantics and real application HTTPS/CSRF behavior."""

from dataclasses import replace
import json
import re

from fastapi.testclient import TestClient
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import create_app
from tests.web.test_website import snapshot


TRUSTED_PEER = "172.30.0.1"
FORWARDED = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.10"}


def upstream_transport(app):
    """Browser uses HTTPS; Nginx-to-Uvicorn transport itself is plain HTTP."""
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED_PEER)

    async def transport(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "scheme": "http"}
        await proxy(scope, receive, send)

    return transport


@pytest.mark.parametrize("peer, scheme, client_ip", [
    (TRUSTED_PEER, "https", "203.0.113.10"),
    ("172.30.0.9", "http", "172.30.0.9"),
    ("127.0.0.1", "http", "127.0.0.1"),
])
def test_forwarded_headers_require_exact_immediate_peer(peer, scheme, client_ip):
    async def inspect_scope(scope, receive, send):
        payload = json.dumps({"scheme": scope["scheme"], "client": scope["client"][0]}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})

    # No lifespan calls are needed for this scope-only ASGI app.
    client = TestClient(upstream_transport(inspect_scope), client=(peer, 50000))
    assert client.get("/", headers=FORWARDED).json() == {"scheme": scheme, "client": client_ip}


@pytest.mark.parametrize("peer, expected_scheme, expected_status", [
    (TRUSTED_PEER, "https", 200),
    ("172.30.0.9", "http", 403),
])
def test_proxy_scheme_controls_redirects_and_https_origin_without_weakening_cookies(
    web_engine, web_settings, peer, expected_scheme, expected_status,
):
    settings = replace(web_settings, secure_cookies=True)
    app = create_app(engine=web_engine, settings=settings)
    with TestClient(upstream_transport(app), base_url="https://resources.bellumgero.net",
                    client=(peer, 50000), headers=FORWARDED) as client:
        redirect = client.get("/resources/", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == f"{expected_scheme}://resources.bellumgero.net/resources"
        assert client.get("/admin").status_code == 401
        auth = (settings.admin_username, settings.admin_password)
        admin = client.get("/admin", auth=auth)
        assert admin.status_code == 200
        assert "; Secure" in admin.headers["set-cookie"]
        assert "SameSite=strict" in admin.headers["set-cookie"]
        token = re.search(r'name="csrf_token" value="([^"]+)"', admin.text).group(1)
        upload = {"snapshot": ("snapshot.json", snapshot(resources=[]), "application/json")}
        data = {"mode": "validate", "csrf_token": token}
        response = client.post("/api/admin/snapshots", auth=auth, files=upload, data=data,
                               headers={"Origin": "https://resources.bellumgero.net"})
        assert response.status_code == expected_status
        if expected_status == 200:
            assert response.json()["mode"] == "validate"
        assert client.post("/api/admin/snapshots", auth=auth, files=upload, data=data,
                           headers={"Origin": "https://evil.example"}).status_code == 403
