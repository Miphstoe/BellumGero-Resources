# Phase 7B.1 implementation report

The offline creature harvesting foundation is implemented. The schema, importer
and matching service are additive; no public routes/pages, production jobs,
schematic importer or deployment changes were introduced.

## Files changed

| File | Change |
| --- | --- |
| `app/db/models.py` | Register the additive creature models with shared metadata |
| `app/db/creatures.py` | Thirteen catalog/provenance/harvest/spawn/diagnostic models, constraints and indexes |
| `database/migrations/versions/20261009_0003_creature_harvesting.py` | Frozen additive PostgreSQL DDL, category seeds, indexes and reverse downgrade |
| `app/importing/creatures/__init__.py` | Package entry |
| `app/importing/creatures/lua.py` | Literal parser; no Lua execution; nested control-flow quarantine |
| `app/importing/creatures/archive.py` | Load-root traversal, inheritance, registration, regions, spawn graph, static/conditional evidence and diagnostics |
| `app/importing/creatures/importer.py` | Read-only dry-run, scoped persistence, idempotency, savepoints, advisory locking and activation/rollback |
| `app/importing/creatures/matching.py` | Read-only same-planet raw-substring candidate matching, current/historical classification and provenance |
| `app/importing/creatures/__main__.py` | Explicit revision/database CLI and before/after Git verification |
| `tests/importing/test_creature_catalog.py` | Parser, database, matching, safety and CLI regression tests |
| `docs/creature-harvesting-foundation.md` | Schema, source/runtime rules, usage and remaining limitations |
| `docs/phase7b1-validation.json` | Actual isolated PostgreSQL import counts and diagnostic breakdown |
| `docs/phase7b1-implementation-report.md` | This report |

## Actual source import

Source: BellumGero-Live commit
`54c71a88190a7bfc6ad98b2b26280670b4b1bd10`. A read-only `git archive` export
provided the tracked source inputs. No repository Lua was executed. The existing
resource-type indexer loaded its 725 taxonomy rows into the isolated test database
before the creature import; the creature importer itself writes only its new tables.

| Measure | Count |
| --- | ---: |
| Parsed creature registration identities | 3,814 |
| Persisted validated definitions in the activated revision | 3,813 |
| Validated creatures with verified planet spawn paths | 1,407 |
| Those with a positive Meat/Hide/Bone definition | 739 |
| Effective harvest definitions, including zero quantities and Milk | 15,252 |
| Exact canonical harvest-class mappings | 1,984 |
| Source files with SHA-256 provenance | 7,138 |
| Spawn groups, including 13 mission-only groups | 480 |
| Lairs | 1,488 |
| Verified source-defined regions | 427 |
| Persisted spawn relationships | 16,445 |
| Creature/planet/region/lair evidence records | 24,882 |

“Activated” means selected in this isolated catalog. It does not certify live
server initialization or current creature presence. Catalog-valid NPC definitions
with zero harvest amounts are retained but do not qualify as harvestable. Named
resource availability requires independent observations on the selected planet.

The custom `wild_foreign_bantha_rori` definition was validated from the actual
source: Meat `meat_wild_rori`/450, Hide `hide_wooly_rori`/825, Bone
`bone_mammal_rori`/250, Milk `milk_wild_rori`/850, level 18. Its Rori world-spawn
path resolves through the registered herd and `rori_world` group. The world's
sentinel geometry supplies no fabricated `(0,0)` waypoint.

## Quarantined and unresolved definitions

* `woodland_kima`: duplicate registration through repeated load paths. The entire
  creature identity is excluded from this revision's validated definitions and
  its two lair relationships cannot establish planet evidence.
* `tatooine_medium_kitonaks_se`: conflicting group registration, including the
  registration at line 222 of `tatooine_medium_squill_sw.lua`. The group and its
  dependent region path are excluded.
* Mission-only groups retain their explicit conditions but have no inferred
  planets. Screenplay functions/control blocks remain conditional diagnostics.
* `bone_mammal` and `meat_reptilian` lack exact canonical rows. Raw substring
  matching remains possible against recorded final resource types. No taxonomy
  row was invented to hide the missing mappings.
* `dungeon1` and `tutorial` lack mapped catalog planets/region roots. Hoth has no
  reviewed active-zone/spawn support; nothing was fabricated for Hoth.

The JSON artifact records the complete diagnostic counts and source locations for
quarantined registrations/references. Many diagnostics identify intentionally
unsupported screenplay or non-creature registry calls, rather than malformed
creature definitions. Unsupported expressions never authorize additional spawns.

## Validation

The full suite passed **486 tests**, including **41 new catalog tests**. One
existing Starlette/httpx TestClient deprecation warning remains.

Tests ran only against the temporary PostgreSQL 16 database
`127.0.0.1:55437/bellum_resources_test`. The fixture enforces loopback and the
`_test` suffix and rejects the configured development database.

Coverage includes inheritance and parent rebinding, all categories, zero/positive
amounts, multi-planet/multi-region paths, raw substring compatibility, cross-planet
rejection, historical/current/stale snapshots, a GH/Core3 instance-code collision,
unknown planets, conditional/static/mission-only spawns, nested Lua control flow,
world geometry, duplicate registrations/repeated includes, dry-run, idempotency,
revision activation/rollback, late-write rollback, CLI source verification and
existing historical/live importer regressions.

Alembic downgrade-to-base/upgrade-head/downgrade/re-upgrade passed. Actual source
dry-run and import succeeded; identical reimport was idempotent. Protected
resource-table row counts remained unchanged, and automated tests also compare an
existing current-resource projection row before/after importing and matching.
Python compilation and whitespace checks passed.

No production server/database, DNS, Nginx installation or Core3 file/service was
modified. Docker was not restarted. The temporary test container was created for
validation and removed afterward. No changes were committed or pushed.

See [the usage and limitations](creature-harvesting-foundation.md) and
[the machine-readable validation record](phase7b1-validation.json).
