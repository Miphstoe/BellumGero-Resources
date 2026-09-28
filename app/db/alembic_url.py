from __future__ import annotations

import os
from collections.abc import Callable, Mapping


PLACEHOLDER_URLS = {"", "driver://placeholder"}


def resolve_alembic_database_url(
    get_config_value: Callable[[str], str | None],
    environ: Mapping[str, str] | None = None,
) -> str:
    explicit_url = get_config_value("sqlalchemy.url")
    if explicit_url and explicit_url not in PLACEHOLDER_URLS:
        return explicit_url

    environment = environ if environ is not None else os.environ
    url = environment.get("BELLUM_DATABASE_URL")
    if not url:
        raise RuntimeError("BELLUM_DATABASE_URL is required unless Alembic sqlalchemy.url is explicitly set")
    return url
