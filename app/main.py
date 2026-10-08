from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Path as PathParameter
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.session import make_engine
from app.importing.core3_live.snapshot import STAT_CODES
from app.web import queries
from app.web.ingestion import UploadRejected, ingest, record_attempt
from app.web.security import authenticate, check_csrf, issue_csrf
from app.web.settings import WebSettings

logger = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))


class UploadBoundary:
    """Authenticate and cap the entire body before multipart parsing/spooling."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        upload = path in {"/admin/snapshots", "/api/admin/snapshots"} and scope["method"] == "POST"
        if upload:
            request = Request(scope)
            try:
                authenticate(request, browser_only=path.startswith("/admin"))
            except HTTPException as exc:
                return await JSONResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)(scope, receive, send)
            limit = self.settings.max_upload_bytes + 65536
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    oversized = int(content_length) > limit
                    if int(content_length) < 0:
                        raise ValueError()
                except ValueError:
                    return await JSONResponse({"errors": [{"code": "invalid_length", "message": "Invalid Content-Length"}]}, status_code=400)(scope, receive, send)
                if oversized:
                    return await self.too_large(scope, receive, send)
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await self.too_large(scope, receive, send)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            delivered = False
            original_receive = receive

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()
            receive = replay
        # Small hardening policy, no scripts or third-party assets required.
        async def headers(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                         (b"referrer-policy", b"same-origin"),
                         (b"content-security-policy", b"default-src 'self'; script-src 'none'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")]
                if path.startswith(("/admin", "/api/admin")):
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)
        await self.app(scope, receive, headers)

    async def too_large(self, scope, receive, send):
        response = JSONResponse({"ok": False, "errors": [{"code": "upload_too_large", "message": "Upload exceeds configured size limit"}]}, status_code=413)
        await response(scope, receive, send)


def create_app(*, engine=None, settings=None):
    settings = settings or WebSettings.from_env()
    owns_engine = engine is None

    @asynccontextmanager
    async def lifespan(application):
        if owns_engine:
            application.state.engine = make_engine()
        try:
            yield
        finally:
            if owns_engine:
                application.state.engine.dispose()

    application = FastAPI(title="Bellum Gero Resources", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.engine = engine
    application.state.web_settings = settings
    application.add_middleware(UploadBoundary, settings=settings)
    application.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

    @application.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        return JSONResponse({"errors": [{"code": "invalid_request", "message": error["msg"],
            "field": ".".join(str(part) for part in error["loc"])} for error in exc.errors()]}, status_code=422)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"errors": [{"code": "request_error", "message": str(exc.detail)}]}
        return JSONResponse(jsonable_encoder(detail), status_code=exc.status_code, headers=exc.headers)

    @application.exception_handler(SQLAlchemyError)
    async def db_error(request, exc):
        logger.error("Database request failed", exc_info=exc)
        return JSONResponse({"errors": [{"code": "database_unavailable", "message": "Resource database is temporarily unavailable"}]}, status_code=503)

    @application.exception_handler(Exception)
    async def internal_error(request, exc):
        logger.error("Website request failed", exc_info=exc)
        return JSONResponse({"errors": [{"code": "internal_error", "message": "Request could not be completed"}]}, status_code=500)

    def status(connection):
        return queries.snapshot_status(connection, settings.freshness_hours)

    def render(request, template, context, code=200):
        common = {"request": request, "title": "Bellum Gero Resources", "stat_codes": list(STAT_CODES), "result": None}
        context = {**context, "result": jsonable_encoder(context.get("result"))}
        if "history" in context:
            context["history"] = jsonable_encoder(context["history"])
        return templates.TemplateResponse(request=request, name=template, context={**common, **context}, status_code=code)

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        with application.state.engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM core3_live_snapshot_imports LIMIT 1"))
        return {"status": "ready"}

    @application.get("/api/snapshot-status")
    def api_status():
        with application.state.engine.connect() as connection:
            return status(connection)

    @application.get("/api/resources")
    def api_resources(request: Request):
        filters = queries.parse_filters(request.query_params)
        with application.state.engine.connect() as connection:
            current_status = status(connection)
            return {**queries.search(connection, filters, current_status), "snapshot_status": current_status}

    @application.get("/api/resources/{resource_id}")
    def api_detail(resource_id: int = PathParameter(ge=1, le=9223372036854775807)):
        with application.state.engine.connect() as connection:
            return queries.resource_detail(connection, resource_id, status(connection))

    @application.get("/api/resource-types")
    def api_types():
        with application.state.engine.connect() as connection:
            return {"items": queries.reference_data(connection, "types")}

    @application.get("/api/planets")
    def api_planets():
        with application.state.engine.connect() as connection:
            return {"items": queries.reference_data(connection, "planets")}

    @application.get("/")
    def homepage(request: Request):
        with application.state.engine.connect() as connection:
            current_status = status(connection)
            data = queries.search(connection, queries.parse_filters({"sort": "recent", "direction": "desc", "page_size": "8"}), current_status)
            current = queries.search(connection, queries.parse_filters({"availability": "current", "page_size": "1"}), current_status)
            return render(request, "index.html", {"status": current_status, "data": data, "current_count": current["total"]})

    @application.get("/resources")
    @application.get("/history")
    def resources(request: Request):
        params = dict(request.query_params)
        historical = request.url.path == "/history"
        if historical:
            params["availability"] = "historical"
        filters = queries.parse_filters(params)
        with application.state.engine.connect() as connection:
            current_status = status(connection)
            return render(request, "resources.html", {"status": current_status,
                "data": queries.search(connection, filters, current_status), "filters": filters, "historical": historical,
                "types": queries.reference_data(connection, "types"), "planets": queries.reference_data(connection, "planets")})

    @application.get("/resources/{resource_id}")
    def details(request: Request, resource_id: int = PathParameter(ge=1, le=9223372036854775807)):
        with application.state.engine.connect() as connection:
            current_status = status(connection)
            resource = queries.resource_detail(connection, resource_id, current_status)
            return render(request, "detail.html", {"status": current_status, "resource": resource,
                "observations": resource["observations"], "lifecycle": resource["lifecycle"]})

    def admin_response(request, result=None, code=200):
        with application.state.engine.connect() as connection:
            context = {"status": status(connection), "history": queries.import_history(connection), "result": result, "csrf_token": issue_csrf(settings)}
        response = render(request, "admin.html", context, code)
        response.set_cookie("bellum_csrf", context["csrf_token"], max_age=3600, httponly=True, secure=settings.secure_cookies, samesite="strict", path="/")
        return response

    @application.get("/admin")
    def admin(request: Request):
        authenticate(request, browser_only=True)
        return admin_response(request)

    @application.get("/api/admin/imports")
    def history(request: Request):
        authenticate(request)
        with application.state.engine.connect() as connection:
            return {"items": queries.import_history(connection)}

    @application.post("/admin/snapshots")
    @application.post("/api/admin/snapshots")
    async def upload(request: Request):
        browser = request.url.path.startswith("/admin")
        auth_kind = authenticate(request, browser_only=browser)
        try:
            async with request.form(max_files=1, max_fields=3, max_part_size=65536) as form:
                if auth_kind == "basic":
                    check_csrf(request, str(form.get("csrf_token", "")))
                mode = str(form.get("mode", "validate"))
                if mode not in {"validate", "import"}:
                    raise UploadRejected("invalid_mode", "Mode must be validate or import")
                snapshot = form.get("snapshot")
                if not isinstance(snapshot, UploadFile):
                    raise UploadRejected("missing_snapshot", "Select a JSON snapshot file")
                content = await snapshot.read(settings.max_upload_bytes + 1)
                if len(content) > settings.max_upload_bytes:
                    error = UploadRejected("upload_too_large", "Snapshot exceeds configured size limit", 413)
                    if mode == "import":
                        await run_in_threadpool(record_attempt, application.state.engine, "failed", error.result())
                    raise error
                result = await run_in_threadpool(ingest, application.state.engine, content, dry_run=mode == "validate")
        except UploadRejected as exc:
            if browser:
                return await run_in_threadpool(admin_response, request, exc.result(), exc.status)
            return JSONResponse(exc.result(), status_code=exc.status)
        if browser:
            return await run_in_threadpool(admin_response, request, result)
        return JSONResponse(jsonable_encoder(result))

    return application


app = create_app()
