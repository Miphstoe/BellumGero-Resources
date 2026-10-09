# Galaxy Harvester offline archive conversion

The converter reads original XML and frozen discovery evidence without network or
database access. It writes a separate archive only with `--convert`. Originals and
the separate research exporter are never edited. Raw files contain private audit
evidence and must stay outside Git and outside the website's public directories.

## Verification status

On October 9, 2026 the WSL path
`/home/miphstoe/GalaxyHarvester-Research/research/galaxyharvester/galaxy-153/`
existed, but contained an older `raw/http` normalized archive. It did not contain
the requested `raw/resources`, `raw/errors`, `raw/names`, `manifests/attempts.jsonl`,
or `reports/latest.json` layout. The original audited archive was therefore not
converted. Full verification of 18,586 resources and all 18,593 names remains
pending. Regression tests use small synthetic XML fixtures.

## Accepted source evidence

Required directories:

```text
raw/resources/*.xml
raw/errors/*.bin
raw/names/names-*.json
```

Optional private acquisition evidence:

```text
manifests/attempts.jsonl
reports/latest.json
```

Every resource response must have a `result` root with `resultText=found`, a
positive integer `spawnID`, nonempty `spawnName` and `resourceType`. Unresolved
responses must have `resultText=new`, a discovered name, and no resource identity,
statistics, or planets. They remain separate observations. File names do not
establish resource identities; XML names and spawn IDs do.

Repeated unresolved responses are acquisition attempts, not duplicate resource
identities. The converter groups them by stripped, case-folded name while still
requiring every XML name to match the frozen discovery evidence. It emits one
unresolved observation per name and preserves every response file and checksum.
`acquisition_attempts` lists actual `source_path`, `raw_source_sha256`, and
`server_time` entries sorted by path. The observation's existing `source_path`
and hash identify the canonical response. The latest parsed `serverTime` wins;
equal timestamps use the lexicographically greatest relative path. A dated
response outranks one with no server time; all-undated legacy attempts use the
same path tie-breaker. Aware timestamps compare as UTC instants. Mixing aware
and naive times within a name's attempts is rejected rather than guessing a zone.

Only server-time and contributor-field differences are ignored when comparing
attempt contents; other substantive differences fail closed. Found identities
under `raw/errors`, unexpected names, malformed XML, and conflicting acquisition
logs remain errors. No spawn ID is invented. HTTP status and acquisition response
time still come only from the canonical response's hash-bound acquisition log;
XML `serverTime` does not fabricate HTTP metadata. All attempts' recognized log
names are checked, including noncanonical ones.

The reader independently verifies every attempt's bytes, hash, name, timestamp,
content agreement, ordering, and canonical choice. Omitting an original response
from the attempt list fails source-record coverage validation. Older single-file
unresolved payloads without `acquisition_attempts` remain supported. The existing
importer retains the list in unresolved details and source-record JSONB and uses
the canonical actual `raw/errors/` path; no schema or importer logic change is
needed. The audited archive has seven unique unresolved names and fourteen
response files. Reports distinguish `unresolved_count` from
`unresolved_response_count`; private `unresolved_acquisitions` records each
canonical path and all attempt references. These additional evidence files do
not increase discovered-name or verified resource-identity counts.

DTD declarations, entities, nested fields, unknown XML fields, duplicate scalar
fields, duplicate planets, malformed timestamps, and XML over 1 MiB are rejected.
Ordinary built-in XML escaping such as `&amp;` is supported. All eleven GH stat
codes are supported, including ER. Missing, empty, and `None` statistics stay
absent; explicit zero stays zero. Observed integers outside archived ranges are
preserved and reported. Contributor fields are discarded from normalized
payloads; original bytes are copied unchanged into private evidence storage.

Optional `serverTime` is validated with the source timestamp parser, including
on unresolved responses. It is supplemental server metadata, not an inferred
resource lifecycle or acquisition timestamp. Optional `Planets` is also retained
in the private raw XML. Structured `planet` elements alone establish associations.
Plain single names and comma-, semicolon-, pipe-, or newline-delimited summaries
are checked against structured names, ignoring case, order, repeated summary
names, and whitespace. Empty/`None` summaries are unspecified. Undelimited
multi-name prose, bracketed lists, annotations, and other separators (such as `/`)
are unsupported summaries: retained without interpretation, never used to infer
IDs or override structured records. Unambiguous name-set disagreements fail.

The audited optional fields `maxWaypointConc`, `verified`, and `verifiedBy` are
recognized with the same duplicate/attribute checks as other scalar fields.
`maxWaypointConc` must be an integer from 0 through 2,147,483,647; it is retained
as `exact.supplemental_metadata.max_waypoint_conc`, never as a standard statistic.
`verified` must be a valid nonempty source timestamp and is retained verbatim as
`exact.supplemental_metadata.verified_at`, without inventing a timezone. Existing
normalized JSON and source-record JSONB support this metadata without migration;
there are no new database columns, stat definitions, or API fields. On unresolved
responses these fields are validated but retained only in private raw evidence,
preserving the existing unresolved payload contract. `verifiedBy` is never copied
into normalized payloads, reports, or logs; its unmodified XML is private evidence.

The frozen discovery JSON contract is intentionally explicit:

```json
{"galaxy_id": 153, "names": ["archta", "dweina"]}
```

A top-level list or `resource_names` list is also accepted. Entries can be strings
or objects with exactly one identity field, `name` or `spawnName`. When present,
`galaxy_id` must be 153. Every `names-*.json` file must represent the same frozen
population. Paged, incremental, differently structured, or disagreeing discovery
files fail closed. An administrator must verify the actual discovery JSON schema
before running against the audited archive; this environment did not contain it.
Do not combine or rewrite the research archive to make an unexplained discrepancy
pass. Add a verified schema adapter here if needed.

Acquisition logs are retained privately and checksummed. Only this explicit,
hash-bound JSONL record shape confers HTTP status or response time:

```json
{"source_path":"raw/errors/dweina.bin","name":"dweina","sha256":"<64 lowercase hex characters>","http_status":200,"response_timestamp":"2026-06-25T01:02:03+00:00"}
```

The path and SHA-256 must identify the actual retained response and the name must
match its XML. Conflicting matched records are rejected. Superseded hashes and
unrecognized acquisition schemas are counted in the report and confer no
metadata. Unknown status and timestamp remain JSON/SQL NULL; HTTP 200 is never
inferred from `resultText=new`. The database already permits NULL HTTP status,
so no migration is required. `reports/latest.json` is private corroborating
evidence, not authority for discovered counts or source identities.

Source timestamps retain their original strings in normalized provenance. Naive
GH timestamps carry no invented timezone. Database observation columns retain
the existing importer/PostgreSQL session-timezone handling; an administrator
should verify that timezone policy before any production import. Planet-specific
entered/unavailable timestamps are imported from the planet evidence.

## Safe local conversion

Use Python 3.11 or newer from the repository root. The converter itself needs
only the standard library. Set a restrictive umask on Linux; on Windows choose
an output parent with a private ACL. The staging root is mode 0700 on POSIX.
Choose a new output path outside the repository and source tree.

```bash
umask 077
python -m app.importing.galaxy_harvester.converter \
  --source /path/to/original/archive \
  --output /path/to/private/normalized-archive \
  --dry-run --verify-audited-counts
```

Dry-run validates XML, identity coverage, acquisition metadata, ranges, and the
audited profile without creating any directory or file. It prints a JSON report.
The audited profile requires 18,593 discovered names, 18,586 unique resources,
exactly the seven names below, and three stat-range anomalies:

```text
dweina eloate fopo golifo ileciium safe seekeheite
```

Run explicit conversion only after that succeeds:

```bash
python -m app.importing.galaxy_harvester.converter \
  --source /path/to/original/archive \
  --output /path/to/private/normalized-archive \
  --convert --verify-audited-counts
```

Omitting `--verify-audited-counts` permits other fully reconciled galaxy-153
archives and representative fixtures; it never relaxes identity or integrity
checks. The historical importer derives expected counts from verified identities
and discovered evidence, without a production-size constant.

The converter stages output beside the destination, verifies copied source hashes,
generates normalized checksums, runs the hardened reader, and publishes via a
same-filesystem rename. Existing destinations are rejected. Failed staging is
removed; an existing validated archive is never overwritten. Source files should
remain frozen during conversion and import. Checksums detect corruption and
internal disagreement; they are not signatures authenticating an untrusted archive.

## Output and report format

```text
normalized/frozen-identities.json
normalized/resources-index.json
normalized/names.json
normalized/planets.json
normalized/source-types.json
normalized/unresolved.json
checksums/sha256sums.txt
validation-report.json
raw/resources/...              # byte-identical private copies
raw/errors/...
raw/names/...
manifests/attempts.jsonl        # when present
reports/latest.json            # when present
```

JSON keys and manifest entries are sorted deterministically. Identities sort by
numeric spawn ID, unresolved observations by name, and planet associations by ID.
Reports omit execution timestamps and filesystem roots, making repeated conversion
byte-reproducible. `checksums/sha256sums.txt` uses standard SHA-256 double-space
relative-path lines and covers raw evidence, normalized JSON, report, and retained
acquisition files. No absolute paths, traversal, symlinks, or drive/stream paths
are accepted in archive evidence references.

`frozen-identities.json` separates `discovered_names`/`discovered_name_count`,
`identities` (resolved only), and `unresolved_names`. Its total-results fields
describe discovered names, not claimed spawn IDs. Every discovered name must have
exactly one resolved identity or one unresolved response. `names.json` indexes
resolved names only. Normalized values are checked against freshly parsed source
XML in addition to mandatory file hash verification.

The validation report has `format_version=1`, source galaxy identity,
`discovered_names`, `resolved_resources`, `unresolved_count`, `unresolved_names`,
`duplicate_spawn_ids`, `duplicate_names`, `unexpected_names`, `identity_conflicts`,
`source_checksums_verified`, `anomalies`, `audited_counts_match`,
`unrecognized_acquisition_records`, and `http_metadata_policy`.
Each anomaly includes spawn ID, name, stat code, observed value, and archived
minimum/maximum. Successful conversion additionally verifies every published
checksum through `read_archive()`. Invalid input exits nonzero with an actionable
error instead of publishing a success report or partial archive.

## Safe database-backed dry-run

Use a dedicated disposable PostgreSQL database with the current migrations and
reference seeds. Do not point testing at the production or development database.
For example, create a new test-only Compose project using `docker-compose.test.yml`
with a free loopback port. It uses tmpfs storage; never reuse a container belonging
to another environment. Set `TEST_DATABASE_URL` for pytest. Test fixtures reject
non-loopback hosts and databases whose names do not end in `_test`.

For importer CLI planning, explicitly set `BELLUM_DATABASE_URL` to that disposable
database URL; the importer CLI does not use `TEST_DATABASE_URL`:

```bash
export BELLUM_DATABASE_URL="$DISPOSABLE_DATABASE_URL"
python -m app.importing.galaxy_harvester.importer import-history \
  --archive /path/to/private/normalized-archive --dry-run
```

The CLI dry-run transaction is READ ONLY. It verifies source integrity, reports
the stable `archive_revision`, resolved and discovered counts, type/planet
reconciliation, statistical anomalies, and expected mutations. It preserves the
existing bipa and dweina regression reports. Unknown types remain unresolved and
do not gain canonical memberships. Unknown planets block actual import.

No production import is authorized by this implementation task. No command here
performs one. An administrator must review the full real-archive report, timezone
policy, unresolved acquisition metadata, and target database separately.

## Import identity, transactions, and compatibility

The stable archive revision is SHA-256 over canonical normalized data and verified
checksum entries. It excludes the archive filesystem root. Transferring an
unchanged archive preserves the batch identity. A GH source-instance advisory
transaction lock serializes imports; existing database uniqueness constraints
handle snapshots, records, stats, names, and lifecycle evidence. Imports use a
savepoint so a caller that catches an exception cannot accidentally commit a
partially successful import. Filesystem and planet validation precede writes.

Source snapshots record the frozen-manifest hash and size, archive revision,
verified checksums, and separate discovered/resolved/unresolved counts. Resource
records carry actual relative XML paths, source hashes, spawn IDs, original names,
and galaxy/source-instance provenance. Historical GH identities remain separate
from `core3:bellum-gero-live`; names alone never merge them.

Unchanged legacy source records with NULL snapshot references are attached to the
new snapshot only when their stored source hashes match, preserving dependent
observations. Nonmatching legacy evidence is retained rather than silently
rewritten. An existing path-identified batch is upgraded to content identity only
when its complete resource and unresolved evidence hash set matches the verified
archive; its previous revision is retained in batch parameters. Nonmatching legacy
batches remain historical audit records and are not guessed to be equivalent.
Repeated verified imports reuse the content-identified batch. Administrators should inspect old imports with
missing/incorrect hashes before reimporting: they cannot safely be assumed to
represent the same evidence. There are no destructive migrations or schema changes.

Older normalized archives lacking actual source evidence, real SHA-256 hashes,
or frozen discovered-name reconciliation are intentionally rejected. Reconvert
from original evidence rather than disabling integrity checks.

## Regression commands

With `TEST_DATABASE_URL` set to a disposable migrated test database:

```bash
python -m pytest tests/importing/test_galaxy_harvester_converter.py \
  tests/importing/test_galaxy_harvester_importer.py -q
python -m pytest -q
```

Coverage includes XML safety, all stats and zero/absence distinctions, multiple
planets/timestamps, seven unresolved observations, discovered-name reconciliation,
duplicate identities, hashes, malicious paths, privacy, acquisition evidence,
deterministic output, dry-run writes, staging failures, mutation rollback, moved
archives, legacy provenance attachment, repeat-import idempotency, bipa anomalies,
and existing Core3 adapter/importer tests.

On October 9, 2026 the complete suite passed: **317 passed**, with one existing
Starlette/httpx deprecation warning. Database tests used a newly created,
loopback-only PostgreSQL 16 tmpfs container on port 55439, separate from all
production/development databases. `git diff --check` passed. No real audited
archive conversion, production import, migration, deployment, or service restart
was performed.
