# Bellum Gero Resources production deployment preparation

This is an operator runbook for a future, separately approved deployment on
`cloud32674` (Ubuntu 24.04) at the proposed hostname `resources.bellumgero.net`.
Phase 6B implements and validates locally only. Do not execute the host, DNS,
certificate, or Nginx steps until deployment is approved. No game-server changes
are required. Keep the website, Wiki, Portal, launcher, and status API untouched.

## Architecture

Nginx terminates HTTPS on the website host and proxies to `127.0.0.1:18080`.
The `app` container runs Python 3.12/Uvicorn as UID/GID 10001 with an installed
application wheel, packaged templates/static assets, and Alembic migrations.
PostgreSQL 16 is reachable only over this project's internal `backend` network;
it has **no published port**. Only the app also joins a separate frontend bridge
so Docker can publish its loopback port. Its data lives in the project-scoped
`postgres-data` named volume. Keep the Compose project name constant across updates.

The application has a read-only root filesystem, a bounded temporary directory,
no Linux capabilities, no privilege escalation, and a 30-second shutdown grace
period. Uvicorn receives SIGTERM through Docker's init process. Both services
restart unless stopped. Migrations never run automatically at startup.

The Docker build uses a digest-pinned Python base and pinned runtime constraints
in `requirements-production.txt`. The build context is an allowlist, excluding
Git, virtual environments, test fixtures, caches, archives, output, and secrets.
The PostgreSQL major-version tag receives patch updates: record and retain the
actual image digest with each release; approve patch pulls deliberately.
For exact release reproduction retain the built application image by digest or
`docker image save`, plus its source revision and database migration revision.
Build tooling/wheel bytes are not guaranteed bit-for-bit identical on rebuild.

## Environment configuration

Prerequisites: Docker Engine with Compose v2, access to the reviewed source,
adequate persistent disk and backup space, and an unused loopback port.
The following commands are intended for Bash on the eventual Ubuntu host.

```bash
cp .env.production.example .env.production
chmod 600 .env.production
# Edit .env.production using your secure editor.
openssl rand -hex 32   # run separately for each password/token/CSRF secret
```

Fill all empty required values. Never commit or paste the populated file into
logs/issues. `.gitignore` excludes `.env.*` except the example templates.
Use URL-safe usernames, database names, and generated hexadecimal passwords.
The example derives `BELLUM_DATABASE_URL` from the same `POSTGRES_*` values:
`postgresql+psycopg://USER:PASSWORD@postgres:5432/DATABASE`. For special characters,
percent-encode the password in an explicit URL and keep its decoded value equal
to `POSTGRES_PASSWORD`. Do not use a host loopback address inside the app container.

Compose interpolation and container configuration must use the **same file**.
For another path, set `BELLUM_ENV_FILE` in that file to its absolute path and
pass that same path to `--env-file`. Shell variables override Compose env-file
values: remove stale `POSTGRES_*` and `BELLUM_*` exports before running commands.
Do not run `docker compose config` without `--quiet` on a populated file: it can
print secrets. Required credentials are checked during Compose interpolation;
application settings also validate paired admin credentials and CSRF length.

Configuration:

| Variable | Production meaning/default |
| --- | --- |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Initial database and credentials; password required |
| `BELLUM_DATABASE_URL` | SQLAlchemy psycopg URL for app **and migrations** |
| `BELLUM_ADMIN_USERNAME`, `BELLUM_ADMIN_PASSWORD` | Browser administrator authentication; both required |
| `BELLUM_UPLOAD_API_TOKEN` | Separate required API bearer token |
| `BELLUM_CSRF_SECRET` | Independent random secret, at least 32 characters |
| `BELLUM_SECURE_COOKIES` | `true`; production Compose enforces this |
| `BELLUM_FORWARDED_ALLOW_IPS` | Required exact TCP peer IP(s) for host Nginx; measure as below, never `*` or an entire bridge subnet |
| `BELLUM_SNAPSHOT_FRESHNESS_HOURS` | `24` |
| `BELLUM_MAX_UPLOAD_BYTES` | `8388608` (8 MiB file limit) |
| `BELLUM_NATIVE_MAX_REDUCTION_FRACTION` | `0.5` single-snapshot reduction safeguard |
| `BELLUM_NATIVE_SUSTAINED_MAX_REDUCTION_FRACTION` | `0.5` sustained reduction safeguard |
| `BELLUM_NATIVE_REVIEW_OVERRIDES` | `false`; enable only after specific operator review |
| `BELLUM_HOST_PORT` | `18080`, always published on `127.0.0.1` |
| `BELLUM_IMAGE_TAG` | Set a distinct reviewed release tag before building |
| `BELLUM_ENV_FILE` | `.env.production` or the matching external absolute path |

Postgres initialization variables only apply to an **empty** data directory.
Editing the password in the environment file does not rotate a running database
role's password. Coordinate actual role rotation and the app URL separately.
The initial Postgres role is a database administrator; do not expose its access
outside this dedicated stack or reuse its credentials elsewhere.

## Build, initialization, and startup

Use one stable command prefix. Every subsequent example assumes this Bash helper:

On first initialization, use `BELLUM_FORWARDED_ALLOW_IPS=127.0.0.1` in the
environment file only as a restrictive bootstrap value. Complete the peer
measurement below and recreate `app` before enabling public proxy traffic.

```bash
dc() { docker compose --env-file .env.production -p bellum-resources -f docker-compose.prod.yml "$@"; }
dc config --quiet
dc build app
dc up -d --wait postgres
dc run --rm --no-deps app alembic upgrade head
dc run --rm --no-deps app alembic current
dc up -d --wait app
dc ps
curl --fail http://127.0.0.1:18080/health/ready
curl --fail http://127.0.0.1:18080/resources
```

`alembic.ini` intentionally has a placeholder URL. Migration execution resolves
`BELLUM_DATABASE_URL` supplied to the app service; no `TEST_DATABASE_URL` is used.
First migration seeds reference data but does not import production snapshots or
historical archives. Import through existing reviewed workflows afterward.
Historical observations cannot establish current Core3 availability; no importer
semantics, upload endpoints, or snapshot contracts are changed by this deployment.

## Nginx and HTTPS (proposed, not activated)

Review [the standalone virtual host example](nginx/resources.bellumgero.net.conf.example).
It proxies only this hostname to `127.0.0.1:18080`, overwrites forwarding headers,
and allows a 9 MiB request body for the default 8 MiB file plus multipart overhead.
The application also bounds multipart overhead and validates file size. If changing
the upload limit, adjust Nginx to exceed the file limit plus 65536 bytes.

Before future activation, verify the hostname resolves to the website server and
obtain a certificate with this hostname as a SAN, a complete chain, and a protected
private key. The example uses Let's Encrypt paths. An HTTP ACME challenge needs
a separately configured challenge route/bootstrap HTTP host; the HTTPS block
cannot load before certificate files exist. DNS validation is another option.
Plan certificate renewal and test it independently. Test the proposed configuration
with `nginx -t` before an approved reload; do not overwrite other virtual hosts.

### Trusted proxy peer configuration

Uvicorn enables proxy headers by default. It accepts `X-Forwarded-Proto` and
`X-Forwarded-For` only when the **immediate TCP peer** is in `FORWARDED_ALLOW_IPS`.
Compose sets that variable from required `BELLUM_FORWARDED_ALLOW_IPS`.
The browser's IP, the public Nginx address, and the Nginx upstream destination
`127.0.0.1` are not necessarily that peer. Docker NAT/userland forwarding commonly
makes a bridge gateway the peer inside the container. Docker Desktop, rootless
engines, and different daemon/network configurations can use another address.
Do not assume a particular gateway or trust all RFC1918 addresses/the bridge CIDR.

During a future approved installation, measure the actual host-to-container path
after startup, with Nginx still inactive for this hostname:

```bash
# From the Ubuntu host, without any forwarding headers:
curl --fail 'http://127.0.0.1:18080/health/live?peer-probe=proxy-review'
dc logs --since 1m app
```

Find the access-log line for that unique probe. Its leading `IP:port` is the raw
peer because the probe supplied no forwarded headers. Compare it to the network
gateways for context (network inspection does not expose environment secrets):

```bash
docker network inspect bellum-resources_frontend bellum-resources_backend \
  --format '{{.Name}} {{json .IPAM.Config}}'
```

Set `BELLUM_FORWARDED_ALLOW_IPS` to the observed **single IP**, or comma-separated
exact IPs only if the actual proxy path uses more than one. Keep the value in the
external environment file, not just a shell export. Recreate the app:

```bash
dc config --quiet
dc up -d --no-deps --force-recreate --wait app
# Simulate the headers the proposed host Nginx overwrites:
curl --silent --show-error --dump-header - --output /dev/null \
  -H 'Host: resources.bellumgero.net' -H 'X-Forwarded-Proto: https' \
  -H 'X-Forwarded-For: 203.0.113.10' http://127.0.0.1:18080/resources/
```

The slash redirect must have `Location: https://resources.bellumgero.net/resources`.
An `http://` location means trust did not match; investigate the peer instead of
restoring wildcard trust. Verify again through actual Nginx after approved HTTPS
activation, including an authenticated browser validation upload with a same-origin
HTTPS `Origin` header. Nginx must keep overwriting `Host`, `X-Forwarded-Proto`, and
`X-Forwarded-For` as in the example, rather than trusting client-supplied values.

Secure cookies remain enforced independently of forwarded headers. Correct scheme
handling is also needed for HTTPS redirects and the application's Origin/CSRF
comparison. Uvicorn does not use `X-Forwarded-Host` to replace `Host`; Nginx explicitly
sets `Host`. Plain loopback HTTP is for health/smoke checks, not browser admin use.

Limitations: an exact IP authenticates a network path, not the Nginx process. Other
local host processes reaching the same published port share that trust. Docker
administrators can change networks or impersonate peers and are already privileged.
Direct connections from other container IPs are untrusted, but traffic relayed or
NATed through a trusted gateway can share its identity. Keep the loopback binding,
dedicated networks, host access controls, and the Nginx header overwrite policy.
Re-measure after Docker/network changes or `down`/network recreation: Docker can
allocate new gateway addresses. Ordinary app recreation on unchanged networks
does not require broadening trust. No address was assumed for `cloud32674` during
local review; its exact peer remains a deployment acceptance check.

References: [Uvicorn proxy-header settings](https://www.uvicorn.org/settings/),
[Docker port publishing](https://docs.docker.com/engine/network/port-publishing/),
and [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/).

## Health and smoke checks

- `/health/live`: HTTP 200, process responds without touching the database.
- `/health/ready`: HTTP 200 only when database access and the required snapshot
  table work. Returns sanitized HTTP 503 on database failure or missing schema.
- Docker checks readiness every 30 seconds; `dc ps` reports health.
- Docker restart policies react to exits, not merely unhealthy status. Investigate
  unhealthy containers instead of expecting Docker to repair a database outage.
- Check `/`, `/resources`, `/history`, `/api/resources`, and `/api/snapshot-status`.
- `/admin` and `/api/admin/imports` must return 401 without valid credentials;
  verify configured browser Basic authentication and the separate API bearer token.
- Check HTTPS cookies, redirects, proxy headers, and upload limits through Nginx
  during a later approved deployment acceptance test.

## Backups and recovery

Back up before every schema migration or bulk import. Keep the reviewed app image,
source revision, `alembic current` output, and securely escrowed environment secrets
with the recovery record. A volume is persistence, **not** a backup. Do not copy a
live PostgreSQL data directory as a logical backup. Never run `dc down -v` on a
valuable database. `dc down` without `-v` retains the named volume.

Logical custom-format backup (Bash; binary redirection must not use older Windows
PowerShell's text pipeline):

```bash
umask 077
mkdir -p backups
backup="backups/resources-$(date -u +%Y%m%dT%H%M%SZ).dump"
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' > "$backup"
test -s "$backup"
sha256sum "$backup" > "$backup.sha256"
dc exec -T postgres pg_restore --list < "$backup" > "$backup.contents"
```

Check each exit status; a nonempty file is insufficient. Verify the checksum after
copying to encrypted off-host storage. `pg_restore --list` checks the archive header,
but only a full restore rehearsal verifies recoverability. Logical dumps do not
include cluster roles/passwords; recreate those from protected recovery records.
Recommend 7 daily, 4 weekly, and 12 monthly verified copies, adjusted to available
space and business recovery requirements. This phase installs no backup jobs.

Restore only into a **fresh isolated** database, never over the live database.
Use a distinct Compose project (e.g. `bellum-resources-recovery`), a separate
external environment file, new secrets, database name, and unused loopback port.
Set that file's `BELLUM_ENV_FILE` to itself. Define `rc` with the same options as
`dc` but this recovery file/project. Then:

```bash
rc up -d --wait postgres
# Fresh Postgres database has no application schema: restore BEFORE migrations.
rc exec -T postgres sh -c 'pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$backup"
rc run --rm --no-deps app alembic current
rc up -d --wait app
```

Use the app image compatible with the dump's migration revision. Validate readiness,
resource counts, historical/live source separation, snapshot status, resource detail
pages, and admin authentication. Compare representative data and migration revision
against the backup record. If testing a newer app, take a copy and explicitly run
its migrations before starting it. Do not change a restored snapshot's captured
time to make stale data appear current.

## Updates and rollback

Record the running app/Postgres image IDs, source revision, and migration revision.
Build a distinct new app tag; retain the previous image. Take and verify a backup,
then pause uploads and stop `app` during schema changes:

```bash
dc stop app
dc run --rm --no-deps app alembic upgrade head
dc up -d --no-deps --wait app
```

Run health/public/admin checks before reopening uploads. If there is no schema
change, restore the previous image tag and recreate `app` with `--no-build`.
For schema-changing updates, first determine whether the old app is compatible.
Do not assume automatic Alembic downgrades are safe. Restore the verified backup
to a fresh volume/database and the matching old app image when necessary; account
for writes after the backup before switching traffic. Keep the failed database
for investigation. Upgrade PostgreSQL major versions only through a separately
reviewed migration; never point a new major version at the old data directory.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Compose rejects a missing variable | Fill the external file; pass matching `--env-file` and `BELLUM_ENV_FILE` |
| Readiness 503 while liveness 200 | `dc ps`, sanitized app logs, Postgres health, URL consistency, and `alembic current` |
| Authentication fails | Paired credentials/token, CSRF secret length, HTTPS secure cookie behavior |
| Nginx 502 | App health, loopback port match, certificate/config validation |
| Upload 413 | Both Nginx body limit and `BELLUM_MAX_UPLOAD_BYTES` |
| Snapshot rejected | Preserve importer errors and count safeguards; review input instead of disabling protection |
| Data appears stale | Latest Core3 capture time; importing historical data does not refresh it |
| Data missing after restart | Confirm unchanged project name, volume, and database name |

Avoid printing populated Compose configuration or environment variables when
collecting diagnostics. Docker access is privileged; restrict it to trusted operators.

## Phase 6B local validation

Use a unique disposable project/environment/port for production-stack smoke checks.
Keep existing local databases untouched. Run automated tests separately using the
repository's existing loopback `_test` database fixture/configuration; fixtures
rebuild that database schema. Never point pytest at the production-stack database.
The validation results for this implementation are recorded in
`docs/production-validation.md`.
