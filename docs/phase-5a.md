# Phase 5A — website foundation and snapshot ingestion

## Architecture and scope

The website extends the existing PostgreSQL schema and Phase 4B snapshot
validator/importer. Integration inspection is recorded in
[phase-5a-integration.md](phase-5a-integration.md). No new migration is required.
The Core3 schema-v1 JSON contract is unchanged. The application neither reads
the live Core3 database nor contacts Galaxy Harvester. Historical data is read
from previously imported database observations.

Jinja pages use a responsive charcoal/amber interface with local CSS, system
fonts and no JavaScript or external artwork. Missing statistics remain null;
explicit zero remains zero. Public IDs in these routes are source_resources.id
(integer), not canonical resources.id or Core3 OIDs. This also exposes historical
source-only records without automatically linking GH and Core3 identities.

## Local setup

Use Python 3.11+ and PostgreSQL 16+. Work on `phase-5a-website-foundation`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
docker compose up -d --wait
$env:BELLUM_DATABASE_URL='postgresql+psycopg://bellum_resources:change_me@127.0.0.1:5432/bellum_resources'
python -m alembic upgrade head
```

These commands target a local development database. Existing resource type
indexing and historical import commands remain available; see the existing
indexer/importer documentation. A seeded but otherwise empty database renders
usable empty pages. Uploads referencing unknown types or planets are rejected,
so index the authoritative Core3 resource types before importing real snapshots.

## Environment configuration

| Variable | Purpose/default |
| --- | --- |
| `BELLUM_DATABASE_URL` | Required PostgreSQL SQLAlchemy psycopg URL |
| `BELLUM_ADMIN_USERNAME` | Browser administrator username; empty disables Basic auth |
| `BELLUM_ADMIN_PASSWORD` | Browser administrator password; must accompany username |
| `BELLUM_UPLOAD_API_TOKEN` | Independent bearer token for machine uploads/history; empty disables bearer access |
| `BELLUM_CSRF_SECRET` | At least 32 characters when browser administration is enabled |
| `BELLUM_MAX_UPLOAD_BYTES` | Snapshot file cap, default 8388608 (8 MiB) |
| `BELLUM_SNAPSHOT_FRESHNESS_HOURS` | Positive freshness threshold, default 24 |
| `BELLUM_SECURE_COOKIES` | `true` default; `false` only for loopback HTTP development |
| `TEST_DATABASE_URL` | Disposable loopback PostgreSQL database, name must end in `_test` |

Secrets are not provided in the repository. Set them using a secret manager,
protected service environment file, or local environment. `.env.example` is a
reference; the app does not load `.env` automatically. Generate independent
secrets locally (do not paste them into source):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Set BELLUM_ADMIN_USERNAME, BELLUM_ADMIN_PASSWORD, BELLUM_UPLOAD_API_TOKEN,
# and BELLUM_CSRF_SECRET to your private values.
$env:BELLUM_SECURE_COOKIES='false'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/`. FastAPI serves both backend and frontend.
Visit `/admin` and use the browser's HTTP Basic authentication prompt. There is
one environment-configured administrator, no account table or registration.
Both username and password are compared in constant time using UTF-8 bytes,
following [FastAPI's Basic authentication guidance](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/).
Use HTTPS outside loopback. Rotate browser password and bearer token externally.
Browsers cache Basic credentials; there is no application logout button.

Browser upload POSTs require the signed one-hour CSRF token issued by `/admin`
and the matching HttpOnly, SameSite=Strict cookie. Origin is checked when sent.
This also applies to Basic-authenticated API uploads. Bearer uploads have no
ambient browser credentials and do not require a CSRF cookie. A bearer token
cannot authenticate the HTML admin pages. All admin routes fail closed without
valid credentials. Upload authentication happens before reading or parsing
the body.

## Pages and APIs

| Route | Purpose |
| --- | --- |
| `GET /` | Search entry, current overview, recent observations, snapshot freshness |
| `GET /resources` | Filtered resource directory with ten statistic columns |
| `GET /resources/{resource_id}` | Source identity, qualities, planets, observation/lifecycle evidence |
| `GET /history` | Historical GH records and inactive Core3 resources |
| `GET /admin` | Protected upload/validation/import console and recent attempts |
| `GET /api/resources` | Paginated read-only resources and snapshot status |
| `GET /api/resources/{resource_id}` | Resource detail with up to 100 observations and 100 lifecycle events |
| `GET /api/resource-types` | Canonical type choices (limit 5000) |
| `GET /api/planets` | Known planet choices |
| `GET /api/snapshot-status` | Latest complete successful current snapshot and stale flag |
| `POST /api/admin/snapshots` | Authenticated multipart validation/import |
| `GET /api/admin/imports` | Protected most recent 30 Core3 import attempts |
| `POST /admin/snapshots` | Protected CSRF-checked HTML upload results |
| `GET /health/live` | Process liveness, independent of database |
| `GET /health/ready` | Database connectivity and snapshot ledger table readiness |

`/api/resources` returns `items`, `total`, `page`, `page_size`, `pages`, and
`snapshot_status`. Items contain source identity, name/type, planets, all ten
statistic values, availability, first/last observation timestamps, current_as_of
and absent_as_of. Filter parameters: `name` (case-insensitive literal substring),
`type` (exact canonical slug or source type key), `planet` (slug),
`availability=all|current|historical`, `source=all|core3|galaxy_harvester`,
`min_CR`/`max_CR` through all ten stat codes, `sort=name|recent|CR|...|UT`,
`direction=asc|desc`, `page` (1–10000), `page_size` (1–100, default 25).
Unknown parameters, invalid numbers and inverted ranges return 422. Statistics
filters and sorting use the latest observation per source resource/code; a
missing latest stat is not backfilled from an older Core3 snapshot.
Queries bind values; sort expressions/operators come from validated allowlists.

```text
/api/resources?planet=corellia&availability=current&min_OQ=800&sort=OQ&direction=desc&page_size=25
```

Current results include active resources from the last accepted snapshot even
if stale, but their `availability` becomes `stale`, never confirmed `current`.
GH is always historical. Core3 planets come from the last-seen snapshot rather
than a union of prior locations. Historical planet names are observation
evidence, not proof of present availability. `absent_as_of` indicates absence
at a complete snapshot, never an exact despawn. The history view lists inactive
resources; detail pages retain earlier observations for resources still active.

## Upload operations

Choose a JSON file on `/admin`, then Validate snapshot or Import snapshot.
Validation results and import summaries appear above the form. Latest snapshot
time/source and the last 30 import attempts are visible. Dry-run calls Phase
4B `dry_run` inside a read-only database transaction; it writes no attempt,
planet, resource or ledger records, including on failure.

Machine clients submit `snapshot` file and `mode=validate|import`; omitted mode
defaults to validate. Use a protected environment token rather than literals:

```powershell
curl.exe -H "Authorization: Bearer $env:BELLUM_UPLOAD_API_TOKEN" -F 'mode=validate' -F 'snapshot=@C:/private/snapshot.json;type=application/json' http://127.0.0.1:8000/api/admin/snapshots
curl.exe -H "Authorization: Bearer $env:BELLUM_UPLOAD_API_TOKEN" -F 'mode=import' -F 'snapshot=@C:/private/snapshot.json;type=application/json' http://127.0.0.1:8000/api/admin/snapshots
```

Success is `{ok:true, mode, summary}`. Upload failures use
`{ok:false, errors:[{code,message}]}`. Authentication/CSRF/multipart errors use
`{errors:[{code,message}]}`. Codes include `invalid_snapshot`, `snapshot_conflict`,
`out_of_order`, `upload_too_large`, and `import_failed`; status codes are
422, 409, 413, and 500/503 as appropriate.

The complete multipart body is bounded to file cap + 64 KiB overhead before
multipart parsing, including chunked bodies. File size is separately checked.
Uploads use generated temporary paths (never user filenames), cleaned after
processing. Resource mutations and successful importer ledger/batch updates
commit atomically. Failed mutation transactions roll back before a sanitized
failure entry is recorded in the existing import_batches table. Duplicates
return the original ledger summary and record a duplicate attempt without
adding resource observations. Authentication/CSRF failures, malformed multipart
requests and body-limit rejections before parsing are not recorded in the DB;
use restricted reverse-proxy access logs for those. Database outage may prevent
failure audit persistence. Logs must remain private; public errors contain no
SQL, credentials, tracebacks or internal paths.

Web imports take a per-source PostgreSQL transaction advisory lock and reject
older/conflicting captures before invoking the importer. Transaction-level locks
are released at transaction end ([PostgreSQL locking documentation](https://www.postgresql.org/docs/current/explicit-locking.html)).
The unchanged CLI may still import older snapshots as historical evidence; it
does not share the new web adapter lock. Run offline imports in an exclusive
maintenance window, not concurrently with web imports.

## Automated tests

Never use production or a development data database: existing regression tests
perform downgrade/upgrade cycles. The shared fixture validates loopback host,
`_test` suffix and a target distinct from BELLUM_DATABASE_URL before migration.
Use the dedicated tmpfs service and nonproduction test credentials:

```powershell
docker compose -p bellum-phase5a-test -f docker-compose.test.yml up -d --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://bellum_test:isolated_local_test@127.0.0.1:55435/bellum_resources_test'
python -m pytest -q
docker compose -p bellum-phase5a-test -f docker-compose.test.yml down
```

The test service binds only 127.0.0.1. Tests use synthetic snapshots; web tests
truncate only fixture data in that disposable test DB. Existing regression
coverage is retained. New tests cover response contracts, filters/sorting,
pagination, empty/populated templates, stale/provenance semantics, latest planet
and missing-stat semantics, escaping, auth, CSRF, upload limits including chunked
requests, structural/reference validation, dry-run immutability, successful and
duplicate imports, conflicts/order and rollback after partial writes.

Validated on October 8, 2026: **97 passed**, no skipped tests, in 4.44 seconds.
The installed Starlette emits one warning that its httpx TestClient transport
is deprecated; it does not affect test outcomes. `pip check` reports no broken
requirements. Tested environment: Python 3.14, FastAPI 0.143.0, Starlette 1.7.0,
SQLAlchemy 2.1.1, psycopg 3.3.6 and PostgreSQL 16.

Headless browser QA checked desktop (1440px), tablet (768px), and mobile (375px)
layouts using synthetic local data. Search, history and historical detail
pages had zero automated WCAG 2A/AA findings; mobile search/detail document width
matched the viewport. Screenshots are local ignored artifacts under
`.phase5a-output/`. A separate design reviewer approved reviewed screenshots;
a different-provider model was unavailable. Browser visual coverage does not
include the authenticated admin console; its rendering/actions are covered by
integration tests. Automated accessibility checks are not a full manual audit.

For repeatable local visual QA after running migrations/tests, use the
development-only helper against the **dedicated test service**:

```powershell
$env:PYTHONPATH='.'
python scripts/phase5a_preview.py
```

It resets resource/import data only in the fixed loopback test database, seeds
explicitly synthetic records, and serves `http://127.0.0.1:8005`. Do not run it
concurrently with pytest. Stop with Ctrl+C. Browser admin remains disabled
unless the normal private environment variables are configured.

## Change inventory

Added application entry point `app/main.py`; web settings, security, ingestion
adapter and read queries in `app/web/`; six Jinja templates and local CSS;
`tests/web/` fixtures and integration tests; `docker-compose.test.yml`;
`scripts/phase5a_preview.py`; and the Phase 5A guide/integration inspection.
Updated `pyproject.toml` dependencies/package data, README, `.env.example`,
`.gitignore`, the test database safety fixture and the development Compose port
binding. Existing models, migrations and Phase 4B parser/importer are unchanged.

## Deployment preparation — no live changes performed

Provision a separate application service at resources.bellumgero.net after code
review. Keep PostgreSQL on a private network/socket; do not expose its port.
The development Compose port is loopback-only. Run migrations as a separate
privileged deployment task, then use a dedicated runtime role with only the
permissions needed by reads and the existing importer. Set secrets outside
Git. Configure backups, restricted log access and process supervision. Run
Uvicorn on loopback behind Nginx; trust forwarded headers only from that proxy:

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Example Nginx excerpt (adapt TLS certificate paths locally; do not install this
configuration on Live as part of Phase 5A):

```nginx
# http context
limit_req_zone $binary_remote_addr zone=resource_admin:10m rate=10r/m;

server {
    listen 443 ssl;
    server_name resources.bellumgero.net;
    ssl_certificate /etc/letsencrypt/live/resources.bellumgero.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/resources.bellumgero.net/privkey.pem;
    client_max_body_size 9m;
    client_body_timeout 20s;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location ~ ^/(admin|api/admin)(/|$) {
        limit_req zone=resource_admin burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Add an HTTP-to-HTTPS redirect separately. Keep secure cookies enabled. Apply
admin IP restrictions/VPN if appropriate. Liveness is `/health/live`; readiness
is `/health/ready` and returns 503 when database access fails. Readiness checks
the ledger table, not full migration consistency or importer reference data.
Public server errors are sanitized. Adjust proxy body cap when changing file
cap and configure a database statement timeout for the runtime role.

## Limits and Phase 5B

- Synchronous ingestion is intended for bounded snapshots, not background jobs.
- One private admin; no user accounts, app logout, in-app password reset or
  application rate limiter. Proxy throttling is required for deployment.
- Public search uses direct SQL queries. Benchmark realistic archive volumes,
  query plans and add nondestructive indexes/cache as needed in Phase 5B.
- Resource observations and lifecycle histories are capped at 100 entries;
  full history pagination is a Phase 5B extension. Resource type choices cap at
  5000. All ten stats remain filterable without inventing absent values.
- Add scheduled private snapshot delivery, freshness alerting, richer type
  ancestry search and crafting/schematic compatibility in Phase 5B.
- No production hosting registration, infrastructure modification, live export,
  production database write, commit, push or merge is part of this work.
