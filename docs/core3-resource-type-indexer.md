# Core3 Static Resource-Type Indexer

## Source-Of-Truth Decision

Core3 remains authoritative for Bellum Gero resource taxonomy. The live checkout inspected for Phase 2 is:

`C:\Users\User\BellumGero-Live`

The actual Git working tree with Core3 source files is nested at:

`C:\Users\User\BellumGero-Live\BellumGero-Live`

The checkout does not contain `datatables/resource/resource_tree.iff` or deployed `.tre` files. It does contain a deterministic SQL datatable dump at:

`MMOCoreORB/sql/datatables.sql`

That dump includes `resource_resource_tree`, whose columns mirror Core3's `ResourceTree::buildTreeFromClient()` reader for `datatables/resource/resource_tree.iff`. The Phase 2 indexer therefore uses the checked-in SQL datatable dump as the safest available static input. It does not use Galaxy Harvester data as authority and does not download stock data.

## Core3 Files Inspected

- `ResourceTree.cpp`: loads `datatables/resource/resource_tree.iff`, reads enum, class columns, stat attributes, min/max ranges, container type, and random name class.
- `ResourceTreeEntry.h`: stores type, class paths, STF class paths, attributes, and `isType()` membership behavior.
- `ResourceTreeNode.cpp`: builds the hierarchy from class-stack transitions and resolves resource type entries.
- `ResourceAttribute.h`: maps Core3 resource attribute names to crafting stat constants.
- `ResourceSpawner.cpp`: copies class paths, STF paths, and randomized stat values from `ResourceTreeEntry` into `ResourceSpawn`.
- `ResourceSpawn.idl` and `ResourceSpawnImplementation.cpp`: show runtime `isType()` checks both STF and display class vectors; missing attributes return `0` through getters.
- `MMOCoreORB/sql/datatables.sql`: contains the available static `resource_resource_tree` dump.

## Indexer Architecture

The implementation lives in `app/indexing/core3/resource_types.py`.

Stages:

1. Discover the Core3 checkout and locate `MMOCoreORB/sql/datatables.sql`.
2. Parse the MySQL-style `resource_resource_tree` insert data.
3. Replay Core3's class-stack hierarchy algorithm.
4. Normalize resource type records with stable source IDs, display classes, direct parents, stat applicability, min/max values, source evidence, and fingerprints.
5. Validate duplicates, missing parents, self-edges, cycles, and invalid stat ranges.
6. Build deterministic transitive closure.
7. Write a deterministic JSON inspection artifact.
8. Optionally load normalized records into the Phase 1 database.

The default CLI path is inspection only and does not write to PostgreSQL.

## CLI Usage

Inspect/dry run:

```powershell
python -m app.indexing.core3.resource_types inspect --core3-root "C:\Users\User\BellumGero-Live"
```

This writes `.phase2-output/core3_resource_types.json`, which is ignored by Git.

Load, explicitly:

```powershell
python -m app.indexing.core3.resource_types load --core3-root "C:\Users\User\BellumGero-Live"
```

Dry-run the load command without writes:

```powershell
python -m app.indexing.core3.resource_types load --core3-root "C:\Users\User\BellumGero-Live" --dry-run
```

## Normalized Data Format

Each resource type includes:

- Core3 source type ID, such as `steel_duralloy`
- internal/Core3 name
- display name from the Core3 class column when available
- direct parent source IDs
- ancestor source IDs
- display class path
- STF/source class path
- one stat applicability/range record for each verified Core3 stat
- source path and SHA-256 fingerprint
- recycled/permanent flags
- container type and random name class
- warnings

The JSON artifact is serialized with sorted keys, stable ordering, no generated IDs, and no timestamps.

## Hierarchy Semantics

Core3 does not use string-prefix hierarchy for crafting resource matching. The resource tree reader maintains a class stack while reading rows. Each non-empty class column updates the current stack level and associates the current row enum with that level. The indexer replays that behavior and then builds direct parent-child edges and transitive closure rows.

The Phase 1 schema supports DAGs. The current Core3 datatable behaves as a single-parent hierarchy, but the closure builder supports multiple parents and records minimum depth.

## Stat Applicability Semantics

Only verified Core3 stat attributes are indexed:

- `CR`: `res_cold_resist`
- `CD`: `res_conductivity`
- `DR`: `res_decay_resist`
- `FL`: `res_flavor`
- `HR`: `res_heat_resist`
- `MA`: `res_malleability`
- `PE`: `res_potential_energy`
- `OQ`: `res_quality`
- `SR`: `res_shock_resistance`
- `UT`: `res_toughness`

Missing attributes are stored as not applicable with null min/max. Explicit `0..0` ranges remain applicable zero ranges. The indexer does not invent `ER`.

## Source Fingerprinting

The JSON artifact records:

- relative source path
- SHA-256 of the source file
- byte size
- parser/indexer schema version
- BellumGero-Live commit SHA when Git can resolve it

## Limitations And Unresolved Data

The checkout does not contain the actual `resource_tree.iff` or production TRE files. The SQL datatable dump is sufficient for deterministic static indexing because it contains the resource tree data in a form matching the Core3 reader, but future validation against deployed assets would require the exact production TRE/IFF asset.

Human-readable display names are taken only from the Core3 datatable class columns. The indexer does not resolve STF files beyond what the datatable already exposes.

The inspected Bellum Gero SQL dump contains `entangle_resistance` on 23 gemstone-related resource types. The verified Core3 `ResourceAttribute` and `ResourceSpawn::getValueOf()` mappings do not expose a crafting stat for that attribute, so Phase 2 reports it as an unknown Core3 attribute and does not map it to `ER`. This preserves the Phase 1 rule that `ER` exists for Galaxy Harvester historical data unless a real Core3 mapping is verified later.

The focused Phase 2A investigation is recorded in [Core3 Entangle Resistance Investigation](core3-entangle-resistance-investigation.md).

## Current Inspection Result

Against commit `e99f91192ba059151bfed4be5e6ea07884801ac0`, the inspection command found:

- source file: `MMOCoreORB/sql/datatables.sql`
- source SHA-256: `c7bffc10817ec739bcb5b6d33a2753efb77c770f6d69e1ce8914de4fb2e2e45a`
- resource types: 725
- direct hierarchy edges: 724
- closure rows: 4,828
- stat range records: 7,250
- unresolved display names: 0
- unresolved parents: 0
- validation errors: 0
- validation warnings: 23 unknown `entangle_resistance` attributes
- `resource_tree.iff` present in checkout: no
- TRE extraction required for this static SQL-dump index: no

## Future GH Mapping

Galaxy Harvester historical data remains source/cross-reference data. Future GH records should map their resource type names to Core3 canonical resource types through `source_resource_types` and reviewable reconciliation, not by making GH hierarchy authoritative.

## Optional custom spawn classifications

The SQL dump remains the baseline. Pass `--spawn-file` explicitly to include a
literal `resources = { ... }` spawn configuration; relative paths resolve against
the discovered Core3 repository. Existing commands and `build_index(root)` remain
valid. The optional second `build_index(root, spawn_path)` argument enables this
extension. No remote files are fetched automatically.

```powershell
python -m app.indexing.core3.resource_types inspect --core3-root C:/path/to/Core3 --spawn-file C:/path/to/resource_manager_spawns.lua --output .phase2-output/custom-taxonomy.json
python -m app.indexing.core3.resource_types load --core3-root C:/path/to/Core3 --spawn-file C:/path/to/resource_manager_spawns.lua --dry-run
```

Each `classes` pair supplies a display name and internal identifier. The implicit
`Resources/resource` root is included in the normalized path. Repeated entries
must agree exactly. Every existing class must agree with its SQL path and display
name; new intermediate parents require their own complete classification evidence.
Unknown parents, inconsistent definitions, repeated identifiers/cycles, duplicate
Lua fields, dynamic expressions and malformed tables fail closed. The reader does
not execute Lua. Optional files are ignored when omitted and errors when explicitly
specified but missing. Spawn attributes are observed values, not taxonomy ranges:
custom classifications have an empty `stat_ranges` array, and their unknown
container/random-name metadata is not inferred.

The artifact includes the spawn file's resolved path, byte size and SHA-256,
and each new classification records its path and checksum. Inspection reports all
custom identifiers, errors and normalized paths without opening a database.

Add `--compare-database` to `load --dry-run` for SELECT-only comparison against
`BELLUM_DATABASE_URL`. The JSON report contains existing mappings, proposed
canonical and mapping inserts, graph size, custom definitions, validation errors
and database conflicts. Existing unresolved mappings require manual reconciliation;
the loader does not overwrite them. Proposed edges are checked for cycles after
projecting source identifiers through preserved canonical mappings.

### Database impact and deployment

No migration is required. Loading creates missing canonical types, authoritative
Bellum Gero source mappings and hierarchy rows. Existing mappings (including aliases),
canonical IDs, metadata and stat ranges are retained. SQL stat ranges are inserted
only for newly created canonical types. Custom classifications introduce no range
rows. Historical resources, spawns, measurements and Galaxy Harvester tables are
not written. Return counts retain their existing meaning as indexed totals, rather
than counts of newly inserted rows. The CLI wraps the entire load in one transaction;
callers of `load_index` must likewise supply a transaction.

Before deployment:

1. Supply the authoritative staging file locally and verify its SHA-256. For the
   October 12, 2026 file, the expected checksum supplied by the operator is
   `e64ac7011bddaf6db14a1c62ea330018f794ec429e7f61ad7261eb2ce642b3f1`.
   The remote staging path is `/srv/bellumgero/resources/staging/resource_manager_spawns.lua`.
2. Inspect using the matching Core3 SQL checkout. Confirm zero errors and the 42
   expected Hoth identifiers in the artifact. Outdated Windows/WSL copies cannot
   validate this requirement.
3. Run the full test suite on an isolated loopback PostgreSQL database ending in
   `_test`; database fixtures reset that test database. Never point tests at production.
4. Review `load --dry-run --compare-database` against the intended database.
5. After operator review and a normal database backup, explicitly run the same
   `load` command without `--dry-run`. This operation is not performed automatically.
6. Dry-run the October 12 snapshot using the existing snapshot importer, then
   explicitly import it through the existing deployment process. Strict reconciliation
   remains unchanged and still rejects any unmapped type.

### Implementation validation (local)

The new fixture tests cover full hierarchy extraction, exact duplicate agreement,
conflicts against SQL, cycles, missing parents, malformed input, missing optional
files, canonical/mapping/range preservation, read-only database planning and strict
snapshot reconciliation. The last three require PostgreSQL.

Final validation on October 12, 2026 passed **576 tests**, including all PostgreSQL
integration tests, with no skips or failures. The single warning is the existing
Starlette/httpx deprecation warning. PostgreSQL 16 ran in a disposable Compose
service on `127.0.0.1:55435/bellum_resources_test`; production was not contacted.

The authoritative file was verified at 10,234,742 bytes with SHA-256
`e64ac7011bddaf6db14a1c62ea330018f794ec429e7f61ad7261eb2ce642b3f1`.
It contains 625 distinct resource types, including exactly 42 Hoth types. All 42
merge without duplicates, conflicts or unresolved parents. All 725 SQL-derived
records remain identical. The merged index contains 767 types, 766 direct edges,
5,132 closure rows and the unchanged 7,250 SQL stat-range slots. The only 23
warnings are the pre-existing unknown `entangle_resistance` attributes.

[Verified Hoth hierarchy report](core3-hoth-classifications.json) lists all 42
identifiers, their exact display names, complete display/identifier paths and
provenance. A classification-only fixture reproduces these paths without shipping
resource names, statistics or live spawn observations. The raw root-level spawn
file is explicitly ignored and is not tracked by Git.

The actual October 12 snapshot was not supplied. Every one of the 625 types found
in the authoritative spawn file is present in the merged taxonomy. This validates
spawn-file type coverage; actual snapshot structure, timestamps, resource identities,
planet mappings and importer count-safety checks still require the snapshot itself.
Use the existing exporter adapter for structural validation and the authenticated
validate-only upload for database reconciliation in staging.

### Authoritative database validation and staging readiness

A separate test transaction loaded the actual SQL baseline and authoritative spawn
file, seeded populated Core3 history and a GH source resource, and rolled back all
validation writes. The read-only plan proposed exactly 42 canonical inserts,
42 Bellum Gero mapping inserts, 42 direct edges and 304 closure rows, with zero stat
range inserts. Existing canonical/mapping rows were preserved byte-for-byte as
JSON representations; all other database table rows, including stat ranges and
populated history/GH fixtures, remained identical. A second load changed no rows.
Strict reconciliation succeeded for a synthetic structurally valid snapshot
containing all 625 distinct spawn-file types. This is type compatibility evidence,
not validation of the missing live snapshot.

The implementation is ready for staging deployment. No production changes were
performed. With `BELLUM_DATABASE_URL` explicitly targeting staging, use these
commands from the staged application environment, replacing `CORE3_ROOT` with a
checkout containing the verified SQL dump:

```sh
sha256sum /srv/bellumgero/resources/staging/resource_manager_spawns.lua
python -m app.indexing.core3.resource_types inspect --core3-root CORE3_ROOT --spawn-file /srv/bellumgero/resources/staging/resource_manager_spawns.lua --output /tmp/core3-custom-taxonomy.json
python -m app.indexing.core3.resource_types load --core3-root CORE3_ROOT --spawn-file /srv/bellumgero/resources/staging/resource_manager_spawns.lua --dry-run --compare-database
```

After checking the checksum and reviewing zero conflicts and the proposed changes,
apply the taxonomy explicitly in staging:

```sh
python -m app.indexing.core3.resource_types load --core3-root CORE3_ROOT --spawn-file /srv/bellumgero/resources/staging/resource_manager_spawns.lua
```

For an already-indexed SQL baseline, expect the 42 custom additions above; a fresh
or partially indexed staging database will have additional baseline inserts.
Repeat the database dry-run to confirm no remaining taxonomy changes. No database
migration or game/GH service change is needed. Validate the actual October 12
snapshot through the existing exporter adapter and authenticated validate-only
upload before importing it in staging. Obtain and review its actual type coverage,
planet mappings and importer safety results before any later production rollout.

Final test command (the test database is disposable and reset by fixtures):

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://bellum_test:isolated_local_test@127.0.0.1:55435/bellum_resources_test?connect_timeout=5'
./.venv/Scripts/python.exe -m pytest -q --tb=short -p no:cacheprovider --basetemp .phase2-output/pytest-full-verified
```
