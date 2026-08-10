"""Compact tool schemas for planner context and deterministic argument checks.

The agenda planner generates ``invoke_tool`` steps from a text prompt.  It has
no native tool-calling mode, so it needs a bounded, deterministic summary of
each tool's declared parameters.  The same manifest is used to reject steps
whose arguments could not have worked before they reach execution.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

_DEFAULT_MAX_MANIFEST_CHARS = 12_000


def build_tool_schema_manifest(
    tools: Mapping[str, Any],
    *,
    max_chars: int = _DEFAULT_MAX_MANIFEST_CHARS,
) -> dict[str, dict]:
    """Build a compact, deterministic manifest from formatted tool objects.

    Each entry has the shape::

        {"required": [...], "properties": {name: type}, "additionalProperties": bool}

    The catalog is ordered alphabetically and sized by JSON payload so a large
    MCP catalog cannot bloat planner context.  Tools that do not carry a
    parseable ``input_schema`` are omitted and are simply not schema-checked.
    """

    manifest: dict[str, dict] = {}
    budget = max(0, int(max_chars))
    for raw_name in sorted(tools):
        tool = tools[raw_name]
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        compact = {
            "required": sorted(
                str(value) for value in (required if isinstance(required, list) else [])
            ),
            "properties": {
                str(key): _compact_type(value) for key, value in properties.items()
            },
            "additionalProperties": bool(schema.get("additionalProperties", False)),
        }
        payload = json.dumps(
            compact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload) > budget:
            continue
        manifest[str(raw_name)] = compact
        budget -= len(payload) + len(str(raw_name)) + 4
        if budget <= 0:
            break
    return manifest


def validate_tool_arguments(
    capability: str,
    arguments: Mapping[str, Any],
    manifest: Mapping[str, dict],
) -> str | None:
    """Return an error message when arguments violate the compact schema.

    Shape checking is strict, matching the OpenAI tool-calling convention:
    every required parameter must be present and unknown keys are rejected when
    ``additionalProperties`` is false (the MCP default).  Type checking is
    deliberately lenient: numeric strings satisfy number parameters and
    ``None`` is treated as an omitted value.  Tools absent from the manifest
    are not validated.
    """

    schema = manifest.get(capability)
    if schema is None:
        return None
    args = dict(arguments or {})
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in args or args.get(name) is None:
            return (
                f"tool {capability} requires argument '{name}'; "
                f"declared parameters: {sorted(properties)}"
            )
    if not schema.get("additionalProperties", False):
        unknown = sorted(set(args) - set(properties))
        if unknown:
            return (
                f"tool {capability} has no parameter '{unknown[0]}'; "
                f"declared parameters: {sorted(properties)}"
            )
    for name, value in args.items():
        if value is None or name not in properties:
            continue
        expected = properties[name]
        if not _type_accepts(expected, value):
            return (
                f"tool {capability} argument '{name}' should be {expected}, "
                f"got {type(value).__name__}"
            )
    return None


def _compact_type(spec: Any) -> str:
    if isinstance(spec, dict):
        type_value = spec.get("type")
        if isinstance(type_value, list):
            types = [str(value) for value in type_value if isinstance(value, str)]
            return types[0] if types else "any"
        if isinstance(type_value, str):
            return type_value
        if "anyOf" in spec or "oneOf" in spec:
            return "any"
    return "any"


def _type_accepts(expected: str, value: Any) -> bool:
    if expected == "any":
        return True
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected in {"integer", "number"}:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value)
                return True
            except ValueError:
                return False
    return True
