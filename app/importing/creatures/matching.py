"""Read-only planet-specific candidates using Core3's literal substring rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import text


def compatibility_sql(type_key='srt.source_type_key', mapped_type='rt.slug', lookup=':lookup',
                      source='si.code', system='ss.code'):
    """One shared runtime compatibility expression for forward/reverse lookup."""
    return f"(({source}=:core3 AND {system}='core3' AND strpos({type_key}, {lookup})>0) OR ({system}='galaxy_harvester' AND strpos({mapped_type}, {lookup})>0))"


def candidate_status(row, *, now, max_age, core3_source_instance='bellum-gero-live'):
    is_core3 = row['system'] == 'core3' and row['source'] == core3_source_instance
    current = (is_core3 and row['is_active'] is True and row['state'] == 'active'
        and row['source_snapshot_id'] is not None and row['source_snapshot_id'] == row['current_source_snapshot_id']
        and row['source_snapshot_id'] == row['last_seen_source_snapshot_id'])
    fresh = current and row['current_as_of'] is not None and timedelta(0) <= now - row['current_as_of'] <= max_age
    return {'availability': 'current_snapshot' if current else 'historical', 'fresh': bool(fresh),
        'runtime_authoritative': bool(current),
        'compatibility': 'raw_type_substring' if is_core3 else 'historical_mapped_type_substring',
        'uncertainty': [] if fresh else ['Historical or stale observation; current harvest availability is unproven.']}


def match_resources(connection, *, repository: str, creature: str, planet: str, category: str,
                    core3_source_instance: str = 'bellum-gero-live', now: datetime | None = None,
                    max_age: timedelta = timedelta(minutes=30), page: int = 1,
                    page_size: int | None = None, include_spawn_evidence: bool = True) -> dict:
    if page < 1 or page_size is not None and not 1 <= page_size <= 100:
        raise ValueError('invalid candidate pagination')
    if category not in ('hide', 'meat', 'bone', 'milk'):
        raise ValueError('unknown harvest category')
    now = now or datetime.now(timezone.utc)
    definition = connection.execute(text('''
        SELECT d.id, d.validation_status, r.id AS revision_id, r.revision,
               r.imported_at, f.path, f.sha256, d.line, d.field_provenance, h.raw_lookup, h.base_amount,
               p.id AS planet_id
        FROM creature_catalog_activations a JOIN creature_revisions r ON r.id=a.revision_id
        JOIN creature_definitions d ON d.revision_id=r.id
        JOIN creature_identities c ON c.id=d.creature_id
        JOIN creature_source_files f ON f.id=d.source_file_id
        JOIN creature_harvests h ON h.definition_id=d.id AND h.category=:category
        JOIN planets p ON p.slug=:planet
        WHERE a.repository=:repository AND c.template_key=:creature AND r.status='validated'
    '''), dict(repository=repository, creature=creature, planet=planet, category=category)).mappings().first()
    result = {'creature': creature, 'planet': planet, 'category': category, 'candidates': [], 'spawn_evidence': [],
              'uncertainty': ['Source definitions establish potential spawns, not a live creature census.',
                  'Core3 returns the first compatible zone resource; snapshot candidate order does not prove runtime order.',
                  'Yield also depends on density, skills, loot rights and runtime conditions.'], 'status': 'unverified'}
    if not definition or definition['validation_status'] != 'validated':
        return result
    result['definition'] = dict(definition)
    evidence_sql = '''
        SELECT e.id, e.confidence, e.conditions, n.details AS region, f.path, f.sha256, n.line
        FROM creature_spawn_evidence e JOIN creature_spawn_nodes n ON n.id=e.region_id
        JOIN creature_source_files f ON f.id=n.source_file_id
        WHERE e.definition_id=:definition AND e.planet_id=:planet
          AND e.confidence='verified_source_definition'
    '''
    if not include_spawn_evidence:
        evidence_sql += ' LIMIT 1'
    evidence = connection.execute(text(evidence_sql), {'definition': definition['id'], 'planet': definition['planet_id']}).mappings().all()
    result['spawn_evidence'] = [dict(e) for e in evidence]
    if not evidence:
        result['status'] = 'no_verified_planet_spawn'
        return result
    effective_lookup = definition['raw_lookup'].strip()
    result['effective_lookup'] = effective_lookup
    if definition['base_amount'] <= 0 or not effective_lookup:
        result['status'] = 'not_harvestable'
        return result
    if category == 'milk':
        result['status'] = 'milk_requires_separate_runtime_validation'
        return result
    # Do not substitute taxonomy ancestry for getType().indexOf(restype).
    # Core3 source keys are the runtime final type. GH uses its explicitly mapped
    # canonical type and is always labeled historical, never runtime-authoritative.
    candidate_sql = '''
        SELECT sr.id AS source_resource_id, sr.source_resource_name AS name,
               si.code AS source, ss.code AS system, srt.source_type_key,
               srt.id AS source_resource_type_id, srt.mapping_status, srt.mapping_confidence,
               rt.slug AS mapped_type, po.id AS planet_observation_id, po.state,
               po.observed_at, po.source_entered_at, po.source_unavailable_at,
               po.confidence, po.source_record_id, po.import_batch_id, po.details,
               rec.source_snapshot_id, snap.content_sha256 AS snapshot_sha256,
               cra.is_active, cra.current_as_of, cra.current_source_snapshot_id,
               cra.last_seen_source_snapshot_id
        FROM source_resources sr JOIN source_instances si ON si.id=sr.source_instance_id
        JOIN source_systems ss ON ss.id=si.source_system_id
        JOIN source_resource_types srt ON srt.id=sr.source_resource_type_id AND srt.source_instance_id=sr.source_instance_id
        LEFT JOIN resource_types rt ON rt.id=srt.canonical_type_id
        JOIN resource_planet_observations po ON po.source_resource_id=sr.id AND po.planet_id=:planet
        LEFT JOIN source_records rec ON rec.id=po.source_record_id
        LEFT JOIN source_snapshots snap ON snap.id=rec.source_snapshot_id
        LEFT JOIN current_resource_availability cra ON cra.source_resource_id=sr.id AND cra.source_instance_id=sr.source_instance_id
        WHERE ''' + compatibility_sql() + '''
        ORDER BY sr.id, po.observed_at DESC NULLS LAST, po.id DESC
    '''
    binds = {'planet': definition['planet_id'], 'lookup': effective_lookup, 'core3': core3_source_instance}
    # Reduce observation histories before pagination; preserve the original
    # latest-observation selection and all legacy unpaginated callers.
    candidate_sql = candidate_sql.replace('SELECT sr.id AS source_resource_id', 'SELECT DISTINCT ON (sr.id) sr.id AS source_resource_id', 1)
    total = connection.execute(text('SELECT count(*) FROM (' + candidate_sql + ') candidates'), binds).scalar_one()
    if page_size is not None:
        candidate_sql += ' LIMIT :limit OFFSET :offset'
        binds.update(limit=page_size, offset=(page-1)*page_size)
    rows = connection.execute(text(candidate_sql), binds).mappings().all()
    result.update(total=total, page=page, page_size=page_size)
    # Preserve individual observations: a resource may have current observations
    # on another planet while this planet has only historical evidence.
    seen = set()
    for row in rows:
        key = row['source_resource_id']
        if key in seen:
            continue
        seen.add(key)
        result['candidates'].append({**dict(row), **candidate_status(row, now=now, max_age=max_age, core3_source_instance=core3_source_instance)})
    result['status'] = 'candidates' if total else 'no_recorded_planet_resource'
    return result


def matching_creatures_for_resource(connection, *, repository, resource_id, planet=None, category=None,
                                    page=1, page_size=25, now=None, max_age=timedelta(minutes=30)):
    """Bounded reverse lookup sharing forward compatibility and status rules.

    Resource IDs are source-resource IDs, matching the existing website. Latest
    observations are reduced per recorded planet before testing spawn existence.
    Pagination counts distinct definitions and retains each eligible planet/category.
    """
    if page < 1 or not 1 <= page_size <= 100 or category not in (None, '', 'hide', 'meat', 'bone', 'milk'):
        raise ValueError('invalid reverse matching parameters')
    now = now or datetime.now(timezone.utc)
    binds = {'repository': repository, 'resource': resource_id, 'core3': 'bellum-gero-live',
             'planet': planet or None, 'category': category or None}
    sql = '''WITH observations AS (
      SELECT DISTINCT ON (po.planet_id) po.planet_id,po.state,po.observed_at,po.source_record_id,po.import_batch_id,
        p.slug AS planet,p.display_name AS planet_name,sr.id AS source_resource_id,
        si.code AS source,ss.code AS system,srt.source_type_key,rt.slug AS mapped_type,
        rec.source_snapshot_id,cra.is_active,cra.current_as_of,cra.current_source_snapshot_id,
        cra.last_seen_source_snapshot_id,snap.content_sha256 AS snapshot_sha256
      FROM source_resources sr JOIN source_instances si ON si.id=sr.source_instance_id
      JOIN source_systems ss ON ss.id=si.source_system_id
      JOIN source_resource_types srt ON srt.id=sr.source_resource_type_id AND srt.source_instance_id=sr.source_instance_id
      LEFT JOIN resource_types rt ON rt.id=srt.canonical_type_id
      JOIN resource_planet_observations po ON po.source_resource_id=sr.id
      JOIN planets p ON p.id=po.planet_id
      LEFT JOIN source_records rec ON rec.id=po.source_record_id
      LEFT JOIN source_snapshots snap ON snap.id=rec.source_snapshot_id
      LEFT JOIN current_resource_availability cra ON cra.source_resource_id=sr.id AND cra.source_instance_id=sr.source_instance_id
      WHERE sr.id=:resource AND (CAST(:planet AS text) IS NULL OR p.slug=:planet)
      ORDER BY po.planet_id,po.observed_at DESC NULLS LAST,po.id DESC
    ), eligible AS (
      SELECT d.id AS definition_id,c.id AS creature_id,c.template_key,h.category,h.raw_lookup,o.*
      FROM creature_definitions d JOIN creature_identities c ON c.id=d.creature_id
      JOIN creature_catalog_activations a ON a.revision_id=d.revision_id AND a.repository=c.repository
      JOIN creature_revisions rev ON rev.id=a.revision_id AND rev.status='validated'
      JOIN creature_harvests h ON h.definition_id=d.id CROSS JOIN observations o
      WHERE c.repository=:repository AND d.validation_status='validated'
        AND h.category IN ('hide','meat','bone') AND h.base_amount>0 AND trim(h.raw_lookup)<>''
        AND (CAST(:category AS text) IS NULL OR h.category=:category)
        AND ''' + compatibility_sql('o.source_type_key', 'o.mapped_type', 'trim(h.raw_lookup)', 'o.source', 'o.system') + '''
        AND EXISTS (SELECT 1 FROM creature_spawn_evidence e
          JOIN creature_spawn_nodes n ON n.id=e.region_id AND n.revision_id=d.revision_id
          WHERE e.definition_id=d.id AND e.planet_id=o.planet_id AND e.confidence='verified_source_definition')
    ) '''
    total = connection.execute(text(sql + 'SELECT count(DISTINCT definition_id) FROM eligible'), binds).scalar_one()
    rows = connection.execute(text(sql + '''SELECT * FROM eligible WHERE definition_id IN (
        SELECT DISTINCT definition_id FROM eligible ORDER BY definition_id LIMIT :limit OFFSET :offset)
        ORDER BY definition_id,planet,category'''), {**binds, 'limit': page_size, 'offset': (page-1)*page_size}).mappings().all()
    return {'total': total, 'rows': [{**dict(r), **candidate_status(r, now=now, max_age=max_age)} for r in rows]}
