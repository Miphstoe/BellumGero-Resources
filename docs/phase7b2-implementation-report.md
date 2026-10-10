# Phase 7B.2 implementation report

Implemented the public Creature Harvesting Directory on the existing activated
Phase 7B.1 catalog. Public handlers use PostgreSQL read-only, repeatable-read
transactions. No importer semantics or production configuration were changed.

## Files in this phase

* `app/main.py`: register public creature routes.
* `app/web/creature_routes.py`: three HTML routes and five read-only API routes.
* `app/web/creatures.py`: validated filters, bounded bulk queries, public provenance,
  spawn evidence, resource-status labels and safe response projections.
* `app/importing/creatures/matching.py`: backward-compatible optional candidate
  pagination and reverse lookup sharing the existing compatibility/status rule.
* `app/web/templates/creatures.html`: directory and filters.
* `app/web/templates/creature_detail.html`: definitions, evidence and planet matches.
* `app/web/templates/resource_creatures.html`: named-resource reverse lookup.
* `app/web/templates/base.html`: primary Creatures navigation.
* `app/web/templates/detail.html`: reverse lookup from existing resource detail.
* `app/web/static/styles.css`: responsive creature-page styles.
* `tests/web/test_creature_directory.py`: API, isolation, matching, pagination,
  provenance, escaping and rendered-page integration coverage.
* `docs/creature-directory.md`: routes, parameters, response contracts, performance
  considerations, examples and player-facing limitations.
* This report.

The working tree also contains the earlier uncommitted Phase 7B.1 foundation
(models, migration, importer, tests and reports). Those changes are preserved;
they are not a new Phase 7B.2 replacement foundation.

## Routes

Pages: `GET /creatures`, `GET /creatures/{id}`,
`GET /resources/{id}/creatures`.

APIs: `GET /api/creatures`, `GET /api/creatures/{id}`,
`GET /api/creatures/{id}/resources`,
`GET /api/creatures/{id}/spawn-evidence`,
`GET /api/resources/{id}/creatures`.

## Validation

Final validation on 2026-10-09: **526 passed, 1 existing warning**, in 21.01 seconds.
This includes **40 new directory tests**, the **41 Phase 7B.1 foundation tests**,
and all existing resource, archive, live-import, schema and security regressions.
The warning is the existing Starlette/httpx TestClient deprecation. Only the
isolated loopback PostgreSQL `_test` database was used.

The full command was `python -m pytest -q --tb=short` using the repository virtual
environment, `TEST_DATABASE_URL` pointing to the owned loopback port 55437 test
container, and `CREATURE_RENDER_OUTPUT=.phase5a-output/phase7b2-renders`.
Python compilation, `git diff --check`, and a separate whitespace scan of new
untracked source/documentation files passed.

Coverage includes same-planet category/class filtering, active revision isolation,
quarantine exclusion, independent pagination, multiple planets/regions, explicit
mission/quest/event/dungeon labels, conditional static coordinates, world-spawn
sentinels, source-path/configuration suppression, all four resource availability
statuses, absent snapshot authority, reverse compatibility, HTML escaping,
constant query counts, and read-only repeatable-read transactions. Existing named
resource pages render successfully with the new reverse link.

Nine local HTML renders passed parser, heading, stylesheet and unresolved-template
checks. They use **synthetic isolated fixtures**, not live creature/resource data:

* [Directory](../.phase5a-output/phase7b2-renders/directory.html)
* [Creature detail](../.phase5a-output/phase7b2-renders/creature.html)
* [Planet resource matches](../.phase5a-output/phase7b2-renders/planet-matches.html)
* [Reverse lookup](../.phase5a-output/phase7b2-renders/reverse.html)
* [Existing resource detail](../.phase5a-output/phase7b2-renders/resource.html)
* [Empty search](../.phase5a-output/phase7b2-renders/empty.html)
* [No active catalog](../.phase5a-output/phase7b2-renders/no-catalog.html)
* [Four resource statuses and event evidence](../.phase5a-output/phase7b2-renders/resource-statuses-and-event-evidence.html)
* [Conditional static coordinates](../.phase5a-output/phase7b2-renders/conditional-static-site.html)

These artifacts and their local stylesheet are in the ignored `.phase5a-output`
directory. Open them locally to inspect saved content; their application navigation
links require the running app. The optional render validator is
`.phase5a-output/validate_creature_renders.py`.

Browser screenshots and actual desktop/mobile visual inspection are unavailable:
the computer-use connector reports no browser surfaces and failed to open its
in-app browser. Local Jinja/TestClient HTML renders are used instead. This is
functional HTML validation, not a claim of browser or visual QA passing.
The frontend-design skill's different-provider evaluator was unavailable. A
same-provider evaluation attempt was also blocked by agent file-read/browser
access; it issued no visual PASS or score. Limited contract review and root static
review were completed alongside the automated HTML integration checks.

## Limitations and migration

No additional Phase 7B.2 migration is needed. The existing Phase 7B.1
`20261009_0003_creature_harvesting.py` migration remains a prerequisite; its
upgrade/downgrade is exercised by the isolated test fixtures.

Potential source locations do not establish live creature activity. Conditional
quest/event/dungeon definitions lacking reliable evidence remain unresolved;
explicit labels require stored source conditions. Hoth has no validated support
in the reviewed source revision. Milk definitions are visible, but their separate
runtime harvesting path is unvalidated and is excluded from runtime matches.
Unmapped raw classes remain raw; no canonical class or planet is invented.

Core3 selects the first compatible resource in the player's zone; public candidate
ordering does not establish that runtime order or a highest-stat selection.
Historical Galaxy Harvester data stays historical. Named resources require
independent selected-planet observations as well as compatible creature evidence.
Missing authoritative snapshots produce the exact required notice and unknown
Core3 availability. Freshness is based on the existing configured snapshot window.

Query-count tests guard against per-creature hydration. Counts and substring
searches can still scan large archives; production-scale query plans and actual
browser/mobile accessibility checks remain follow-up validation work.

No BellumGero-Live files, production servers/databases, DNS or Nginx installations
were changed. No deployment, Core3/Docker restart, commit or push was performed.
The owned isolated PostgreSQL test container is removed after validation.
