# Phase 6B local validation results

## Proxy-trust review follow-up

Replaced wildcard `FORWARDED_ALLOW_IPS` with required external
`BELLUM_FORWARDED_ALLOW_IPS`. No production peer address was assumed. The runbook
now includes raw-peer measurement, exact-IP configuration, HTTPS redirect checks,
and revalidation after network recreation. The existing Nginx example already
overwrites the required headers and needed no changes.

Repeated the disposable Docker smoke harness with loopback-only bootstrap trust,
then measured its raw access-log peer through the host-published port. For this
local run the peer was `172.21.0.1`; **do not copy that address to production**.
Recreated the app with only that exact IP trusted. Results:

- Compose configuration passed; an empty required trust setting was rejected.
- Host-port requests with Nginx-style headers produced an HTTPS slash redirect.
- A second container connecting directly on the frontend bridge could not spoof
  HTTPS: its identical forwarded headers produced an HTTP redirect.
- Authenticated validation uploads with a secure CSRF cookie and matching HTTPS
  Origin succeeded. A different HTTPS Origin was rejected with 403.
- Existing health, authentication, actual snapshot import, persistence, outage,
  and backup/restore smoke checks passed again.
- Added five tests in `tests/web/test_proxy_trust.py` for exact-peer trust, forwarded
  client identity, redirects, secure cookies, and the real application's Origin/CSRF
  handling. Focused proxy/website run: **35 passed**. Full suite: **445 passed**,
  with the existing TestClient deprecation warning. `git diff --check` passed.

Commands: the same isolated Compose smoke harness described below;
`python -m pytest tests/web/test_proxy_trust.py tests/web/test_website.py -q --tb=short`;
and `python -m pytest -q --tb=short`, using the existing isolated `_test` database.
Temporary resources and generated secrets were removed afterward. The original
Phase 6B results below are retained as historical validation evidence.

Remaining boundary: exact-IP trust identifies the host/gateway path, not Nginx as
a process. Local processes and gateway-relayed traffic can share that identity.
Target-host peer measurement and actual host Nginx/HTTPS acceptance remain future
deployment checks. No production server, DNS, or Nginx installation was modified.

Validated on October 9, 2026 using Docker Desktop's Linux engine (28.3.0).
No production connections, DNS changes, host Nginx changes, or deployments occurred.

## Build and configuration

`docker build -t bellum-gero-resources:phase6b-validation .` succeeded with the
digest-pinned Python 3.12 base and `requirements-production.txt` constraints.
The application image manifest was
`sha256:67c9b2b385494fdf03dc2b717177905164f793c9af47c8715e75f509b54ceff4`.
`python -m pip check` succeeded inside the running application container.

The smoke harness generated independent random local credentials in an ignored
environment file, chose an unused loopback port, and used this Compose prefix:

```text
docker compose --env-file .phase6b-output/.env.validation -p bellum-phase6b-validation -f docker-compose.prod.yml
```

`config --quiet` passed. The harness inspected resolved configuration in memory
without printing credentials and confirmed URL/POSTGRES credential consistency,
loopback-only app publishing, no database published ports, and an internal backend.
It inspected the running app's UID 10001, read-only filesystem, dropped capabilities,
and Docker healthy status. PostgreSQL image used:
`postgres@sha256:0ea6700a3b4f0ae6ce746519073558aed4d88a79d8d07622a9a644946c7319c4`.

The initial local smoke test identified that an app attached only to an internal
network had no effective published host port on this engine. Final Compose adds
an app-only frontend bridge; PostgreSQL remains exclusively on the internal backend.

## Container and database checks

The disposable harness (`.venv/Scripts/python.exe .phase6b-output/validate.py`)
completed all checks below successfully:

| Commands/checks | Actual result |
| --- | --- |
| `up -d --wait postgres`, `up -d --no-build app` before migrations | Liveness 200; readiness sanitized 503 for missing schema |
| `run --rm --no-deps app alembic upgrade head` | Fresh initialization succeeded |
| `run --rm --no-deps app alembic current` | `20260929_0002 (head)` |
| `up -d --wait --no-build app` | Docker readiness healthy |
| GET `/`, `/resources`, `/history`, `/api/resources`, `/api/snapshot-status` | All 200 |
| Missing/wrong Basic or Bearer authentication | 401 |
| Configured Basic `/admin` and Bearer `/api/admin/imports` | 200 |
| Basic snapshot upload without CSRF | 403 |
| Admin CSRF cookie | Secure and SameSite=strict |
| Authenticated invalid snapshot | 422 |
| Valid canonical empty Core3 snapshot with explicit `mode=import` | 200, current snapshot advanced, fresh status persisted |
| `restart app postgres` | Readiness recovered; imported snapshot status unchanged |
| `down` followed by `up -d --wait --no-build` | Container recreation retained imported snapshot through named volume |
| `stop postgres` | Liveness 200, readiness sanitized 503 |
| `up -d --wait postgres` after outage | Readiness returned to 200 |
| `pg_dump -Fc --no-owner --no-acl` | Valid binary `PGDMP` archive |
| `pg_restore --list` | Archive contained application schema and snapshot ledger |
| `pg_restore --exit-on-error --single-transaction` to fresh `phase6b_recovery_test` | Restored successfully; imported snapshot ledger count was 1 |

The empty canonical snapshot was a controlled persistence fixture, not a native
exporter override. Native snapshot reduction safeguards remain unchanged and
covered by the regression suite. Browser login through actual HTTPS was not tested;
secure cookie attributes were checked over local HTTP without disabling them.

The harness removed its own project containers/network/volume and generated
environment file. It did not touch existing local databases. Backup bytes stayed
in memory. Temporary self-signed certificate files were also removed.

## Nginx example

The exact proposed virtual host was mounted read-only into a disposable
`nginx:stable-alpine` container with `--network none` and temporary local self-signed
certificate files at the example paths. `nginx -t` succeeded. No proxy server was
started or host Nginx configuration installed. This checks syntax, not real DNS,
certificate issuance/renewal, public TLS, or the target host's Nginx version.

## Automated regressions

Started the repository's existing alternate isolated test service:

```powershell
docker compose -p bellum-adapter-hardening -f .phase5a-output/adapter-hardening-test.yml up -d --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://bellum_test:isolated_local_test@127.0.0.1:55437/bellum_resources_test'
./.venv/Scripts/python.exe -m pytest -q --tb=short
git diff --check
```

Result: **440 passed**, with one existing Starlette/TestClient deprecation warning.
The pytest run used the existing host virtual environment; the production image's
pinned Linux runtime was separately exercised by migration, HTTP, persistence,
outage, and restore smoke checks. Whitespace validation passed. Git reported only
its normal LF-to-CRLF working-copy notice. The temporary test service was removed
after validation.

## Before an eventual deployment

No local application blocker remains. Still required: operator review/approval,
protected production secrets, target-host port/disk/Docker checks, reviewed backup
storage and retention arrangements, a separate hostname/DNS decision, a valid
certificate with renewal, and approved installation/testing of the standalone
Nginx host. Existing host services and configuration have not been inspected or
changed. Neither application commits nor pushes were performed.
