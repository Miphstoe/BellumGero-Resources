from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text

from app.importing.creatures.archive import build_catalog
from app.importing.creatures.importer import import_catalog
from tests.importing.test_creature_catalog import source_tree, add_candidate
from tests.database.conftest import create_resource_type


@pytest.fixture
def directory(client, web_engine, source_tree):
    root, put = source_tree
    # Positive fixture creature, zero-only catalog creature and unreachable one.
    put('scripts/mobile/extra.lua', '''
      other=Creature:new{customName="Catalog Only",level=1}
      CreatureTemplates:addCreatureTemplate(other,"catalog_only")
    ''')
    path = root / 'MMOCoreORB/bin/scripts/mobile/creatures.lua'
    path.write_text(path.read_text() + '\nincludeFile("extra.lua")', encoding='utf-8')
    catalog = build_catalog(root, repository='bellum-gero-live', revision='web-a')
    with web_engine.begin() as connection:
        connection.execute(text('TRUNCATE creature_revisions,creature_identities CASCADE'))
        for slug in ('meat_wild_rori', 'hide_wooly_rori', 'bone_mammal_rori', 'milk_wild_rori'):
            if not connection.execute(text('SELECT id FROM resource_types WHERE slug=:slug'), {'slug': slug}).scalar():
                create_resource_type(connection, slug)
        import_catalog(connection, catalog, activate=True)
        ids = dict(connection.execute(text("SELECT template_key,id FROM creature_identities WHERE repository='bellum-gero-live'")).all())
    yield client, ids
    with web_engine.begin() as connection:
        connection.execute(text('TRUNCATE creature_revisions,creature_identities CASCADE'))


def candidate(web_engine, *, ledger=True, **kwargs):
    with web_engine.begin() as connection:
        ids = {
            'core3_instance': connection.execute(text("SELECT id FROM source_instances WHERE code='bellum-gero-live' AND source_system_id=(SELECT id FROM source_systems WHERE code='core3')")).scalar_one(),
            'gh_instance': connection.execute(text("SELECT id FROM source_instances WHERE code='galaxy-153'")).scalar_one(),
        }
        resource_id = add_candidate(connection, ids, **kwargs)
        if kwargs.get('current') and ledger and kwargs.get('source', 'core3') == 'core3':
            connection.execute(text('''INSERT INTO core3_live_snapshot_imports
              (source_instance_id,source_snapshot_id,captured_at,content_sha256,complete,status,advanced_current)
              SELECT source_instance_id,current_source_snapshot_id,current_as_of,:hash,true,'complete',true
              FROM current_resource_availability WHERE source_resource_id=:id'''), {'id': resource_id, 'hash': 'a'*64})
    return resource_id


def test_default_search_planets_categories_classes(directory):
    client, ids = directory
    result = client.get('/api/creatures').json()
    assert result['total'] == 2
    assert {r['template_key'] for r in result['items']} == {'wild_foreign_bantha_rori', 'zero'}
    assert client.get('/api/creatures?name=Bantha').json()['total'] == 2
    assert client.get('/api/creatures?name=wild_foreign_bantha_rori').json()['total'] == 1
    assert client.get('/api/creatures?planet=tatooine').json()['total'] == 0
    assert client.get('/api/creatures?planet=rori').json()['total'] == 2
    assert client.get('/api/creatures?category=meat').json()['total'] == 1
    assert client.get('/api/creatures?category=milk').json()['total'] == 2
    assert client.get('/api/creatures?resource_class=hide_wooly_rori').json()['total'] == 2
    assert client.get('/api/creatures?category=meat&resource_class=hide_wooly_rori').json()['total'] == 0
    assert client.get('/api/creatures?min_level=20&max_level=20').json()['total'] == 1
    assert client.get('/api/creatures?include_other=true').json()['total'] == 3
    assert client.get('/api/creatures?include_other=true&planet=rori').json()['total'] == 2


def test_pagination_and_deduplication(directory):
    client, _ = directory
    first = client.get('/api/creatures?page_size=1').json()
    second = client.get('/api/creatures?page_size=1&page=2').json()
    assert first['total'] == 2 and first['pages'] == 2
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert len(first['items'][0]['planets']) == 2
    assert client.get('/api/creatures?page=100').json()['items'] == []


def test_evidence_multiple_regions_and_world_sentinel(directory):
    client, ids = directory
    creature = ids['wild_foreign_bantha_rori']
    detail = client.get(f'/api/creatures/{creature}').json()
    assert {p['slug'] for p in detail['planets']} == {'rori', 'naboo'}
    result = client.get(f'/api/creatures/{creature}/spawn-evidence?planet=rori&page_size=1').json()
    assert result['total'] == 2
    all_items = client.get(f'/api/creatures/{creature}/spawn-evidence').json()['items']
    assert len(all_items) == 4
    assert all(e['geometry'] is None for e in all_items if e['world_spawn'])
    assert all(e['provenance']['source_file'].startswith('MMOCoreORB/') for e in all_items)


@pytest.mark.parametrize('params', ['page=0', 'page_size=101', 'min_level=20&max_level=1', 'category=ore',
    'spawn_type=made_up', 'include_other=1', 'unknown=value', 'planet=missing', 'page=1&page=2', 'name='+'x'*161])
def test_invalid_api_filters(directory, params):
    client, _ = directory
    response = client.get('/api/creatures?' + params)
    assert response.status_code == 422
    assert response.json()['errors'][0]['code'] == 'invalid_filter'


def test_invalid_ids_and_missing_planet(directory):
    client, ids = directory
    assert client.get('/api/creatures/0').status_code == 422
    assert client.get('/api/creatures/999999').status_code == 404
    assert client.get(f"/api/creatures/{ids['zero']}/resources").status_code == 422
    assert client.get('/api/resources/999999/creatures').status_code == 404


def test_resource_matching_current_stale_historical_unknown(directory, web_engine):
    client, ids = directory
    candidate(web_engine, name='current', current=True)
    candidate(web_engine, name='stale', current=True, stale=True)
    candidate(web_engine, source='gh', name='archive')
    candidate(web_engine, name='unknown')
    candidate(web_engine, name='wrongplanet', planets=('naboo',), current=True)
    response = client.get(f"/api/creatures/{ids['wild_foreign_bantha_rori']}/resources?planet=rori&category=meat")
    assert response.status_code == 200, response.text
    result = response.json()
    rows = {r['name']: r for r in result['categories'][0]['items']}
    assert {name: r['availability'] for name, r in rows.items()} == {
        'current': 'current', 'stale': 'stale', 'archive': 'historical', 'unknown': 'unknown'}
    assert rows['archive']['system'] == 'galaxy_harvester'
    assert not result['unknown_current_availability']


def test_no_authoritative_snapshot_notice(directory, web_engine):
    client, ids = directory
    candidate(web_engine, name='unverified projection', current=True, ledger=False)
    result = client.get(f"/api/creatures/{ids['wild_foreign_bantha_rori']}/resources?planet=rori&category=meat").json()
    assert result['notice'] == 'No authoritative current resource snapshot is available.'
    assert result['categories'][0]['items'][0]['availability'] == 'unknown'


def test_matching_pagination_counts_distinct_resources(directory, web_engine):
    client, ids = directory
    for name in ('one', 'two', 'three'):
        candidate(web_engine, source='gh', name=name)
    url = f"/api/creatures/{ids['wild_foreign_bantha_rori']}/resources?planet=rori&category=meat&page_size=1"
    first, second = client.get(url).json(), client.get(url+'&page=2').json()
    assert first['categories'][0]['total'] == 3
    assert len(first['categories'][0]['items']) == 1
    assert first['categories'][0]['items'] != second['categories'][0]['items']


def test_reverse_lookup_same_recorded_planets_and_shared_rule(directory, web_engine):
    client, ids = directory
    resource = candidate(web_engine, name='reverse', type_key='prefix_meat_wild_rori_suffix', planets=('rori', 'naboo'), current=True)
    response = client.get(f'/api/resources/{resource}/creatures')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 1
    item = result['items'][0]
    assert item['id'] == ids['wild_foreign_bantha_rori']
    assert {m['planet'] for m in item['matches']} == {'rori', 'naboo'}
    assert all(m['availability'] == 'current' for m in item['matches'])
    assert client.get(f'/api/resources/{resource}/creatures?planet=tatooine').json()['total'] == 0
    assert client.get(f'/api/resources/{resource}/creatures?category=hide').json()['total'] == 0
    assert client.get(f'/api/resources/{resource}/creatures?category=milk').json()['total'] == 0


def test_reverse_history_and_planet_filter(directory, web_engine):
    client, _ = directory
    resource = candidate(web_engine, source='gh', name='historic reverse', planets=('rori',))
    item = client.get(f'/api/resources/{resource}/creatures').json()['items'][0]
    assert len(item['matches']) == 1
    assert item['matches'][0]['availability'] == 'historical'
    assert client.get(f'/api/resources/{resource}/creatures?planet=naboo').json()['total'] == 0


def test_only_active_validated_definitions(directory, web_engine, source_tree):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text("UPDATE creature_definitions SET validation_status='unresolved' WHERE creature_id=:id"), {'id': ids['zero']})
        inactive = build_catalog(source_tree[0], repository='bellum-gero-live', revision='inactive')
        import_catalog(connection, inactive)
    assert client.get('/api/creatures?include_other=true').json()['total'] == 2
    assert client.get(f"/api/creatures/{ids['zero']}").status_code == 404


def test_explicit_conditional_type_and_planet_are_same_evidence(directory, web_engine):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text('''UPDATE creature_spawn_evidence SET conditions='{"activation":"event_only"}'
          WHERE planet_id=(SELECT id FROM planets WHERE slug='naboo')'''))
    assert client.get('/api/creatures?planet=naboo&spawn_type=event').json()['total'] == 2
    assert client.get('/api/creatures?planet=rori&spawn_type=event').json()['total'] == 0
    assert client.get('/api/creatures?planet=rori&spawn_type=dynamic').json()['total'] == 2
    evidence = client.get(f"/api/creatures/{ids['zero']}/spawn-evidence?planet=naboo").json()['items']
    assert all(e['spawn_type'] == 'event' for e in evidence)


def test_local_paths_and_details_not_exposed(directory, web_engine):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text("UPDATE creature_source_files SET path='C:\\private\\secrets-' || id || '.lua'"))
        connection.execute(text('UPDATE creature_definitions SET field_provenance=CAST(:value AS jsonb)'),
            {'value': '{"meatType":{"path":"/private/config.lua","line":1}}'})
        connection.execute(text('''UPDATE creature_spawn_evidence SET conditions=
          '{"activation":"event_only","secret":"C:/private/config"}' '''))
    responses = [client.get(f"/api/creatures/{ids['zero']}").text,
                 client.get(f"/api/creatures/{ids['zero']}/spawn-evidence").text]
    assert all('private' not in response and 'secret' not in response for response in responses)


def test_directory_query_count_does_not_grow_per_creature(directory, web_engine):
    client, _ = directory
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(web_engine, 'before_cursor_execute', capture)
    try:
        assert client.get('/api/creatures?page_size=1').status_code == 200
        small = len(statements)
        statements.clear()
        assert client.get('/api/creatures?page_size=100').status_code == 200
        assert len(statements) == small
    finally:
        event.remove(web_engine, 'before_cursor_execute', capture)


def test_reverse_pagination_has_constant_query_count(directory, web_engine):
    client, _ = directory
    resource = candidate(web_engine, source='gh', name='many matches', type_key='hide_wooly_rori')
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(web_engine, 'before_cursor_execute', capture)
    try:
        first = client.get(f'/api/resources/{resource}/creatures?page_size=1')
        small = len(statements)
        statements.clear()
        all_rows = client.get(f'/api/resources/{resource}/creatures?page_size=100')
        assert first.status_code == all_rows.status_code == 200
        assert first.json()['total'] == 2
        assert len(all_rows.json()['items']) == 2
        assert len(statements) == small
    finally:
        event.remove(web_engine, 'before_cursor_execute', capture)


def test_unknown_location_filter_and_api_detail_validation(directory):
    client, ids = directory
    result = client.get('/api/creatures?include_other=true&spawn_type=unknown').json()
    assert [r['template_key'] for r in result['items']] == ['catalog_only']
    assert client.get(f"/api/creatures/{ids['zero']}?unknown=value").status_code == 422


def test_unresolved_conditional_source_is_warning_not_planet_evidence(directory, web_engine):
    client, ids = directory
    with web_engine.begin() as connection:
        revision = connection.execute(text("SELECT revision_id FROM creature_catalog_activations WHERE repository='bellum-gero-live'")).scalar_one()
        connection.execute(text('''INSERT INTO creature_import_diagnostics(revision_id,line,code,details)
          VALUES(:revision,12,'conditional_spawn',CAST(:details AS jsonb))'''),
          {'revision': revision, 'details': '{"expression":["tatooine","wild_foreign_bantha_rori",0,1,2,3,0,0]}'})
    creature = client.get(f"/api/creatures/{ids['wild_foreign_bantha_rori']}").json()
    assert creature['unresolved_locations'][0]['code'] == 'conditional_spawn'
    assert any('Conditional' in warning for warning in creature['location_warnings'])
    assert {p['slug'] for p in creature['planets']} == {'rori', 'naboo'}


def test_no_active_catalog_empty_state_api(directory, web_engine):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text('DELETE FROM creature_catalog_activations'))
    assert client.get('/api/creatures').json()['items'] == []
    assert client.get(f"/api/creatures/{ids['zero']}").status_code == 404


def test_requests_are_read_only_repeatable_read(directory, monkeypatch):
    from app.web import creatures
    client, _ = directory
    original = creatures.search
    def checked(connection, filters):
        assert connection.exec_driver_sql('SHOW transaction_read_only').scalar_one() == 'on'
        assert connection.exec_driver_sql('SHOW transaction_isolation').scalar_one() == 'repeatable read'
        return original(connection, filters)
    monkeypatch.setattr(creatures, 'search', checked)
    assert client.get('/api/creatures').status_code == 200


def test_projection_unchanged_after_forward_and_reverse_requests(directory, web_engine):
    client, ids = directory
    resource = candidate(web_engine, name='preserved', current=True)
    with web_engine.connect() as connection:
        before = connection.execute(text('SELECT row_to_json(c) FROM current_resource_availability c WHERE source_resource_id=:id'), {'id': resource}).scalar_one()
    assert client.get(f"/api/creatures/{ids['wild_foreign_bantha_rori']}/resources?planet=rori").status_code == 200
    assert client.get(f'/api/resources/{resource}/creatures').status_code == 200
    with web_engine.connect() as connection:
        after = connection.execute(text('SELECT row_to_json(c) FROM current_resource_availability c WHERE source_resource_id=:id'), {'id': resource}).scalar_one()
    assert before == after


@pytest.mark.parametrize('activation,label', [('mission_only', 'mission'), ('quest_only', 'quest'),
    ('dungeon_only', 'dungeon'), ('event_only', 'event')])
def test_explicit_conditional_spawn_labels(directory, web_engine, activation, label):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text('''UPDATE creature_spawn_evidence SET conditions=CAST(:conditions AS jsonb)
            WHERE definition_id=(SELECT id FROM creature_definitions WHERE creature_id=:id)
            AND planet_id=(SELECT id FROM planets WHERE slug='naboo')'''),
            {'conditions': '{"conditions":[{"activation":"' + activation + '"}]}',
             'id': ids['wild_foreign_bantha_rori']})
    filtered = client.get(f'/api/creatures?planet=naboo&spawn_type={label}').json()
    assert [row['template_key'] for row in filtered['items']] == ['wild_foreign_bantha_rori']
    evidence = client.get(f"/api/creatures/{ids['wild_foreign_bantha_rori']}/spawn-evidence?planet=naboo").json()
    assert {row['spawn_type'] for row in evidence['items']} == {label}
    assert client.get(f'/api/creatures?planet=rori&spawn_type={label}').json()['total'] == 0


def test_public_geometry_static_and_dynamic_are_distinct():
    from app.web.creatures import public_geometry
    assert public_geometry({'x': 10, 'y': 20, 'z': 3, 'heading': 0, 'cell': 'private'}) == {
        'x': 10, 'y': 20, 'z': 3, 'heading': 0}
    assert public_geometry({'x': 100, 'y': 200, 'shape': [{'unresolved': 'CIRCLE'}, 50]}) == {
        'x': 100, 'y': 200, 'shape': {'kind': 'CIRCLE', 'parameters': [50]}}


def save_render(name, response):
    """Optional local QA artifacts; synthetic isolated fixtures, never live data."""
    import os
    from pathlib import Path
    output = os.environ.get('CREATURE_RENDER_OUTPUT')
    if output:
        target = Path(output)
        target.mkdir(parents=True, exist_ok=True)
        css = Path('app/web/static/styles.css').read_text(encoding='utf-8')
        (target / 'styles.css').write_text(css, encoding='utf-8')
        html = response.text.replace('http://testserver/static/styles.css', 'styles.css')
        (target / (name + '.html')).write_text(html, encoding='utf-8')


def test_public_html_routes_navigation_and_local_renders(directory, web_engine):
    client, ids = directory
    creature = ids['wild_foreign_bantha_rori']
    resource = candidate(web_engine, source='gh', name='Historical Wool', type_key='hide_wooly_rori')
    for name, path in [('directory', '/creatures'), ('creature', f'/creatures/{creature}'),
        ('planet-matches', f'/creatures/{creature}?planet=rori'),
        ('reverse', f'/resources/{resource}/creatures'), ('resource', f'/resources/{resource}'),
        ('empty', '/creatures?planet=tatooine')]:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert 'href="/creatures"' in response.text
        assert '<html' in response.text and '</html>' in response.text
        assert 'C:\\Users' not in response.text
        save_render(name, response)
    assert f'/resources/{resource}/creatures' in client.get(f'/resources/{resource}').text
    detail = client.get(f'/creatures/{creature}?planet=rori').text
    assert 'No authoritative current resource snapshot is available.' in detail
    assert 'Historical Wool' in detail
    assert 'historical' in detail.lower()
    assert 'milk' in detail.lower()


def test_imported_text_escaped_on_all_new_html_pages(directory, web_engine):
    client, ids = directory
    malicious = '<script>alert("imported")</script>'
    with web_engine.begin() as connection:
        connection.execute(text("UPDATE creature_names SET value=:value WHERE kind='customName'"), {'value': malicious})
    resource = candidate(web_engine, source='gh', name=malicious, type_key='hide_wooly_rori')
    for path in ('/creatures', f"/creatures/{ids['wild_foreign_bantha_rori']}?planet=rori",
                 f'/resources/{resource}/creatures'):
        response = client.get(path)
        assert response.status_code == 200
        assert malicious not in response.text
        assert '&lt;script&gt;' in response.text


def test_detail_independent_resource_and_evidence_pagination(directory, web_engine):
    client, ids = directory
    for name in ('Page A Wool', 'Page B Wool'):
        candidate(web_engine, source='gh', name=name, type_key='hide_wooly_rori')
    creature = ids['wild_foreign_bantha_rori']
    first = client.get(f'/creatures/{creature}?planet=rori&category=hide&page_size=1')
    assert first.status_code == 200
    assert 'resource_page=2' in first.text
    assert 'page=2' in first.text
    second = client.get(f'/creatures/{creature}?planet=rori&category=hide&page_size=1&resource_page=2')
    assert second.status_code == 200
    assert 'Page A Wool' in first.text and 'Page B Wool' not in first.text
    assert 'Page B Wool' in second.text and 'Page A Wool' not in second.text
    assert client.get(f'/creatures/{creature}?resource_page=0').status_code == 422


def test_no_catalog_html_empty_state(directory, web_engine):
    client, _ = directory
    with web_engine.begin() as connection:
        connection.execute(text('DELETE FROM creature_catalog_activations'))
    response = client.get('/creatures')
    assert response.status_code == 200
    assert 'no ' in response.text.lower()
    save_render('no-catalog', response)


def test_html_resource_status_and_conditional_evidence_labels(directory, web_engine):
    client, ids = directory
    candidate(web_engine, name='Current Meat', current=True)
    candidate(web_engine, name='Stale Meat', current=True, stale=True)
    candidate(web_engine, source='gh', name='Historical Meat')
    candidate(web_engine, name='Unknown Meat')
    with web_engine.begin() as connection:
        connection.execute(text('''UPDATE creature_spawn_evidence SET conditions='{"activation":"event_only"}'
            WHERE planet_id=(SELECT id FROM planets WHERE slug='rori')'''))
    response = client.get(f"/creatures/{ids['wild_foreign_bantha_rori']}?planet=rori&category=meat")
    assert response.status_code == 200
    for state in ('current', 'stale', 'historical', 'unknown'):
        assert f'creature-state-{state}' in response.text
    assert 'event_only' in response.text
    assert 'World spawn region' in response.text
    assert 'CIRCLE' in response.text
    assert 'highest-stat' in response.text
    assert 'Galaxy Harvester history does not prove live availability' in response.text
    save_render('resource-statuses-and-event-evidence', response)


def test_conditional_static_geometry_retains_location_kind(directory, web_engine):
    client, ids = directory
    with web_engine.begin() as connection:
        connection.execute(text('''UPDATE creature_spawn_nodes SET kind='static',
            details=jsonb_set(details,'{geometry}',CAST(:geometry AS jsonb))
            WHERE kind='region' AND details->>'name'='forest' '''),
            {'geometry': '{"x":12,"y":34,"z":5}'})
        connection.execute(text('''UPDATE creature_spawn_evidence SET conditions='{"activation":"event_only"}'
            WHERE region_id IN (SELECT id FROM creature_spawn_nodes WHERE kind='static')'''))
    creature = ids['wild_foreign_bantha_rori']
    rows = client.get(f'/api/creatures/{creature}/spawn-evidence?planet=rori').json()['items']
    static = next(row for row in rows if row['location_kind'] == 'static')
    assert static['spawn_type'] == 'event' and static['geometry'] == {'x': 12, 'y': 34, 'z': 5}
    response = client.get(f'/creatures/{creature}?planet=rori')
    assert response.status_code == 200
    assert 'Source coordinates' in response.text and 'Z height 5' in response.text
    assert 'Region origin/center' not in response.text
    save_render('conditional-static-site', response)
