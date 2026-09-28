# Core3 Entangle Resistance Investigation

Phase 2A investigated whether Core3 `entangle_resistance` should be mapped to a canonical `ER` stat for Bellum Gero Resources.

## Conclusion

`entangle_resistance` is a verified Core3 runtime resource attribute: Core3 reads it from the resource tree, stores it on `ResourceTreeEntry`, generates values for dynamic resource spawns, preserves it in scripted resource spawns, persists it on `ResourceSpawn`, and exposes it through generic resource attribute display paths.

It is not a verified Core3 crafting stat enum. Core3 has no `CraftingManager::ER`, no `res_entangle` or `res_entangle_resist` mapping, and no typed `ResourceSpawn::getValueOf(int stat)` path for entangle resistance. Phase 2 therefore keeps `ER` out of the Core3 static index and reports `entangle_resistance` as an unknown Core3 attribute pending an explicit future mapping decision.

Classification: **A, verified runtime stat**, with this qualification: it is a generic runtime resource attribute, not a verified typed crafting stat.

## Source References

Meaningful current-tree references found in `C:\Users\User\BellumGero-Live\BellumGero-Live`:

- `MMOCoreORB/sql/datatables.sql`: 23 `resource_resource_tree` rows contain `entangle_resistance`.
- `MMOCoreORB/bin/scripts/managers/resource_manager_spawns.lua`: 140 scripted spawn attributes contain `entangle_resistance`.
- `MMOCoreORB/bin/scripts/managers/ghoutput.xml`: 32 exported/generated resource attributes contain `entangle_resistance`.
- `MMOCoreORB/doc/ConversationEditor/stringfiles.js`: `entangle_resistance` and `tooltip_entangle_resistance` localize to "Entangle Resistance".

Archived or legacy references also exist in `MMOCoreORB/sql/updates/archived/unused_Tables.sql`, `r2489_client_strings.sql`, and `PRE_TC_OR_swgemu.sql`.

No meaningful source-code references were found for `res_entangle`, `res_entangle_resist`, `CraftingManager::ER`, or a `public static final short ER` equivalent.

## Affected Resource Types

All 23 affected types are gemstone-related:

| Source type | Display name | Parent | Entangle range |
| --- | --- | --- | --- |
| `armophous_baltaran` | Bal'ta'ran Crystal Amorphous Gemstone | `gemstone_armophous` | 696..800 |
| `armophous_baradium` | Baradium Amorphous Gemstone | `gemstone_armophous` | 57..185 |
| `armophous_bospridium` | Bospridium Amorphous Gemstone | `gemstone_armophous` | 1..105 |
| `armophous_plexite` | Plexite Amorphous Gemstone | `gemstone_armophous` | 217..345 |
| `armophous_regvis` | Regvis Amorphous Gemstone | `gemstone_armophous` | 137..265 |
| `armophous_rudic` | Rudic Amorphous Gemstone | `gemstone_armophous` | 297..424 |
| `armophous_ryll` | Ryll Amorphous Gemstone | `gemstone_armophous` | 377..504 |
| `armophous_sedrellium` | Sedrellium Amorphous Gemstone | `gemstone_armophous` | 456..584 |
| `armophous_stygium` | Stygium Amorphous Gemstone | `gemstone_armophous` | 536..664 |
| `armophous_vendusii` | Vendusii Crystal Amorphous Gemstone | `gemstone_armophous` | 616..744 |
| `crystalline_byrothsis` | Byrothsis Crystalline Gemstone | `gemstone_crystalline` | 500..581 |
| `crystalline_gallinorian` | Gallinorian Rainbow Gem Crystalline Gemstone | `gemstone_crystalline` | 544..644 |
| `crystalline_green_diamond` | Green Diamond Crystalline Gemstone | `gemstone_crystalline` | 606..706 |
| `crystalline_kerol_firegem` | Kerol Fire-Gem Crystalline Gemstone | `gemstone_crystalline` | 669..769 |
| `crystalline_laboi_mineral_crystal` | Laboi Mineral Crystal Crystalline Gemstone | `gemstone_crystalline` | 856..956 |
| `crystalline_seafah_jewel` | Seafah Jewel Crystalline Gemstone | `gemstone_crystalline` | 731..831 |
| `crystalline_sormahil_firegem` | Sormahil Fire Gem Crystalline Gemstone | `gemstone_crystalline` | 794..894 |
| `crystalline_vertex` | Vertex Crystalline Gemstone | `gemstone_crystalline` | 919..1000 |
| `gemstone` | Gemstone | `mineral` | 1..1000 |
| `gemstone_armophous` | Amorphous Gemstone | `gemstone` | 1..800 |
| `gemstone_crystalline` | Crystalline Gemstone | `gemstone` | 500..1000 |
| `gemstone_mixed_low_quality` | Low Quality Gemstones | `gemstone` | 200..200 |
| `gemstone_unknown` | Unknown Gem Type | `gemstone` | 1..1000 |

## Runtime Behavior

`ResourceTree::buildTreeFromClient()` reads resource attribute name/min/max columns without a whitelist and calls `ResourceTreeEntry::addAttribute()`. `ResourceTreeEntry` stores attributes in an `attributeMap` keyed by the original string. `ResourceAttribute` preserves unknown names and ranges, although unknown names keep `index = 0` because `ResourceAttribute::setIndex()` maps only the ten verified Core3 crafting stats.

`ResourceSpawner::createResourceSpawn()` iterates every `ResourceTreeEntry` attribute, randomizes a value within min/max, and calls `newSpawn->addAttribute(attribName, randomValue)`. `ResourceSpawner::spawnScriptResources()` also reads every Lua `attributes` pair and calls `newSpawn->addAttribute()` with the original string key.

`ResourceSpawn` stores attributes in `VectorMap<string, int> spawnAttributes`. `ResourceSpawnImplementation::addAttribute()` inserts arbitrary string keys, `getValueOf(string attribute)` can retrieve them, and the generic examine/deed display paths iterate all stored attributes. `ResourceContainerImplementation` delegates resource-container attribute display to the spawn object, so players can see arbitrary stored spawn attributes when localization exists.

Typed crafting/stat lookup is different. `CraftingManager.idl` defines only `CR`, `CD`, `DR`, `HR`, `FL`, `MA`, `PE`, `OQ`, `SR`, and `UT`. `ResourceSpawnImplementation::getValueOf(int stat)` switches only over those ten constants, and `ResourceCommand.h` accepts only those ten resource-stat filters. No schematic or experimentation consumer of `entangle_resistance` was found.

## Git History

`git log -G` over `MMOCoreORB` found entangle-related history in SQL/resource dumps, resource manager spawn files, and client-string material. The history did not reveal a later Core3 `ER` enum, `res_entangle` mapping, or crafting-stat integration.

## Phase 2 Indexer Impact

Phase 2 produced 7,250 stat range rows because the inspected Core3 taxonomy has 725 resource types and the indexer emits one normalized slot for each of the 10 verified Core3 crafting stats:

`725 resource types * 10 verified Core3 stats = 7,250 stat range rows`

Unknown attributes such as `entangle_resistance` are reported as validation warnings and are not converted into synthetic `ER` rows.

## Tree Structure

The Phase 2 static inspection found:

- source of truth: `MMOCoreORB/sql/datatables.sql`
- resource types: 725
- direct hierarchy edges: 724
- transitive closure rows: 4,828
- root count: 1, `resource`
- multi-parent nodes: 0
- all nodes reachable from root: yes
- maximum hierarchy depth: 7
- validation errors: 0
- validation warnings: 23 unknown `entangle_resistance` attributes

The current tree behaves as a single-parent hierarchy, although the Bellum Gero schema and closure builder support DAG data if Core3 data ever requires it.

## Tests

Phase 2A added focused tests confirming that:

- `entangle_resistance` is reported as an unknown Core3 attribute and is not mapped to `ER`.
- normalized indexes emit exactly one stat range slot per verified Core3 stat per resource type.
