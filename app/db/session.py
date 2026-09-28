from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


def make_engine(database_url: str | None = None):
    return create_engine(database_url or get_settings().database_url, future=True)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, future=True)

