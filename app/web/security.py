import base64
import binascii
import secrets

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer


def same(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def authenticate(request: Request, *, browser_only: bool = False) -> str:
    settings = request.app.state.web_settings
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if not browser_only and scheme.lower() == "bearer":
        if settings.api_token and same(credential, settings.api_token):
            return "bearer"
    if scheme.lower() == "basic" and settings.admin_username:
        try:
            username, password = base64.b64decode(credential, validate=True).decode("utf-8").split(":", 1)
            username_ok = same(username, settings.admin_username)
            password_ok = same(password, settings.admin_password)
            if username_ok and password_ok:
                return "basic"
        except (ValueError, UnicodeError, binascii.Error):
            pass
    raise HTTPException(401, detail={"errors": [{"code": "unauthorized", "message": "Administrator authentication required"}]},
                        headers={"WWW-Authenticate": 'Basic realm="Bellum Gero Resources", charset="UTF-8"'})


def issue_csrf(settings) -> str:
    return URLSafeTimedSerializer(settings.csrf_secret, salt="snapshot-csrf").dumps(secrets.token_urlsafe(32))


def check_csrf(request: Request, token: str):
    cookie = request.cookies.get("bellum_csrf", "")
    settings = request.app.state.web_settings
    try:
        if not settings.csrf_secret or not token or not same(token, cookie):
            raise BadSignature("missing or mismatched token")
        URLSafeTimedSerializer(settings.csrf_secret, salt="snapshot-csrf").loads(token, max_age=3600)
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if origin and not same(origin, expected):
            raise BadSignature("cross-origin request")
    except BadSignature:
        raise HTTPException(403, detail={"errors": [{"code": "csrf", "message": "Reload the admin page and try again"}]})
