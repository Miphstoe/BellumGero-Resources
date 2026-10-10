"""A literal Lua reader, not a Lua interpreter.

Only top-level assignments, :new literal tables, includes and known registrations
are interpreted. Functions and control blocks are skipped, never evaluated.
Unknown expressions are retained and cannot authorize spawn evidence.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Unknown:
    expression: str


@dataclass(frozen=True)
class Token:
    value: str
    line: int


PATTERN = re.compile(r'--\[(=*)\[.*?\]\1\]|--[^\n]*|\[(=*)\[.*?\]\2\]|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|[A-Za-z_]\w*|==|~=|<=|>=|\.\.|[^\s]', re.S)


def tokenize(source: str) -> list[Token]:
    tokens = []
    line, previous = 1, 0
    for match in PATTERN.finditer(source):
        line += source.count('\n', previous, match.start())
        value = match.group()
        if not value.startswith('--'):
            tokens.append(Token(value, line))
        line += value.count('\n')
        previous = match.end()
    return tokens


class Reader:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

    def peek(self, offset=0):
        return self.tokens[self.i + offset].value if self.i + offset < len(self.tokens) else ''

    def pop(self):
        value = self.peek()
        self.i += 1
        return value

    def expression(self, stops=(',', ';', '}')):
        start = self.i
        value = self.atom()
        if self.peek() and self.peek() not in stops:
            # Consume the entire unsupported expression, respecting delimiters.
            depth = 0
            while self.peek():
                token = self.peek()
                if depth == 0 and token in stops:
                    break
                self.pop()
                if token in ('(', '[', '{'):
                    depth += 1
                elif token in (')', ']', '}'):
                    depth -= 1
            return Unknown(' '.join(t.value for t in self.tokens[start:self.i]))
        return value

    def atom(self):
        token = self.pop()
        if token == '{':
            fields, array = {}, []
            while self.peek() and self.peek() != '}':
                before = self.i
                if re.fullmatch(r'[A-Za-z_]\w*', self.peek()) and self.peek(1) == '=':
                    key = self.pop()
                    self.pop()
                    fields[key] = self.expression()
                elif self.peek() == '[':
                    self.pop()
                    key = self.expression(stops=(']',))
                    if self.pop() != ']' or self.pop() != '=':
                        raise ValueError('unsupported table index')
                    value = self.expression()
                    if isinstance(key, (str, int)):
                        fields[str(key)] = value
                    else:
                        array.append(Unknown('dynamic table key'))
                else:
                    array.append(self.expression())
                if self.peek() in (',', ';'):
                    self.pop()
                if self.i == before:
                    raise ValueError('stalled table parser')
            if self.pop() != '}':
                raise ValueError('unterminated table')
            return {'fields': fields, 'array': array}
        if token[:1] in ('"', "'"):
            # Python and Lua disagree on numeric/unicode escapes. Only decode
            # their common literal escape subset; retain all others as unknown.
            if re.search(r'''\\(?![abfnrtv\\"'])''', token):
                return Unknown(token)
            try:
                return ast.literal_eval(token)
            except (ValueError, SyntaxError):
                return Unknown(token)
        if re.fullmatch(r'\d+(?:\.\d*)?(?:[eE][+-]?\d+)?', token):
            return float(token) if any(c in token for c in '.eE') else int(token)
        if token in ('-', '+') and re.fullmatch(r'\d+(?:\.\d*)?', self.peek()):
            return (-1 if token == '-' else 1) * self.atom()
        if token in ('true', 'false', 'nil'):
            return {'true': True, 'false': False, 'nil': None}[token]
        return Unknown(token)


def statements(source: str):
    """Yield source-ordered declarative operations; dynamic blocks are quarantined."""
    reader = Reader(tokenize(source))
    while reader.peek():
        line = reader.tokens[reader.i].line
        name = reader.pop()
        if name in ('function', 'if', 'for', 'while', 'repeat', 'do'):
            stack = [name + '_pending' if name in ('for', 'while') else name]
            while reader.peek() and stack:
                if reader.peek() == 'spawnMobile' and reader.peek(1) == '(':
                    spawn_line = reader.tokens[reader.i].line
                    reader.pop()
                    reader.pop()
                    args = []
                    while reader.peek() and reader.peek() != ')':
                        args.append(reader.expression(stops=(',', ')')))
                        if reader.peek() == ',':
                            reader.pop()
                    reader.pop()
                    yield ('diagnostic', spawn_line, 'conditional_spawn', plain(args))
                    continue
                token = reader.pop()
                if token in ('function', 'if', 'for', 'while', 'repeat'):
                    stack.append(token + '_pending' if token in ('for', 'while') else token)
                elif token == 'do':
                    if stack[-1].endswith('_pending'):
                        stack[-1] = stack[-1].removesuffix('_pending')
                    else:
                        stack.append('do')
                elif token in ('end', 'until'):
                    stack.pop()
            yield ('diagnostic', line, 'dynamic_block', name)
            if name != 'function':
                # Top-level control flow may mutate declarations or load paths.
                # The remaining file cannot be treated as unconditional data.
                return
            continue
        if name == 'local':
            name = reader.pop()
        if reader.peek() in ('.', '['):
            # Field/index writes are outside the declarative subset. In
            # particular do not mistake x.meatType = ... for a global assignment.
            start = reader.i
            while reader.peek() and reader.tokens[reader.i].line == line:
                reader.pop()
            tail = ' '.join(t.value for t in reader.tokens[start:reader.i])
            if '=' in tail:
                yield ('diagnostic', line, 'property_mutation', name + tail)
                if not (name == 'package' and tail.startswith('. path =')):
                    return
            continue
        if reader.peek() == '=' and re.fullmatch(r'[A-Za-z_]\w*', name):
            reader.pop()
            parent = None
            if reader.peek(1) == ':' and reader.peek(2) == 'new':
                parent = reader.pop()
                reader.pop()
                reader.pop()
                if reader.peek() == '(':
                    reader.pop()
                    value = reader.atom()
                    if reader.peek() == ')':
                        reader.pop()
                else:
                    value = reader.atom()
            else:
                value = reader.atom()
            # Do not accept a literal prefix of a dynamic assignment.
            if reader.peek() in ('+', '-', '*', '/', '..', '(', '[', '.'):
                yield ('diagnostic', line, 'dynamic_assignment', name)
                while reader.peek() and reader.tokens[reader.i].line == line:
                    reader.pop()
            else:
                yield ('assignment', line, name, (parent, value))
            continue
        call = name
        if name == 'CreatureTemplates' and reader.peek() == ':':
            reader.pop()
            call = reader.pop()
        if re.fullmatch(r'[A-Za-z_]\w*', call) and reader.peek() == '(':
            reader.pop()
            args = []
            while reader.peek() and reader.peek() != ')':
                args.append(reader.expression(stops=(',', ')')))
                if reader.peek() == ',':
                    reader.pop()
            reader.pop()
            if call in ('includeFile', 'addCreatureTemplate', 'addLairTemplate', 'addSpawnGroup', 'addDestroyMissionGroup', 'spawnMobile'):
                yield ('call', line, call, args)
            else:
                yield ('diagnostic', line, 'unsupported_call', {'name': call, 'arguments': plain(args)})
        elif name == 'spawnMobile':
            yield ('diagnostic', line, 'dynamic_spawn', name)


def fields(value):
    return value.get('fields', {}) if isinstance(value, dict) else {}


def array(value):
    return value.get('array', []) if isinstance(value, dict) else []


def plain(value):
    if isinstance(value, Unknown):
        return {'unresolved': value.expression}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value
