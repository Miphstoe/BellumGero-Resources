# Bellum Gero Resources

Phase 5A adds a FastAPI/Jinja website, read-only resource APIs, and protected
snapshot uploads using the existing Phase 4B importer. See
[Phase 5A setup, API, security and deployment guide](docs/phase-5a.md) and
[integration inspection](docs/phase-5a-integration.md).

Native Core3 schema-v1 exports are supported through a strict compatibility
adapter with count-reduction review safeguards. See the
[adapter contract, validation, review and relay guide](docs/core3-snapshot-adapter.md).

```powershell
python -m pip install -e '.[test]'
# Set BELLUM_DATABASE_URL and the administrator environment variables first.
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The frontend is served by FastAPI; no separate frontend process or Node build
is required. Work remains local on `phase-5a-website-foundation` for review.

Design repository for the Bellum Gero resource, crafting, and creature data website.

Current status: Phase 1 database foundation. The repository now contains PostgreSQL schema design, SQLAlchemy models, Alembic migrations, reference-data seeds, and database integration tests. No production database, importer, API, frontend, Core3 exporter, schematic indexer, creature indexer, or deployment scaffolding has been created yet.

## Design Documents

- [Architecture](docs/architecture.md)
- [Data Sources](docs/data-sources.md)
- [Database Schema](docs/database-schema.md)
- [Import Plan](docs/import-plan.md)
- [Database Implementation Notes](docs/database-implementation.md)
- [Core3 Resource-Type Indexer](docs/core3-resource-type-indexer.md)
- [Core3 Entangle Resistance Investigation](docs/core3-entangle-resistance-investigation.md)

## Phase 1 Database Setup

Install dependencies:

```powershell
python -m pip install -e ".[test]"
```

Start PostgreSQL 16 for local development:

```powershell
docker compose up -d postgres
docker compose exec postgres createdb -U bellum_resources bellum_resources_test
```

Configure environment:

```powershell
$env:BELLUM_DATABASE_URL = "postgresql+psycopg://bellum_resources:change_me@localhost:5432/bellum_resources"
$env:TEST_DATABASE_URL = "postgresql+psycopg://bellum_resources:change_me@localhost:5432/bellum_resources_test"
```

Apply the schema:

```powershell
python -m alembic upgrade head
```

Normal Alembic commands use `BELLUM_DATABASE_URL`. The pytest fixture explicitly injects `TEST_DATABASE_URL`
into Alembic's config so test downgrade/upgrade cycles cannot target the development database when both
environment variables are set.

Run database tests:

```powershell
python -m pytest
```

## Guardrails

- `C:\Users\User\BellumGero-Live` is the authoritative Bellum Gero/Core3 reference and must not be modified by this project.
- `/home/miphstoe/GalaxyHarvester-Research` is historical/reference material and must not be modified by this project.
- Raw Galaxy Harvester XML archives must remain outside this Git repository.
- [Offline historical archive conversion and import validation](docs/galaxy-harvester-archive.md) documents the converter, integrity policy, and safe local verification commands.
- Galaxy Harvester personal/account/contributor data is out of scope for the public Bellum Gero Resources database.
