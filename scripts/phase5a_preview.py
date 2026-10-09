"""Local synthetic visual QA. Refuses any database outside the test service."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text
import uvicorn

from app.db.session import make_engine
from app.importing.core3_live.importer import import_snapshot
from tests.importing.test_core3_live_importer import ensure_core3_type, resource_payload, write_snapshot


def main():
    url = "postgresql+psycopg://bellum_test:isolated_local_test@127.0.0.1:55435/bellum_resources_test"
    os.environ["BELLUM_DATABASE_URL"] = url
    engine = make_engine(url)
    with TemporaryDirectory(prefix="bellum-preview-") as directory, engine.begin() as connection:
        connection.execute(text("TRUNCATE source_resources, resources, import_batches CASCADE"))
        core = connection.execute(text("SELECT id FROM source_instances WHERE code='bellum-gero-live'")).scalar_one()
        ensure_core3_type(connection, {"core3_instance": core})
        # All records are synthetic and exist only in the disposable test DB.
        resources = [resource_payload(oid=str(880000 + i), name=name,
                     planets=[planet], stats={"OQ": oq, "DR": 620 + i * 30, "UT": 410 + i * 25})
                     for i, (name, planet, oq) in enumerate([
                         ("Synthetic amber ore", "corellia", 921),
                         ("Synthetic crystal sample", "yavin4", 870),
                         ("Synthetic alloy survey", "naboo", 742),
                         ("Synthetic mineral deposit", "tatooine", 0)])]
        import_snapshot(connection, write_snapshot(Path(directory) / "preview.json", resources=resources))
        connection.execute(text("""INSERT INTO source_resources
            (source_instance_id, source_resource_id, source_resource_name, identity_status, confidence)
            SELECT id, 'synthetic-archive', 'Synthetic archive sample', 'source_only', 'historical'
            FROM source_instances WHERE code='galaxy-153'"""))
    engine.dispose()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8005)


if __name__ == "__main__":
    main()
