# Phase 5A integration inspection

The repository starts with SQLAlchemy models, two Alembic migrations, PostgreSQL
connection helpers, offline indexers/importers, and pytest integration tests.
There is no existing FastAPI app, authentication, template/static layer or API.
`BELLUM_DATABASE_URL` is required by `app.core.config.get_settings`; reuse
`app.db.session.make_engine` and SQLAlchemy connections.

Snapshot contract and structural validation belong to
`app.importing.core3_live.snapshot.read_snapshot`. Database reconciliation and
read-only validation belong to `core3_live.importer.dry_run`; mutations belong
to `import_snapshot`, with transaction ownership at the caller. Preserve all
three interfaces and the schema-v1 contract. API files use server-generated
temporary paths, bounded request bodies and the same parser/importer.

The upload service adds a PostgreSQL transaction advisory lock per Core3 source,
checks duplicate/conflict/watermark results before import, and rejects older
uploads. The offline importer retains its historical-import behavior. Failed
resource mutations roll back before recording a sanitized failed attempt in
the existing import_batches table. Validate-only requests never write records.

Public queries use source_resources as their identity: Galaxy Harvester rows
can lack a canonical resource, so canonical-only queries would omit history.
Current status comes exclusively from current_resource_availability for
bellum-gero-live. Latest stats retain null-versus-zero, and planet evidence
uses the last-seen snapshot for Core3 rather than accumulating old planets.
Expose provenance, observation times and stale status without inferred despawns.

Add app/main.py, app/web modules, Jinja templates and static CSS. Authentication
uses environment-configured HTTP Basic for the small private admin interface;
browser POSTs additionally require a signed double-submit CSRF token. Machine
uploads use an environment-configured bearer token. Neither is enabled without
configuration; all administrative routes fail closed. No schema migration is
needed. Test against a disposable loopback PostgreSQL database only.
