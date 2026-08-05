"""Streaming parser for DeepSeek-style DSML tool calls emitted as text."""

from __future__ import annotations

import html
import json
import re
import uuid
from typing import Any

from loguru import logger

from .types import ToolCallFunctionObject, ToolCallObject

_TAG_PREFIX = r"[|｜]+\s*DSML\s*[|｜]+"
_START = re.compile(rf"<\s*{_TAG_PREFIX}\s*tool_calls\b[^>]*>", re.IGNORECASE)
_END = re.compile(rf"</\s*{_TAG_PREFIX}\s*tool_calls\s*>", re.IGNORECASE)
_INVOKE = re.compile(
    rf"<\s*{_TAG_PREFIX}\s*invoke\b(?P<attrs>[^>]*)>"
    rf"(?P<body>.*?)"
    rf"</\s*{_TAG_PREFIX}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PARAMETER = re.compile(
    rf"<\s*{_TAG_PREFIX}\s*parameter\b(?P<attrs>[^>]*)>"
    rf"(?P<body>.*?)"
    rf"</\s*{_TAG_PREFIX}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ATTRIBUTE = re.compile(
    r"(?P<name>[A-Za-z_][\w.-]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
_LOOKBEHIND = 96


def _attributes(raw: str) -> dict[str, str]:
    return {
        match.group("name"): html.unescape(match.group("value"))
        for match in _ATTRIBUTE.finditer(raw)
    }


def _parameter_value(attrs: dict[str, str], body: str) -> Any:
    value = html.unescape(body.strip())
    if "value" in attrs and not value:
        value = attrs["value"]
    if attrs.get("number", "").lower() == "true":
        return (
            float(value) if any(char in value.lower() for char in ".e") else int(value)
        )
    if attrs.get("boolean", "").lower() == "true":
        return value.lower() == "true"
    if attrs.get("json", "").lower() == "true":
        return json.loads(value)
    return value


def parse_dsml_tool_calls(block: str) -> list[ToolCallObject]:
    """Convert one complete DSML tool_calls block to native tool call objects."""
    calls: list[ToolCallObject] = []
    for invoke in _INVOKE.finditer(block):
        invoke_attrs = _attributes(invoke.group("attrs"))
        tool_name = invoke_attrs.get("name", "").strip()
        if not re.fullmatch(r"[A-Za-z_][\w.-]*", tool_name):
            logger.warning("Ignoring DSML invoke with an invalid tool name")
            continue
        arguments: dict[str, Any] = {}
        try:
            for parameter in _PARAMETER.finditer(invoke.group("body")):
                parameter_attrs = _attributes(parameter.group("attrs"))
                parameter_name = parameter_attrs.get("name", "").strip()
                if not re.fullmatch(r"[A-Za-z_][\w.-]*", parameter_name):
                    raise ValueError("DSML parameter has an invalid name")
                arguments[parameter_name] = _parameter_value(
                    parameter_attrs, parameter.group("body")
                )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Ignoring malformed DSML call for {tool_name}: {exc}")
            continue
        calls.append(
            ToolCallObject(
                # Tool result messages are correlated by ID. A random suffix
                # avoids collisions when a provider emits more than one DSML
                # batch during a conversation.
                id=f"dsml_call_{uuid.uuid4().hex}",
                index=len(calls),
                function=ToolCallFunctionObject(
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                ),
            )
        )
    return calls


class DSMLStreamParser:
    """Hold marker-sized text tails so DSML never leaks to display or TTS."""

    def __init__(self) -> None:
        self.buffer = ""
        self.collecting = False

    def feed(self, chunk: str) -> tuple[str, list[ToolCallObject] | None]:
        self.buffer += chunk
        if not self.collecting:
            start = _START.search(self.buffer)
            if start:
                visible = self.buffer[: start.start()]
                self.buffer = self.buffer[start.start() :]
                self.collecting = True
                return visible, self._complete_calls()
            safe_length = max(0, len(self.buffer) - _LOOKBEHIND)
            visible, self.buffer = self.buffer[:safe_length], self.buffer[safe_length:]
            return visible, None
        return "", self._complete_calls()

    def finish(self) -> tuple[str, list[ToolCallObject] | None]:
        if self.collecting:
            calls = self._complete_calls()
            if calls is None:
                logger.warning("Suppressing incomplete DSML tool call block")
            self.buffer = ""
            return "", calls
        visible, self.buffer = self.buffer, ""
        return visible, None

    def _complete_calls(self) -> list[ToolCallObject] | None:
        end = _END.search(self.buffer)
        if not end:
            return None
        block = self.buffer[: end.end()]
        self.buffer = self.buffer[end.end() :]
        calls = parse_dsml_tool_calls(block)
        if not calls:
            logger.warning("Suppressing DSML block containing no valid tool calls")
        return calls
