"""Transaction-owning adapter; Phase 4B owns validation and resource mutations."""
import json
import logging
import secrets
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.importing.core3_live import importer
from app.importing.core3_live.snapshot import SOURCE_INSTANCE
from app.importing.core3_live.exporter_adapter import CountSafetyPolicy, assess_count_safety, prepare_snapshot

logger = logging.getLogger(__name__)


def sustained_count_baseline(connection):
    """Accepted high water since the last successful, server-recorded count review.

    Failed attempts, duplicates and non-advancing history cannot reset it.
    Raw snapshot metadata is never trusted as operator authorization.
    """
    row = connection.execute(text("""
        WITH accepted AS (
            SELECT l.captured_at, s.record_count, b.summary
            FROM core3_live_snapshot_imports l
            JOIN source_snapshots s ON s.id = l.source_snapshot_id
            JOIN import_batches b ON b.id = s.import_batch_id
            JOIN source_instances si ON si.id = l.source_instance_id
            WHERE si.code = :source AND l.complete AND l.status = 'complete'
              AND l.advanced_current AND b.status = 'complete'
        ), reviewed AS (
            SELECT max(captured_at) AS captured_at FROM accepted
            WHERE summary #>> '{count_safety,override_applied}' = 'true'
        )
        SELECT max(a.record_count) AS resource_count, r.captured_at AS reset_at
        FROM accepted a CROSS JOIN reviewed r
        WHERE r.captured_at IS NULL OR a.captured_at >= r.captured_at
        GROUP BY r.captured_at
    """), {"source": SOURCE_INSTANCE}).mappings().first()
    return (row["resource_count"], row["reset_at"]) if row else (None, None)


class UploadRejected(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

    def result(self):
        return {"ok": False, "errors": [{"code": self.code, "message": self.message}]}


def record_attempt(engine, status: str, summary: dict):
    # Separate transaction: the failed resource transaction has already rolled back.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO import_batches (source_instance_id, import_kind, status,
                tool_version, finished_at, summary)
            SELECT id, 'core3_web_upload', :status, 'phase5a', now(), CAST(:summary AS jsonb)
            FROM source_instances WHERE code = :source
        """), {"status": status, "summary": json.dumps(summary, default=str), "source": SOURCE_INSTANCE})


def ingest(engine, content: bytes, *, dry_run: bool, count_policy: CountSafetyPolicy | None = None,
           allow_review_override: bool = False, review_sha256: str = "", review_reason: str = ""):
    audit = {"input_sha256": hashlib.sha256(content).hexdigest(), "input_byte_size": len(content)}
    safety = None
    try:
        prepared = prepare_snapshot(content)
        audit = prepared.audit
        if review_sha256 or review_reason:
            if not allow_review_override:
                raise UploadRejected("override_not_authorized", "Native count-review overrides are disabled", 403)
            if not prepared.native or not review_reason.strip() or len(review_reason) > 500 or not secrets.compare_digest(
                review_sha256.encode("utf-8"), audit["normalized_sha256"].encode("utf-8")
            ):
                raise UploadRejected("invalid_review_override", "Review requires the exact normalized SHA-256 and a reason of 1-500 characters")
        with TemporaryDirectory(prefix="bellum-snapshot-") as directory:
            path = Path(directory) / "snapshot.json"
            path.write_bytes(prepared.content)
            with engine.begin() as connection:
                if dry_run:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                else:
                    connection.execute(text("SELECT pg_advisory_xact_lock(52105, 1)"))
                plan = importer.dry_run(connection, path)
                validation = plan["validation"]
                if validation["captured_at_conflict"]:
                    raise UploadRejected("snapshot_conflict", "This timestamp already has different snapshot content", 409)
                last = validation["last_advanced_current_at"]
                if not validation["duplicate"] and last and plan["snapshot"]["captured_at"] <= last:
                    raise UploadRejected("out_of_order", "Snapshot must be newer than the latest successful current snapshot", 409)
                if prepared.native:
                    previous_count = connection.execute(text("""
                        SELECT s.record_count FROM core3_live_snapshot_imports l
                        JOIN source_snapshots s ON s.id = l.source_snapshot_id
                        JOIN source_instances si ON si.id = l.source_instance_id
                        WHERE si.code = :source AND l.complete AND l.status = 'complete' AND l.advanced_current
                        ORDER BY l.captured_at DESC LIMIT 1
                    """), {"source": SOURCE_INSTANCE}).scalar_one_or_none()
                    baseline, reset_at = sustained_count_baseline(connection)
                    safety = assess_count_safety(plan["snapshot"]["resources"], previous_count, count_policy or CountSafetyPolicy(),
                        sustained_baseline_count=baseline)
                    safety["sustained_baseline_reset_at"] = reset_at
                    safety["override_applied"] = bool(review_sha256 and safety["review_required"] and not validation["duplicate"])
                    safety["duplicate"] = validation["duplicate"]
                    plan.update(adapter=audit, count_safety=safety)
                    if not dry_run and not validation["duplicate"] and safety["review_required"] and not safety["override_applied"]:
                        raise UploadRejected("snapshot_review_required", "Native snapshot is empty or exceeds the immediate or sustained count reduction limit; validate and obtain an authorized hash-bound review override", 409)
                if dry_run:
                    return {"ok": True, "mode": "validate", "summary": plan}
                summary = importer.import_snapshot(connection, path)
                if prepared.native:
                    summary.update(adapter=audit, count_safety=safety)
                    if safety["override_applied"]:
                        summary["review_reason"] = review_reason.strip()
                # Do not expose or retain an ephemeral host filesystem path.
                if summary.get("status") != "duplicate":
                    connection.execute(text("""
                        UPDATE source_snapshots SET external_path = NULL
                        WHERE id IN (SELECT source_snapshot_id FROM core3_live_snapshot_imports
                                     WHERE id = :ledger_id)
                    """), {"ledger_id": summary["ledger_id"]})
                    if prepared.native:
                        connection.execute(text("""
                            UPDATE source_snapshots SET metadata = metadata || CAST(:audit AS jsonb)
                            WHERE id = (SELECT source_snapshot_id FROM core3_live_snapshot_imports WHERE id = :ledger_id)
                        """), {"ledger_id": summary["ledger_id"], "audit": json.dumps(audit)})
                        connection.execute(text("""
                            UPDATE import_batches SET summary = summary || CAST(:summary AS jsonb)
                            WHERE id = (SELECT import_batch_id FROM source_snapshots
                                WHERE id = (SELECT source_snapshot_id FROM core3_live_snapshot_imports WHERE id = :ledger_id))
                        """), {"ledger_id": summary["ledger_id"], "summary": json.dumps(summary, default=str)})
                    connection.execute(text("""
                        UPDATE import_batches SET parameters = parameters - 'snapshot_path'
                        WHERE id IN (SELECT import_batch_id FROM source_snapshots
                                     WHERE id IN (SELECT source_snapshot_id FROM core3_live_snapshot_imports WHERE id = :ledger_id))
                    """), {"ledger_id": summary["ledger_id"]})
            if summary.get("status") == "duplicate":
                record_attempt(engine, "duplicate", summary)
            return {"ok": True, "mode": "import", "summary": summary}
    except UploadRejected as exc:
        error = exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        # Validator errors can contain hostile or enormous values. Bound them.
        error = UploadRejected("invalid_snapshot", str(exc)[:500])
    except SQLAlchemyError:
        logger.exception("Snapshot database operation failed")
        error = UploadRejected("import_failed", "Database operation failed; resource changes were rolled back", 503)
    except Exception:
        logger.exception("Unexpected snapshot import failure")
        error = UploadRejected("import_failed", "Import failed; resource changes were rolled back", 500)
    if not dry_run:
        try:
            record_attempt(engine, "failed", {**error.result(), **({"adapter": audit} if audit else {}),
                **({"count_safety": safety} if safety is not None else {})})
        except SQLAlchemyError:
            logger.exception("Unable to record failed upload attempt")
    raise error
