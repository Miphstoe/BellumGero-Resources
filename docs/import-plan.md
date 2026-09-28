# Import Plan

## Phase Order

1. Finalize and review this design.
2. Create database migrations for provenance, planets, stats, resource types, source identities, observations, anomalies, and reconciliation.
3. Seed stat definitions, source systems, known planets, and initial Core3/GH source instances.
4. Build a Core3 static resource type indexer from `resource_tree.iff` or an exported equivalent.
5. Build a GH historical importer that accepts an external archive path and import manifest.
6. Import GH historical data into source/provenance tables only; do not auto-merge with Core3.
7. Build read-only resource website/API from GH historical data.
8. Build the private Core3 resource exporter.
9. Build the private Core3 importer and current availability views.
10. Add reconciliation review tools for GH/Core3 candidate matches.
11. Add schematic static indexer.
12. Add crafting compatibility and best-resource queries.
13. Add creature indexer and harvest resource views.
14. Add loot/items.
15. Add Discord/resource alerts.

Stop after each major phase for review before expanding the blast radius.

## GH Historical Import

Importer input:

- external archive path such as `/home/miphstoe/GalaxyHarvester-Research/research/galaxyharvester/galaxy-153/`
- source manifest with frozen name list, expected counts, hashes, and archive metadata
- target source instance: `galaxy_harvester:galaxy-153`

Importer behavior:

- refuse to run if the archive path is missing unless explicitly running in schema-only dry run mode
- never copy raw XML into this repository
- compute source file hash and byte size
- create an `import_batch`
- create `source_snapshots` and `source_records`
- insert GH resource rows as `source_resources`
- insert stat rows as `resource_stat_observations`
- insert planet evidence as `resource_planet_observations`
- insert source type rows and ranges as `source_resource_types` and `resource_type_stat_ranges`
- create `unresolved_source_resources` rows for unresolved names
- create anomaly rows for out-of-range values such as `bipa`

Idempotency:

- unique source identities prevent duplicate GH spawn IDs per source instance
- source record hashes allow unchanged files to be skipped
- import batch summary records counts, unresolved names, anomalies, and checksum validation

## Seven Unresolved GH Names

Create rows in `unresolved_source_resources` only:

- `source_instance_id = galaxy_harvester:galaxy-153`
- `source_name`
- `result_status = unresolved_new_result`
- `http_status = 200`
- `source_result_text = new`
- parsed response/source record reference where available

Do not create:

- `resources`
- `source_resources`
- fake `spawnID`
- fake planet availability
- fake stats

## bipa Anomaly Handling

Import `bipa` exactly as source data reports it:

- `DR = 154`
- `FL = 217`
- `PE = 880`

Then insert anomaly records:

- `stat_below_source_type_min` for `DR`
- `stat_present_when_type_range_not_applicable` or `stat_outside_source_type_range` for `FL`
- `stat_above_source_type_max` for `PE`

Do not clamp or correct values.

## Core3 Exporter Later

Exporter should produce a snapshot like:

```json
{
  "schema_version": 1,
  "server_instance": "bellum-gero-live",
  "server_epoch": "epoch-code",
  "core3_revision": "...",
  "exporter_version": "...",
  "observed_at": "...",
  "complete": true,
  "resources": [
    {
      "core3_object_id": "...",
      "name": "...",
      "type": "...",
      "classes": ["..."],
      "stf_classes": ["..."],
      "attributes": {
        "res_quality": 900
      },
      "spawned": null,
      "despawned": 1234567890,
      "planets": ["corellia"],
      "pool": "...",
      "zone_restriction": ""
    }
  ]
}
```

Important exporter rule: include only attributes present in `spawnAttributes`, not `getValueOf()` results for every possible stat. That is how the website preserves missing versus explicit zero.

## Migration Path

Safe path:

1. Historical GH import into source-only tables.
2. Build website pages from source-only historical data.
3. Add Core3 authoritative live feed into separate source identities.
4. Add reconciliation candidate generation and manual confirmation.
5. Add schematic static data.
6. Add compatibility queries using type closure.
7. Add best-resource scoring.
8. Add creature data and harvest mapping.
9. Add loot/items and alerts.

Do not block the first website version on perfect reconciliation.

## Risks and Open Questions

- The expected GH XML archive directory was not present at the documented path during this research pass. Before implementing the importer, locate or restore that archive and manifest.
- Core3 `spawned` is defined on `ResourceSpawn` but was not observed being set in the verified creation path. Confirm whether Bellum Gero sets it elsewhere or whether exporter first-observed time should be the reliable creation observation.
- Core3 creature harvest lookup uses substring matching on final type in `getCurrentSpawn`; decide later whether the website should mirror that exactly for creature pages or use ancestry-correct matching with a compatibility note.
- Determine whether Core3 has any hidden/non-resource use of Entangle Resistance before deciding ER is forever GH-only.
- Decide public URL identity shape: UUID, slug with disambiguator, or source-qualified IDs for historical pages.
- Confirm whether resource type canonicalization should start from Core3 `resource_tree.iff` only, then map GH types, or seed with GH then overlay Core3. Recommendation: Core3 first.

