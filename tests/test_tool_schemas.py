"""Deterministic tool-schema manifest and argument validation."""

from __future__ import annotations

from types import SimpleNamespace

from src.open_llm_vtuber.autonomy.tool_schemas import (
    build_tool_schema_manifest,
    validate_tool_arguments,
)


def _tool(input_schema: dict) -> SimpleNamespace:
    return SimpleNamespace(input_schema=input_schema)


def test_manifest_compacts_schemas_deterministically():
    tools = {
        "get_email": _tool(
            {
                "type": "object",
                "properties": {"message_id": {"type": "string"}},
                "required": ["message_id"],
                "additionalProperties": False,
            }
        ),
        "search_emails": _tool(
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page_token": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            }
        ),
        "no_schema": object(),
    }

    manifest = build_tool_schema_manifest(tools)

    assert manifest["get_email"] == {
        "required": ["message_id"],
        "properties": {"message_id": "string"},
        "additionalProperties": False,
    }
    assert manifest["search_emails"]["properties"] == {
        "query": "string",
        "page_token": "string",
        "limit": "integer",
    }
    assert "no_schema" not in manifest


def test_manifest_respects_size_cap():
    tools = {
        f"tool_{index:03d}": _tool({"properties": {"x": {"type": "string"}}})
        for index in range(50)
    }

    manifest = build_tool_schema_manifest(tools, max_chars=400)

    assert manifest
    assert len(manifest) < len(tools)


def test_manifest_handles_nullable_and_anyof_types():
    tool = _tool(
        {
            "properties": {
                "name": {"type": ["string", "null"]},
                "count": {"type": "integer"},
                "extra": {"anyOf": [{"type": "string"}, {"type": "number"}]},
            }
        }
    )

    manifest = build_tool_schema_manifest({"mixed": tool})

    assert manifest["mixed"]["properties"] == {
        "name": "string",
        "count": "integer",
        "extra": "any",
    }


def test_validate_requires_declared_parameters():
    manifest = build_tool_schema_manifest(
        {
            "get_email": _tool(
                {
                    "properties": {"message_id": {"type": "string"}},
                    "required": ["message_id"],
                    "additionalProperties": False,
                }
            )
        }
    )

    error = validate_tool_arguments(
        "get_email",
        {
            "email_id": "94bff83f9ff5270b5bff233944be828d98b2b61442d2c4aa652c74428ce08997"
        },
        manifest,
    )

    assert error is not None
    assert "message_id" in error
    assert validate_tool_arguments("get_email", {"message_id": "abc"}, manifest) is None


def test_validate_rejects_unknown_keys_when_disallowed():
    manifest = build_tool_schema_manifest(
        {
            "search_emails": _tool(
                {
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": [],
                    "additionalProperties": False,
                }
            ),
            "lenient_tool": _tool(
                {
                    "properties": {"query": {"type": "string"}},
                    "required": [],
                    "additionalProperties": True,
                }
            ),
        }
    )

    error = validate_tool_arguments(
        "search_emails",
        {"query": "is:unread", "max_results": 5},
        manifest,
    )
    assert error is not None
    assert "max_results" in error
    assert (
        validate_tool_arguments(
            "search_emails",
            {"query": "is:unread", "limit": 5},
            manifest,
        )
        is None
    )
    # additionalProperties=true accepts caller-specific extra keys.
    assert (
        validate_tool_arguments(
            "lenient_tool",
            {"query": "x", "anything": "y"},
            manifest,
        )
        is None
    )


def test_validate_is_type_lenient_for_numbers():
    manifest = build_tool_schema_manifest(
        {
            "search_emails": _tool(
                {
                    "properties": {"limit": {"type": "integer"}},
                    "required": ["limit"],
                }
            )
        }
    )

    assert validate_tool_arguments("search_emails", {"limit": "5"}, manifest) is None
    assert (
        validate_tool_arguments("search_emails", {"limit": True}, manifest) is not None
    )
    assert (
        validate_tool_arguments("search_emails", {"limit": None}, manifest) is not None
    )


def test_validate_skips_unknown_capabilities():
    assert validate_tool_arguments("no_such_tool", {"x": 1}, {"other": {}}) is None
