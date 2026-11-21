"""A single registry supplies both API declarations and Python dispatch."""
from dataclasses import dataclass
import json
from typing import Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable

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
    def __init__(self):
        self.tools = {}

    def register(self, spec):
        if spec.name in self.tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self.tools[spec.name] = spec

    def schemas(self):
        return [spec.declaration() for spec in self.tools.values()]

    def execute(self, call):
        name = call["function"]["name"]
        try:
            if name not in self.tools:
                raise ValueError(f"Unknown tool: {name}")
            args = json.loads(call["function"]["arguments"])
            spec = self.tools[name]
            validate(args, spec.parameters)
            result = spec.handler(**args)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except (ValueError, TypeError, OSError) as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)
