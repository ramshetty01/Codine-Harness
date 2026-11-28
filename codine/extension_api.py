"""Version-one registration API used inside the supervised extension worker.

This API is not an OS sandbox. The host grants trust before importing an entrypoint.
"""
import copy
import json
import re
from .registry import validate

API_VERSION = 1
MAX_REGISTRATIONS = 32
MAX_DESCRIPTOR_BYTES = 32000


def parameters(schema, depth=0):
    if depth > 12 or not isinstance(schema, dict):
        raise ValueError('Extension parameter schema exceeds supported nesting.')
    kind = schema.get('type')
    if kind not in ('object', 'array', 'string', 'integer', 'number', 'boolean'):
        raise ValueError('Extension schemas need an explicit supported type.')
    allowed = {'type', 'description', 'enum'}
    if kind == 'object': allowed |= {'properties', 'required', 'additionalProperties'}
    if kind == 'array': allowed |= {'items'}
    if kind in ('integer', 'number'): allowed |= {'minimum', 'maximum'}
    if set(schema) - allowed:
        raise ValueError('Unsupported extension schema keyword.')
    if 'description' in schema and not isinstance(schema['description'], str):
        raise ValueError('Schema description must be text.')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum'] or len(schema['enum']) > 64):
        raise ValueError('Schema enum needs 1–64 values.')
    if kind == 'object':
        props, required = schema.get('properties', {}), schema.get('required', [])
        if (not isinstance(props, dict) or len(props) > 64 or
                not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required) or
                len(set(required)) != len(required) or schema.get('additionalProperties') is not False):
            raise ValueError('Object schemas need known required fields and additionalProperties=false.')
        for name, child in props.items():
            if not isinstance(name, str) or not 1 <= len(name) <= 80:
                raise ValueError('Invalid extension argument name.')
            parameters(child, depth + 1)
    elif kind == 'array':
        parameters(schema.get('items'), depth + 1)
    for key in ('minimum', 'maximum'):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError('Numeric schema bound must be a number.')


class ExtensionAPI:
    """Registered handlers stay in the worker; only descriptors cross the boundary."""
    version = API_VERSION

    def __init__(self, capabilities, *, version=API_VERSION):
        if type(version) is not int or version != API_VERSION:
            raise ValueError('Unsupported extension API version.')
        if not isinstance(capabilities, (list, tuple)) or not set(capabilities) <= {'tools', 'commands', 'events', 'ui'}:
            raise ValueError('Invalid extension capabilities.')
        self.capabilities = frozenset(capabilities)
        self.handlers = {'tools': {}, 'commands': {}, 'events': {}, 'ui': {}}
        self.descriptors = {'tools': [], 'commands': [], 'events': [], 'ui': []}

    def _add(self, kind, name, handler, **metadata):
        if kind not in self.capabilities:
            raise ValueError('Extension did not declare capability: ' + kind)
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_]{0,23}', name):
            raise ValueError('Extension names need 1–24 lowercase letters, digits or underscores.')
        if name in self.handlers[kind] or not callable(handler):
            raise ValueError('Duplicate extension registration or non-callable handler.')
        if sum(len(rows) for rows in self.descriptors.values()) >= MAX_REGISTRATIONS:
            raise ValueError('Extension registration limit reached.')
        descriptor = {'name': name, **metadata}
        snapshot = copy.deepcopy(self.descriptors)
        snapshot[kind].append(descriptor)
        raw = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_DESCRIPTOR_BYTES:
            raise ValueError('Extension descriptors exceed 32 KB.')
        self.descriptors = json.loads(raw)
        self.handlers[kind][name] = handler

    def tool(self, name, description, schema, handler):
        if not isinstance(description, str) or not description.strip():
            raise ValueError('Tool description must be nonempty text.')
        parameters(schema)
        if schema['type'] != 'object': raise ValueError('Tool arguments must be an object.')
        self._add('tools', name, handler, description=description, parameters=schema)

    def command(self, name, description, handler):
        if not isinstance(description, str) or not description.strip():
            raise ValueError('Command description must be nonempty text.')
        self._add('commands', name, handler, description=description)

    def on(self, event, handler):
        if event != 'turn_end': raise ValueError('Only the completed turn_end event is supported in API v1.')
        self._add('events', event, handler)

    def panel(self, name, handler):
        self._add('ui', name, handler)

    def describe(self):
        return {'version': API_VERSION, 'registrations': copy.deepcopy(self.descriptors)}

    def invoke(self, kind, name, payload=None):
        handler = self.handlers.get(kind, {}).get(name)
        if handler is None: raise ValueError('Unknown extension handler.')
        if kind == 'tools':
            spec = next(row for row in self.descriptors[kind] if row['name'] == name)
            validate(payload, spec['parameters'])
        elif kind == 'commands' and not isinstance(payload, str):
            raise ValueError('Extension command input must be text.')
        elif kind == 'events' and not isinstance(payload, dict):
            raise ValueError('Extension event input must be an object.')
        result = handler() if kind == 'ui' else handler(payload)
        if kind == 'ui' and not isinstance(result, str):
            raise ValueError('UI contributions must be plain text.')
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 64000:
            raise ValueError('Extension result exceeds 64 KB.')
        return json.loads(encoded)
