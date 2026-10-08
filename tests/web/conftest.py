import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.web.settings import WebSettings
from tests.database.conftest import *  # noqa: F403
from tests.importing.test_core3_live_importer import ensure_core3_type


@pytest.fixture()
def web_engine(engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE source_resources, resources, import_batches CASCADE"))
        core_id = connection.execute(text("SELECT id FROM source_instances WHERE code = 'bellum-gero-live'")).scalar_one()
        ensure_core3_type(connection, {"core3_instance": core_id})
    yield engine
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE source_resources, resources, import_batches CASCADE"))


@pytest.fixture()
def web_settings():
    return WebSettings(admin_username="test-admin", admin_password="test-password",
                       api_token="test-api-token", csrf_secret="s" * 40,
                       secure_cookies=False, max_upload_bytes=4096)


@pytest.fixture()
def client(web_engine, web_settings):
    with TestClient(create_app(engine=web_engine, settings=web_settings)) as client:
        yield client
