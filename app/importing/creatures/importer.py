"""Transactional catalog persistence. This module never writes resource tables."""
from __future__ import annotations

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.creatures import (CreatureRevision, CreatureCatalogActivation, CreatureSourceFile,
    CreatureIdentity, CreatureDefinition, CreatureName, HarvestCategory, CreatureHarvest,
    HarvestClassMapping, SpawnNode, SpawnRelationship, CreatureSpawnEvidence, CreatureImportDiagnostic)
from app.db.models import Planet, ResourceType
from .archive import Catalog


def activate_revision(connection, *, repository: str, revision_id: int):
    """Switch or roll back the catalog pointer in the caller's transaction."""
    connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'creature:' + repository})
    revision = connection.execute(select(CreatureRevision.__table__).where(CreatureRevision.id == revision_id)).mappings().one()
    if revision['repository'] != repository or revision['status'] != 'validated':
        raise ValueError('only a validated revision of this repository may be activated')
    connection.execute(pg_insert(CreatureCatalogActivation).values(repository=repository, revision_id=revision_id)
        .on_conflict_do_update(index_elements=['repository'], set_={'revision_id': revision_id}))


def import_catalog(connection, catalog: Catalog, *, dry_run=False, activate=False):
    """Caller commits. Savepoint rolls back every catalog write on any failure.

    Dry runs validate planet/class mappings too, but issue no INSERT/UPDATE/DELETE.
    Failed validation may be archived, but never activated.
    """
    planets = {r.slug: r.id for r in connection.execute(select(Planet.id, Planet.slug))}
    for row in connection.execute(select(Planet.id, Planet.core3_zone_name)):
        if row.core3_zone_name:
            planets[row.core3_zone_name] = row.id
    types = {r.slug: r.id for r in connection.execute(select(ResourceType.id, ResourceType.slug))}
    diagnostics = list(catalog.diagnostics)
    for zone in catalog.zones:
        if zone not in planets:
            diagnostics.append(dict(path='', line=0, code='unknown_planet', details={'planet': zone}))
    for key, creature in catalog.creatures.items():
        for category, harvest in creature['harvest'].items():
            if harvest['raw_lookup'].strip() and harvest['raw_lookup'].strip() not in types:
                diagnostics.append(dict(path=creature['path'], line=creature['line'], code='unmapped_harvest_class',
                    details={'creature': key, 'category': category, 'raw_lookup': harvest['raw_lookup']}))
    evidence = [e for e in catalog.evidence if e['planet'] in planets]
    summary = {**catalog.summary, 'diagnostics': len(diagnostics), 'mapped_spawn_evidence': len(evidence),
               'validated_spawn_creatures': len({e['creature'] for e in evidence}), 'dry_run': dry_run}
    if dry_run:
        return {**summary, 'diagnostic_records': diagnostics}
    with connection.begin_nested():
        connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'creature:' + catalog.repository})
        existing = connection.execute(select(CreatureRevision.__table__).where(
            CreatureRevision.repository == catalog.repository, CreatureRevision.revision == catalog.revision)).mappings().first()
        if existing:
            if existing['manifest_hash'] != catalog.manifest_hash:
                raise ValueError('revision already exists with different source manifest; use a new revision')
            if activate:
                activate_revision(connection, repository=catalog.repository, revision_id=existing['id'])
            return {**existing['summary'], 'revision_id': existing['id'], 'idempotent': True}
        if activate and catalog.fatal:
            raise ValueError('catalog has fatal validation diagnostics and cannot be activated')
        revision_id = connection.execute(insert(CreatureRevision).values(repository=catalog.repository,
            revision=catalog.revision, manifest_hash=catalog.manifest_hash,
            status='rejected' if catalog.fatal else 'validated', summary=summary).returning(CreatureRevision.id)).scalar_one()
        def add(model, **values):
            return connection.execute(insert(model).values(**values).returning(model.id)).scalar_one()
        file_ids = {path: add(CreatureSourceFile, revision_id=revision_id, path=path, **data) for path, data in catalog.files.items()}
        for code in ('hide', 'meat', 'bone', 'milk'):
            connection.execute(pg_insert(HarvestCategory).values(code=code).on_conflict_do_nothing())
        definition_ids = {}
        for key, creature in sorted(catalog.creatures.items()):
            identity_id = connection.execute(pg_insert(CreatureIdentity).values(repository=catalog.repository, template_key=key)
                .on_conflict_do_update(index_elements=['repository', 'template_key'], set_={'template_key': key})
                .returning(CreatureIdentity.id)).scalar_one()
            definition_id = add(CreatureDefinition, revision_id=revision_id, creature_id=identity_id,
                source_file_id=file_ids[creature['path']], line=creature['line'], level=creature['level'],
                field_provenance=creature['field_provenance'],
                parent_symbol=creature['parent'], validation_status='validated' if creature['valid'] else 'unresolved')
            definition_ids[key] = definition_id
            for kind, value in creature['names'].items():
                connection.execute(insert(CreatureName).values(definition_id=definition_id, kind=kind, value=value))
            for category, harvest in creature['harvest'].items():
                connection.execute(insert(CreatureHarvest).values(definition_id=definition_id, category=category, **harvest))
                if harvest['raw_lookup'].strip() in types:
                    connection.execute(insert(HarvestClassMapping).values(definition_id=definition_id, category=category,
                        resource_type_id=types[harvest['raw_lookup'].strip()], method='trimmed_exact_catalog_slug'))
        node_ids = {}
        for (kind, key), node in sorted(catalog.nodes.items()):
            node_ids[(kind, key)] = add(SpawnNode, revision_id=revision_id, kind=kind, key=key,
                source_file_id=file_ids[node['path']], line=node['line'], planet_id=planets.get(node['planet']), details=node['details'])
        for parent, child, conditions in catalog.edges:
            add(SpawnRelationship, parent_id=node_ids[parent], child_id=node_ids.get(child),
                definition_id=definition_ids[child[1]] if child[0] == 'creature' else None, conditions=conditions)
        for e in evidence:
            add(CreatureSpawnEvidence, definition_id=definition_ids[e['creature']], planet_id=planets[e['planet']],
                region_id=node_ids[e['region']], lair_id=node_ids[e['lair']], confidence='verified_source_definition', conditions=e['conditions'])
        for diagnostic in diagnostics:
            add(CreatureImportDiagnostic, revision_id=revision_id, source_file_id=file_ids.get(diagnostic['path']),
                line=diagnostic['line'], code=diagnostic['code'], details=diagnostic['details'])
        if activate:
            activate_revision(connection, repository=catalog.repository, revision_id=revision_id)
        return {**summary, 'revision_id': revision_id, 'idempotent': False}
