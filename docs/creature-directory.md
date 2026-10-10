# Phase 7B.2 — public Creature Harvesting Directory

This phase exposes the existing Phase 7B.1 catalog through the existing FastAPI,
Jinja2 and CSS application. It does not load Lua during requests, replace the
importer, create a second resource matcher, or write resource availability.

## Public pages

| Route | Purpose |
| --- | --- |
| `GET /creatures` | Search and filter the directory |
| `GET /creatures/{id}` | Creature definitions, verified planet paths, conditions and planet-specific resource candidates |
| `GET /resources/{id}/creatures` | Creatures compatible with a named resource on its recorded planets |

The primary navigation includes Creatures. Existing named-resource detail pages
link to reverse lookup. Creature IDs are stable `creature_identities.id` values;
only definitions from the selected validated revision of `bellum-gero-live` are
visible. Resource IDs are existing **source-resource IDs**, matching the resource
directory, not canonical resource IDs.

Default directory results require a positive, nonempty Meat, Hide or Bone
definition and verified source-defined spawn evidence. `include_other=true`
includes other validated catalog creatures, including unlocated/nonharvestable
ones. It never bypasses selected-planet evidence checks. Quarantined and unresolved
creature definitions do not become public verified creatures.

Directory query parameters:

| Parameter | Meaning |
| --- | --- |
| `name` | Literal substring of custom name, localization reference or template identifier, maximum 160 characters |
| `planet` | Existing catalog planet slug; every result must have verified evidence on this planet |
| `category` | `hide`, `meat`, `bone`, `milk`; filter requires a positive definition |
| `resource_class` | Exact trimmed raw lookup or an existing exact canonical harvest-class mapping; maximum 160 characters |
| `min_level`, `max_level` | Nonnegative integer bounds; unknown levels do not satisfy a bound |
| `spawn_type` | `dynamic`, `static`, `mission`, `quest`, `event`, `dungeon`, `unknown` |
| `include_other` | `true` or `false`, default `false` |
| `page`, `page_size` | Page 1–10000; size 1–100, default 25 |

Category and class filters apply to the **same harvest definition**. Planet and
spawn-type filters apply to the **same evidence path**. Unknown location filtering
can include unlocated creatures when other catalog creatures are enabled and no
planet is selected. Conditional type labels require explicit source conditions;
they are not inferred from filenames, creature names or mission-group names.

Examples:

```text
/creatures?name=bantha&planet=rori&category=hide
/creatures?resource_class=bone_mammal&min_level=10&max_level=30
/creatures?include_other=true&spawn_type=unknown
/creatures?planet=naboo&spawn_type=event&page_size=50
```

Detail pages accept `planet`, `category`, `page`, `page_size` and `resource_page`.
Evidence pagination uses `page`; resource pagination uses `resource_page` so they
do not advance together. Selecting a planet explicitly enables resource matching;
the application does not guess the player's planet.

## Read-only API contracts

| Endpoint | Parameters and response |
| --- | --- |
| `GET /api/creatures` | Directory parameters; paginated creature summaries |
| `GET /api/creatures/{id}` | No query parameters; validated creature details and public provenance |
| `GET /api/creatures/{id}/resources` | Required `planet`, optional `category`, `page`, `page_size`; independently paginated category results |
| `GET /api/creatures/{id}/spawn-evidence` | Optional `planet`, `page`, `page_size`; paginated individual evidence records |
| `GET /api/resources/{id}/creatures` | Optional `planet`, `category`, `page`, `page_size`; paginated distinct creatures with all eligible planet/category matches |

Paginated collections use the existing pattern:

```json
{"items": [], "total": 0, "page": 1, "page_size": 25, "pages": 0}
```

Creature summaries include identity/template ID, display name, level, revision,
effective raw harvest lookup strings and base amounts, verified planets and spawn
types. Detail also includes localization reference, source import time, sanitized
field/file provenance, mission-group identifiers and unresolved-location warnings.
The localization reference is not presented as an invented translated name.

Evidence records preserve individual locations and source conditions. They expose
planet/name/type, `location_kind`, `world_spawn`, geometry, confidence, and public provenance.
Location kind remains static or region even when an event/quest condition changes
the spawn-type label; conditional static sites therefore keep their coordinate meaning.
Static X/Y coordinates and Z/height are distinct from dynamic-region origins or
centers. Shapes use `kind` and numeric `parameters`. World sentinels have geometry
`null` and never become `(0,0)` waypoints. Missing locations remain unknown.

Resource responses contain `creature_id`, selected planet, source revision,
snapshot status, notice and `categories`. Each category has its own paginated
`items`, matching status and uncertainty. Candidates include source-resource ID,
name, source system/instance, final/mapped type, timestamps, source record/batch
IDs, snapshot hash, compatibility method and public availability label:

* `current`: compatible selected-planet observation in an authoritative, fresh
  Core3 current projection.
* `stale`: compatible authoritative projection outside the website's configured
  freshness window, or with a future/unusable freshness timestamp.
* `historical`: historical GH record or previously observed Core3 compatibility
  that is not current on this planet.
* `unknown`: Core3 observations without a current projection or without an
  authoritative current snapshot ledger. These do not prove present availability.

Freshness uses the existing `BELLUM_SNAPSHOT_FRESHNESS_HOURS` setting. GH always
remains historical. When no authoritative snapshot exists, the page/API displays:

> No authoritative current resource snapshot is available.

Reverse lookup uses the **same shared compatibility expression and candidate
status function** as the Phase 7B.1 forward service. It first restricts the named
resource to its recorded planets, then requires a verified creature path on each
planet and a positive carcass harvest definition. Pagination deduplicates
creatures while retaining all matching planet/category entries. Milk is excluded
from reverse runtime compatibility until its separate runtime path is validated.

Invalid parameters, duplicate query keys, unknown planets or out-of-range IDs
return the application's existing structured `422` errors. Missing or inactive
creature/resource identities return structured `404`; database failures use the
existing generic `503` handler. Empty valid searches return `200` with empty items.

## Safety and performance

Each new route uses a **read-only, repeatable-read transaction**. All queries in a
response therefore see one activation/snapshot state, and accidental writes are
blocked by PostgreSQL. Existing authentication, admin writes and CSP remain intact.
Imported text is autoescaped by Jinja; no new JavaScript/framework is introduced.

Public projections allowlist provenance, geometry, conditions and resource
fields. Source paths must be repository-relative `MMOCoreORB/...` paths. Absolute
paths, parent traversal, backslash paths and raw diagnostic/configuration payloads
are not exposed. A suppressed path is `null`, while source revision/hash/line
provenance remains available when present.

Directory counts use `EXISTS` predicates rather than multiplying creature rows by
spawn/harvest joins. A bounded page is hydrated in bulk for definitions and planet
summaries; there is no per-creature query loop. Evidence pages are limited to 100.
Forward matching reduces observation histories to the latest row per resource
before database pagination; each request makes at most four category lookups.
Reverse lookup reduces observations per recorded planet and uses indexed evidence
existence checks, with distinct-definition pagination and bulk hydration.

Existing revision/status, category/lookup, definition/planet and resource/planet
observation indexes are reused. No additional Phase 7B.2 migration is required.
Substring name/type queries and exact total counts can still require scans as the
archive grows; monitor production-scale query plans before adding caching or
specialized indexes. No request creates an unbounded creature × resource × spawn
evidence join. The original unpaginated internal matcher remains compatible;
all public callers provide bounded pagination.

## Player-facing limitations

Potential source-defined locations are not a live creature census. Static and
dynamic definitions do not prove current activation; mission, quest, dungeon and
event eligibility may remain unresolved. Unverified conditional definitions may
produce warnings but never expand verified planet availability.

Core3 selects the first zone resource whose final type contains the creature's
trimmed raw lookup. It does not select a guaranteed highest-stat candidate. The
runtime iteration order is not exported. Density, skill, loot rights, creature
state and activation conditions also affect harvesting.

Some raw classes, including `bone_mammal` and `meat_reptilian`, have no exact
canonical catalog row. They remain usable for actual substring compatibility and
raw-class filtering. No mapping or planet availability is fabricated to hide the
gap. Milk definitions are displayed, with runtime matching explicitly unvalidated.

Hoth has no reviewed active-zone/spawn support in the source revision used for
Phase 7B.1, and is not added by this website phase. A future custom planet requires
an authoritative Planet mapping, validated creature spawn evidence and independent
planet-specific named resource observations. A matching resource class is never
sufficient evidence of planet availability.

See the Phase 7B.2 implementation report for validation results and render artifacts.
