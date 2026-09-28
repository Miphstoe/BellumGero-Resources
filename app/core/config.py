from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str


def get_settings() -> Settings:
    database_url = os.environ.get("BELLUM_DATABASE_URL")
    if not database_url:
        raise RuntimeError("BELLUM_DATABASE_URL is required")
    return Settings(database_url=database_url)

