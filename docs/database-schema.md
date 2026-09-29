# Database Schema Proposal

This is a logical schema for PostgreSQL. It is not a migration.

## Conventions

- Surrogate primary keys are UUIDs or generated bigint IDs. UUIDs are useful for public URLs; bigint is simpler internally. Either is acceptable. The examples use UUID public IDs plus natural unique constraints.
- Timestamps are `timestamptz` when they represent an observed/imported time. Source timestamps should store the original value and timezone confidence.
- Source payload references point to external files and hashes, not copied raw XML.
- Stat values are stored as nullable integers in row form, not wide columns only.

## Provenance

### `source_systems`

Rows identify systems such as `galaxy_harvester` and `core3`.

Columns:

- `id` PK
- `code` text unique not null
- `name` text not null
- `description` text

### `source_instances`

Examples: GH galaxy 153; Bellum Gero Core3 live server.

Columns:

- `id` PK
- `source_system_id` FK
- `code` text not null
- `display_name` text not null
- `external_id` text
- `metadata` jsonb not null default `{}`
- unique `(source_system_id, code)`

For GH 153: `source_system=galaxy_harvester`, `code=galaxy-153`, `external_id=153`.

### `server_instances`

Core3 server installations.

Columns:

- `id` PK
- `code` text unique not null
- `display_name` text not null
- `environment` text not null
- `created_at` timestamptz not null
- `retired_at` timestamptz

### `server_epochs`

Represents wipes, imports, or identity-reset windows.

Columns:

- `id` PK
- `server_instance_id` FK not null
- `epoch_code` text not null
- `started_at` timestamptz
- `ended_at` timestamptz
- `description` text
- unique `(server_instance_id, epoch_code)`

Resource names may be reused across epochs; source identity must include epoch where applicable.

### `import_batches`

Columns:

- `id` PK
- `source_instance_id` FK not null
- `import_kind` text not null
- `status` text not null
- `started_at` timestamptz not null
- `finished_at` timestamptz
- `tool_version` text
- `source_revision` text
- `parameters` jsonb not null default `{}`
- `summary` jsonb not null default `{}`

### `source_snapshots`

Columns:

- `id` PK
- `import_batch_id` FK not null
- `snapshot_kind` text not null
- `external_path` text
- `content_sha256` text
- `byte_size` bigint
- `record_count` integer
- `observed_at` timestamptz
- `source_entered_at` timestamptz
- `metadata` jsonb not null default `{}`
- unique `(import_batch_id, external_path, content_sha256)`

External raw archive paths are stored here; files are not copied into Git.

### `source_records`

Generic parsed record provenance.

Columns:

- `id` PK
- `source_snapshot_id` FK
- `record_type` text not null
- `source_key` text
- `parse_status` text not null
- `parse_error` text
- `payload_ref` text
- `payload_hash` text
- `normalized_payload` jsonb
- unique `(source_snapshot_id, record_type, source_key)`

Use this for unresolved GH names too.

### `anomalies`

Columns:

- `id` PK
- `source_record_id` FK
- `entity_kind` text not null
- `entity_id` uuid
- `anomaly_code` text not null
- `severity` text not null
- `message` text not null
- `details` jsonb not null default `{}`
- `created_at` timestamptz not null
- unique `(source_record_id, anomaly_code, entity_kind, entity_id)`

`bipa` should produce stat range anomalies without changing source values.

## Planets

### `planets`

Columns:

- `id` PK
- `slug` text unique not null
- `display_name` text not null
- `core3_zone_name` text
- `active` boolean not null default true

### `source_planets`

Columns:

- `id` PK
- `source_instance_id` FK not null
- `source_planet_id` text not null
- `source_planet_name` text not null
- `planet_id` FK
- `metadata` jsonb not null default `{}`
- unique `(source_instance_id, source_planet_id)`

## Stats

### `stat_definitions`

Columns:

- `code` text PK
- `display_name` text not null
- `core3_attribute_name` text
- `description` text
- `is_canonical` boolean not null default true

Seed canonical codes:

- `CR`, `CD`, `DR`, `FL`, `HR`, `MA`, `PE`, `OQ`, `SR`, `UT`, `ER`

`ER` display name: `Entangle Resistance`. `core3_attribute_name` remains null unless a future Core3 equivalent is verified.

Core3 live snapshots accept only `CR`, `CD`, `DR`, `FL`, `HR`, `MA`, `PE`,
`OQ`, `SR`, and `UT`. `ER` is rejected for Core3 imports. The importer follows
the established full-matrix observation convention: each Core3 resource snapshot
creates one observation row for each of the ten supported Core3 stat
definitions. Missing source values are stored as `is_present=false,
value=null`; explicit zero values are stored as `is_present=true, value=0`.

## Resource Types

### `resource_types`

Canonical Bellum Gero resource type nodes.

Columns:

- `id` PK
- `slug` text unique not null
- `display_name` text not null
- `kind` text not null
- `is_spawnable` boolean not null default false
- `core3_stf_name` text
- `core3_display_class` text
- `container_type` text
- `inventory_type` text
- `specific_planet_id` FK nullable
- `metadata` jsonb not null default `{}`

### `resource_type_edges`

Adjacency list.

Columns:

- `parent_type_id` FK not null
- `child_type_id` FK not null
- `source` text not null
- PK `(parent_type_id, child_type_id, source)`
- check `parent_type_id <> child_type_id`

### `resource_type_closure`

Closure table for fast compatibility queries.

Columns:

- `ancestor_type_id` FK not null
- `descendant_type_id` FK not null
- `depth` integer not null
- PK `(ancestor_type_id, descendant_type_id)`
- index `(descendant_type_id, ancestor_type_id)`

This supports "can final type X fill slot requiring ancestor Y?" with an indexed lookup.

### `source_resource_types`

Columns:

- `id` PK
- `source_instance_id` FK not null
- `source_type_key` text not null
- `source_type_name` text
- `canonical_type_id` FK
- `mapping_status` text not null
- `mapping_confidence` text not null
- `metadata` jsonb not null default `{}`
- unique `(source_instance_id, source_type_key)`

### `resource_type_stat_ranges`

Canonical or source-specific range evidence.

Columns:

- `id` PK
- `resource_type_id` FK
- `source_resource_type_id` FK
- `stat_code` FK to `stat_definitions`
- `min_value` integer
- `max_value` integer
- `is_applicable` boolean not null
- `source_record_id` FK
- check `resource_type_id is not null or source_resource_type_id is not null`
- unique `(resource_type_id, stat_code)` for canonical rows
- unique `(source_resource_type_id, stat_code)` for source rows

Nullable semantics:

- `is_applicable=false`, `min_value=null`, `max_value=null`: not applicable.
- `is_applicable=true`, `min_value=0`, `max_value=0`: explicit zero-only stat if ever verified.
- Do not use GH's `0/0` directly as the only not-applicable signal after import.

## Resources

### `resources`

Canonical resources are created only when a real resource identity is known or manually confirmed.

Columns:

- `id` PK
- `public_slug` text unique
- `name` text not null
- `canonical_type_id` FK nullable until reconciled
- `created_at` timestamptz not null
- `status` text not null
- `notes` text

Do not create rows here for the seven unresolved GH names.

### `source_resources`

Source-specific resource identities.

Columns:

- `id` PK
- `source_instance_id` FK not null
- `server_instance_id` FK nullable
- `server_epoch_id` FK nullable
- `source_resource_id` text not null
- `source_resource_name` text not null
- `source_resource_type_id` FK
- `canonical_resource_id` FK nullable
- `identity_status` text not null
- `confidence` text not null
- `first_source_record_id` FK
- `last_source_record_id` FK
- `metadata` jsonb not null default `{}`
- unique `(source_instance_id, server_epoch_id, source_resource_id)`
- index `(source_instance_id, lower(source_resource_name))`

GH `spawnID` belongs here as `source_resource_id`. Core3 object ID/export ID belongs here in a different source instance and epoch.

### `resource_names`

Columns:

- `id` PK
- `canonical_resource_id` FK nullable
- `source_resource_id` FK nullable
- `name` text not null
- `normalized_name` text not null
- `source_record_id` FK
- unique `(source_resource_id, normalized_name)`
- index `(normalized_name)`

This supports matching without making name the canonical identity.

### `resource_stat_observations`

Columns:

- `id` PK
- `source_resource_id` FK not null
- `stat_code` FK not null
- `value` integer
- `is_present` boolean not null
- `observed_at` timestamptz
- `source_record_id` FK
- `import_batch_id` FK not null
- unique `(source_resource_id, stat_code, source_record_id)`

Nullable semantics:

- `is_present=false`, `value=null`: missing/not-applicable in source.
- `is_present=true`, `value=0`: explicit zero.
- `is_present=true`, `value=null`: invalid; reject with a check constraint.

### `resource_type_memberships`

Optional materialized membership per source resource.

Columns:

- `source_resource_id` FK not null
- `resource_type_id` FK not null
- `membership_kind` text not null
- `source_record_id` FK
- PK `(source_resource_id, resource_type_id)`

This can be derived from final type plus closure, but materializing helps importer audits and source-specific ancestry preservation.

## Availability and History

### `resource_observations`

General observation facts.

Columns:

- `id` PK
- `source_resource_id` FK not null
- `source_record_id` FK
- `observation_kind` text not null
- `observed_at` timestamptz
- `source_entered_at` timestamptz
- `source_unavailable_at` timestamptz
- `confidence` text not null
- `details` jsonb not null default `{}`
- index `(source_resource_id, observation_kind, observed_at)`

Kinds include `gh_resource_row`, `core3_snapshot_resource`, `core3_create_event`, `core3_despawn_event`.

### `resource_planet_observations`

Columns:

- `id` PK
- `source_resource_id` FK not null
- `planet_id` FK not null
- `source_planet_id` FK
- `state` text not null
- `observed_at` timestamptz
- `source_entered_at` timestamptz
- `source_unavailable_at` timestamptz
- `confidence` text not null
- `source_record_id` FK
- `import_batch_id` FK not null
- details jsonb not null default `{}`
- index `(planet_id, state, observed_at)`
- index `(source_resource_id, planet_id, observed_at)`

GH imports should record available-as-archived or unavailable-as-archived evidence without inventing continuous intervals. Core3 exports can add authoritative current membership and later create/remove events.

### `resource_lifecycle_events`

Columns:

- `id` PK
- `source_resource_id` FK not null
- `event_type` text not null
- `event_time` timestamptz
- `event_time_confidence` text not null
- `planet_id` FK nullable
- `source_record_id` FK
- `import_batch_id` FK not null
- `details` jsonb not null default `{}`
- index `(source_resource_id, event_type, event_time)`

Do not collapse GH `entered`, GH `unavailable`, first exporter observation, last observation, Core3 created, and Core3 despawned into one misleading pair of columns.

### `core3_live_snapshot_imports`

Ledger of Core3 live snapshots accepted by the importer.

Columns:

- `id` PK
- `source_instance_id` FK not null
- `source_snapshot_id` FK not null unique
- `captured_at` timestamptz not null
- `content_sha256` text not null
- `complete` boolean not null
- `status` text not null
- `advanced_current` boolean not null default false
- `imported_at` timestamptz not null default now
- `metadata` jsonb not null default `{}`
- unique `(source_instance_id, captured_at)`
- index `(source_instance_id, captured_at)`
- index `(source_instance_id, advanced_current)`

For a source instance and `captured_at`, the database permits only one ledger
row. The same content hash is idempotent at the importer layer. A different
hash is a snapshot conflict and must not mutate authoritative current state.
`advanced_current=false` allows valid historical/out-of-order snapshots to be
retained without moving current availability backward.

### `current_resource_availability`

Materialized authoritative current state for source resources.

Columns:

- `source_resource_id` FK primary key
- `source_instance_id` FK not null
- `is_active` boolean not null
- `last_seen_source_snapshot_id` FK nullable
- `last_seen_at` timestamptz nullable
- `current_source_snapshot_id` FK not null
- `current_as_of` timestamptz not null
- `absent_source_snapshot_id` FK nullable
- `absent_as_of` timestamptz nullable
- `updated_at` timestamptz not null default now
- `details` jsonb not null default `{}`
- index `(source_instance_id, is_active)`
- index `(current_source_snapshot_id)`

Rows are keyed by database `source_resource_id`, not a bare Core3 OID. When a
newer complete Core3 snapshot advances current state, present resources become
active and previously active resources from the same source instance that are
absent become inactive as of that snapshot. Absence does not fabricate an exact
despawn event.

## Unresolved Source Records

### `unresolved_source_resources`

Columns:

- `id` PK
- `source_instance_id` FK not null
- `import_batch_id` FK not null
- `source_name` text not null
- `normalized_name` text not null
- `result_status` text not null
- `http_status` integer
- `source_result_text` text
- `source_record_id` FK
- `details` jsonb not null default `{}`
- unique `(source_instance_id, normalized_name, import_batch_id)`

The seven unresolved GH names live here. They do not create `resources` or `source_resources` rows.

## Reconciliation

### `resource_match_candidates`

Columns:

- `id` PK
- `left_source_resource_id` FK not null
- `right_source_resource_id` FK not null
- `score` numeric
- `status` text not null
- `evidence` jsonb not null default `{}`
- `created_at` timestamptz not null
- unique `(left_source_resource_id, right_source_resource_id)`

Evidence can include name, mapped type, stat similarity, temporal compatibility, planet evidence, and epoch. Do not auto-confirm on name alone.

### `resource_identity_links`

Confirmed links between source identities and canonical resources.

Columns:

- `canonical_resource_id` FK not null
- `source_resource_id` FK not null
- `link_status` text not null
- `confirmed_by` text not null
- `confirmed_at` timestamptz not null
- `evidence` jsonb not null default `{}`
- PK `(canonical_resource_id, source_resource_id)`
- unique `(source_resource_id)` where `link_status='confirmed'`

### `resource_identity_conflicts`

Columns:

- `id` PK
- `candidate_id` FK
- `conflict_type` text not null
- `details` jsonb not null
- `status` text not null
- `created_at` timestamptz not null

## Future Schematics

Do not implement now, but reserve schema direction:

- `schematics`
- `schematic_slots`
- `schematic_slot_allowed_types`
- `schematic_experimentation_properties`
- `schematic_experimentation_weights`
- `component_requirements`

Important indexes later:

- `resource_type_closure(ancestor_type_id, descendant_type_id)`
- `source_resources(canonical_resource_id)`
- current availability materialized view keyed by `(planet_id, resource_type_id)`
- `resource_stat_observations(stat_code, value)`

Slot compatibility query shape:

1. Slot requires type `T`.
2. Find current resources whose final type is a descendant of `T` using `resource_type_closure`.
3. Filter by planet/current state.
4. Join stats for scoring when recommendation logic is implemented.

## Future Creatures

Reserve schema direction:

- `creatures`
- `creature_source_identities`
- `creature_planet_observations`
- `creature_harvest_profiles`
- `creature_harvest_resources`

Harvest resource display later joins creature harvest type to current resource availability on the selected planet.

## Important Indexes

High-priority:

- `source_resources(source_instance_id, server_epoch_id, source_resource_id)` unique
- `source_resources(source_instance_id, lower(source_resource_name))`
- `resource_names(normalized_name)`
- `resource_type_closure(ancestor_type_id, descendant_type_id)`
- `resource_type_closure(descendant_type_id, ancestor_type_id)`
- `resource_planet_observations(planet_id, state, observed_at)`
- `resource_planet_observations(source_resource_id, planet_id, observed_at)`
- `resource_stat_observations(stat_code, value)`
- `resource_match_candidates(status, score)`
- trigram index on resource names for search

Later materialized views:

- `current_resource_availability`
- `current_resource_stats`
- `resource_search_documents`
- `resource_type_effective_ranges`

## Duplicate Prevention

Constraints should prevent:

- duplicate source system codes
- duplicate source instance codes per system
- duplicate source resource IDs per source instance and server epoch
- duplicate source type keys per source instance
- duplicate source planet IDs per source instance
- duplicate stat observations for the same source record/stat/resource
- confirmed linking of one source resource to multiple canonical resources

Names should be indexed, not globally unique.
