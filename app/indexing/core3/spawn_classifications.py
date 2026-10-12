"""Strict literal-only Lua spawn classification reader. Never executes Lua."""
from __future__ import annotations

import re
from app.importing.creatures.lua import Reader, tokenize, Unknown, array, fields
from .resource_types import NormalizedResourceType, validate_normalized_types


class SpawnReader(Reader):
    def atom(self):
        if self.peek() != '{':
            value = super().atom()
            if isinstance(value, Unknown):
                raise ValueError(f"Unsupported Lua literal: {value.expression}")
            return value
        self.pop()
        named, values = {}, []
        while self.peek() and self.peek() != '}':
            if re.fullmatch(r'[A-Za-z_]\w*', self.peek()) and self.peek(1) == '=':
                key = self.pop()
                self.pop()
                if key in named:
                    raise ValueError(f"Duplicate Lua field: {key}")
                named[key] = self.atom()
            else:
                values.append(self.atom())
            if self.peek() != '}':
                if self.pop() not in (',', ';'):
                    raise ValueError('Expected comma or closing brace in spawn table')
        if self.pop() != '}':
            raise ValueError('Unterminated spawn table')
        return {'fields': named, 'array': values}


def parse_spawn_classifications(source):
    reader = SpawnReader(tokenize(source))
    if reader.pop() != 'resources' or reader.pop() != '=':
        raise ValueError('Expected resources = { ... } in spawn file')
    resources = reader.atom()
    if reader.peek() == ';':
        reader.pop()
    if reader.peek() or fields(resources) or not array(resources):
        raise ValueError('Expected a nonempty literal resources array with no executable Lua')
    definitions = {}
    for ordinal, entry in enumerate(array(resources), 1):
        if array(entry):
            raise ValueError(f'Spawn entry {ordinal}: expected named fields only')
        data = fields(entry)
        key = data.get('type')
        classes = data.get('classes')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', key) or fields(classes) or not array(classes):
            raise ValueError(f'Spawn entry {ordinal}: expected type identifier and classes array')
        path = []
        for pair in array(classes):
            parts = array(pair)
            if fields(pair) or len(parts) != 2 or not all(isinstance(v, str) and v.strip() == v and v for v in parts):
                raise ValueError(f'{key}: expected display-name/identifier pairs')
            if not re.fullmatch(r'[a-z][a-z0-9_]*', parts[1]):
                raise ValueError(f'{key}: invalid class identifier {parts[1]}')
            path.append(tuple(parts))
        if path[-1][1] != key:
            raise ValueError(f'{key}: final class identifier must equal type')
        path = tuple(path)
        if key in definitions and definitions[key] != path:
            raise ValueError(f'conflicting-definition:{key}')
        definitions[key] = path
    return definitions


def extend_classifications(baseline, source, fingerprint, validation):
    definitions = parse_spawn_classifications(source)
    known = {item.source_type_id: item for item in baseline}
    # Every new intermediate node must itself have a complete spawn definition.
    # A misspelled or unknown parent must never silently become authoritative.
    for key, pairs in sorted(definitions.items()):
        if pairs[0][1] != 'resource':
            pairs = (('Resources', 'resource'), *pairs)
        ids = tuple(identifier for _, identifier in pairs)
        names = tuple(name for name, _ in pairs)
        if len(set(ids)) != len(ids):
            validation.errors.append(f'cycle:{key}')
            continue
        for level, identifier in enumerate(ids):
            if identifier in known:
                node = known[identifier]
                if node.stf_class_path != ids[:level + 1] or node.class_path != names[:level + 1]:
                    validation.errors.append(f'conflicting-classification:{key}:{identifier}')
            elif identifier != key:
                parent_definition = definitions.get(identifier)
                if parent_definition is None:
                    validation.errors.append(f'missing-parent:{key}:{identifier}')
                else:
                    if parent_definition[0][1] != 'resource':
                        parent_definition = (('Resources', 'resource'), *parent_definition)
                    if parent_definition != pairs[:level + 1]:
                        validation.errors.append(f'conflicting-classification:{key}:{identifier}')
        if key not in known:
            known[key] = NormalizedResourceType(
                source_type_id=key, internal_name=key, display_name=names[-1], display_name_resolved=True,
                direct_parents=ids[-2:-1], ancestors=ids[:-1], class_path=names, stf_class_path=ids,
                stat_ranges=(), source_path=fingerprint.relative_path, source_fingerprint=fingerprint.sha256,
                recycled=False, permanent=False, container_type=None, random_name_class=None,
                warnings=('spawn-classification-only:stat-ranges-unknown',),
            )
    result = tuple(sorted(known.values(), key=lambda item: item.source_type_id))
    validate_normalized_types(result, validation)
    return result
