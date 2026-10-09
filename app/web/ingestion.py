"""Transaction-owning adapter; Phase 4B owns validation and resource mutations."""
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.importing.core3_live import importer
from app.importing.core3_live.snapshot import SOURCE_INSTANCE

logger = logging.getLogger(__name__)


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


def ingest(engine, content: bytes, *, dry_run: bool):
    try:
        with TemporaryDirectory(prefix="bellum-snapshot-") as directory:
            path = Path(directory) / "snapshot.json"
            path.write_bytes(content)
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
                if dry_run:
                    return {"ok": True, "mode": "validate", "summary": plan}
                summary = importer.import_snapshot(connection, path)
                # Do not expose or retain an ephemeral host filesystem path.
                if summary.get("status") != "duplicate":
                    connection.execute(text("""
                        UPDATE source_snapshots SET external_path = NULL
                        WHERE id IN (SELECT source_snapshot_id FROM core3_live_snapshot_imports
                                     WHERE id = :ledger_id)
                    """), {"ledger_id": summary["ledger_id"]})
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
            record_attempt(engine, "failed", error.result())
        except SQLAlchemyError:
            logger.exception("Unable to record failed upload attempt")
    raise error
