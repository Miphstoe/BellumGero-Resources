# Architecture

## Recommendation

Use a single maintainable monorepo with PostgreSQL, a small backend/API, a server-rendered or hybrid frontend, and offline/import jobs. Avoid microservices until there is a real operational need.

Recommended stack:

- Database: PostgreSQL 16+.
- Backend/API: Python with FastAPI, SQLAlchemy 2, Alembic, Pydantic, and psycopg.
- Frontend: Next.js or SvelteKit can work, but the conservative recommendation is server-rendered React/Next.js only if interactive filtering needs it. If the first public version is mostly searchable resource pages, FastAPI plus Jinja/HTMX is also viable.
- Cache: PostgreSQL indexes/materialized views first; add Redis only when alerting, job queues, or hot search endpoints need it.
- Jobs/importers: Python CLI commands in the same repo/container image.
- Testing: pytest for importers/API, migration tests, fixture-based parser tests, and SQL integration tests with disposable Postgres.
- Deployment: Docker Compose on existing Linux infrastructure with separate services for `postgres`, `app`, optional `worker`, and reverse proxy integration.
- Backups: `pg_dump` logical backups plus WAL/base backups if uptime needs grow.

## Why PostgreSQL

PostgreSQL is the right fit because the core problem is relational and provenance-heavy:

- Resource identity requires source-specific keys, canonical records, observations, candidate matches, and confirmed links.
- Resource type hierarchy needs recursive queries or closure tables with strong constraints.
- Stats need normalized rows, partial indexes, and null semantics.
- Historical availability needs evidence/event rows without fabricating intervals.
- Search/filtering benefits from B-tree, trigram, GIN, generated columns, materialized views, and window queries.
- Future schematics and creatures are natural relational joins against resource type ancestry and current availability.
- Backups, migrations, and operational tooling are mature and straightforward on Linux.

SQLite would be simpler at first but weaker for concurrent imports, API traffic, advanced indexing, and online migrations. MySQL/MariaDB could work and matches GH historically, but PostgreSQL gives better constraint/indexing tools for reconciliation, JSON provenance, and future search.

## Repository Layout

Recommended layout once implementation begins:

```text
README.md
docs/
  architecture.md
  data-sources.md
  database-schema.md
  import-plan.md
app/
  api/
  web/
  core/
database/
  migrations/
  seeds/
importers/
  galaxy_harvester/
  core3/
indexers/
  core3_static/
scripts/
tests/
```

Do not create implementation folders until the corresponding phase starts. This pass only creates `README.md` and `docs/`.

## Future Core3 Synchronization

Do not query the live Berkeley DB directly. Use a bounded private handoff:

```text
Core3 runtime
  -> private resource exporter
  -> atomic JSON snapshot/event files
  -> private importer
  -> Bellum Gero Resources PostgreSQL
  -> read-only API/cache
  -> public website
```

Exporter design goals:

- Run inside or near the Core3 environment with minimal privileges.
- Emit complete snapshots and optional delta event files.
- Include server instance, server epoch, exporter version, Core3 source revision, observed_at, completeness flags, and resource object identities.
- Write to a staging path and atomically rename when complete.
- Never expose the Core3 object database to the public website.

Importer design goals:

- Validate schema version and hashes before ingest.
- Store each file as a source snapshot.
- Upsert source identities idempotently.
- Record observation rows rather than mutating history in place.
- Materialize current availability for fast reads.

## Public API Shape Later

Start read-only:

- `GET /resources`
- `GET /resources/{canonical_resource_id}`
- `GET /resource-types`
- `GET /planets`
- `GET /current/resources`
- `GET /schematics/{id}/compatible-resources` later
- `GET /creatures/{id}/harvests` later

Keep write/admin/import endpoints private.

## Design Constraints

- Core3 is authoritative for current/future Bellum Gero mechanics.
- GH is historical evidence.
- Raw GH XML stays outside Git.
- Missing/not-applicable stats remain distinct from explicit zero.
- Source values are preserved even when anomalous.
- GH/Core3 source identities are not interchangeable.
- No automatic GH/Core3 merge by name alone.

