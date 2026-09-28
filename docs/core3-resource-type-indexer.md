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
