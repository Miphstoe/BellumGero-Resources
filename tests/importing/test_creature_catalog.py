from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.importing.creatures.archive import build_catalog
from app.importing.creatures.importer import activate_revision, import_catalog
from app.importing.creatures.lua import Unknown, fields, statements
from app.importing.creatures.matching import match_resources
from tests.database.conftest import create_resource_type, create_source_resource


@pytest.fixture
def source_tree(tmp_path):
    base = tmp_path / 'MMOCoreORB/bin'
    def put(path, source):
        target = base / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')
    put('conf/config.lua', 'Core3 = { ZonesEnabled = {"rori", "naboo"} }')
    put('scripts/managers/resource_manager.lua', 'activeZones = "rori,naboo"')
    put('scripts/mobile/creatures.lua', '''
        Creature = {level=0, meatType="", meatAmount=0, hideType="", hideAmount=0,
                    boneType="", boneAmount=0, milkType="", milk=0}
        Lair = {mobiles={}, bossMobiles={}}
        includeFile("child.lua")
        includeFile("graph.lua")
    ''')
    put('scripts/mobile/child.lua', '''
        parent = Creature:new {level=18, customName="Wild Foreign Bantha",
          objectName="@mob/creature_names:bantha", meatType="meat_wild_rori", meatAmount=450,
          hideType="hide_wooly_rori", hideAmount=825, boneType="bone_mammal_rori", boneAmount=250,
          milkType="milk_wild_rori", milk=850}
        child = parent:new {level=20}
        empty = parent:new {meatAmount=0}
        CreatureTemplates:addCreatureTemplate(child,"wild_foreign_bantha_rori")
        CreatureTemplates:addCreatureTemplate(empty,"zero")
    ''')
    put('scripts/mobile/graph.lua', '''
        herd = Lair:new {mobiles={{"wild_foreign_bantha_rori",1},{"zero",1}}}
        addLairTemplate("herd", herd)
        world = {lairSpawns={{lairTemplateName="herd",weighting=28,numberToSpawn=15,
                             minDifficulty=18,maxDifficulty=500,spawnLimit=-1,size=25}}}
        addSpawnGroup("world",world)
    ''')
    for zone in ('rori', 'naboo'):
        put(f'scripts/managers/planet/{zone}_regions.lua', f'''
          {zone}_regions = {{
            {{"world",0,0,{{RECTANGLE,0,0}},SPAWNAREA+WORLDSPAWNAREA,{{"world"}},2048}},
            {{"forest",100,200,{{CIRCLE,50}},SPAWNAREA,{{"world"}},20}}
          }}
        ''')
    return tmp_path, put


def build(source_tree, revision='a'):
    return build_catalog(source_tree[0], repository='fixture', revision=revision)


def test_inheritance_categories_and_world_geometry(source_tree):
    catalog = build(source_tree)
    assert not catalog.fatal
    creature = catalog.creatures['wild_foreign_bantha_rori']
    assert creature['level'] == 20
    assert creature['harvest']['hide'] == {'raw_lookup': 'hide_wooly_rori', 'base_amount': 825}
    assert creature['harvest']['milk']['base_amount'] == 850
    assert catalog.creatures['zero']['harvest']['meat']['base_amount'] == 0
    assert len(catalog.evidence) == 8
    world = [n for (kind, _), n in catalog.nodes.items() if kind == 'region' and n['details']['world_spawn']]
    assert len(world) == 2
    assert all(n['details']['geometry'] is None for n in world)
    assert creature['names']['objectName'].startswith('@mob/')


def test_unreachable_and_dynamic_blocks_never_authorize(source_tree):
    _, put = source_tree
    put('scripts/mobile/unloaded.lua', 'CreatureTemplates:addCreatureTemplate(child,"unloaded")')
    put('scripts/mobile/graph.lua', '''
      if enabled then includeFile("unloaded.lua") end
      function spawn() spawnMobile("rori","wild_foreign_bantha_rori",0,1,2,3,0,0) end
      herd=Lair:new{mobiles={{"wild_foreign_bantha_rori",1}}}
      addLairTemplate("herd",herd)
      world={lairSpawns={{lairTemplateName="herd",weighting=getWeight(),numberToSpawn=15}}}
      addSpawnGroup("world",world)
    ''')
    catalog = build(source_tree)
    assert 'unloaded' not in catalog.creatures
    assert not catalog.evidence
    assert catalog.fatal
    assert 'dynamic_block' in {d['code'] for d in catalog.diagnostics}


@pytest.mark.parametrize('expression', ['getType()', '"meat" .. suffix', 'unknown'])
def test_unknown_harvest_is_not_guessed(source_tree, expression):
    _, put = source_tree
    put('scripts/mobile/child.lua', f'child=Creature:new{{meatType={expression},meatAmount=5}}\nCreatureTemplates:addCreatureTemplate(child,"bad")')
    catalog = build(source_tree)
    assert not catalog.creatures['bad']['valid']
    assert any(d['code'] == 'unresolved_harvest' for d in catalog.diagnostics)


def test_duplicate_registration_rejected(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'CreatureTemplates:addCreatureTemplate(child,"wild_foreign_bantha_rori")')
    catalog = build(source_tree)
    assert not catalog.fatal
    assert catalog.summary['parsed_creatures'] == 2
    assert 'wild_foreign_bantha_rori' not in catalog.creatures


def test_include_escape_rejected(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'includeFile("../../../../outside.lua")')
    assert build(source_tree).fatal


def test_missing_parent_and_include_diagnostics(source_tree):
    _, put = source_tree
    put('scripts/mobile/child.lua', 'child=missing:new{}\nCreatureTemplates:addCreatureTemplate(child,"bad")\nincludeFile("absent.lua")')
    catalog = build(source_tree)
    assert catalog.fatal
    assert 'bad' not in catalog.creatures
    assert {'missing_include', 'unresolved_definition'} <= {d['code'] for d in catalog.diagnostics}


@pytest.fixture
def loaded(db, source_tree):
    for key in ('meat_wild_rori', 'hide_wooly_rori', 'bone_mammal_rori', 'milk_wild_rori'):
        create_resource_type(db, key)
    catalog = build(source_tree)
    imported = import_catalog(db, catalog, activate=True)
    return catalog, imported


def test_idempotency_dry_run_and_mapping(db, source_tree):
    before = db.execute(text('SELECT count(*) FROM creature_revisions')).scalar_one()
    dry = import_catalog(db, build(source_tree), dry_run=True)
    assert db.execute(text('SELECT count(*) FROM creature_revisions')).scalar_one() == before
    assert dry['mapped_spawn_evidence'] == 8
    first = import_catalog(db, build(source_tree), activate=True)
    again = import_catalog(db, build(source_tree), activate=True)
    assert first['revision_id'] == again['revision_id']
    assert again['idempotent']
    assert db.execute(text('SELECT count(*) FROM creature_definitions')).scalar_one() == 2
    assert db.execute(text('SELECT count(*) FROM creature_spawn_evidence')).scalar_one() == 8


def test_revision_activation_and_rollback(db, source_tree, loaded):
    first = loaded[1]['revision_id']
    second = import_catalog(db, build(source_tree, 'b'), activate=True)['revision_id']
    assert second != first
    with db.begin_nested() as transaction:
        activate_revision(db, repository='fixture', revision_id=first)
        transaction.rollback()
    assert db.execute(text('SELECT revision_id FROM creature_catalog_activations WHERE repository=\'fixture\'')).scalar_one() == second
    activate_revision(db, repository='fixture', revision_id=first)
    assert db.execute(text('SELECT count(*) FROM creature_definitions')).scalar_one() == 4
    assert db.execute(text('SELECT count(*) FROM current_resource_availability')).scalar_one() == 0


def test_revision_content_conflict_rolls_back(db, source_tree, loaded):
    _, put = source_tree
    put('scripts/mobile/child.lua', 'changed=Creature:new{}\nCreatureTemplates:addCreatureTemplate(changed,"changed")')
    with pytest.raises(ValueError, match='different source manifest'):
        import_catalog(db, build(source_tree))
    assert db.execute(text('SELECT count(*) FROM creature_definitions')).scalar_one() == 2


def test_failed_activation_has_no_partial_writes(db, source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'includeFile("missing.lua")')
    with pytest.raises(ValueError, match='fatal'):
        import_catalog(db, build(source_tree), activate=True)
    assert db.execute(text('SELECT count(*) FROM creature_revisions')).scalar_one() == 0


def test_unknown_planet_not_evidence(db, source_tree):
    _, put = source_tree
    put('conf/config.lua', 'Core3={ZonesEnabled={"hoth"}}')
    put('scripts/managers/planet/hoth_regions.lua', 'hoth_regions={{"world",0,0,{RECTANGLE,0,0},SPAWNAREA+WORLDSPAWNAREA,{"world"},20}}')
    result = import_catalog(db, build(source_tree), activate=True)
    assert result['mapped_spawn_evidence'] == 0
    assert db.execute(text('SELECT count(*) FROM creature_import_diagnostics WHERE code=\'unknown_planet\'')).scalar_one() == 1


def add_candidate(db, ids, *, source='core3', name='testium', planets=('rori',), type_key='meat_wild_rori', current=False, stale=False):
    instance = ids['core3_instance' if source == 'core3' else 'gh_instance']
    type_id = db.execute(text('SELECT id FROM resource_types WHERE slug=:slug'), {'slug': type_key}).scalar_one_or_none()
    if type_id is None:
        type_id = create_resource_type(db, type_key)
    source_type_id = db.execute(text('''INSERT INTO source_resource_types(source_instance_id,source_type_key,canonical_type_id,mapping_status,mapping_confidence)
        VALUES(:instance,:key,:type,'mapped','test') ON CONFLICT(source_instance_id,source_type_key) DO UPDATE SET canonical_type_id=:type RETURNING id'''),
        {'instance': instance, 'key': type_key, 'type': type_id}).scalar_one()
    resource_id = create_source_resource(db, instance, name, name)
    db.execute(text('UPDATE source_resources SET source_resource_type_id=:type WHERE id=:id'), {'type': source_type_id, 'id': resource_id})
    batch_id = db.execute(text("INSERT INTO import_batches(source_instance_id,import_kind,status) VALUES(:instance,'test','complete') RETURNING id"), {'instance': instance}).scalar_one()
    snapshot_id = db.execute(text("INSERT INTO source_snapshots(import_batch_id,snapshot_kind) VALUES(:batch,'test') RETURNING id"), {'batch': batch_id}).scalar_one()
    record_id = db.execute(text("INSERT INTO source_records(source_snapshot_id,record_type,source_key,parse_status) VALUES(:snapshot,'resource',:key,'parsed') RETURNING id"), {'snapshot': snapshot_id, 'key': name}).scalar_one()
    observed = datetime.now(timezone.utc) - (timedelta(days=2) if stale else timedelta())
    for planet in planets:
        db.execute(text('''INSERT INTO resource_planet_observations(source_resource_id,planet_id,state,observed_at,confidence,source_record_id,import_batch_id)
          SELECT :resource,id,'active',:observed,'test',:record,:batch FROM planets WHERE slug=:planet'''),
          {'resource': resource_id, 'observed': observed, 'record': record_id, 'batch': batch_id, 'planet': planet})
    if current:
        db.execute(text('''INSERT INTO current_resource_availability(source_resource_id,source_instance_id,is_active,last_seen_source_snapshot_id,last_seen_at,current_source_snapshot_id,current_as_of)
            VALUES(:resource,:instance,true,:snapshot,:observed,:snapshot,:observed)'''), {'resource': resource_id, 'instance': instance, 'snapshot': snapshot_id, 'observed': observed})
    return resource_id


def match(db, **kwargs):
    return match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='rori', category='meat', **kwargs)


def test_correct_planet_raw_substring_and_multiplanet(db, ids, loaded):
    add_candidate(db, ids, name='right', planets=('rori', 'naboo'), current=True)
    add_candidate(db, ids, name='wrongplanet', planets=('naboo',), current=True)
    add_candidate(db, ids, name='wrongtype', type_key='meat_wild', current=True)
    add_candidate(db, ids, name='substring', type_key='prefix_meat_wild_rori_suffix', current=True)
    result = match(db)
    assert {c['name'] for c in result['candidates']} == {'right', 'substring'}
    assert all(c['availability'] == 'current_snapshot' and c['fresh'] for c in result['candidates'])
    naboo = match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='naboo', category='meat')
    assert {c['name'] for c in naboo['candidates']} == {'right', 'wrongplanet'}


def test_historical_and_stale_not_current_guarantees(db, ids, loaded):
    add_candidate(db, ids, source='gh', name='archive')
    add_candidate(db, ids, name='stale', current=True, stale=True)
    result = match(db)
    candidates = {c['name']: c for c in result['candidates']}
    assert candidates['archive']['availability'] == 'historical'
    assert not candidates['archive']['runtime_authoritative']
    assert candidates['stale']['availability'] == 'current_snapshot'
    assert not candidates['stale']['fresh']


def test_resource_current_elsewhere_is_historical_here(db, ids, loaded):
    resource = add_candidate(db, ids, name='moved', current=True)
    db.execute(text('UPDATE current_resource_availability SET last_seen_source_snapshot_id=NULL WHERE source_resource_id=:id'), {'id': resource})
    assert match(db)['candidates'][0]['availability'] == 'historical'


def test_missing_spawn_zero_amount_and_milk(db, loaded):
    assert match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='tatooine', category='meat')['status'] == 'no_verified_planet_spawn'
    assert match_resources(db, repository='fixture', creature='zero', planet='rori', category='meat')['status'] == 'not_harvestable'
    assert match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='rori', category='milk')['status'] == 'milk_requires_separate_runtime_validation'
    assert match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='hoth', category='meat')['status'] == 'unverified'


def test_literal_static_and_conditional_spawn(source_tree):
    _, put = source_tree
    put('scripts/screenplays/screenplays.lua', 'includeFile("spawns.lua")')
    put('scripts/screenplays/spawns.lua', '''
      spawnMobile("rori","wild_foreign_bantha_rori",300,12,34,56,90,0)
      function conditional() spawnMobile("naboo","wild_foreign_bantha_rori",0,1,2,3,0,0) end
      spawnMobile("rori","wild_foreign_bantha_rori",0,unknown,2,3,0,0)
    ''')
    catalog = build(source_tree)
    static = [e for e in catalog.evidence if e['region'][0] == 'static']
    assert len(static) == 1
    assert static[0]['planet'] == 'rori'
    node = catalog.nodes[static[0]['region']]
    assert node['details']['geometry'] == {'x': 12, 'z': 34, 'y': 56, 'heading': 90}
    assert {'conditional_spawn', 'unvalidated_static_spawn'} <= {d['code'] for d in catalog.diagnostics}


def test_inheritance_binds_parent_at_construction(source_tree):
    _, put = source_tree
    put('scripts/mobile/child.lua', '''
       parent=Creature:new{meatType="meat_wild_rori",meatAmount=10}
       child=parent:new{}
       parent=Creature:new{meatType="different",meatAmount=99}
       CreatureTemplates:addCreatureTemplate(child,"bound")
    ''')
    assert build(source_tree).creatures['bound']['harvest']['meat']['base_amount'] == 10


def test_dynamic_assignment_does_not_reuse_old_symbol(source_tree):
    _, put = source_tree
    put('scripts/mobile/child.lua', '''
       child=Creature:new{meatType="meat_wild_rori",meatAmount=10}
       child=replace(child)
       CreatureTemplates:addCreatureTemplate(child,"bad")
    ''')
    assert 'bad' not in build(source_tree).creatures


def test_relevant_property_mutation_blocks_activation(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'child.meatAmount = runtimeAmount()')
    assert build(source_tree).fatal


def test_import_savepoint_rolls_back_on_write_failure(db, source_tree, monkeypatch):
    catalog = build(source_tree)
    # Force a late FK failure, after revision/files/creatures were inserted.
    catalog.edges.append((('group', 'world'), ('creature', 'missing'), {}))
    with pytest.raises(KeyError):
        import_catalog(db, catalog)
    assert db.execute(text('SELECT count(*) FROM creature_revisions')).scalar_one() == 0
    assert db.execute(text('SELECT count(*) FROM creature_identities')).scalar_one() == 0


@pytest.mark.parametrize('category,lookup', [('hide', 'hide_wooly_rori'), ('bone', 'bone_mammal_rori')])
def test_other_carcass_categories(db, ids, loaded, category, lookup):
    add_candidate(db, ids, name=category, type_key=lookup, current=True)
    result = match_resources(db, repository='fixture', creature='wild_foreign_bantha_rori', planet='rori', category=category)
    assert [r['name'] for r in result['candidates']] == [category]


def test_source_text_retained_and_runtime_trim_applied(db, ids, source_tree):
    _, put = source_tree
    put('scripts/mobile/child.lua', '''
      child=Creature:new{meatType="  meat_wild_rori  ",meatAmount=10}
      CreatureTemplates:addCreatureTemplate(child,"wild_foreign_bantha_rori")
    ''')
    import_catalog(db, build(source_tree), activate=True)
    add_candidate(db, ids, current=True)
    result = match(db)
    assert result['definition']['raw_lookup'] == '  meat_wild_rori  '
    assert result['effective_lookup'] == 'meat_wild_rori'
    assert result['candidates']
    assert result['definition']['field_provenance']['meatType']['path'].endswith('child.lua')


def test_existing_current_projection_unchanged(db, ids, loaded, source_tree):
    add_candidate(db, ids, current=True)
    before = db.execute(text('SELECT row_to_json(c) FROM current_resource_availability c')).scalar_one()
    import_catalog(db, build(source_tree, 'b'), activate=True)
    match(db)
    after = db.execute(text('SELECT row_to_json(c) FROM current_resource_availability c')).scalar_one()
    assert before == after


def test_lua_cannot_execute(source_tree):
    root, put = source_tree
    sentinel = root / 'executed.txt'
    put('scripts/mobile/graph.lua', 'os.execute("touch executed.txt")\nfunction dangerous() error("executed") end')
    build(source_tree)
    assert not sentinel.exists()


def test_keyed_table_and_unshared_escape_semantics():
    ops = list(statements(r'''t={ ["safe"] = 1, name="\123", other="safe" }'''))
    value = fields(ops[0][3][1])
    assert value['safe'] == 1
    assert isinstance(value['name'], Unknown)


def test_gh_never_current_even_with_colliding_instance_code(db, ids, loaded):
    db.execute(text("UPDATE source_instances SET code='bellum-gero-live' WHERE id=:id"), {'id': ids['gh_instance']})
    add_candidate(db, ids, source='gh', current=True)
    candidate = match(db)['candidates'][0]
    assert candidate['availability'] == 'historical'
    assert not candidate['runtime_authoritative']


def test_uncertain_spawn_cannot_qualify(db, ids, loaded):
    add_candidate(db, ids, current=True)
    db.execute(text("UPDATE creature_spawn_evidence SET confidence='unverified'"))
    assert match(db)['status'] == 'no_verified_planet_spawn'


def test_nested_do_in_function_cannot_escape_quarantine(source_tree):
    _, put = source_tree
    put('scripts/screenplays/screenplays.lua', '''
      function nested()
        do local value=1 end
        spawnMobile("rori","wild_foreign_bantha_rori",0,1,2,3,0,0)
      end
    ''')
    catalog = build(source_tree)
    assert not any(e['region'][0] == 'static' for e in catalog.evidence)
    assert any(d['code'] == 'conditional_spawn' for d in catalog.diagnostics)


def test_dynamic_group_condition_quarantined(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', '''
      herd=Lair:new{mobiles={{"wild_foreign_bantha_rori",1}}}
      addLairTemplate("herd",herd)
      world={lairSpawns={{lairTemplateName="herd",weighting=runtime(),numberToSpawn=15}}}
      addSpawnGroup("world",world)
    ''')
    catalog = build(source_tree)
    assert not catalog.evidence
    assert any(d['code'] == 'dynamic_group_conditions' for d in catalog.diagnostics)


def test_unknown_function_receiving_creature_blocks_activation(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'mutate(child)')
    catalog = build(source_tree)
    assert catalog.fatal
    assert any(d['code'] == 'unsupported_call' for d in catalog.diagnostics)


@pytest.mark.parametrize('failure', ['revision', 'dirty', 'untracked_source', 'changed_during_parse'])
def test_cli_git_verification_precedes_database_access(source_tree, monkeypatch, failure):
    import sys
    import app.importing.creatures.__main__ as cli
    root, _ = source_tree
    revision = 'a' * 40
    calls = {'head': 0}
    paths = list(build(source_tree).files)
    def git_output(command, **kwargs):
        if 'rev-parse' in command:
            calls['head'] += 1
            return 'b' * 40 if failure == 'revision' or failure == 'changed_during_parse' and calls['head'] > 1 else revision
        if 'status' in command:
            return ' M source.lua' if failure == 'dirty' else ''
        if 'ls-files' in command:
            return '' if failure == 'untracked_source' else '\0'.join(paths)
        raise AssertionError(command)
    def forbidden_database(*args):
        raise AssertionError('database accessed before source verification')
    monkeypatch.setattr(cli.subprocess, 'check_output', git_output)
    monkeypatch.setattr(cli, 'make_engine', forbidden_database)
    monkeypatch.setattr(sys, 'argv', ['creatures', '--root', str(root), '--repository', 'fixture',
        '--revision', revision, '--database-url', 'postgresql://test@127.0.0.1/example_test', '--dry-run'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_repeated_include_does_not_hide_duplicate_registration(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', 'includeFile("child.lua")')
    catalog = build(source_tree)
    assert not catalog.creatures
    assert {'repeated_include', 'duplicate_registration'} <= {d['code'] for d in catalog.diagnostics}


def test_mission_only_group_retained_without_planet_inference(source_tree):
    _, put = source_tree
    put('scripts/mobile/graph.lua', '''
      herd=Lair:new{mobiles={{"wild_foreign_bantha_rori",1}}}
      addLairTemplate("herd",herd)
      missions={lairSpawns={{lairTemplateName="herd",minDifficulty=1,maxDifficulty=30}}}
      addDestroyMissionGroup("rori_destroy_missions",missions)
    ''')
    catalog = build(source_tree)
    assert not catalog.fatal
    node = catalog.nodes[('group', 'mission:rori_destroy_missions')]
    assert node['planet'] is None
    assert node['details']['_activation'] == 'mission_only'
    assert not catalog.evidence
    assert any(c['activation'] == 'mission_only' for _, _, c in catalog.edges)


@pytest.mark.parametrize('field,code', [('level', 'unresolved_level'), ('customName', 'unresolved_name')])
def test_dynamic_metadata_is_reported(source_tree, field, code):
    _, put = source_tree
    put('scripts/mobile/child.lua', f'child=Creature:new{{{field}=compute()}}\nCreatureTemplates:addCreatureTemplate(child,"dynamic")')
    catalog = build(source_tree)
    assert any(d['code'] == code for d in catalog.diagnostics)
