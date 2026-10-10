"""Read-only public projections over the activated Phase 7B.1 catalog."""
from datetime import timedelta
from math import ceil
from pathlib import PurePosixPath
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import text

from app.importing.creatures.matching import match_resources, matching_creatures_for_resource
from app.web.queries import invalid

REPOSITORY = 'bellum-gero-live'
CATEGORIES = ('hide', 'meat', 'bone', 'milk')
SPAWN_TYPES = ('dynamic', 'static', 'mission', 'quest', 'event', 'dungeon', 'unknown')
NO_SNAPSHOT = 'No authoritative current resource snapshot is available.'
BASE = ''' FROM creature_definitions d JOIN creature_identities c ON c.id=d.creature_id
 JOIN creature_catalog_activations a ON a.revision_id=d.revision_id AND a.repository=c.repository
 JOIN creature_revisions rev ON rev.id=a.revision_id AND rev.status='validated'
 JOIN creature_source_files sf ON sf.id=d.source_file_id
 LEFT JOIN creature_names cn ON cn.definition_id=d.id AND cn.kind='customName'
 LEFT JOIN creature_names ln ON ln.definition_id=d.id AND ln.kind='objectName'
 WHERE c.repository=:repository AND d.validation_status='validated' '''
NAME = "coalesce(nullif(cn.value,''),CASE WHEN ln.value NOT LIKE '@%' THEN nullif(ln.value,'') END,c.template_key)"
# Conditions are source facts, never inferred from filenames/planet names.
SPAWN_KIND = '''CASE
 WHEN e.conditions @> '{"activation":"event_only"}'::jsonb OR e.conditions @> '{"conditions":[{"activation":"event_only"}]}'::jsonb THEN 'event'
 WHEN e.conditions @> '{"activation":"quest_only"}'::jsonb OR e.conditions @> '{"conditions":[{"activation":"quest_only"}]}'::jsonb THEN 'quest'
 WHEN e.conditions @> '{"activation":"mission_only"}'::jsonb OR e.conditions @> '{"conditions":[{"activation":"mission_only"}]}'::jsonb THEN 'mission'
 WHEN e.conditions @> '{"activation":"dungeon_only"}'::jsonb OR e.conditions @> '{"conditions":[{"activation":"dungeon_only"}]}'::jsonb THEN 'dungeon'
 WHEN n.kind='static' THEN 'static' WHEN n.kind='region' THEN 'dynamic' ELSE 'unknown' END'''
EVIDENCE = ''' FROM creature_spawn_evidence e JOIN planets p ON p.id=e.planet_id
 JOIN creature_spawn_nodes n ON n.id=e.region_id AND n.revision_id=d.revision_id
 WHERE e.definition_id=d.id AND e.confidence='verified_source_definition' '''


def pagination(params):
    values = {}
    for key, default, maximum in (('page', 1, 10000), ('page_size', 25, 100)):
        try:
            value = int(params.get(key, default))
        except (TypeError, ValueError):
            invalid(f'{key} must be an integer')
        if not 1 <= value <= maximum:
            invalid(f'{key} must be between 1 and {maximum}')
        values[key] = value
    return values


def parse_filters(params, *, mode='directory'):
    allowed = {'page', 'page_size'}
    if mode == 'directory':
        allowed |= {'name', 'planet', 'category', 'resource_class', 'min_level', 'max_level', 'spawn_type', 'include_other'}
    elif mode in ('resources', 'evidence'):
        allowed |= {'planet'}
        if mode == 'resources':
            allowed.add('category')
    elif mode == 'detail':
        allowed |= {'planet', 'category', 'resource_page'}
    elif mode == 'reverse':
        allowed |= {'planet', 'category'}
    if any(key not in allowed for key in params):
        invalid('Unknown search parameter')
    # Duplicate query keys must not produce ambiguous HTML/API interpretations.
    if hasattr(params, 'multi_items') and len(params.multi_items()) != len(params):
        invalid('Repeated search parameter')
    values = {**dict(params), **pagination(params)}
    for key in ('name', 'planet', 'resource_class'):
        if len(values.get(key, '')) > 160:
            invalid(f'{key} is too long')
    if values.get('category', '') not in ('', *CATEGORIES):
        invalid('Invalid category')
    if values.get('spawn_type', '') not in ('', *SPAWN_TYPES):
        invalid('Invalid spawn_type')
    if values.get('include_other', 'false') not in ('false', 'true'):
        invalid('include_other must be true or false')
    values['include_other'] = values.get('include_other') == 'true'
    if mode == 'detail':
        values['resource_page'] = pagination({'page': params.get('resource_page', 1)})['page']
    for key in ('min_level', 'max_level'):
        if values.get(key, '') == '':
            values.pop(key, None)
            continue
        try:
            values[key] = int(values[key])
        except (ValueError, TypeError):
            invalid(f'{key} must be an integer')
        if not 0 <= values[key] <= 2147483647:
            invalid(f'{key} is outside the supported range')
    if values.get('min_level', 0) > values.get('max_level', 2147483647):
        invalid('Minimum level exceeds maximum')
    return values


def not_found():
    raise HTTPException(404, detail={'errors': [{'code': 'not_found', 'message': 'Creature not found in activated validated catalog'}]})


def public_path(path):
    if not isinstance(path, str) or '\\' in path:
        return None
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or '..' in parsed.parts or not path.startswith('MMOCoreORB/'):
        return None
    return path


def provenance(path, sha256=None, line=None):
    return {'source_file': public_path(path), 'sha256': sha256, 'line': line}


def pages(items, total, filters):
    return {'items': items, 'total': total, 'page': filters['page'], 'page_size': filters['page_size'],
            'pages': ceil(total / filters['page_size'])}


def page_links(request, data):
    params = dict(request.query_params)
    links = {}
    for label, page in (('previous', data['page'] - 1), ('next', data['page'] + 1)):
        links[label] = None
        if 1 <= page <= data['pages']:
            links[label] = request.url.path + '?' + urlencode({**params, 'page': page})
    return links


def validate_planet(connection, planet):
    if planet and not connection.execute(text('SELECT 1 FROM planets WHERE slug=:planet'), {'planet': planet}).scalar():
        invalid('Unknown planet')


def catalog_references(connection):
    planets = [dict(r) for r in connection.execute(text('SELECT slug,display_name FROM planets ORDER BY display_name')).mappings()]
    classes = connection.execute(text('''SELECT DISTINCT trim(h.raw_lookup) AS code
      FROM creature_harvests h JOIN creature_definitions d ON d.id=h.definition_id
      JOIN creature_catalog_activations a ON a.revision_id=d.revision_id
      JOIN creature_revisions r ON r.id=a.revision_id AND r.status='validated'
      WHERE a.repository=:repository AND d.validation_status='validated' AND h.base_amount>0
        AND trim(h.raw_lookup)<>'' ORDER BY code LIMIT 5000'''), {'repository': REPOSITORY}).scalars().all()
    return {'planets': planets, 'classes': classes, 'categories': CATEGORIES, 'spawn_types': SPAWN_TYPES}


def creature_rows(connection, rows):
    """Hydrate one bounded page in two bulk queries, never one query per creature."""
    if not rows:
        return []
    result = {r['definition_id']: {**dict(r), 'harvests': [], 'planets': [], 'spawn_types': []} for r in rows}
    ids = list(result)
    for row in connection.execute(text('''SELECT definition_id,category,raw_lookup,base_amount
        FROM creature_harvests WHERE definition_id=ANY(:ids) ORDER BY category'''), {'ids': ids}).mappings():
        result[row['definition_id']]['harvests'].append(dict(row))
    for row in connection.execute(text('SELECT DISTINCT d.id AS definition_id,p.slug,p.display_name,' + SPAWN_KIND + ' AS spawn_type '
        + ' FROM creature_definitions d JOIN creature_spawn_evidence e ON e.definition_id=d.id '
        + "JOIN planets p ON p.id=e.planet_id JOIN creature_spawn_nodes n ON n.id=e.region_id AND n.revision_id=d.revision_id "
        + "WHERE d.id=ANY(:ids) AND e.confidence='verified_source_definition' ORDER BY p.display_name"), {'ids': ids}).mappings():
        item = result[row['definition_id']]
        planet = {'slug': row['slug'], 'display_name': row['display_name']}
        if planet not in item['planets']:
            item['planets'].append(planet)
        if row['spawn_type'] not in item['spawn_types']:
            item['spawn_types'].append(row['spawn_type'])
    for item in result.values():
        if not item['spawn_types']:
            item['spawn_types'] = ['unknown']
    return [result[r['definition_id']] for r in rows]


def search(connection, filters):
    validate_planet(connection, filters.get('planet'))
    clauses, binds = [], {'repository': REPOSITORY}
    if not filters['include_other']:
        clauses += ["EXISTS (SELECT 1 FROM creature_harvests h WHERE h.definition_id=d.id AND h.category IN ('hide','meat','bone') AND h.base_amount>0 AND trim(h.raw_lookup)<>'')",
                    'EXISTS (SELECT 1 ' + EVIDENCE + ')']
    if filters.get('name'):
        literal = filters['name'].replace('~', '~~').replace('%', '~%').replace('_', '~_')
        binds['name'] = '%' + literal + '%'
        clauses.append("(c.template_key ILIKE :name ESCAPE '~' OR cn.value ILIKE :name ESCAPE '~' OR ln.value ILIKE :name ESCAPE '~')")
    for key, operator in (('min_level', '>='), ('max_level', '<=')):
        if key in filters:
            clauses.append(f'd.level {operator} :{key}')
            binds[key] = filters[key]
    if filters.get('category') or filters.get('resource_class'):
        harvest = "h.definition_id=d.id AND h.base_amount>0 AND trim(h.raw_lookup)<>''"
        if filters.get('category'):
            harvest += ' AND h.category=:category'
            binds['category'] = filters['category']
        if filters.get('resource_class'):
            harvest += ''' AND (trim(h.raw_lookup)=:resource_class OR EXISTS (
                SELECT 1 FROM harvest_class_mappings m JOIN resource_types rt ON rt.id=m.resource_type_id
                WHERE m.definition_id=h.definition_id AND m.category=h.category AND rt.slug=:resource_class))'''
            binds['resource_class'] = filters['resource_class']
        clauses.append('EXISTS (SELECT 1 FROM creature_harvests h WHERE ' + harvest + ')')
    if filters.get('planet') or filters.get('spawn_type'):
        evidence = EVIDENCE
        if filters.get('planet'):
            evidence += ' AND p.slug=:planet'
            binds['planet'] = filters['planet']
        if filters.get('spawn_type'):
            evidence += ' AND (' + SPAWN_KIND + ')=:spawn_type'
            binds['spawn_type'] = filters['spawn_type']
        clause = 'EXISTS (SELECT 1 ' + evidence + ')'
        if filters.get('spawn_type') == 'unknown' and not filters.get('planet'):
            clause = '(' + clause + ' OR NOT EXISTS (SELECT 1 ' + EVIDENCE + '))'
        clauses.append(clause)
    sql = BASE + ''.join(' AND ' + c for c in clauses)
    total = connection.execute(text('SELECT count(*) ' + sql), binds).scalar_one()
    rows = connection.execute(text('SELECT c.id,d.id AS definition_id,c.template_key,' + NAME + ''' AS name,
        d.level,rev.revision,rev.repository ''' + sql + ' ORDER BY lower(' + NAME + '),c.id LIMIT :limit OFFSET :offset'),
        {**binds, 'limit': filters['page_size'], 'offset': (filters['page']-1)*filters['page_size']}).mappings().all()
    return pages(creature_rows(connection, rows), total, filters)


def detail(connection, creature_id):
    row = connection.execute(text('SELECT c.id,d.id AS definition_id,c.template_key,' + NAME + ''' AS name,
        d.level,d.revision_id,rev.revision,rev.repository,rev.imported_at,d.field_provenance,
        sf.path,sf.sha256,d.line,ln.value AS localization_reference ''' + BASE + '''
        AND c.id=:id'''),
        {'repository': REPOSITORY, 'id': creature_id}).mappings().first()
    if not row:
        not_found()
    item = creature_rows(connection, [row])[0]
    item['provenance'] = provenance(item.pop('path'), item.pop('sha256'), item.pop('line'))
    fields = {'level', 'customName', 'objectName', 'meatType', 'meatAmount', 'hideType', 'hideAmount',
              'boneType', 'boneAmount', 'milkType', 'milk'}
    item['field_provenance'] = {key: provenance(value.get('path'), line=value.get('line'))
        for key, value in item['field_provenance'].items() if key in fields and isinstance(value, dict)}
    # Retain mission-only and unresolved conditions without manufacturing planets.
    mission = connection.execute(text('''SELECT DISTINCT g.key FROM creature_spawn_relationships lc
        JOIN creature_spawn_nodes l ON l.id=lc.parent_id AND l.revision_id=:revision
        JOIN creature_spawn_relationships gl ON gl.child_id=l.id
        JOIN creature_spawn_nodes g ON g.id=gl.parent_id AND g.revision_id=l.revision_id
        WHERE lc.definition_id=:definition AND g.details->>'_activation'='mission_only'
        ORDER BY g.key LIMIT 100'''), {'revision': row['revision_id'], 'definition': row['definition_id']}).scalars().all()
    item['location_warnings'] = []
    if not item['planets']:
        item['location_warnings'].append('No verified planet location is available for this creature.')
    if mission:
        item['location_warnings'].append('Mission-only definitions exist; their planet and runtime activation are unverified.')
    item['mission_groups'] = list(mission)
    unresolved = connection.execute(text('''SELECT diag.code,diag.line,f.path,f.sha256
        FROM creature_import_diagnostics diag LEFT JOIN creature_source_files f ON f.id=diag.source_file_id
        WHERE diag.revision_id=:revision AND diag.code IN ('conditional_spawn','unvalidated_static_spawn','unresolved_harvest')
          AND (diag.details->>'creature'=:template OR diag.details->'expression'->>1=:template
               OR diag.details->'arguments'->>1=:template)
        ORDER BY diag.id LIMIT 50'''), {'revision': row['revision_id'], 'template': row['template_key']}).mappings().all()
    item['unresolved_locations'] = [{'code': r['code'], 'provenance': provenance(r['path'], r['sha256'], r['line'])} for r in unresolved]
    if unresolved:
        item['location_warnings'].append('Conditional or unresolved source definitions exist; their locations and runtime activation are not verified.')
    return item


def public_geometry(value):
    if not isinstance(value, dict):
        return None
    result = {k: v for k, v in value.items() if k in ('x', 'y', 'z', 'heading') and type(v) in (int, float)}
    shape = value.get('shape')
    if isinstance(shape, list) and shape:
        kind = shape[0].get('unresolved') if isinstance(shape[0], dict) else shape[0]
        if kind in ('CIRCLE', 'RECTANGLE', 'RING') and all(type(n) in (int, float) for n in shape[1:]):
            result['shape'] = {'kind': kind, 'parameters': shape[1:]}
    return result or None


def safe_conditions(value, depth=0):
    if depth > 5:
        return None
    allowed = {'activation', 'conditions', 'minDifficulty', 'maxDifficulty', 'size', 'spawnLimit',
               'weighting', 'numberToSpawn', 'role', 'weight_or_count', 'respawn_seconds'}
    if isinstance(value, dict):
        return {k: safe_conditions(v, depth+1) for k, v in value.items() if k in allowed}
    if isinstance(value, list):
        return [safe_conditions(v, depth+1) for v in value[:30]]
    if type(value) in (int, float, bool) or value is None:
        return value
    if isinstance(value, str) and len(value) <= 100 and '/' not in value and '\\' not in value:
        return value
    return None


def public_availability(row, status):
    if row['system'] == 'core3' and (status['captured_at'] is None or row.get('is_active') is None):
        return 'unknown'
    return ('current' if row['fresh'] else 'stale') if row['runtime_authoritative'] else 'historical'


def spawn_evidence(connection, creature, filters):
    validate_planet(connection, filters.get('planet'))
    sql = ''' FROM creature_spawn_evidence e JOIN planets p ON p.id=e.planet_id
        JOIN creature_spawn_nodes n ON n.id=e.region_id
        JOIN creature_definitions d ON d.id=e.definition_id AND d.revision_id=n.revision_id
        JOIN creature_source_files f ON f.id=n.source_file_id
        WHERE e.definition_id=:definition AND e.confidence='verified_source_definition' '''
    binds = {'definition': creature['definition_id']}
    if filters.get('planet'):
        sql += ' AND p.slug=:planet'
        binds['planet'] = filters['planet']
    total = connection.execute(text('SELECT count(*) ' + sql), binds).scalar_one()
    rows = connection.execute(text('SELECT e.id,p.slug AS planet,p.display_name AS planet_name,n.kind,n.details,e.conditions,e.confidence,f.path,f.sha256,n.line,' + SPAWN_KIND + ' AS spawn_type ' + sql
        + ' ORDER BY p.display_name,n.key,e.id LIMIT :limit OFFSET :offset'),
        {**binds, 'limit': filters['page_size'], 'offset': (filters['page']-1)*filters['page_size']}).mappings().all()
    items = []
    for row in rows:
        info = row['details']
        geometry = None if info.get('world_spawn') else public_geometry(info.get('geometry'))
        items.append({'id': row['id'], 'planet': row['planet'], 'planet_name': row['planet_name'],
            'name': info.get('name') if isinstance(info.get('name'), str) else 'Static spawn site',
            'spawn_type': row['spawn_type'], 'location_kind': row['kind'], 'world_spawn': bool(info.get('world_spawn')),
            'geometry': geometry, 'conditions': safe_conditions(row['conditions']),
            'confidence': row['confidence'], 'provenance': provenance(row['path'], row['sha256'], row['line'])})
    return pages(items, total, filters)


def resources(connection, creature, filters, status):
    if not filters.get('planet'):
        invalid('planet is required for resource matching')
    validate_planet(connection, filters['planet'])
    categories = [filters['category']] if filters.get('category') else list(CATEGORIES)
    results = []
    for category in categories:  # Fixed four-category bound, never per-result queries.
        matched = match_resources(connection, repository=REPOSITORY, creature=creature['template_key'],
            planet=filters['planet'], category=category, page=filters['page'], page_size=filters['page_size'],
            include_spawn_evidence=False, max_age=timedelta(hours=status['freshness_hours']))
        candidates = []
        for row in matched['candidates']:
            label = public_availability(row, status)
            candidates.append({k: row[k] for k in ('source_resource_id', 'name', 'source', 'system', 'source_type_key',
                'mapped_type', 'observed_at', 'source_entered_at', 'source_unavailable_at', 'confidence',
                'source_record_id', 'import_batch_id', 'snapshot_sha256', 'current_as_of', 'compatibility', 'uncertainty')}
                | {'availability': label, 'fresh': row['fresh']})
        total = matched.get('total', 0)
        results.append({'category': category, 'status': matched['status'], 'uncertainty': matched['uncertainty'],
                        **pages(candidates, total, filters)})
    return {'creature_id': creature['id'], 'planet': filters['planet'], 'categories': results,
            'snapshot_status': status, 'notice': NO_SNAPSHOT if status['captured_at'] is None else None,
            'unknown_current_availability': status['captured_at'] is None,
            'revision': creature['revision']}


def reverse_lookup(connection, resource_id, filters, status):
    validate_planet(connection, filters.get('planet'))
    matched = matching_creatures_for_resource(connection, repository=REPOSITORY, resource_id=resource_id,
        planet=filters.get('planet'), category=filters.get('category'), page=filters['page'], page_size=filters['page_size'],
        max_age=timedelta(hours=status['freshness_hours']))
    ids = sorted({r['creature_id'] for r in matched['rows']})
    rows = connection.execute(text('SELECT c.id,d.id AS definition_id,c.template_key,' + NAME +
        ' AS name,d.level,rev.revision,rev.repository ' + BASE + ' AND c.id=ANY(:ids) ORDER BY d.id'),
        {'repository': REPOSITORY, 'ids': ids}).mappings().all() if ids else []
    items = creature_rows(connection, rows)
    by_definition = {item['definition_id']: item for item in items}
    for item in items:
        item['matches'] = []
    for row in matched['rows']:
        label = public_availability(row, status)
        by_definition[row['definition_id']]['matches'].append({k: row[k] for k in (
            'planet', 'planet_name', 'category', 'raw_lookup', 'observed_at', 'source', 'system',
            'source_record_id', 'import_batch_id', 'snapshot_sha256', 'uncertainty')}
            | {'availability': label, 'revision': by_definition[row['definition_id']]['revision']})
    return {**pages(items, matched['total'], filters), 'snapshot_status': status,
            'notice': NO_SNAPSHOT if status['captured_at'] is None else None}
