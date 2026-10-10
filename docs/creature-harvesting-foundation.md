# Phase 7B.1: offline creature harvesting foundation

This phase adds a private data/service foundation. It does not add website pages,
HTTP routes, production jobs, schematic importers, or changes to resource-import
semantics. BellumGero-Live is an input, never a write target.

## Data relationships

The additive migration is `20261009_0003`, following `20260929_0002`.
`app/db/creatures.py` registers its models with the existing SQLAlchemy metadata.

| Tables | Purpose |
| --- | --- |
| `creature_revisions`, `creature_source_files` | Explicit repository/revision, manifest hash, import timestamp, validation summary, source paths and SHA-256 hashes |
| `creature_catalog_activations` | One atomically replaceable validated revision per repository |
| `creature_identities`, `creature_definitions` | Stable registration keys and revision-specific level, inheritance, validation and field provenance |
| `creature_names` | Separate custom names and unresolved localization references; localization keys are not invented translations |
| `harvest_categories`, `creature_harvests` | Hide, Meat, Bone, Milk; original lookup text and effective inherited base amount, including zero |
| `harvest_class_mappings` | Optional exact canonical `ResourceType` mapping, independent of planet |
| `creature_spawn_nodes` | Normalized revision-scoped regions, groups, lairs and literal static spawn sites |
| `creature_spawn_relationships` | Region → group → lair → creature, with source-defined gating conditions |
| `creature_spawn_evidence` | Creature definition + existing `Planet` + resolved spawn path; multiple paths and planets allowed |
| `creature_import_diagnostics` | File/line-linked unsupported expressions, duplicates, unknown planets/classes and unresolved references |

Creature records have no planet field. Resource classes are not planet evidence.
Named resources, their final types, observations and current availability remain
in the existing resource tables. Multiple planet observations per named resource
are supported without copying or changing the resource identity.

## Safe source interpretation

`app/importing/creatures/lua.py` tokenizes a conservative literal subset of Lua.
It does not invoke Lua, `eval`, shell script content, or repository functions.
Supported operations are literal assignments/tables, `Parent:new { ... }`, known
registration calls and literal `includeFile` paths. Inheritance binds the parent
table at construction time; registration captures effective values in load order.
Unresolvable harvest expressions invalidate that creature definition. Original
lookup whitespace is preserved; matching applies Core3's trim operation.

Only the explicit mobile loader `MMOCoreORB/bin/scripts/mobile/creatures.lua`
and its reachable includes populate the creature/group/lair catalog. Includes
resolve against the mobile scripts root, matching CreatureTemplateManager.
Paths escaping the scripts tree, cycles and missing mobile includes block
activation. Conflicting duplicate registration keys are quarantined in their
entirety; no arbitrary first/last winner enters the spawn graph. Ordinary reuse
of Lua globals in unrelated declarative tables does not redefine an already
captured creature registration.

Repeated includes are interpreted again, matching `Lua::runFile`; their duplicate
registrations cannot be hidden by file deduplication. Top-level control flow
blocks mobile-catalog activation. Unsupported calls are reported; calls receiving
creature/spawn definition objects also block activation. Relevant field mutation
cannot silently leave a previously captured definition eligible for activation.

Planet region roots come from literal `Core3.ZonesEnabled` in
`MMOCoreORB/bin/conf/config.lua`, then
`scripts/managers/planet/{zone}_regions.lua`, matching PlanetManager. The resource
manager's `activeZones` file is retained as provenance; it cannot establish a
creature's planet. Only resolvable spawn-region flags, geometry and complete
region/group/lair/creature paths authorize potential spawn evidence. Positive
weights/counts are required; excluded entries remain diagnostics. Difficulty,
limits, weighting, size and lair roles remain attached as conditions.

The separate `addDestroyMissionGroup` registry is retained as mission-only group
nodes and lair relationships. Its group names do not establish planet mappings,
and these nodes do not become world-spawn paths. Mission eligibility and runtime
activation remain unresolved.

World-spawn rectangle sentinels retain their raw geometry and `world_spawn=true`,
with display geometry `null`. They are not waypoints at `(0, 0)`. Circle,
rectangle and ring definitions describe potential regions, not guaranteed
creature coordinates. Region provenance points to the defining table declaration.

The explicit DirectorManager screenplay include root is inspected separately.
Fully literal, top-level `spawnMobile` calls on configured planets, with known
creatures and outdoor cell zero, may establish static evidence. Core3 argument
order is preserved as X, Z/height, Y, heading. Calls inside functions/control
blocks produce `conditional_spawn` diagnostics, even if their arguments are
literal. Mission, quest, event, respawn and callback invocation cannot be assumed.
Unsupported screenplay syntax excludes that file's evidence. No directory glob,
comment, filename or creature name establishes active availability.

## Import and activation

Migrate only an explicitly selected isolated database for review:

```powershell
$env:BELLUM_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/DATABASE_test'
./.venv/Scripts/python.exe -m alembic upgrade head
```

The CLI requires a clean Git checkout, full checked-out commit hash, repository
identity and explicit database URL. Every read source file must be tracked.
Git revision and cleanliness are checked again after parsing. Use a source
checkout readable by the same operating system as Python/Git.

```powershell
./.venv/Scripts/python.exe -m app.importing.creatures `
  --root C:\path\to\BellumGero-Live `
  --repository bellum-gero-live --revision FULL_COMMIT_SHA `
  --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/DATABASE_test' `
  --dry-run
```

Dry-run uses a read-only transaction, performs planet/class mapping validation
and outputs a JSON summary plus diagnostics. Remove `--dry-run` to archive the
revision; add `--activate` to atomically select it. They cannot be combined.
The SQLAlchemy API accepts a caller-owned transaction:

```python
catalog = build_catalog(root, repository="bellum-gero-live", revision=commit_sha)
with engine.begin() as connection:
    summary = import_catalog(connection, catalog, activate=True)
```

The programmatic builder accepts explicit synthetic revisions for fixtures;
production-facing callers must enforce the CLI's Git verification contract.
Persisting a catalog uses a savepoint and repository-scoped PostgreSQL advisory
transaction lock. Any write failure rolls back all its writes. Reimporting the
same revision and manifest is idempotent. Changed content under the same revision
is rejected. Revision definitions are retained when another revision activates.
Rollback selects a previously validated revision using
`activate_revision(connection, repository=..., revision_id=...)` in a transaction.
Failed global validation cannot activate. Entity-level uncertainties and unknown
planets may be archived; unverified entities/paths cannot qualify matches.

The importer writes only its new catalog tables. It never inserts, updates or
deletes `current_resource_availability` or existing resource observations.
Canonical class/planet mappings are captured at import time; repairing a missing
mapping requires a new catalog revision, not mutation through an idempotent rerun.

The reviewed taxonomy lacks exact `bone_mammal` and `meat_reptilian` rows, although
their planet-specific final types exist. Such lookup strings remain intact and
can still match final resource types using Core3's substring rule. An absent exact
class mapping produces a diagnostic, not a fabricated taxonomy row or an
assumption about resource availability.

## Planet-specific matching contract

```python
result = match_resources(
    connection, repository="bellum-gero-live",
    creature="wild_foreign_bantha_rori", planet="rori", category="meat",
)
```

`app/importing/creatures/matching.py` is read-only. It uses the activated validated
revision, requires verified source-defined evidence on the selected catalog
planet, and rejects empty lookup strings and zero quantities. It returns the
definition, repository revision, field/file provenance, potential regions and
conditions, named resource candidates and explicit uncertainty. No observed
resource on another planet qualifies. Milk is stored but returns
`milk_requires_separate_runtime_validation`; the verified carcass-harvesting
path covers Meat, Hide and Bone.

The authoritative local source at revision
`54c71a88190a7bfc6ad98b2b26280670b4b1bd10` establishes these rules:

* `CreatureManagerImplementation.cpp`, `harvest`, selects the effective raw
  creature field and asks for the player's zone name. Density/coordinates affect
  yield after resource selection.
* `ResourceManagerImplementation.cpp`, `getCurrentSpawn`, delegates to the spawner.
* `ResourceSpawner.cpp`, `getCurrentSpawn`, copies the selected zone's resource
  references and returns the first whose final `getType()` contains the lookup
  string. This is literal substring matching, not ancestry, exact class matching,
  stat ranking or a base-class fallback.
* `ResourceMap.cpp` registers a resource in all its spawn zones and detaches
  despawned resources. `ResourceSpawnImplementation.cpp`, `getSpawnZones`, can
  select several zones when there is no single-zone restriction.

Core3 candidates therefore use the source final type key with literal `strpos`.
Galaxy Harvester candidates use their explicitly mapped canonical type for
historical compatibility; that mapping cannot prove the original runtime type.
No quality ranking or guaranteed runtime winner is returned.

Current classification requires an active Core3 projection **and this planet's
observation linked to both the current and last-seen snapshot**. A resource active
elsewhere does not make an old observation here current. Historical GH records
always remain historical, regardless of their state label. Snapshot timestamps,
source-record/batch IDs, observation confidence and snapshot hashes are returned.
`fresh` defaults to a nonfuture snapshot no older than 30 minutes; callers can
provide `now` and `max_age`. `runtime_authoritative` identifies evidence from the
Core3 current projection; it is not a guarantee that a stale snapshot is still
correct. Stale/historical candidates have explicit uncertainty.

Future private API handlers can wrap this service for creature/planet/category
filters. Reverse named-resource discovery must enumerate the resource's recorded
planets and the verified creature/planet paths, then apply the same raw substring
rule. It must never infer a planet from taxonomy. No HTTP API or public UI was
added in this phase.

## Hoth and remaining limitations

The reviewed source has no Hoth region/mobile/screenplay definitions and neither
`ZonesEnabled` nor resource `activeZones` enables Hoth. The current catalog seeds
ten mapped planets. `dungeon1` and `tutorial` have no mapped catalog planet/region
root in this import. Unknown zone keys produce diagnostics and no planet evidence.

Hoth onboarding requires authoritative zone and spawn definitions, a catalog
Planet/zone mapping, source resource mappings, and actual planet-specific resource
observations. Add these through separately reviewed additive changes. A Hoth
catalog row or a matching resource class alone would prove none of those facts.

This is source-defined potential availability, not verification of a running
server's configuration, successful script initialization or a live census.
The tracked base configuration is the source contract; external runtime overrides
such as `conf/config-local.lua`, deployment configuration and live zone state are
not certified by this catalog.
Lua `require` modules, computed includes, arbitrary function calls/mutations and
mission/event/callback control flow are not interpreted. Unsupported constructs
cannot supply spawn evidence. Relevant top-level property mutation blocks catalog
activation. Imported Milk definitions do not establish milking eligibility.
Player skill, ownership, creature state, runtime event activation, collision and
spawn suppression are outside this phase. Runtime resource iteration order is
not exported, so even a fresh compatible candidate list cannot identify a
guaranteed first resource. Historical GH taxonomy mappings remain less certain
than Core3 final-type observations.

## Validation record

See the accompanying Phase 7B.1 validation report for persisted counts, test
results and the exact quarantined source definitions. Counts describe the
reviewed revision and parser version, not production state.
