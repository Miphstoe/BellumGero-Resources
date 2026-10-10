# Phase 7B.5 — Localization and presentation

Implemented on `feature/creature-harvesting-directory`, 2026-10-10.
Ready for review and a controlled staging deployment after approval. No deployment,
commit or push was performed. The initially clean working tree was preserved;
running staging containers and production services were not changed.

## Changes and resolution policy

The new HTML-only `DisplayNames` resolver supplies readable creature names,
region names and classification labels to the three creature page families.
It returns ordinary strings, allowing Jinja's existing autoescaping to protect
custom names, translation values and identifiers.

Creature name precedence: literal custom name; resolved custom/object-name
localization reference; existing literal display name; formatted template identifier.
Unresolved `@mob/creature_names:bantha` references use the creature template
fallback, preserving variant information such as `Bantha Matriarch`.
Underscores become spaces, words are title-cased, and digits remain intact.
Original names and identifiers are never overwritten.

Region references resolve by their full reference when a verified translation
mapping is supplied. Otherwise their key is formatted, for example
`@tatooine_region_names:western_dune_sea_1` becomes `Western Dune Sea 1`.
Malformed references without a key use `Unnamed source definition`.

Classifications prefer existing `resource_types.display_name` values, whose
importer derives names from the source taxonomy class path. Without a usable
taxonomy label, known category prefixes move to the end: `bone_mammal` becomes
`Mammal Bone`, `hide_wooly` becomes `Wooly Hide`. Other identifiers use the same
deterministic word formatter. Filter option values remain exact original codes.

The HTML routes perform two bulk presentation lookups for a nonempty page:
names for its bounded definition IDs and taxonomy names. There are no per-row
queries, schema writes or Lua/source reads during requests. API routes do not
call the presentation helper.

Template identifiers, classification identifiers, full revision hashes, source
paths and provenance remain accessible through native `<details>/<summary>`
disclosures. Creature level, quantities, recorded planets, location confidence,
missing-snapshot notices and uncertainty warnings remain visible. Region geometry
and coordinates still describe potential source locations, never guaranteed live
creature positions. Resource matching and quantity values are unchanged.

## Localization inspection and limits

Inspected the creature archive/importer, stored `customName`/`objectName`
references, spawn projections, resource taxonomy importer and saved offline
Core3 source structure. The repository and offline source contain no `.stf`
assets found by the inspection, and no reusable string-table resolver was present.
Lua localization references identify strings; they do not provide their values.
No game-server checkout or external service was accessed for this phase.

`DisplayNames(localization={full_reference: verified_value})` supports verified
localized values and has automated coverage. No translation map ships or is loaded
by the application because none was available to verify. Actual creature/region
localization references therefore use readable fallbacks today. These fallbacks
are not claimed to be official SWG translations. A future verified mapping can
be passed by the HTML presentation factory without changing API or database data.
Title-cased identifiers may not preserve unknown acronyms or official punctuation.

Search continues to cover its existing template/custom-name/reference fields;
formatted display labels and future translated values do not introduce a new
search field or alter substring semantics.

## Compatibility and validation

No API route path, service projection, field name, value or structure changed.
No importer, matching calculation, taxonomy relationship, activation rule,
snapshot rule, migration, Docker configuration or authentication code changed.
Regression tests compare API responses before/after HTML requests, including
geometry, conditions and provenance, and exercise all existing filter families.

Tests run using the repository virtual environment and a new disposable database
`bellum_phase7b5_test` on the existing isolated loopback PostgreSQL port 55440.
The retained release/recovery datasets and staging databases were not used for
these destructive test fixtures. The new database remains disposable.

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://bellum_test:isolated_local_test@127.0.0.1:55440/bellum_phase7b5_test'
./.venv/Scripts/python.exe -m pytest tests/web/test_creature_presentation.py tests/web/test_creature_directory.py -q --tb=short
./.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=.phase5a-output/phase7b5-tests.xml
git diff --check
```

Targeted result: **68 passed**, one existing warning, 6.51 seconds.
Full suite: **554 passed**, one existing warning, 23.07 seconds.
Warning: existing Starlette/httpx TestClient deprecation.
Coverage includes supplied localization values, missing/malformed references,
fallback names with digits/unusual identifiers, numeric region suffixes, taxonomy
precedence, unknown classifications, escaping, technical disclosures, retained
source facts, directory/detail/reverse rendering, name/planet/category/level/class
filters and unchanged API behavior. No browser visual acceptance is claimed.

## Controlled staging deployment and rollback procedure

Prepared instructions only; none executed. No staging-specific deployment file
was found in this repository inspection, so the exact existing staging project,
service, environment file, port and image identity must be confirmed by its
operator. Do not substitute production Compose defaults or guess staging names.

1. Review the diff and tests. After explicit authorization, create a release
   commit on the existing feature branch. Record the current staging image digest,
   container configuration, catalog revision and baseline routes. The supplied
   `c51fc98` is contextual baseline information, not a newly verified running image.
2. Build a distinct immutable candidate image from the reviewed feature revision
   without changing the running container. Confirm the package contains
   `app.web.creature_presentation` and all three updated templates.
3. Under a separately authorized staging window, select the already established
   staging environment/project and replace only its app service with the candidate
   image. No migration, taxonomy reload, catalog import or activation is needed.
   Preserve the exact database connection, volume, networks and proxy settings.
4. Check readiness and existing resource pages/APIs, then creature directory,
   detail and reverse lookup. Check name/template search, all filters, classification
   option values, quantities, same-planet matching, missing-snapshot warnings and
   technical disclosure contents against the baseline.
5. Perform actual desktop/mobile and keyboard inspection: native disclosures,
   wrapping, scrolling, readable labels, empty states and escaping. Compare all
   five creature API responses with baseline data using stable snapshot inputs.
6. If acceptance fails, restore the recorded previous staging image digest and
   recreate only that app service with its original configuration. Recheck health,
   public pages, API responses and filters. No database downgrade/restore or game,
   Docker daemon, PostgreSQL, Nginx, DNS, Wiki or Plane change is part of this phase.

## Complete file inventory

Added:

- `app/web/creature_presentation.py`
- `tests/web/test_creature_presentation.py`
- `docs/phase7b5-implementation-report.md`

Modified:

- `app/web/creature_routes.py`
- `app/web/templates/creatures.html`
- `app/web/templates/creature_detail.html`
- `app/web/templates/resource_creatures.html`
- `tests/web/test_creature_directory.py`

Ignored local artifacts: `.phase5a-output/update_phase7b5_templates.py` (one-time
template edit helper), `.phase5a-output/phase7b5-tests.xml`, and routine pytest/
bytecode cache updates. They are not release files.

Suggested commit message:
`Improve creature directory display labels and provenance disclosures`
