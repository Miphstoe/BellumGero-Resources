# Core3 native snapshot compatibility adapter

## Verified exporter contract

Inspected BellumGero-Live remote `Main`, commit
`d3b9a7fc6f271ffbb8e9576f8928445fdafb71b5`, on October 8, 2026. The files were
available remotely although absent in the local game-server checkout:

- [Exporter documentation](https://github.com/Miphstoe/BellumGero-Live/blob/d3b9a7fc6f271ffbb8e9576f8928445fdafb71b5/MMOCoreORB/doc/resource-snapshot-exporter.md)
- [Exporter implementation](https://github.com/Miphstoe/BellumGero-Live/blob/d3b9a7fc6f271ffbb8e9576f8928445fdafb71b5/MMOCoreORB/src/server/zone/managers/resource/resourcesnapshot/ResourceSnapshotExporter.cpp)
- [In-shift selection predicate](https://github.com/Miphstoe/BellumGero-Live/blob/d3b9a7fc6f271ffbb8e9576f8928445fdafb71b5/MMOCoreORB/src/server/zone/managers/resource/resourcesnapshot/ResourceSnapshotRecord.h)

No game-server files or services were changed. Documentation was also found on
exporter feature branches; implementation targets the inspected Main contract.
Do not assume this proves the currently running binary's revision/configuration.

Native schema version 1 uses `source_system=core3`, a persistent configured
`source_instance`, integer Unix UTC-second timestamps and:

```json
{
  "schema_version": 1,
  "source_system": "core3",
  "source_instance": "bellum-gero-live",
  "generated_at": 1800000002,
  "captured_at": 1800000000,
  "capture_started_at": 1800000000,
  "capture_completed_at": 1800000001,
  "consistency": "interval",
  "complete": true,
  "selection": "core3_in_shift",
  "resource_count": 1,
  "resources": [{
    "source_resource_id": "1234567890123456789",
    "source_type_id": "copper_borocarbitic",
    "name": "ExampleResource",
    "stats": {"CD": 800, "OQ": 0},
    "planets": ["corellia"],
    "spawn_map_state": "present",
    "lifecycle": {"spawned_at": null, "expires_at": 1800100000, "despawned_at": null},
    "active": true
  }],
  "galaxy": {"id": 2, "name": "Bellum Gero"},
  "build": {"revision": null, "commit": null}
}
```

`captured_at` equals `capture_started_at`: it is the sole activity cutoff.
Capture examines resources sequentially, so `interval` is an observation
interval, not a whole-server transaction. Active selection is strictly
`expires_at > captured_at`, including resources with zero planet-map keys.
Such resources have `planets=[]` and `spawn_map_state=empty`; nonempty maps have
`present`. Deadline equality, zero, and earlier expiration do not qualify.
An expiration inside the capture interval can still qualify: compare with the
cutoff, not completion or generation time.

The exporter emits null creation/historical-despawn timestamps, since neither
is established. `build.commit` is also null; configured `build.revision` may be
a string or null. Optional galaxy settings may be unconfigured (id 0, name "").
OIDs are exact unsigned-64-bit decimal strings. The exporter emits only present
crafting attributes for CR/CD/DR/FL/HR/MA/PE/OQ/SR/UT and retains zero. There is
no ER mapping. It sorts resources by numeric OID and planet strings lexically.

## Conversion and validation

`app/importing/core3_live/exporter_adapter.py` provides database-free functions:

- `adapt_exporter_snapshot(dict_or_bytes)` validates and converts native data.
- `prepare_snapshot(bytes)` recognizes formats and prepares stable importer
  bytes plus input audit fingerprints.
- `assess_count_safety(count, previous_count, policy)` reports count anomalies.

The complete native metadata cluster identifies native input. A single reserved
native field is sufficient to **reject legacy fallback**, not to accept native
input. Partial native metadata, mixed resource arrays, native fields on legacy
records, legacy revision/version/flattened fields on native inputs, and internal
normalized envelopes fail closed. Both formats reject duplicate JSON keys,
NaN/Infinity and invalid encoding **before classification**, so duplicate keys
cannot erase evidence of native structure. Both formats ultimately pass through
the existing Phase 4B validator and database reconciliation. Genuine unambiguous
legacy snapshots preserve their original bytes/hash and documented revision/version
fields, empty arrays and nonreserved annotations. Previously tolerated ambiguous
duplicate-key and non-JSON numeric inputs are intentionally rejected.
Galaxy Harvester code, type mappings and database migrations are unchanged.

Conversion maps `source_resource_id -> oid`, `source_type_id -> type`, converts
integer seconds to timezone-aware UTC ISO strings and flattens lifecycle fields.
Names and planet strings remain exact, including Unicode/whitespace; missing
stats remain absent, empty planet arrays remain empty and zero remains zero.
No floating-point conversion of OIDs occurs; native IDs over uint64 and
noncanonical leading-zero representations are rejected, not rewritten.

All required top-level metadata is checked, including schema/source identity,
complete=true, exact selection/consistency, integer resource_count matching the
array, start=cutoff <= completion <= generation. Required timestamps must be
nonnegative integer seconds within the UTC datetime range; booleans, floats,
strings and millisecond epochs are rejected. Lifecycle expiration is required
and must be strictly after the cutoff. Missing optional creation/despawn fields
are treated as unknown/null; non-null values are rejected for this verified
native contract. Unsupported map states or mismatch with planet keys are rejected.
Phase 4B checks duplicate OIDs, malformed IDs/records, stat codes/integer values,
planet strings, and database type/planet references. Schema validation cannot be
bypassed by operator review.

Native generation/capture/selection/consistency/build/galaxy metadata is retained
in the normalized content/audit. `build.revision` maps only when known; commit
is never guessed. Canonical JSON encoding sorts object keys and fixes whitespace,
so identical native payloads with different indentation/key order have identical
importer content hashes. Arrays preserve order; real metadata changes remain
different content and conflict at the same capture timestamp. Unknown future
selection, consistency and lifecycle modes require a deliberate adapter update.

The raw input SHA-256 and original byte size are stored in source_snapshots
metadata and import summaries. Duplicate upload attempt summaries retain that
attempt's raw fingerprint, including reformatted retries. Failed import attempts
retain the fingerprint even when adaptation fails. Original files are temporary
and cleaned, not archived by the website; a relay must retain originals if full
file recovery is required. No raw hash is injected into canonical importer bytes,
which would make formatting-only retries conflict.

## Count safety and authorized review

Native uploads compare resource_count with the latest successful **advanced**
complete snapshot's recorded count for bellum-gero-live, regardless of whether
that baseline entered through the native or existing website format. Older
historical imports never replace the baseline. Existing advisory locking and
atomic resource/import transactions are preserved. In addition to the immediate
preceding count, the adapter checks a **sustained high-water baseline**: the largest
accepted advanced snapshot count since the last successfully applied operator
count review, or across all accepted history when no review exists. It queries
existing ledger/snapshot counts and server-generated import-batch summary JSONB.
No schema migration is needed; accepted legacy snapshots also contribute.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `BELLUM_NATIVE_MAX_REDUCTION_FRACTION` | `0.5` | Maximum permitted reduction fraction, 0–1 |
| `BELLUM_NATIVE_REVIEW_OVERRIDES` | `false` | Explicit server authorization for per-snapshot reviews |
| `BELLUM_NATIVE_SUSTAINED_MAX_REDUCTION_FRACTION` | `0.5` | Maximum cumulative reduction from accepted high water, 0–1 |

A reduction **greater than** either threshold requires review; exactly 50% is
accepted at the defaults. `8 -> 6 -> 4 -> 3` contains no individual drop over 50%,
but the final snapshot is 62.5% below the high water and is blocked. The sustained
baseline does not expire after a time window or delivery gap; a slower cumulative
decline cannot evade review by waiting. Empty initial snapshots and nonempty-to-empty changes
always require review, even with a threshold of 1. After an explicitly reviewed
empty snapshot has been accepted, subsequent empty snapshots are not unexpected.
Initial nonempty exports establish the baseline. Count growth is accepted and
raises the high water; ordinary smaller declines never lower it. A successfully
reviewed suspicious snapshot starts a new high-water period at that count so
legitimate despawns and subsequent recovery can proceed. Failed/rolled-back
reviews, validation-only requests, duplicates (even with review fields), and
historical non-advancing snapshots cannot reset it. Snapshot-supplied metadata
is not operator authorization. Review fields on a nonsuspicious snapshot are
not applied and do not reset the baseline.

Validate-only returns `summary.count_safety` with prior/current counts, immediate
and sustained reductions/thresholds, high-water count, latest reviewed reset
time, `review_reasons`, `review_required`, and `override_applied`, plus
`summary.adapter.normalized_sha256`. Validation is structural/reference/count
assessment, **not approval for reconciliation**. A valid anomalous dry-run returns
200 and review_required=true without any writes. Import without review returns
409 `snapshot_review_required` before calling the authoritative importer. Failed
attempt summaries preserve the assessment and fingerprint for operator review.

For a legitimate large despawn:

1. Keep automatic relay paused for the rejected snapshot; inspect the original
   export, Core3 logs and actual server state through separately authorized work.
2. Run authenticated validate-only and record the exact normalized hash.
3. Have an operator explicitly enable `BELLUM_NATIVE_REVIEW_OVERRIDES` in the
   website service configuration during its separately approved deployment.
4. Submit the **same file** with `mode=import`, `review_sha256=<normalized hash>`,
   and `review_reason=<nonempty explanation, at most 500 characters>`.
5. Preserve the review record and disable review capability when no longer needed.

The existing Basic administrator (with CSRF) or bearer administrator must be
authenticated. The request cannot enable review capability. Disabled capability
returns 403; wrong/missing hashes or reasons return 422. Review applies only to
count anomalies, never identity/schema/reference/order/conflict checks. The
hash binds review to inspected data; its reason and applied flag are persisted
atomically with a successful import. Repeat accepted snapshots retain normal
duplicate handling without requiring another review. No permanent importer block
prevents a legitimate population reduction once explicitly reviewed.

This is intentionally a **native-export policy**, not a change to the established
website format's authoritative semantics. A trusted administrator can still
submit legacy snapshots; automatic relay must submit unchanged native exports,
not convert them externally to bypass review. There is no unauthenticated endpoint.

## Validation and tests

Structural-only validation requires no database or production credentials:

```powershell
python -m pytest tests/importing/test_core3_exporter_adapter.py -q
python -m app.importing.core3_live.exporter_adapter --snapshot C:/private/resource-snapshot.json
```

The CLI never imports or connects to PostgreSQL. It reports that database
reference/count-baseline checks have not been performed. For full read-only
validation, use the existing authenticated endpoint:

```powershell
curl.exe -H "Authorization: Bearer $env:BELLUM_UPLOAD_API_TOKEN" -F 'mode=validate' -F 'snapshot=@C:/private/resource-snapshot.json;type=application/json' https://resources.bellumgero.net/api/admin/snapshots
```

These are instructions for future operator use, not operations executed by this
change. `/admin`'s normal validate/import form also accepts native files; hash-bound
overrides are available through multipart API fields, not a new browser bypass.

Run all database integration tests only on a disposable loopback `_test` database
using the established TEST_DATABASE_URL guard and dedicated Compose test service.
Use an unoccupied loopback port; do not reuse production or development data.
Test snapshots are synthetic and are never imported into live data.

Verification after hardening on October 8, 2026: **259 tests passed**, including all existing
regressions and the new native upload integration tests, on a freshly created
disposable PostgreSQL 16 service bound only to 127.0.0.1:55437. Separately,
**136 pure adapter tests passed** without a database. The full suite emits one
pre-existing Starlette/httpx TestClient deprecation warning. Coverage includes
conversion/identity/timestamp/null/stat/planet rules, malformed metadata,
nonsemantic-format duplicate detection, dry-run immutability, blocked anomalies,
hash/reason/authorization checks, legitimate reviewed deactivation, reference
validation despite review, ordering/conflicts, unchanged auth/CSRF, and rollback.
Hardening regressions cover single-field native contamination, duplicate-key
format erasure, mixed record arrays, preserved genuine legacy snapshots,
cumulative declines and delivery gaps, reviewed baseline resets/recovery,
failed/duplicate reviews, legacy-history baselines and spoofed review metadata.
No automated tests targeted production. The test service is removed after QA.

## Secure relay, failures and recovery

The verified default exporter interval is **300 seconds**, a fixed delay after
completion/failure. Actual cadence can exceed five minutes by capture duration.
The exporter atomically replaces one published file and may retain the old file
after a failure. Relay the published filename only, not `.tmp.*` files, and read
through one open descriptor so replacement cannot mix old/new bytes.

Deploy relay separately after review: authenticated HTTPS with verified server
certificate, a least-privilege service identity, restricted owner/ACL access to
the exporter file (created 0600), secrets outside Git/logs/command literals, no
public DB port, bounded sizes/timeouts, private audit storage, and a durable retry
queue of original bytes. Match the configured source lineage exactly. Never
rewrite source_instance or mark incomplete payloads complete.

Only advance the relay's acknowledged hash after a successful import/duplicate
response. Retry the same bytes with bounded backoff after timeouts/5xx; a lost
response after commit is safe because the same export is idempotent. Stop and
alert on 401/403 (credentials/review authorization), 422 (contract/reference
validation), 413 (size), or 409 (count review, order, timestamp conflict). Do not
auto-approve an anomaly. Keep the latest accepted resource state when failures
occur, and use snapshot freshness/relay monitoring to expose staleness.

After interrupted or missed delivery, validate and import the newest complete
published snapshot. Missed intermediate observations cannot be reconstructed
from the overwrite-only exporter; never invent despawn times or histories.
Older queued snapshots must not roll back the current watermark. A backward
system-clock adjustment can violate interval ordering or produce an older/same
capture; reject and await a later consistent capture rather than rewriting times.

Remaining deployment compatibility checks: confirm the binary matches the
inspected exporter revision; confirm its configured source_instance is exactly
`bellum-gero-live` (remote docs' development/lineage examples are not aliases);
verify file permissions, actual resource counts/type keys/zone identifiers, clocks,
build metadata and representative size. Plan exporter activation only in the
separately scheduled restart. Confirm the website runtime role can update the
existing snapshot/batch JSONB audit fields. The unchanged offline Phase 4B CLI
does not share this web adapter policy/lock; keep CLI imports exclusive and do
not hand native files directly to it.

No deployment, server restart, production import, database migration, game-server
edit, Galaxy Harvester change, resource mapping change, commit or push is performed.
