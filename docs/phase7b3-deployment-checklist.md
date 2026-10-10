# Phase 7B.3 deployment and recovery checklist

**Prepared only. None of these production actions was executed.** Obtain separate
deployment approval and an operator maintenance window before using this checklist.
The companion [validation report](phase7b3-validation-report.md) records actual
local evidence and the final recommendation.

Continuation review on 2026-10-09 confirmed the migration chain, library import
and activation signatures, clean-checkout CLI restriction, image packaging,
backup strategy and destructive downgrade warning against the implementation.
Unchecked boxes below are future operator gates, not evidence of completed work.
The validation report records a **NO-GO for deployment** pending browser/mobile
QA and the separately authorized production acceptance/backup gates.

## Architecture and release record

The repository's intended architecture is Ubuntu 24.04 with host Nginx terminating
HTTPS for `resources.bellumgero.net`, proxying to a Dockerized FastAPI/Uvicorn app
published **only** at `127.0.0.1:18080`. PostgreSQL 16 has no host-published port
in production and uses the internal backend network and persistent named volume.
The app also joins the frontend bridge, runs as UID/GID 10001, and has a read-only
root filesystem. This description comes from the reviewed repository configuration,
not a fresh inspection of the production host.

- [ ] Record the approved application source commit, built image digest, previous
  image digest, PostgreSQL image digest/version, Compose project name, environment
  file location, volume/database identity, and maintenance owner.
- [ ] Review the earlier uncommitted 7B.1/7B.2 changes before creating an approved
  release artifact. The local `phase7b3-validation` image is a working-tree
  validation image, not an approved production release.
- [ ] Record the actual production `alembic current` output. It was deliberately
  **not queried** in this phase. Do not assume it is `20260929_0002`.
- [ ] Confirm expected migration path ends at `20261009_0003` and contains no
  unrelated/unreviewed migrations. Stop on an unexpected revision or multiple heads.
- [ ] Record the existing creature activation pointer, if any, and resource counts
  and source/snapshot identities. Keep credentials and private source evidence out
  of logs and public reports.
- [ ] Verify free disk/backup capacity, database connectivity, existing hostname/TLS
  configuration and an unused expected app port under the separate approval.
  This checklist does not authorize DNS or Nginx changes.

Use the stable helper from [production-deployment.md](production-deployment.md):

```bash
dc() { docker compose --env-file .env.production -p bellum-resources -f docker-compose.prod.yml "$@"; }
dc config --quiet
```

Keep `BELLUM_ENV_FILE` and `--env-file` consistent. Do not print populated Compose
configuration. Preserve secure cookies and restrictive proxy trust. The trusted
IP is the measured **immediate container TCP peer**, often a bridge gateway,
not automatically `127.0.0.1`; never restore wildcard trust. Re-measure if networks
change. Exact-IP trust identifies the host/gateway path, not the Nginx process.
Nginx must overwrite forwarding headers and preserve the public Host. Perform
the runbook's HTTPS redirect, Origin/CSRF and secure-cookie checks after approval.

## Backup and restoration gate

- [ ] Pause snapshot uploads and other application writes for a consistent release
  baseline. Record the cutoff and recovery point objective. Preserve later source
  snapshots externally so they can be reviewed/replayed if a restore is needed.
- [ ] Take a custom-format `pg_dump -Fc --no-owner --no-acl` backup using PostgreSQL
  16 tools. Capture exit status, file size and SHA-256; copy to encrypted off-host
  storage and verify the checksum again. Retain roles/secrets separately.
- [ ] Inspect `pg_restore --list`, then **fully restore** into a fresh, separately
  isolated database/volume. A nonempty dump or list output alone is insufficient.
- [ ] Start the matching previous image against that restored database; verify
  migration revision, resource counts, representative record hashes, snapshot
  ledger/status, authentication, and existing public pages/APIs.
- [ ] Record restore duration, success evidence and recovery owner. Do not proceed
  if the actual production backup has not passed this rehearsal. Local Phase 7B.3
  dump/restore evidence does not substitute for verification of that backup.

The existing runbook contains Bash binary-safe backup/restore commands. Do not
pipe binary dumps through older Windows PowerShell text redirection. Never copy
a live data directory or use `docker compose down -v` on valuable data.

## Migration, offline import and activation

- [ ] Build and retain a distinct reviewed release image; verify packaged creature
  modules/templates and migration `20261009_0003`. Do not deploy from a mutable
  `local` tag. Retain the previous image before making any change.
- [ ] Stop the app during the approved migration/import window; do not restart
  Core3, the Docker daemon, PostgreSQL or Nginx for this feature.
- [ ] Run the release image's `alembic upgrade head` as an explicit one-off app
  command. Verify `alembic current` reports `20261009_0003`. Migrations do not run
  automatically on app startup.
- [ ] Verify existing taxonomy/planet mappings against the rehearsal. Do not
  silently reload the resource taxonomy or historical archive as part of the
  creature import: those are separate resource-writing operations.
- [ ] Prepare the already approved offline source archive outside public paths.
  Pin commit `54c71a88190a7bfc6ad98b2b26280670b4b1bd10`; archive SHA-256
  `494786ebaf99cc122d4e6f9f53addebe7066dee3ef506d1b0725e0e1298d5a5a`.
  Extract safely into a private directory and mount it **read-only** in the one-off
  importer. Do not use or modify the active game-server checkout.
  Verify the archive bytes with `sha256sum` before extraction; the catalog manifest
  checks reachable source files and does not replace this archive checksum gate.
  Reject absolute paths, parent traversal and escaping symlinks during extraction.
- [ ] Rebuild the catalog and require manifest SHA-256
  `c7d2ed31dfd376823e4a6e02102c3a18e806421b0afc79c1e2ff981c5737e4f7`.
  A different archive must be independently reviewed, not relabeled as this pin.
- [ ] Run a READ ONLY dry-run, review diagnostic counts, then import with
  `activate=False`. Check 3,813 validated definitions, 24,882 evidence rows,
  739 positive carcass creatures with verified planet paths, expected duplicate
  registration exclusions and unchanged resource-table fingerprints/counts.
- [ ] Record diagnostics: no verified static nodes or event/quest/dungeon planet
  paths in this pin; conditional screenplays/mission references remain warnings.
  Do not bypass parser validation or fabricate Hoth/other locations to fill gaps.
- [ ] Activate only the validated imported revision in a separate transaction,
  recording the previous pointer and new revision ID. Recheck manifest and counts
  before activation. Retain previous revisions for catalog-pointer recovery.

The existing `python -m app.importing.creatures` CLI requires a **clean Git
checkout** and Git executable. An extracted archive in the production image has
neither, so that CLI must not be assumed to work there. Use the already validated
library workflow in a reviewed one-off operator script. Essential operations are:

```python
from pathlib import Path
from sqlalchemy import text
from app.db.session import make_engine
from app.importing.creatures.archive import build_catalog
from app.importing.creatures.importer import import_catalog, activate_revision

revision = '54c71a88190a7bfc6ad98b2b26280670b4b1bd10'
expected = 'c7d2ed31dfd376823e4a6e02102c3a18e806421b0afc79c1e2ff981c5737e4f7'
catalog = build_catalog(Path('/source'), repository='bellum-gero-live', revision=revision)
if catalog.fatal or catalog.manifest_hash != expected:
    raise RuntimeError('Pinned source validation failed')
engine = make_engine()  # existing protected BELLUM_DATABASE_URL; never print it

# DRY-RUN invocation only:
with engine.begin() as connection:
    connection.exec_driver_sql('SET TRANSACTION READ ONLY')
    dry = import_catalog(connection, catalog, dry_run=True)
# Review a sanitized summary; do not dump raw diagnostic/configuration payloads.

# IMPORT invocation, separately approved after dry-run review:
with engine.begin() as connection:
    imported = import_catalog(connection, catalog, activate=False)
# Record imported['revision_id']; independently verify SQL counts/resources.

# ACTIVATION invocation only after the preceding review:
with engine.begin() as connection:
    row = connection.execute(text('SELECT status,manifest_hash FROM creature_revisions WHERE id=:id'),
                             {'id': imported['revision_id']}).one()
    if row.status != 'validated' or row.manifest_hash != expected:
        raise RuntimeError('Activation precondition failed')
    activate_revision(connection, repository='bellum-gero-live', revision_id=imported['revision_id'])
engine.dispose()
```

Split dry-run, import and activation into separate operator invocations; the snippet
shows the API sequence, **not** a script to execute all three blindly. Mount the
reviewed operator script and extracted archive read-only via `dc run --rm --no-deps
--volume ... app python /run/release_import.py`. Bind mounts belong to the Docker
host. The standard deployment image should not contain raw/private source archives.

## Application and acceptance gate

- [ ] Start/recreate only the app with the reviewed image and unchanged database
  project/volume. Verify container health, UID 10001 and intended restrictions.
- [ ] Check `/health/live` and `/health/ready`; readiness alone checks the existing
  snapshot schema/connectivity and **does not establish catalog activation**.
- [ ] Check `/`, `/resources`, `/history`, `/api/resources`, `/api/snapshot-status`,
  a known historical resource and any real snapshot status. Historical imports
  must not advance Core3 current availability.
- [ ] Check `/creatures`, an actual creature detail, and an actual named-resource
  reverse page. Check all five creature APIs, planet/category/raw-class filtering,
  page 1/page 2, empty states, multiple planets/regions and quarantine exclusion.
- [ ] Verify same-planet candidate rules and multi-planet resource associations.
  When no authoritative Core3 snapshot exists, require the exact missing-snapshot
  notice. Do not upload an invented/empty snapshot to make telemetry appear fresh.
- [ ] Verify browser/admin authentication, Bearer API authentication, CSRF failure
  cases, Secure/SameSite cookie attributes, HTTPS redirects and same-origin upload
  behavior through the actual host proxy. Keep uploads paused until these pass.
- [ ] Perform real desktop/mobile browser inspection, keyboard navigation, table
  scrolling, long provenance wrapping and escaping; this phase had no browser.
- [ ] Measure request and database latency on actual production archive scale;
  inspect slow query plans. Use the Phase 7B.3 local measurements as comparison,
  not a production SLA. Do not add indexes without an observed relevant plan.
- [ ] Review errors, resource row counts/fingerprints, active catalog revision and
  snapshot ledger. Reopen uploads only after acceptance and owner sign-off.

## Rollback and recovery

1. Keep uploads/writes paused; record failure time, recent import IDs, logs and
   snapshot inputs. Preserve the failed database for investigation.
2. If only catalog data/activation is wrong and schema/app remain compatible,
   switch the activation pointer back to the recorded validated previous revision
   using `activate_revision` in a transaction. If this was the first catalog,
   remove only that repository's activation pointer after explicit review; do not
   delete resource data or pretend an invalid revision is valid. Recheck pages.
3. If only application code is wrong and the prior image is verified compatible
   with the current additive schema, restore that exact image digest and recreate
   only the app. Verify health/resource/admin checks before reopening writes.
4. For schema incompatibility or damaged/imported data, restore the **verified
   pre-change backup to a fresh database/volume** with matching PostgreSQL major
   version and prior app image. Check migration revision and hashes/counts before
   changing the protected app database connection under approval. Keep old/failed
   volumes intact. Reconcile/replay any post-backup writes from retained inputs.
5. Repeat HTTPS/public/API/authentication acceptance, document recovery point and
   duration, and obtain owner approval before reopening writes.

**Do not use `alembic downgrade` as the default recovery strategy.** Migration
0003's downgrade drops creature tables and their imported provenance. Passing
disposable-schema downgrade tests does not establish safe production rollback.
No database restore, DNS switch, proxy reload or production command is authorized
by this document itself.
