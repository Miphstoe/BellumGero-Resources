# Data Sources

## Bellum Gero Core3

Verified reference repository:

`C:\Users\User\BellumGero-Live`

Core3 is authoritative for Bellum Gero mechanics and future/current server observations.

Relevant verified files:

- `MMOCoreORB/src/server/zone/managers/resource/ResourceManagerImplementation.cpp`
- `MMOCoreORB/src/server/zone/managers/resource/resourcespawner/ResourceSpawner.cpp`
- `MMOCoreORB/src/server/zone/managers/resource/resourcespawner/resourcemap/ResourceMap.cpp`
- `MMOCoreORB/src/server/zone/managers/resource/resourcespawner/resourcetree/ResourceTree.cpp`
- `MMOCoreORB/src/server/zone/managers/resource/resourcespawner/resourcetree/ResourceTreeEntry.h`
- `MMOCoreORB/src/server/zone/managers/resource/resourcespawner/resourcetree/ResourceAttribute.h`
- `MMOCoreORB/src/server/zone/objects/resource/ResourceSpawn.idl`
- `MMOCoreORB/src/server/zone/objects/resource/ResourceSpawnImplementation.cpp`
- `MMOCoreORB/src/server/zone/objects/manufactureschematic/ingredientslots/ResourceSlot.h`
- `MMOCoreORB/src/server/zone/managers/crafting/labratories/SharedLabratory.cpp`
- `MMOCoreORB/src/server/zone/managers/creature/CreatureManagerImplementation.cpp`

### Resource Lifecycle Findings

Resource startup loads object storage named `resourcespawns` via `ObjectDatabaseManager::loadObjectDatabase("resourcespawns", true)`. Runtime resource spawns are `ResourceSpawn` objects and should not be read directly from the live Berkeley DB by the website.

Resource generation follows the expected path:

1. `ResourceTree` reads `datatables/resource/resource_tree.iff`.
2. `ResourceTreeEntry` carries the resource type, display class chain, STF class chain, min/max stat ranges, pool counts, zone restrictions, survey/recycle tool type, and container type.
3. `ResourceSpawner::createResourceSpawn` creates a `ResourceSpawn` in `resourcespawns`, assigns type/name/classes/stats, expiration, spawn maps, and pool membership.
4. `ResourceMap` indexes all spawned resources by lower-case resource name and active resources by planet/zone.
5. `ResourceSpawner::despawn` removes the spawn from planet maps and clears pool membership, but leaves the global resource map entry available for name lookup.

### Core3 Resource Identity

Core3 runtime objects have object IDs, names, final resource types, class/STF ancestry vectors, stats, planet spawn maps, and `despawned` timestamps. The database should treat Core3 IDs as source identities in a Core3 namespace, not as canonical website IDs.

Resource names are unique in the runtime map, but the schema should not use name alone as canonical identity. Server wipes and future imports can reuse names.

### Core3 Stat Findings

`ResourceSpawn` stores `spawnAttributes` as `VectorMap<string, int>`. Missing attributes are absent from the map. However, `ResourceSpawnImplementation::getValueOf(...)` returns `0` when an attribute is missing.

Core3's verified mapped stat set is:

- `CR` = `res_cold_resist`
- `CD` = `res_conductivity`
- `DR` = `res_decay_resist`
- `FL` = `res_flavor`
- `HR` = `res_heat_resist`
- `MA` = `res_malleability`
- `PE` = `res_potential_energy`
- `OQ` = `res_quality`
- `SR` = `res_shock_resistance`
- `UT` = `res_toughness`

No Core3 resource stat mapping for `ER` was found in the verified Core3 resource stat code. The schema must still preserve `ER` because Galaxy Harvester treats it as first-class historical source data.

### Resource Type Matching Findings

Core3 does not rely on string-prefix matching for crafting resource slots. `ResourceSlot::add` checks `incomingResource->getSpawnObject()->isType(contentType)`. `ResourceSpawn::isType` checks all STF classes and display classes attached to a spawn.

This means a resource of a narrow final type can satisfy an ancestor class required by a schematic slot. The website should model type ancestry explicitly with a closure table, not with string prefixes.

One caveat: `ResourceSpawner::getCurrentSpawn(restype, zoneName)`, used by creature harvesting, currently checks whether `resourceSpawn->getType().indexOf(restype) != -1` inside the planet's active resource map. That is a Core3 behavior to document and validate later, but crafting compatibility should follow `isType` ancestry.

### Planet Findings

Default active zones in Core3 include:

- `corellia`
- `dantooine`
- `dathomir`
- `endor`
- `lok`
- `naboo`
- `rori`
- `talus`
- `tatooine`
- `yavin4`

These should seed known planet mappings, but the schema should allow more planets.

### Crafting Findings

`SharedLabratory::getWeightedValue` computes weighted averages from filled resource and component slots by draft slot quantity. It ignores stats that resolve to `0`.

Future schematic support must know:

- schematic slot content type
- slot quantity
- slot kind/resource-or-component behavior
- resource type ancestry
- resource stats by code
- resource availability by planet/current lifecycle state

### Creature Harvesting Findings

Creature harvesting resolves meat, hide, bone, and milk resource type strings from creature data and calls `ResourceManager::getCurrentSpawn(restype, zoneName)`. Later creature pages can show current harvested resource by joining creature harvest type to currently available resource observations for a planet.

## Galaxy Harvester Research

Verified reference repository:

`/home/miphstoe/GalaxyHarvester-Research`

The repository was readable through WSL after permission escalation. The recovered XML archive directory expected at `/home/miphstoe/GalaxyHarvester-Research/research/galaxyharvester/galaxy-153/` was not present in the checkout during this pass, and a focused search under `/home/miphstoe` did not find a `galaxy-153` directory. Therefore, the archive metrics below are treated as request-supplied project facts, while the GH application/schema findings are verified from the repository.

### Request-Supplied Historical Archive Facts

Galaxy ID `153`, name `SWG Bellum Gero`.

Historical acquisition status:

- 18,593 frozen source names
- 18,586 validated historical resources
- 564 resource types
- 10 observed planets
- 472 resources available-as-archived
- 18,114 historically unavailable
- zero duplicate resource names
- zero duplicate GH spawnIDs
- zero missing required identities
- zero corrupt cached XML
- zero checksum/size mismatches
- zero cached names outside the frozen source snapshot

Unresolved GH names:

- `dweina`
- `eloate`
- `fopo`
- `golifo`
- `ileciium`
- `safe`
- `seekeheite`

These must remain unresolved source records and must not create canonical resources or fake spawn IDs.

Known source anomaly:

- `bipa`: `DR=154` while type bounds are `400-474`; `FL=217` while bounds are `0-0`; `PE=880` while bounds are `500-593`.

The source values must be preserved exactly and flagged as anomalies.

### Verified GH Schema Findings

`database/createSWGresourcedb.sql` defines:

- `tResources(spawnID, spawnName, galaxy, entered, enteredBy, resourceType, unavailable, unavailableBy, verified, verifiedBy, CR, CD, DR, FL, HR, MA, PE, OQ, SR, UT, ER)`
- `tResourcePlanet(spawnID, planetID, entered, enteredBy, unavailable, unavailableBy, verified, verifiedBy)`
- `tResourceType(... CRmin/CRmax ... UTmin/UTmax, ERmin/ERmax, containerType, inventoryType, specificPlanet, elective)`
- `tResourceTypeGroup(resourceType, resourceGroup)`
- `tResourceGroupCategory(resourceGroup, resourceCategory)`
- `tResourceEvents(galaxy, spawnID, userID, eventTime, eventType, planetID, eventDetail)`

GH uses `spawnID` as its resource row identity. In Bellum Gero Resources it should be stored as a source identity, not as a canonical resource ID.

GH resource type seed data includes resource type stat ranges. It uses zero min/max to mean the stat is not applicable for a type in many places. The Bellum Gero schema should normalize applicability instead of relying on zero ranges.

GH planet seed data includes more than the ten Bellum Gero observed historical planets, including Hoth, Dromund Kaas, Kashyyyk, Mandalore, Mustafar, Taanab, Lothal, Jakku, Chandrila, Nal Hutta, Ord Mantell, Kuat, Ghomrassen, Coruscant, Moraband, Florrum, and Sullust. The Bellum Gero schema should therefore avoid hard-coding ten planets.

### ER Investigation

Verified in GH code:

- `html/ghNames.py` maps `ER` to `Entangle Resist`.
- `html/ghObjects.py` displays `ER` as `Entangle Resistance`.
- `database/createSWGresourcedb.sql` stores `ER` on resources and `ERmin`/`ERmax` on resource types.
- GH search, filters, resource lists, resource pages, and imports treat `ER` as a first-class stat.

Verified in Core3 code:

- No `ER` mapping exists in `ResourceAttribute.h`.
- `CraftingManager.idl` declares `CR`, `CD`, `DR`, `HR`, `FL`, `MA`, `PE`, `OQ`, `SR`, and `UT`, but not `ER`.

Design conclusion: `ER` should be preserved as a canonical stat code with source availability metadata. For GH observations it is `source_supported=true`. For current Core3 observations it should be absent unless a future Bellum Gero exporter finds a legitimate equivalent. Do not map it to another Core3 stat and do not drop it.

## Privacy Boundary

GH has account, session, favorites, alerts, waypoints, friends, payments, reputation, and contributor fields. These should not be imported into public Bellum Gero Resources tables.

For GH resource provenance, actor fields such as `enteredBy`, `unavailableBy`, and `verifiedBy` should be omitted or reduced to a coarse actor class such as `user`, `system`, or `unknown`. Do not expose GH user IDs or contributor identity.
