"""A single registry supplies both API declarations and Python dispatch."""
from dataclasses import dataclass, field
import json
import hashlib
import uuid
from typing import Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable
    effect: str = "read"
    deferred: bool = False
    availability: Callable | None = None
    revision: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)

    def declaration(self):
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.parameters}}


def validate(value, schema, path="arguments"):
    kind = schema.get("type")
    matches = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float),
        "boolean": lambda: type(value) is bool,
    }
    if kind in matches and not matches[kind]():
        raise ValueError(f"{path} must be {kind}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}.")
    if kind == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"Missing {path}.{key}.")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ValueError(f"Unknown arguments: {', '.join(sorted(unknown))}.")
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{path}.{key}")
    if kind == "array":
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{index}]")
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below its minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} is above its maximum.")


class ToolRegistry:
    def __init__(self, allowed=None, *, deferred_limit=8, deferred_bytes=32768):
        self.tools = {}
        self.allowed = allowed or (lambda spec: True)
        if type(deferred_limit) is not int or not 1 <= deferred_limit <= 32 or type(deferred_bytes) is not int or not 1024 <= deferred_bytes <= 131072:
            raise ValueError('Invalid deferred tool bounds.')
        self.deferred_limit, self.deferred_bytes = deferred_limit, deferred_bytes
        self.active = {}
        self.pending = {}

    def register(self, spec):
        if spec.name in self.tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self.tools[spec.name] = spec

    def _available(self, spec, cache=None):
        if not self.allowed(spec): return False
        if spec.availability is None: return True
        key = id(spec.availability)
        if cache is not None and key in cache: return cache[key]
        try: available = bool(spec.availability())
        except Exception: available = False
        if cache is not None: cache[key] = available
        return available

    @staticmethod
    def version(spec):
        raw = json.dumps(spec.declaration(), sort_keys=True, allow_nan=False).encode()
        identity = f'{spec.revision}:{id(spec)}:{id(spec.handler)}:{id(spec.availability)}'.encode()
        return hashlib.sha256(identity + raw).hexdigest()

    def _sync(self, cache=None):
        cache = {} if cache is None else cache
        for name, (spec, version) in list(self.active.items()):
            if self.tools.get(name) is not spec or self.version(spec) != version or not self._available(spec, cache):
                self.active.pop(name, None)

    def catalog(self, query='', limit=10):
        if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Discovery query is at most 200 characters and limit is 1–20.')
        cache = {}
        self._sync(cache)
        rows = []
        for name, spec in sorted(self.tools.items()):
            if not spec.deferred or not self._available(spec, cache): continue
            if query.casefold() not in (name+' '+spec.description).casefold(): continue
            rows.append({'name': name, 'description': spec.description[:240],
                         'version': self.version(spec), 'active': name in self.active})
            if len(rows) > limit: break
        return {'tools': rows[:limit], 'truncated': len(rows) > limit,
                'notice': 'Only already connected, trusted integrations. load_tool schedules an exact schema for the next request; it grants no execution permission.'}

    def _bounds(self, entries):
        if len(entries) > self.deferred_limit: raise ValueError('Too many loaded integration tools; unload some first.')
        size = len(json.dumps([spec.declaration() for spec, _ in entries.values()], ensure_ascii=False).encode())
        if size > self.deferred_bytes: raise ValueError('Loaded integration schemas exceed byte limit; unload tools or narrow the integration.')

    def request_load(self, name, version):
        self._sync()
        spec = self.tools.get(name)
        if spec is None or not spec.deferred or not self._available(spec) or version != self.version(spec):
            raise ValueError('Tool unavailable or discovery revision changed; discover again.')
        candidate = {**self.active, **{key: row for key, row in self.pending.items() if row is not None}}
        for key, row in self.pending.items():
            if row is None: candidate.pop(key, None)
        candidate[name] = (spec, version)
        self._bounds(candidate)
        self.pending[name] = (spec, version)
        return {'name': name, 'status': 'scheduled', 'notice': 'Available only after this complete tool batch is durably saved and its schema appears in a future model request.'}

    def request_unload(self, names):
        if not isinstance(names, list) or not 1 <= len(names) <= 32 or any(not isinstance(name, str) for name in names):
            raise ValueError('Provide 1–32 integration tool names.')
        for name in names:
            if name not in self.tools or not self.tools[name].deferred: raise ValueError('Only integration schemas can be unloaded.')
        for name in names: self.pending[name] = None
        return {'status': 'scheduled', 'unload': names}

    def activate_pending(self):
        self._sync()
        pending, self.pending = self.pending, {}
        candidate = dict(self.active)
        skipped = []
        for name, row in pending.items():
            if row is None: candidate.pop(name, None)
            elif self.tools.get(name) is row[0] and self.version(row[0]) == row[1] and self._available(row[0]): candidate[name] = row
            else: skipped.append(name)
        try: self._bounds(candidate)
        except ValueError: return ['Deferred tool activation rejected: schema bounds changed.']
        self.active = candidate
        return ['Deferred tool activation skipped unavailable or changed tools.'] if skipped else []

    def cancel_pending(self):
        self.pending.clear()

    def schemas(self):
        cache = {}
        self._sync(cache)
        return [spec.declaration() for spec in self.tools.values()
                if self._available(spec, cache) and (not spec.deferred or spec.name in self.active)]

    def execute(self, call):
        name = call["function"]["name"]
        try:
            if name not in self.tools:
                raise ValueError(f"Unknown tool: {name}")
            args = json.loads(call["function"]["arguments"])
            spec = self.tools[name]
            if not self._available(spec):
                raise ValueError(f"Tool {name} is disabled by the current planning policy.")
            self._sync()
            if spec.deferred and name not in self.active:
                raise ValueError('Integration tool schema is not loaded; discover and load it for a future request first.')
            validate(args, spec.parameters)
            result = spec.handler(**args)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except (ValueError, TypeError, OSError) as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)
