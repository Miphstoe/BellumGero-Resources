"""Build a deterministic source-defined catalog from validated Core3 load roots."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .lua import Unknown, array, fields, plain, statements


CATEGORIES = {'hide': ('hideType', 'hideAmount'), 'meat': ('meatType', 'meatAmount'),
              'bone': ('boneType', 'boneAmount'), 'milk': ('milkType', 'milk')}


@dataclass
class Catalog:
    repository: str
    revision: str
    files: dict = field(default_factory=dict)
    creatures: dict = field(default_factory=dict)
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    fatal: bool = False
    zones: list = field(default_factory=list)
    parsed_creature_count: int = 0

    def diagnostic(self, path, line, code, **details):
        self.diagnostics.append(dict(path=path, line=line, code=code, details=details))

    @property
    def manifest_hash(self):
        manifest = {'files': self.files, 'zones': self.zones, 'parser': 1}
        return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()

    @property
    def summary(self):
        return dict(files=len(self.files), parsed_creatures=self.parsed_creature_count,
                    stored_creatures=len(self.creatures),
                    validated_creatures=sum(c['valid'] for c in self.creatures.values()),
                    creatures_with_verified_spawn=len({e['creature'] for e in self.evidence}),
                    spawn_nodes=len(self.nodes), spawn_relationships=len(self.edges),
                    spawn_evidence=len(self.evidence), diagnostics=len(self.diagnostics),
                    zones=self.zones, fatal=self.fatal)


def build_catalog(root: Path, *, repository: str, revision: str) -> Catalog:
    """Root is the repository containing MMOCoreORB; revision is always explicit.

    Definitions may be validated without a spawn path. Only verified graph paths
    become planet evidence. No filesystem glob makes a definition active.
    """
    if not repository or not revision.strip():
        raise ValueError('repository and explicit revision are required')
    root = root.resolve()
    scripts = root / 'MMOCoreORB/bin/scripts'
    mobile = scripts / 'mobile'
    result = Catalog(repository, revision)
    symbols, field_sources, registered, visited, visiting = {}, {}, {}, set(), set()
    conflicts = set()

    def read(path, role):
        path = path.resolve()
        if not path.is_relative_to(root):
            raise ValueError('source path escapes repository')
        key = path.relative_to(root).as_posix()
        content = path.read_bytes()
        result.files[key] = {'sha256': hashlib.sha256(content).hexdigest(), 'role': role}
        return key, content.decode('utf-8-sig')

    def effective(symbol):
        if symbol not in symbols:
            return None
        parent, value, _, _ = symbols[symbol]
        if not isinstance(value, dict):
            return None
        return fields(value)

    def visit(path):
        path = path.resolve()
        if not path.is_relative_to(scripts.resolve()):
            result.diagnostic('', 0, 'unsafe_include', target=str(path))
            result.fatal = True
            return
        if path in visiting:
            result.diagnostic('', 0, 'include_cycle', target=str(path))
            result.fatal = True
            return
        if path in visited:
            # Lua::runFile executes repeated includes again. Skipping them would
            # hide duplicate registrations and changes in load-order inheritance.
            result.diagnostic('', 0, 'repeated_include', target=str(path.relative_to(root)))
        visiting.add(path)
        try:
            key, source = read(path, 'mobile_load_path')
        except (OSError, UnicodeError) as exc:
            result.diagnostic('', 0, 'missing_include', target=str(path), error=str(exc))
            result.fatal = True
            visiting.remove(path)
            return
        try:
            operations = list(statements(source))
        except (ValueError, RecursionError) as exc:
            result.diagnostic(key, 0, 'unsupported_lua_syntax', error=str(exc))
            result.fatal = True
            visiting.remove(path)
            visited.add(path)
            return
        for op, line, name, value in operations:
            if op == 'diagnostic':
                result.diagnostic(key, line, name, expression=value)
                if name == 'dynamic_assignment':
                    symbols[value] = (None, Unknown(value), key, line)
                if name == 'dynamic_block' and value != 'function':
                    result.fatal = True
                if name == 'property_mutation':
                    symbol = value.split('.', 1)[0].split('[', 1)[0]
                    props = effective(symbol) or {}
                    if any(k in props for k in ('meatType', 'hideType', 'boneType', 'mobiles', 'lairSpawns')):
                        result.fatal = True
                if name == 'unsupported_call':
                    for arg in value['arguments']:
                        props = effective(arg.get('unresolved', '')) if isinstance(arg, dict) else None
                        if props and any(k in props for k in ('meatType', 'hideType', 'boneType', 'mobiles', 'lairSpawns')):
                            result.diagnostic(key, line, 'unsafe_definition_call', call=value['name'], argument=arg)
                            result.fatal = True
            elif op == 'assignment':
                parent, table = value
                own_fields = fields(table)
                field_sources[name] = {**field_sources.get(parent, {}),
                    **{field: {'path': key, 'line': line} for field in own_fields}}
                # :new binds the current parent TABLE, not a future global with
                # the same name. Snapshot inherited fields at construction time.
                if parent:
                    base = effective(parent)
                    table = {'fields': {**base, **fields(table)}, 'array': []} if base is not None and isinstance(table, dict) else Unknown(parent)
                symbols[name] = (parent, table, key, line)
            elif name == 'includeFile':
                if len(value) == 1 and isinstance(value[0], str):
                    visit(mobile / value[0])
                else:
                    result.diagnostic(key, line, 'dynamic_include', arguments=plain(value))
                    result.fatal = True
            elif name in ('addCreatureTemplate', 'addLairTemplate', 'addSpawnGroup', 'addDestroyMissionGroup'):
                kind = {'addCreatureTemplate': 'creature', 'addLairTemplate': 'lair', 'addSpawnGroup': 'group', 'addDestroyMissionGroup': 'mission'}[name]
                args = value if kind == 'creature' else list(reversed(value))
                if len(args) != 2 or not isinstance(args[0], Unknown) or not re.fullmatch(r'\w+', args[0].expression) or not isinstance(args[1], str):
                    result.diagnostic(key, line, 'dynamic_registration', arguments=plain(value))
                    continue
                symbol, template = args[0].expression, args[1]
                identity = kind, template
                if identity in registered:
                    conflicts.add(identity)
                    result.diagnostic(key, line, 'duplicate_registration', kind=kind, template=template)
                # Capture effective values at registration time (Lua load order).
                registered[identity] = (effective(symbol), key, line, symbols.get(symbol, (None,))[0], dict(field_sources.get(symbol, {})))
            elif name == 'spawnMobile':
                result.diagnostic(key, line, 'unvalidated_static_spawn', arguments=plain(value))
        visiting.remove(path)
        visited.add(path)

    visit(mobile / 'creatures.lua')
    result.parsed_creature_count = sum(kind == 'creature' for kind, _ in registered)
    for (kind, template), (props, path, line, parent, provenance) in sorted(registered.items()):
        if (kind, template) in conflicts or props is None:
            result.diagnostic(path, line, 'unresolved_definition', kind=kind, template=template)
            continue
        if kind == 'creature':
            harvest, valid = {}, True
            for category, (lookup_field, amount_field) in CATEGORIES.items():
                lookup, amount = props.get(lookup_field, ''), props.get(amount_field, 0)
                if not isinstance(lookup, str) or type(amount) is not int or amount < 0:
                    result.diagnostic(path, line, 'unresolved_harvest', creature=template, category=category,
                                      lookup=plain(lookup), amount=plain(amount))
                    valid = False
                else:
                    harvest[category] = {'raw_lookup': lookup, 'base_amount': amount}
            level = props.get('level')
            if level is not None and type(level) is not int:
                result.diagnostic(path, line, 'unresolved_level', creature=template, expression=plain(level))
            for name_field in ('customName', 'objectName'):
                if name_field in props and not isinstance(props[name_field], str):
                    result.diagnostic(path, line, 'unresolved_name', creature=template, field=name_field, expression=plain(props[name_field]))
            result.creatures[template] = dict(path=path, line=line, parent=parent,
                level=level if type(level) is int else None, valid=valid, harvest=harvest,
                field_provenance={k: v for k, v in provenance.items() if k in
                    {'level', 'customName', 'objectName', 'meatType', 'meatAmount', 'hideType', 'hideAmount', 'boneType', 'boneAmount', 'milkType', 'milk'}},
                names={k: props[k] for k in ('customName', 'objectName') if isinstance(props.get(k), str) and props[k]})
        else:
            if kind == 'mission':
                props = {**props, '_activation': 'mission_only'}
                result.diagnostic(path, line, 'mission_planet_unverified', group=template)
            node_key = ('group', 'mission:' + template) if kind == 'mission' else (kind, template)
            result.nodes[node_key] = dict(path=path, line=line, planet=None, details=plain(props), props=props)

    # Core3 config's ZonesEnabled governs planet roots. Resource activeZones is
    # retained as provenance but cannot authorize creature spawn regions.
    try:
        config_path, config = read(root / 'MMOCoreORB/bin/conf/config.lua', 'zone_configuration')
        operations = list(statements(config))
        if any(op == 'diagnostic' for op, _, _, _ in operations):
            raise ValueError('dynamic config expressions require manual review')
        configs = [fields(v[1]) for op, _, _, v in operations if op == 'assignment' and isinstance(v[1], dict)]
        zone_values = [p['ZonesEnabled'] for p in configs if 'ZonesEnabled' in p]
        if len(zone_values) != 1 or not isinstance(zone_values[0], dict) or not all(isinstance(z, str) for z in array(zone_values[0])):
            raise ValueError('ZonesEnabled must be one literal array')
        result.zones = sorted(set(array(zone_values[0])))
        read(scripts / 'managers/resource_manager.lua', 'resource_zone_configuration')
    except (OSError, ValueError, UnicodeError) as exc:
        result.diagnostic('', 0, 'unvalidated_zone_configuration', error=str(exc))
        result.fatal = True
    for zone in result.zones:
        try:
            path, source = read(scripts / f'managers/planet/{zone}_regions.lua', 'planet_region_root')
        except OSError:
            result.diagnostic('', 0, 'missing_region_root', planet=zone)
            continue
        for op, line, name, value in statements(source):
            if op == 'diagnostic':
                result.diagnostic(path, line, name, expression=value)
            if op != 'assignment' or name != f'{zone}_regions':
                continue
            for index, row in enumerate(array(value[1])):
                cells = array(row)
                if len(cells) < 6:
                    continue
                region_name, x, y, shape, flags, groups = cells[:6]
                mask = flags.expression.split() if isinstance(flags, Unknown) else []
                allowed = {'SPAWNAREA', 'WORLDSPAWNAREA', 'NOBUILDZONEAREA', 'NOMOBSPAWNAREA', '+'}
                if 'SPAWNAREA' not in mask or any(t not in allowed for t in mask):
                    if array(groups):
                        result.diagnostic(path, line, 'unvalidated_region_flags', region=plain(region_name), flags=plain(flags))
                    continue
                shape_values = array(shape)
                world = 'WORLDSPAWNAREA' in mask
                geometry_valid = (type(x) in (int, float) and type(y) in (int, float) and
                    len(shape_values) >= 2 and isinstance(shape_values[0], Unknown) and
                    shape_values[0].expression in ('CIRCLE', 'RECTANGLE', 'RING') and
                    all(type(n) in (int, float) for n in shape_values[1:]))
                if not geometry_valid or not isinstance(region_name, str):
                    result.diagnostic(path, line, 'unvalidated_region_geometry', region=plain(cells))
                    continue
                node_key = 'region', f'{zone}:{index}:{region_name}'
                result.nodes[node_key] = dict(path=path, line=line, planet=zone,
                    details={'name': region_name, 'world_spawn': world,
                             'geometry': None if world else {'x': x, 'y': y, 'shape': plain(shape_values)},
                             'raw_geometry': plain(cells[1:4]), 'flags': plain(flags)}, props={})
                for group in array(groups):
                    if isinstance(group, str):
                        result.edges.append((node_key, ('group', group), {'activation': 'source_defined_random_spawn'}))
                    else:
                        result.diagnostic(path, line, 'dynamic_region_group', value=plain(group))

    # DirectorManager's explicit screenplay include tree. Only a fully literal,
    # top-level spawn call can establish static evidence; function/control-block
    # calls remain diagnostics, irrespective of registration or inferred names.
    screenplay_root = scripts / 'screenplays'
    screenplay_seen = set()
    def screenplay(path):
        path = path.resolve()
        if not path.is_relative_to(screenplay_root.resolve()):
            result.diagnostic('', 0, 'unsafe_screenplay_include', target=str(path))
            result.fatal = True
            return
        if path in screenplay_seen:
            return
        screenplay_seen.add(path)
        try:
            key, source = read(path, 'screenplay_load_path')
        except OSError:
            result.diagnostic('', 0, 'missing_screenplay_include', target=str(path))
            return
        try:
            operations = list(statements(source))
        except (ValueError, RecursionError) as exc:
            result.diagnostic(key, 0, 'unsupported_screenplay_syntax', error=str(exc))
            return
        for op, line, name, args in operations:
            if op == 'diagnostic':
                if name in ('conditional_spawn', 'dynamic_block', 'unsupported_call', 'property_mutation'):
                    result.diagnostic(key, line, name, expression=plain(args))
            elif op == 'call' and name == 'includeFile':
                if len(args) == 1 and isinstance(args[0], str):
                    screenplay(screenplay_root / args[0])
                else:
                    result.diagnostic(key, line, 'dynamic_screenplay_include', arguments=plain(args))
            elif op == 'call' and name == 'spawnMobile':
                if (len(args) == 8 and isinstance(args[0], str) and args[0] in result.zones and
                    isinstance(args[1], str) and args[1] in result.creatures and result.creatures[args[1]]['valid'] and
                    all(type(a) in (int, float) for a in args[2:]) and args[7] == 0):
                    node_key = ('static', f'{key}:{line}:{len(result.nodes)}')
                    result.nodes[node_key] = dict(path=key, line=line, planet=args[0], props={},
                        details={'geometry': {'x': args[3], 'z': args[4], 'y': args[5], 'heading': args[6]},
                                 'activation': 'literal_top_level_load_call', 'respawn_seconds': args[2]})
                    result.edges.append((node_key, ('creature', args[1]), {'activation': 'literal_top_level_load_call'}))
                    result.evidence.append(dict(creature=args[1], planet=args[0], region=node_key, lair=node_key,
                                                conditions={'activation': 'literal_top_level_load_call'}))
                else:
                    result.diagnostic(key, line, 'unvalidated_static_spawn', arguments=plain(args))
    if (screenplay_root / 'screenplays.lua').is_file():
        screenplay(screenplay_root / 'screenplays.lua')

    for node_key, node in list(result.nodes.items()):
        props = node['props']
        if node_key[0] == 'group':
            for spawn in array(props.get('lairSpawns')):
                info = fields(spawn)
                lair = info.get('lairTemplateName')
                if isinstance(lair, str):
                    # Unknown gating expressions cannot authorize a spawn path.
                    if any(isinstance(v, Unknown) for v in info.values()):
                        result.diagnostic(node['path'], node['line'], 'dynamic_group_conditions', group=node_key[1], conditions=plain(info))
                        continue
                    mission = props.get('_activation') == 'mission_only'
                    if not mission and any(type(info.get(k)) not in (int, float) or info[k] <= 0 for k in ('weighting', 'numberToSpawn')):
                        result.diagnostic(node['path'], node['line'], 'inactive_group_entry', group=node_key[1], conditions=plain(info))
                        continue
                    result.edges.append((node_key, ('lair', lair), {**plain(info), 'activation': 'mission_only' if mission else 'source_defined_random_spawn'}))
        if node_key[0] == 'lair':
            for field_name in ('mobiles', 'bossMobiles'):
                for mobile_entry in array(props.get(field_name)):
                    values = array(mobile_entry)
                    if len(values) >= 2 and isinstance(values[0], str) and type(values[1]) in (int, float) and values[1] > 0:
                        result.edges.append((node_key, ('creature', values[0]), {'role': field_name, 'weight_or_count': values[1], 'activation': 'random_or_lair_condition'}))
                    else:
                        result.diagnostic(node['path'], node['line'], 'dynamic_lair_mobile', entry=plain(values))
    valid_edges = []
    for parent, child, conditions in result.edges:
        exists = (child[1] in result.creatures and result.creatures[child[1]]['valid']) if child[0] == 'creature' else child in result.nodes
        if exists:
            valid_edges.append((parent, child, conditions))
        else:
            node = result.nodes[parent]
            result.diagnostic(node['path'], node['line'], 'unresolved_spawn_reference', parent=list(parent), child=list(child))
    result.edges = valid_edges
    outgoing = {}
    for parent, child, conditions in valid_edges:
        outgoing.setdefault(parent, []).append((child, conditions))
    for region, node in result.nodes.items():
        if region[0] != 'region':
            continue
        for group, c1 in outgoing.get(region, []):
            for lair, c2 in outgoing.get(group, []):
                for creature, c3 in outgoing.get(lair, []):
                    result.evidence.append(dict(creature=creature[1], planet=node['planet'], region=region,
                        lair=lair, conditions={'path': [list(region), list(group), list(lair)], 'conditions': [c1, c2, c3]}))
    # Multiple paths may share one region/lair/creature; retain all paths together.
    deduplicated = {}
    for evidence in result.evidence:
        key = evidence['creature'], evidence['planet'], evidence['region'], evidence['lair']
        if key in deduplicated:
            deduplicated[key]['conditions'].setdefault('additional_paths', []).append(evidence['conditions'])
        else:
            deduplicated[key] = evidence
    result.evidence = list(deduplicated.values())
    return result
