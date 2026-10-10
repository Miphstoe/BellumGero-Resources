# Phase 7B.3 — Release readiness and real-data validation

Assessment date: 2026-10-09 (America/New_York).

**GO for deployment review; NO-GO for deployment.** The retained real-data
rehearsal is corroborated, the missing report now exists, and all 526 automated
tests pass. Desktop/mobile browser QA, actual production backup rehearsal and
production proxy acceptance remain gates. This report does not authorize deployment.
Phase 7B.3's reporting and local validation work is complete to the limits below;
release acceptance remains incomplete.

## 1. Existing progress and preservation

Initial `git status --short` showed five modified tracked files and the untracked
7B.1/7B.2 implementation, migration, tests and documentation, including the 7B.3
deployment checklist. The requested validation report was absent. No AGENTS.md
was found in the repository search. Existing implementation files were preserved.

The checklist alone was not treated as evidence. Inspection found these ignored
artifacts under `.phase5a-output/phase7b3/`:

- `validation.json`, with `completed: true`, catalog/import counts, historical
  selection provenance, table fingerprints, matching assertions and timings.
- `supplemental.json`, recording three harvest-category examples, logical
  backup/restore, packaged-image smoke checks and HTML validation.
- `explain-plans.json`, actual PostgreSQL EXPLAIN ANALYZE/BUFFERS plans.
- `isolated-release.dump`, 11 rendered HTML files and stylesheet, saved API
  responses, and `local-uvicorn.log`.

The prior scripts `.phase5a-output/phase7b3_validate.py` and
`phase7b3_final_checks.py` establish the tested operations. They target the owned
Compose project `bellum-phase7b3-real`, PostgreSQL bound to loopback port 55440,
and named test databases. Both retained rehearsal databases were available.
Saved Phase 7B.1 JSON and the Phase 7B.2 report provide earlier context.

Completed before interruption: offline catalog rebuild/import/idempotence,
representative historical import, planet matching, HTML/API/loopback HTTP checks,
query counts/timings/plans, packaged-image smoke, and logical backup/restore.
Browser QA was explicitly not performed. A checklist entry was never used to
infer a test pass. Prior packaged-image and elapsed-time results are retained
evidence, not fresh measurements from this continuation.

## 2. Continuation work and reproducibility

- Rebuilt the catalog from the existing offline extracted source, without reading
  or modifying BellumGero-Live. Reverified archive, catalog manifest and dump hashes.
- Rechecked counts and all recorded non-creature table fingerprints in both
  `bellum_release_test` and `bellum_release_restore_test`, using read-only SQL.
- Rechecked all 15 recorded routes against both databases with current application
  code, including all five creature APIs, and reconfirmed no current snapshot.
- Created `bellum_phase7b3_continuation_test` inside the verified isolated
  container, separate from the retained real-data databases, and ran the full suite.
- Explicitly downgraded migration 0003 to 0002 and upgraded to head in that
  disposable database; non-creature table fingerprints stayed identical.
- Analyzed the retained query plans and reviewed checklist instructions against
  code, migration, Dockerfile and production Compose configuration.
- Checked browser tooling availability; documented the concrete blocker.
- Created this report and clarified evidence/checksum gates in the checklist.

New ignored evidence is `continuation_verify.py`, `continuation-verification.json`,
`continuation_migration.py`, `continuation-migration.json`, and
`continuation-tests.xml` in `.phase5a-output/phase7b3/`.

Reproduce the read-only evidence check from the repository root with
`.venv/Scripts/python.exe .phase5a-output/phase7b3/continuation_verify.py` while
the owned isolated container and offline source remain available. The fresh test
command was `.venv/Scripts/python.exe -m pytest -q --tb=short
--junitxml=.phase5a-output/phase7b3/continuation-tests.xml`, with
`TEST_DATABASE_URL` explicitly targeting loopback 55440 and
`bellum_phase7b3_continuation_test`. Tests reset that disposable database;
never point them at a retained archive or production database.

These ignored artifacts are local evidence, not packaged release assets. Preserve
them privately for review; the summary report cannot replace their source data.
No catalog reimport or full historical import was unnecessarily repeated.

## 3. Catalog identity and actual counts

Source revision: `54c71a88190a7bfc6ad98b2b26280670b4b1bd10`.

Archive SHA-256:
`494786ebaf99cc122d4e6f9f53addebe7066dee3ef506d1b0725e0e1298d5a5a`.

Catalog manifest SHA-256:
`c7d2ed31dfd376823e4a6e02102c3a18e806421b0afc79c1e2ff981c5737e4f7`.

| Measurement | Actual result |
| --- | ---: |
| Reachable source files | 7,138 |
| Parsed creature registrations | 3,814 |
| Stored, validated definitions | 3,813 |
| Creatures with verified source spawn paths | 1,407 |
| Harvestable carcass creatures with verified planet paths | 739 |
| Spawn-evidence rows | 24,882 |
| Spawn nodes / relationships | 2,395 / 16,445 |
| Creatures with multiple verified planets | 181 |
| Persisted import diagnostics | 16,364 |

Nodes comprise 1,488 lairs, 480 groups and 427 regions. There are no verified
static nodes in this pin. Dynamic-path creatures total 1,407; 2,406 definitions
have unknown locations. Mission/quest/event/dungeon planet paths are not verified.
There are 15,745 parser diagnostics; mapping adds 619 diagnostics (617 unmapped
harvest classes and two unknown planets). Diagnostics are not all fatal errors.
Duplicate registrations and unresolved definitions remain quarantined as tested.

Verified planet-definition counts (overlap across planets): Corellia 250,
Dantooine 205, Dathomir 194, Endor 193, Lok 167, Naboo 191, Rori 210,
Talus 228, Tatooine 241 and Yavin 4 153. Hoth is unsupported, not invented.
These are source-defined potential locations, not observed runtime creature activity.

## 4. Historical integration

The previous rehearsal selected 693 real Galaxy Harvester resources across 183
types from an 18,627-resource legacy index. Selection: up to four actual resources
per hide/meat/bone/milk type, `bipa`, and twelve other multi-planet resources.
Original XML bytes were hash-verified and reconverted; the derived name list is
representative selection evidence, not a complete discovery snapshot.

Results: 693 source records, canonical resources, source resources, names and
resource observations; 7,623 stat observations; 764 planet observations; 1,386
lifecycle events; 14 multi-planet resources. Conversion had zero unresolved
names, duplicate identities or identity conflicts. Three `bipa` source-stat
anomalies were retained. `audited_counts_match` is false: this subset is not a
full-archive audit. HTTP metadata is absent unless explicitly hash-bound.

Creature import and repeat import preserved the resource-table fingerprints;
public requests also preserved them. The continuation reverified those recorded
fingerprints in both retained databases. Full historical archive integration at
18,627-resource scale was not performed in this phase.

## 5. Planet-specific matching

Retained assertions checked 602 reverse pages, 25 forward creature/resource pairs,
eight multi-planet reverse results and 693 cross-planet negative cases. The
negative count includes fallback reverse checks across the selected resources;
it is not 693 distinct harvestable forward matches.

Category examples: `agrilat_rasp` with Corellian bone `poivi` and meat `pepre`;
`canoid` with Corellian hide `nochroebe`. `bachelor_gualama` has verified Naboo
and Rori evidence. `royal_imperial_guard` correctly presents unresolved-location
warnings and no verified planets. Candidate associations require compatible raw
class/category and independently supported same-planet resource observations.

All real candidates remained historical. No authoritative Core3 snapshot was
available or fabricated; missing-snapshot notices remain present. Real-data
current/stale status behavior is therefore unverified, although fixture-based
automated regressions cover all four availability states. Milk runtime matching
is not validated and remains excluded from carcass matches.

## 6. Real-data HTML and APIs

All three new page families and five creature APIs returned HTTP 200 on the real
dataset, alongside resource detail, resources API, snapshot API and health routes.
The prior rehearsal tested TestClient, real loopback Uvicorn HTTP and the packaged
image. This continuation rechecked the 15 saved route cases through TestClient
against both original and restored datasets; it did not repeat image/HTTP timings.

Eleven saved real-data HTML documents passed parser, stylesheet and unresolved
template checks. Filtering by planet/category/class, pagination, empty results,
quarantine exclusion and private-path suppression were exercised. Invalid paging,
oversized names, Hoth and duplicate parameters return 422. HTML parsing is
functional rendering evidence, not visual browser acceptance.

## 7. Performance and PostgreSQL plans

Retained measurements used PostgreSQL 16.15, 3,813 definitions, 24,882 evidence
rows and the 693-resource subset. Each timing had one warm-up and seven runs.
They describe a warm local single-client rehearsal, not production latency or a
load/concurrency test. Catalog parsing took 6.410 seconds. The 56.212-second
catalog block includes import, repeat import, fingerprints and count assertions.

| Operation | Service median ms | EXPLAIN execution total ms |
| --- | ---: | ---: |
| Directory | 55.896 | 65.326 |
| Planet filter | 20.815 | 16.765 |
| Category filter | 59.311 | 70.457 |
| Raw-class filter | 60.766 | 59.180 |
| Page 20 | 53.605 | 57.001 |
| Detail | 7.419 | 4.513 |
| Resource matching | 10.472 | 4.144 |
| Reverse lookup | 71.205 | 76.921 |

HTML request medians: directory 65.722 ms, filtered directory 32.471 ms,
creature detail 56.994 ms, reverse page 87.236 ms; matching API 21.103 ms.
Directory SQL statement count stayed five at page sizes 1, 25 and 100.

Continuation analysis of actual retained plans found indexed and sequential
scans, aggregates, nested loops and sorting. Existing revision/status and
planet/definition indexes are used, alongside resource observation indexes.
Reverse lookup is the slowest measured service/plan and includes repeated work
up to 11,439 actual rows times loops at an individual node. No root-level temp
blocks were written in the captured plans. No index change is justified solely
by this small rehearsal. Counts/substring searches and reverse lookup need
production-scale rehearsal before a performance guarantee can be made.

## 8. Automated regression and security

Fresh continuation result: **526 passed, one warning, 22.68 seconds**, JUnit
evidence saved. The warning is the existing Starlette/httpx TestClient deprecation.
The suite includes 40 directory tests and 41 catalog foundation tests, plus
existing resource, historical archive, live importer, schema and security tests.

Coverage includes SQL/HTML input handling, escaping on new pages, private
provenance suppression, unauthenticated admin rejection, existing authentication,
CSRF/upload behavior, read-only repeatable-read requests, active revision isolation,
import atomicity/idempotence and activation-pointer rollback. The prior real-data
supplement explicitly checked read-only/repeatable-read behavior and Secure,
SameSite=strict cookies in the packaged image. This is regression coverage, not
a penetration test or production proxy security audit. `git diff --check` passed.

## 9. Browser/mobile QA

**NOT PERFORMED; unmet release gate.** In this continuation `agent-browser` was
not installed (command not recognized), and the UI connector returned
`apps: []`, `browsers: []`. No desktop/mobile screenshots, viewport inspection,
keyboard navigation or browser layout pass is claimed. Earlier resource-page
screenshots elsewhere in the workspace do not validate these creature pages.

Required follow-up: actual desktop and narrow mobile inspection of directory,
detail and reverse pages; filters, keyboard focus, table scrolling, long provenance,
empty/unresolved states, and visible escaping. Saved HTML is available for that review.

## 10. Migration, backup and deployment checklist review

The implemented chain is `20260929_0002` -> `20261009_0003`, additive creature
tables/indexes. Explicit continuation downgrade/re-upgrade succeeded in the new
disposable database and preserved non-creature fingerprints. Activation-pointer
rollback is covered by the passing catalog tests. Downgrade drops catalog data
and is not a safe default production recovery mechanism.

Retained isolated logical backup/restore: 44 table fingerprints identical,
2,317,557 bytes, 2.622 seconds, restored revision `20261009_0003`; dump SHA-256
`c0a214dd28d7011e2719518ac2c2f2adcef49d378c5ede160553f281dd0f58bb`.
Continuation verified the dump checksum and retained restored resource data/routes.
This is a post-import rehearsal, not proof that an actual pre-release production
backup or previous application image can be restored successfully.

The checklist matches the importer: read-only dry-run, `activate=False` import,
validated-revision activation in a separate transaction, and pointer recovery.
The CLI requires clean Git checkout/Git executable; the extracted archive requires
the library workflow instead. Archive checksum and reachable-file manifest are
separate gates, now clarified. The Dockerfile packages migrations and runs UID/GID
10001 without automatic migration; production Compose specifies read-only app
filesystem, loopback app port and an internal PostgreSQL network without host port.
These statements describe repository configuration, not inspected production state.

The checklist retains explicit future approval, immutable image, actual revision,
source archive, manifest, backup/restore and acceptance requirements. No production
instruction was executed. The snippet is an API sequence requiring a reviewed
operator script; it is not an independently packaged deployment tool.

## 11. Risks, blockers and decision

- Browser/mobile acceptance remains untested and blocks deployment sign-off.
- Full historical archive scale and concurrent-load performance remain untested.
- Actual production revision, previous image compatibility, backup restoration,
  immediate proxy peer trust, HTTPS/Origin/CSRF and secure-cookie behavior require
  separately authorized operator checks. No production state is assumed verified.
- No real authoritative current snapshot was available; current/stale real-data
  acceptance remains pending. Conditional/event/quest/dungeon locations, Hoth and
  milk runtime harvesting retain documented limitations.
- The implementation is uncommitted and needs release review and an immutable
  release artifact. Local ignored evidence must be retained for reviewers.

**Final recommendation: GO for deployment review; NO-GO to deploy now.** Review
can proceed with this report, preserved implementation and corrected checklist.
Resolve the stated acceptance gates before granting release approval. Nothing was
deployed, committed or pushed; BellumGero-Live, production databases and the live
Resources website were untouched. No production service was restarted.

## 12. Complete changed-file inventory

Continuation repository changes:

- `docs/phase7b3-validation-report.md` (new).
- `docs/phase7b3-deployment-checklist.md` (clarifications).

Preserved pre-existing modified/untracked implementation and documentation:

- `app/db/models.py`
- `app/db/creatures.py`
- `app/main.py`
- `app/importing/creatures/__init__.py`
- `app/importing/creatures/__main__.py`
- `app/importing/creatures/archive.py`
- `app/importing/creatures/importer.py`
- `app/importing/creatures/lua.py`
- `app/importing/creatures/matching.py`
- `app/web/creature_routes.py`
- `app/web/creatures.py`
- `app/web/static/styles.css`
- `app/web/templates/base.html`
- `app/web/templates/detail.html`
- `app/web/templates/creature_detail.html`
- `app/web/templates/creatures.html`
- `app/web/templates/resource_creatures.html`
- `database/migrations/versions/20261009_0003_creature_harvesting.py`
- `docs/creature-directory.md`
- `docs/creature-harvesting-foundation.md`
- `docs/phase7b1-implementation-report.md`
- `docs/phase7b1-validation.json`
- `docs/phase7b2-implementation-report.md`
- `tests/importing/test_creature_catalog.py`
- `tests/web/test_creature_directory.py`

New ignored validation files are listed in section 2. Pytest also updates ignored
cache/bytecode files. The owned continuation database remains disposable; both
real-data rehearsal databases and the prior evidence were preserved.
