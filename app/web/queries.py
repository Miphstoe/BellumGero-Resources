from datetime import datetime, timedelta, timezone
from math import ceil

from fastapi import HTTPException
from sqlalchemy import text

from app.importing.core3_live.snapshot import SOURCE_INSTANCE, STAT_CODES


def snapshot_status(connection, freshness_hours):
    captured = connection.execute(text("""
        SELECT max(l.captured_at) FROM core3_live_snapshot_imports l
        JOIN source_instances si ON si.id = l.source_instance_id
        WHERE si.code = :source AND l.complete AND l.status = 'complete' AND l.advanced_current
    """), {"source": SOURCE_INSTANCE}).scalar_one()
    return {"source_instance": SOURCE_INSTANCE, "source_system": "core3",
            "captured_at": captured, "freshness_hours": freshness_hours,
            "stale": captured is None or datetime.now(timezone.utc) - captured > timedelta(hours=freshness_hours)}


BASE = """
    FROM source_resources sr
    JOIN source_instances si ON si.id = sr.source_instance_id
    JOIN source_systems ss ON ss.id = si.source_system_id
    LEFT JOIN source_resource_types srt ON srt.id = sr.source_resource_type_id
    LEFT JOIN resource_types rt ON rt.id = srt.canonical_type_id
    LEFT JOIN current_resource_availability cra ON cra.source_resource_id = sr.id AND si.code = 'bellum-gero-live'
"""


def invalid(message):
    raise HTTPException(422, detail={"errors": [{"code": "invalid_filter", "message": message}]})


def parse_filters(params):
    allowed = {"name", "type", "planet", "availability", "source", "sort", "direction", "page", "page_size"}
    allowed |= {f"{bound}_{code}" for bound in ("min", "max") for code in STAT_CODES}
    if len(params) > len(allowed) or any(k not in allowed for k in params):
        invalid("Unknown search parameter")
    values = dict(params)
    for key, default, maximum in (("page", 1, 10000), ("page_size", 25, 100)):
        try:
            number = int(values.get(key, default))
        except (ValueError, TypeError):
            invalid(f"{key} must be an integer")
        if number < 1 or number > maximum:
            invalid(f"{key} must be between 1 and {maximum}")
        values[key] = number
    for key in ("name", "type", "planet"):
        if len(values.get(key, "")) > 160:
            invalid(f"{key} is too long")
    for key, default, options in (
        ("availability", "all", {"all", "current", "historical"}),
        ("source", "all", {"all", "core3", "galaxy_harvester"}),
        ("sort", "name", {"name", "recent", *STAT_CODES}),
        ("direction", "asc", {"asc", "desc"}),
    ):
        values.setdefault(key, default)
        if values[key] not in options:
            invalid(f"Invalid {key}")
    for code in STAT_CODES:
        for bound in ("min", "max"):
            key = f"{bound}_{code}"
            if key in values and values[key] != "":
                try:
                    values[key] = int(values[key])
                except (ValueError, TypeError):
                    invalid(f"{key} must be an integer")
                if abs(values[key]) > 2147483647:
                    invalid(f"{key} is outside the supported integer range")
            else:
                values.pop(key, None)
        if f"min_{code}" in values and f"max_{code}" in values and values[f"min_{code}"] > values[f"max_{code}"]:
            invalid(f"Minimum {code} exceeds maximum")
    return values


def latest_stat_sql():
    return """SELECT rso.value FROM resource_stat_observations rso
              WHERE rso.source_resource_id = sr.id AND rso.stat_code = :stat_code
              ORDER BY rso.observed_at DESC NULLS LAST, rso.id DESC LIMIT 1"""


def search(connection, filters, status):
    clauses, binds = [], {}
    if filters.get("name"):
        clauses.append("sr.source_resource_name ILIKE :name ESCAPE '~'")
        literal = filters["name"].replace("~", "~~").replace("%", "~%").replace("_", "~_")
        binds["name"] = f"%{literal}%"
    if filters.get("type"):
        clauses.append("(rt.slug = :type OR srt.source_type_key = :type)")
        binds["type"] = filters["type"]
    if filters.get("planet"):
        clauses.append("""EXISTS (SELECT 1 FROM resource_planet_observations rpo
            JOIN planets p ON p.id = rpo.planet_id
            LEFT JOIN source_records rec ON rec.id = rpo.source_record_id
            WHERE rpo.source_resource_id = sr.id AND p.slug = :planet
            AND (ss.code <> 'core3' OR cra.source_resource_id IS NULL
                 OR rec.source_snapshot_id = cra.last_seen_source_snapshot_id))""")
        binds["planet"] = filters["planet"]
    if filters["availability"] == "current":
        clauses.append("coalesce(cra.is_active, false) = true")
    elif filters["availability"] == "historical":
        clauses.append("coalesce(cra.is_active, false) = false")
    if filters["source"] != "all":
        clauses.append("ss.code = :source")
        binds["source"] = filters["source"]
    for code in STAT_CODES:
        for bound, operator in (("min", ">="), ("max", "<=")):
            key = f"{bound}_{code}"
            if key in filters:
                expression = latest_stat_sql().replace(":stat_code", f":code_{code}")
                clauses.append(f"({expression}) {operator} :{key}")
                binds[key], binds[f"code_{code}"] = filters[key], code
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = connection.execute(text("SELECT count(*) " + BASE + where), binds).scalar_one()
    sorting = {"name": "lower(sr.source_resource_name)", "recent": "(SELECT max(observed_at) FROM resource_observations WHERE source_resource_id = sr.id)"}
    if filters["sort"] in STAT_CODES:
        order = f"({latest_stat_sql()})"
        binds["stat_code"] = filters["sort"]
    else:
        order = sorting[filters["sort"]]
    # Identifiers/operators derive exclusively from the validated allowlist.
    ids = connection.execute(text("SELECT sr.id " + BASE + where +
        f" ORDER BY {order} {filters['direction']} NULLS LAST, sr.id ASC LIMIT :limit OFFSET :offset"),
        {**binds, "limit": filters["page_size"], "offset": (filters["page"] - 1) * filters["page_size"]}).scalars().all()
    return {"items": resource_rows(connection, ids, status), "total": total,
            "page": filters["page"], "page_size": filters["page_size"], "pages": ceil(total / filters["page_size"])}


def resource_rows(connection, ids, status):
    if not ids:
        return []
    rows = connection.execute(text("""SELECT sr.id, sr.source_resource_name AS name,
        sr.source_resource_id, si.code AS source_instance, ss.code AS source_system,
        coalesce(rt.display_name, srt.source_type_name, srt.source_type_key) AS resource_type,
        coalesce(rt.slug, srt.source_type_key) AS type_slug, coalesce(cra.is_active, false) AS active,
        cra.current_as_of, cra.absent_as_of,
        (SELECT min(observed_at) FROM resource_observations WHERE source_resource_id = sr.id) AS first_observed_at,
        (SELECT max(observed_at) FROM resource_observations WHERE source_resource_id = sr.id) AS last_observed_at
        """ + BASE + " WHERE sr.id = ANY(:ids)"), {"ids": ids}).mappings().all()
    stats = connection.execute(text("""SELECT DISTINCT ON (source_resource_id, stat_code)
        source_resource_id, stat_code, value FROM resource_stat_observations
        WHERE source_resource_id = ANY(:ids)
        ORDER BY source_resource_id, stat_code, observed_at DESC NULLS LAST, id DESC
    """), {"ids": ids}).mappings().all()
    planets = connection.execute(text("""SELECT DISTINCT sr.id, p.display_name
        FROM source_resources sr JOIN source_instances si ON si.id = sr.source_instance_id
        JOIN source_systems ss ON ss.id = si.source_system_id
        JOIN resource_planet_observations rpo ON rpo.source_resource_id = sr.id
        JOIN planets p ON p.id = rpo.planet_id
        LEFT JOIN source_records rec ON rec.id = rpo.source_record_id
        LEFT JOIN current_resource_availability cra ON cra.source_resource_id = sr.id
        WHERE sr.id = ANY(:ids) AND (ss.code <> 'core3' OR cra.source_resource_id IS NULL
            OR rec.source_snapshot_id = cra.last_seen_source_snapshot_id)
        ORDER BY p.display_name
    """), {"ids": ids}).all()
    by_id = {}
    for row in rows:
        item = dict(row)
        active = item.pop("active")
        item.update(stats={code: None for code in STAT_CODES}, planets=[],
                    availability=("stale" if status["stale"] else "current") if active else "historical")
        by_id[item["id"]] = item
    for row in stats:
        if row["stat_code"] in STAT_CODES:
            by_id[row["source_resource_id"]]["stats"][row["stat_code"]] = row["value"]
    for resource_id, name in planets:
        by_id[resource_id]["planets"].append(name)
    return [by_id[i] for i in ids if i in by_id]


def resource_detail(connection, resource_id, status):
    rows = resource_rows(connection, [resource_id], status)
    if not rows:
        raise HTTPException(404, detail={"errors": [{"code": "not_found", "message": "Resource not found"}]})
    item = rows[0]
    item["observations"] = [dict(r) for r in connection.execute(text("""SELECT observation_kind, observed_at, confidence
        FROM resource_observations WHERE source_resource_id = :id ORDER BY observed_at DESC NULLS LAST, id DESC LIMIT 100
    """), {"id": resource_id}).mappings()]
    item["lifecycle"] = [dict(r) for r in connection.execute(text("""SELECT event_type, event_time, event_time_confidence AS confidence
        FROM resource_lifecycle_events WHERE source_resource_id = :id ORDER BY event_time DESC NULLS LAST, id DESC LIMIT 100
    """), {"id": resource_id}).mappings()]
    return item


def reference_data(connection, kind):
    if kind == "planets":
        sql = "SELECT slug, display_name FROM planets ORDER BY display_name"
    else:
        sql = "SELECT slug, display_name FROM resource_types ORDER BY display_name LIMIT 5000"
    return [dict(row) for row in connection.execute(text(sql)).mappings()]


def import_history(connection):
    return [dict(row) for row in connection.execute(text("""SELECT b.id, b.status, b.started_at, b.finished_at, b.summary
        FROM import_batches b JOIN source_instances si ON si.id = b.source_instance_id
        WHERE si.code = :source AND b.import_kind IN ('core3_live_resource_snapshot', 'core3_web_upload')
        ORDER BY b.started_at DESC, b.id DESC LIMIT 30
    """), {"source": SOURCE_INSTANCE}).mappings()]
