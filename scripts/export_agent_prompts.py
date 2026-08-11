#!/usr/bin/env python3
"""Export a read-only Markdown reconstruction of current LLM requests.

This diagnostic never calls an LLM, executes an MCP tool, or consumes transient
physical observations.  It intentionally writes under ``tmp/``, which the
repository already ignores.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prompts import prompt_loader  # noqa: E402
from src.open_llm_vtuber.autonomy.agenda import AgendaStore  # noqa: E402
from src.open_llm_vtuber.autonomy.agenda_context import (  # noqa: E402
    AgendaContext,
    build_agenda_context,
)
from src.open_llm_vtuber.autonomy.agenda_planner import (  # noqa: E402
    StructuredAgendaPlanner,
)
from src.open_llm_vtuber.autonomy.idle_relocation import (  # noqa: E402
    idle_scene_snapshot,
)
from src.open_llm_vtuber.memory.memory_manager import (  # noqa: E402
    CONTEXT_SUMMARY_SYSTEM_PROMPT,
    LONGTERM_COMPACTION_SYSTEM_PROMPT,
    LONGTERM_EXTRACTION_SYSTEM_PROMPT,
    TOPIC_CHANGE_SYSTEM_PROMPT,
    USER_FACT_EXTRACTION_SYSTEM_PROMPT,
)
from src.open_llm_vtuber.mcpp.tool_adapter import ToolAdapter  # noqa: E402
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager  # noqa: E402
from src.open_llm_vtuber.mcpp.types import FormattedTool  # noqa: E402
from src.open_llm_vtuber.physical_context import (  # noqa: E402
    EMBODIED_PRESENTATION_POLICY,
)
from src.open_llm_vtuber.utils.timezone import now_local  # noqa: E402
from src.open_llm_vtuber.workspace.workspace_prompt_builder import (  # noqa: E402
    format_section,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "tmp" / "agent-prompts" / "current.md"
DEFAULT_WORKSPACE = Path.home() / "embodied-agent"
SKIPPED_UTILITY_PROMPTS = {
    "group_conversation_prompt",
    "proactive_speak_prompt",
    "live2d_expression_prompt",
    "mcp_prompt",
}
LOCAL_SERVER_MODULES = {
    "bash": "bash_tools",
    "scene": "scene_tools",
    "screen": "screen_tools",
    "calendar": "calendar_tools",
    "scheduler": "scheduler",
    "todo": "todo_tools",
    "timer": "timer_tools",
    "weather": "weather_tools",
    "library": "library_tools",
    "plan": "plan_tools",
    "idle": "idle_tools",
    "skills": "skill_tools",
    "drive": "drive_tools",
    "gmail": "gmail_tools",
}


@dataclass
class PromptRequest:
    key: str
    title: str
    active: bool
    trigger: str
    source: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    limitations: str = ""
    segments: list[tuple[str, str, str]] | None = None


def _read(path: Path, maximum: int | None = None) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[:maximum] if maximum is not None else text


def _latest_history(conf_uid: str) -> tuple[Path | None, list[dict[str, Any]]]:
    root = PROJECT_ROOT / "chat_history" / conf_uid
    candidates = sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime)
    for path in reversed(candidates):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, list):
            continue
        last_human = max(
            (index for index, item in enumerate(raw) if item.get("role") == "human"),
            default=-1,
        )
        if last_human < 0:
            continue
        messages = []
        for item in raw[: last_human + 1]:
            if item.get("role") == "metadata" or not item.get("content"):
                continue
            role = "user" if item.get("role") == "human" else "assistant"
            messages.append({"role": role, "content": str(item["content"])})
        return path, messages
    return None, []


def _workspace_context(workspace: Path) -> str:
    sections: list[str] = []
    for title, filename in (
        ("AGENT", "AGENT.md"),
        ("RULES", "RULE.md"),
        ("MEMORY", "MEMORY.md"),
        ("USER INFORMATION", "USER.md"),
    ):
        sections.append(format_section(title, _read(workspace / filename, 4_000)))
    body = "\n\n---\n\n".join(item for item in sections if item.strip())
    if not body:
        return ""
    return "\n".join(
        [
            "",
            "================ WORKSPACE CONTEXT ================",
            "Follow these persistent workspace files when relevant.",
            "Do not mention this context block unless directly asked.",
            "",
            body,
            "================ END WORKSPACE CONTEXT ================",
            "",
        ]
    )


def _memory_context(workspace: Path, history: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    today = _read(workspace / "memory" / f"{date.today().isoformat()}.md").strip()
    if today:
        sections.append(f"# Today's memory\n{today}")
    recent = history[-12:]
    if recent:
        rendered = "\n".join(f"{item['role']}: {item['content']}" for item in recent)
        sections.append(f"# Current conversation context\n{rendered[-4_000:]}")
    return "\n\n".join(sections)


async def _load_tools(
    enabled_servers: list[str], conf_uid: str, scene_mode: str
) -> tuple[ToolManager, list[str]]:
    raw: dict[str, FormattedTool] = {}
    unavailable: list[str] = []
    for server in enabled_servers:
        module_name = LOCAL_SERVER_MODULES.get(server)
        if not module_name:
            unavailable.append(server)
            continue
        try:
            module = importlib.import_module(f"mcp_servers.{module_name}")
            for tool in await module.mcp.list_tools():
                raw[tool.name] = FormattedTool(
                    input_schema=tool.inputSchema,
                    related_server=server,
                    description=tool.description,
                    generic_schema=None,
                )
        except Exception as exc:  # noqa: BLE001
            unavailable.append(f"{server} ({type(exc).__name__})")
    # ``format_tools_for_api`` is pure; bypass ToolAdapter.__init__ so exporting
    # does not even initialize the external MCP registry.
    adapter = object.__new__(ToolAdapter)
    openai_tools, claude_tools = adapter.format_tools_for_api(raw)
    return (
        ToolManager(openai_tools, claude_tools, raw, conf_uid, scene_mode),
        unavailable,
    )


def _tool_access_prompt(manager: ToolManager) -> str:
    reachable = set(manager.get_reachable_objects())
    hidden_groups: dict[str, dict[str, int]] = {}
    for name, tool in manager.tools.items():
        required = manager.get_required_object(name)
        if required is None or required in reachable:
            continue
        servers = hidden_groups.setdefault(required, {})
        servers[tool.related_server] = servers.get(tool.related_server, 0) + 1
    entries = []
    for object_name, servers in sorted(hidden_groups.items()):
        count = sum(servers.values())
        entries.append(
            f"- `{object_name}`: {', '.join(sorted(servers))} "
            f"({count} hidden tool{'s' if count != 1 else ''})."
        )
    if not entries:
        return EMBODIED_PRESENTATION_POLICY
    return (
        "## Tool Locations\n\n"
        "Only currently accessible tool schemas are supplied. The groups below "
        "are hidden at your current position; move near the corresponding object "
        "when one would help, and its schemas will become available.\n\n"
        + "\n".join(entries)
        + "\n\n"
        + EMBODIED_PRESENTATION_POLICY
    )


def _build_main_request(
    config: dict[str, Any],
    workspace: Path,
    manager: ToolManager,
    unavailable: list[str],
) -> PromptRequest:
    character = config["character_config"]
    conf_uid = str(character["conf_uid"])
    history_path, messages = _latest_history(conf_uid)
    segments: list[tuple[str, str, str]] = []
    persona = str(character.get("persona_prompt") or "")
    if persona:
        segments.append(
            ("persona", "conf.yaml:character_config.persona_prompt", persona)
        )
    base = persona
    for name, filename in config["system_config"].get("tool_prompts", {}).items():
        if name in SKIPPED_UTILITY_PROMPTS:
            continue
        content = prompt_loader.load_util(filename)
        segments.append((name, f"prompts/utils/{filename}.txt", content))
        base += content
    core = _workspace_context(workspace)
    segments.append(("workspace_core", str(workspace), core))
    base += core
    interrupt = "If you received `[interrupted by user]` signal, you were interrupted."
    segments.append(("interrupt", "BasicMemoryAgent.set_system", interrupt))
    base = f"{base}\n\n{interrupt}"
    memory = _memory_context(workspace, messages)
    memory_wrapper = (
        "Use the following memory as background context. It may be incomplete or "
        "outdated, so prefer the user's latest message when there is a conflict.\n\n"
        + memory
    )
    segments.append(
        (
            "active_memory",
            "MemoryManager + reconstructed recent history",
            memory_wrapper,
        )
    )
    base = f"{base}\n\n{memory_wrapper}"
    access = _tool_access_prompt(manager)
    segments.append(("tool_access", "ToolManager (non-consuming snapshot)", access))
    base = f"{base}\n\n{access}"
    tools = manager.get_formatted_tools("OpenAI")
    limits = (
        f"Reconstructed from {history_path or 'no history'}. Transient VR "
        "observations were not consumed."
    )
    if unavailable:
        limits += " Offline schemas unavailable for: " + ", ".join(unavailable) + "."
    return PromptRequest(
        "conversation",
        "Main conversation: basic_memory_agent",
        True,
        "Every normal user conversation turn.",
        "service_context.py + basic_memory_agent.py",
        base,
        messages,
        tools,
        limits,
        segments,
    )


class _Capture:
    def __init__(self, response: dict[str, Any]):
        self.prompt = ""
        self.response = response

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return json.dumps(self.response)


async def _capture_agenda(
    context: AgendaContext, entry: dict[str, Any], conf_uid: str, scene_mode: str
) -> list[PromptRequest]:
    specs: list[
        tuple[
            str,
            str,
            str,
            dict[str, Any],
            Callable[[StructuredAgendaPlanner], Awaitable[Any]],
        ]
    ] = []
    specs.append(
        (
            "agenda_intention",
            "Autonomy: intention proposal",
            "When autonomous work is proposed.",
            {
                "kind": "idle",
                "title": "snapshot",
                "description": "snapshot",
                "motivation_type": "snapshot",
                "evidence_refs": list(context.evidence_refs)[:1],
                "novelty": "snapshot",
                "completion_condition": "snapshot",
                "preferred_object": None,
                "required_capabilities": [],
                "report_policy": "silent",
                "risk": "low",
                "requires_approval": False,
            },
            lambda planner: planner.propose_intention(context),
        )
    )
    specs.append(
        (
            "agenda_next_step",
            "Autonomy: next step",
            "When planning the next executable step.",
            {
                "kind": "reflect",
                "purpose": "snapshot",
                "required_object": None,
                "required_capability": None,
                "arguments": {},
                "timeout_seconds": 60,
                "plausible_duration_seconds": 1,
                "interruptible": True,
                "side_effecting": False,
            },
            lambda planner: planner.next_step(entry, context),
        )
    )
    specs.append(
        (
            "agenda_completion",
            "Autonomy: completion evaluation",
            "After evidence is gathered.",
            {"complete": False, "summary": "snapshot"},
            lambda planner: planner.evaluate_completion(
                entry, [str(entry.get("progress_summary", ""))]
            ),
        )
    )
    specs.append(
        (
            "agenda_noteworthy",
            "Autonomy: noteworthy decision",
            "For conditional completion reporting.",
            {"noteworthy": False},
            lambda planner: planner.is_noteworthy(
                entry, str(entry.get("progress_summary", "")), context
            ),
        )
    )
    specs.append(
        (
            "agenda_reconsider",
            "Autonomy: reconsideration",
            "After autonomous work is interrupted.",
            {"decision": "queue"},
            lambda planner: planner.reconsider(entry, context),
        )
    )
    requests = []
    for key, title, trigger, response, invoke in specs:
        capture = _Capture(response)
        planner = StructuredAgendaPlanner(capture)
        try:
            await invoke(planner)
            limitation = (
                "Uses the newest persisted agenda entry as the representative entry."
            )
        except Exception as exc:  # noqa: BLE001
            limitation = f"Prompt captured, but representative validation ended with {type(exc).__name__}."
        requests.append(
            PromptRequest(
                key,
                title,
                True,
                trigger,
                "autonomy/agenda_planner.py",
                "",
                [{"role": "user", "content": capture.prompt}],
                None,
                limitation,
                [(key, "StructuredAgendaPlanner", capture.prompt)],
            )
        )
    return requests


def _memory_requests(
    workspace: Path, messages: list[dict[str, Any]]
) -> list[PromptRequest]:
    rendered = "\n".join(
        f"{item['role']}: {item['content']}" for item in messages[-12:]
    )
    latest = next(
        (item["content"] for item in reversed(messages) if item["role"] == "user"), ""
    )
    daily = _read(workspace / "memory" / f"{date.today().isoformat()}.md")
    longterm = _read(workspace / "MEMORY.md")
    definitions = [
        (
            "memory_topic",
            "Memory: topic change",
            "Before a turn when contextual memory exists.",
            TOPIC_CHANGE_SYSTEM_PROMPT,
            f"Prior conversation:\n{rendered}\n\nNew user message:\n{latest}\n\nHas the topic changed? Answer YES or NO.",
        ),
        (
            "memory_summary",
            "Memory: contextual summary",
            "When contextual memory is compacted to daily memory.",
            CONTEXT_SUMMARY_SYSTEM_PROMPT,
            rendered,
        ),
        (
            "memory_longterm",
            "Memory: durable fact extraction",
            "When a prior daily memory is promoted.",
            LONGTERM_EXTRACTION_SYSTEM_PROMPT,
            daily,
        ),
        (
            "memory_user",
            "Memory: user fact extraction",
            "When a prior daily memory is promoted.",
            USER_FACT_EXTRACTION_SYSTEM_PROMPT,
            daily,
        ),
        (
            "memory_compaction",
            "Memory: long-term compaction",
            "When MEMORY.md exceeds its configured limit.",
            LONGTERM_COMPACTION_SYSTEM_PROMPT,
            longterm,
        ),
    ]
    return [
        PromptRequest(
            key,
            title,
            True,
            trigger,
            "memory/memory_manager.py",
            system,
            [{"role": "user", "content": user}],
            None,
            "Input is reconstructed from current persisted memory and recent history.",
            [
                (f"{key}.system", "memory_manager constant", system),
                (f"{key}.user", "current persisted state", user),
            ],
        )
        for key, title, trigger, system, user in definitions
    ]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _duplicate_index(requests: list[PromptRequest]) -> tuple[list[str], list[str]]:
    segments = []
    for request in requests:
        for name, source, content in request.segments or []:
            normalized = _normalize(content)
            if len(normalized) >= 80:
                segments.append((f"{request.key}/{name}", source, content, normalized))
    exact: list[str] = []
    similar: list[str] = []
    for index, left in enumerate(segments):
        for right in segments[index + 1 :]:
            if left[3] == right[3]:
                exact.append(f"- `{left[0]}` = `{right[0]}` ({len(left[2])} chars)")
                continue
            ratio = SequenceMatcher(None, left[3], right[3]).ratio()
            if ratio >= 0.82:
                similar.append(f"- `{left[0]}` ≈ `{right[0]}` ({ratio:.0%})")
    return exact, similar


def _json_block(value: Any) -> str:
    return "~~~~json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n~~~~"


def _render(requests: list[PromptRequest], metadata: dict[str, Any]) -> str:
    exact, similar = _duplicate_index(requests)
    lines = [
        "# Current Agent Prompt Snapshot",
        "",
        "> Local diagnostic containing unredacted conversation and memory content. Do not commit or share it.",
        "",
        "## Snapshot metadata",
        "",
        _json_block(metadata),
        "",
        "## Request inventory",
        "",
        "| Request | Active | System chars | Message chars | Tool schemas |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in requests:
        message_chars = len(json.dumps(item.messages, ensure_ascii=False))
        lines.append(
            f"| {item.title} | {'yes' if item.active else 'no'} | {len(item.system)} | {message_chars} | {len(item.tools or [])} |"
        )
    lines.extend(
        [
            "",
            "## Duplication index",
            "",
            "### Exact",
            "",
            *(exact or ["No exact duplicate segments found."]),
            "",
            "### High similarity",
            "",
            *(similar or ["No segment pairs at or above 82% similarity."]),
            "",
        ]
    )
    for item in requests:
        lines.extend(
            [
                f"## {item.title}",
                "",
                f"- Active: **{'yes' if item.active else 'no'}**",
                f"- Trigger: {item.trigger}",
                f"- Source: `{item.source}`",
                f"- Reconstruction note: {item.limitations or 'None.'}",
                "",
                "### Segment sizes",
                "",
                "| Segment | Source | Characters | Share of request text |",
                "|---|---|---:|---:|",
            ]
        )
        request_chars = len(item.system) + len(
            json.dumps(item.messages, ensure_ascii=False)
        )
        for name, source, content in item.segments or []:
            share = len(content) / request_chars if request_chars else 0.0
            lines.append(f"| `{name}` | `{source}` | {len(content)} | {share:.1%} |")
        lines.extend(
            [
                "",
                "### System",
                "",
                _json_block(item.system),
                "",
                "### Messages",
                "",
                _json_block(item.messages),
                "",
            ]
        )
        if item.tools is not None:
            lines.extend(["### Tools", "", _json_block(item.tools), ""])
    lines.extend(
        [
            "## Inactive conditional paths",
            "",
            "- Prompt-mode MCP fallback: inactive because the configured OpenAI-compatible backend uses native tool schemas.",
            "- Group conversation prompt: configured but added only during group conversations.",
            "- Proactive speak prompt: added as a system message only for proactive-speech turns.",
            "- Letta, Hume, Mem0, and alternate character configurations: not selected by current `conf.yaml`.",
            "- Live2D expression prompt: intentionally skipped by current system-prompt construction.",
            "",
        ]
    )
    return "\n".join(lines)


async def build_report(
    config_path: Path = PROJECT_ROOT / "conf.yaml",
    workspace: Path = DEFAULT_WORKSPACE,
) -> str:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    character = config["character_config"]
    conf_uid = str(character["conf_uid"])
    settings = character["agent_config"]["agent_settings"]["basic_memory_agent"]
    disabled = set(settings.get("mcp_disabled_servers") or [])
    registry_config = json.loads(
        (PROJECT_ROOT / "mcp_servers.json").read_text(encoding="utf-8")
    )
    enabled = [
        name for name in registry_config.get("mcp_servers", {}) if name not in disabled
    ]
    scene_mode = (
        "vr"
        if (PROJECT_ROOT / "stage_state" / conf_uid / "vr_stage.json").exists()
        else "normal"
    )
    manager, unavailable = await _load_tools(enabled, conf_uid, scene_mode)
    main = _build_main_request(config, workspace, manager, unavailable)
    history = main.messages
    store = AgendaStore(workspace)
    state = store.load()
    entries = sorted(state.entries, key=lambda item: item.created_at, reverse=True)
    entry = (
        asdict(entries[0])
        if entries
        else {"title": "<no persisted agenda entry>", "progress_summary": ""}
    )
    capabilities = set(manager.tools)
    try:
        scene = idle_scene_snapshot(conf_uid, scene_mode)
        objects = {item.object_id for item in scene.objects}
    except Exception:  # noqa: BLE001
        objects = set()
    context = build_agenda_context(
        store,
        client_uid="__offline_prompt_snapshot__",
        capabilities=capabilities,
        object_names=objects,
        execution_mode="offline_snapshot",
    )
    context.payload["execution_mode"] = "live"
    context.payload["physical_context"] = (
        "<offline: transient VR observations are not reconstructible without consuming live state>"
    )
    agenda = await _capture_agenda(context, entry, conf_uid, scene_mode)
    requests = [main, *agenda, *_memory_requests(workspace, history)]
    metadata = {
        "generated_at": now_local().isoformat(),
        "config": str(config_path),
        "conf_uid": conf_uid,
        "character_name": character.get("character_name"),
        "conversation_agent": character["agent_config"]["conversation_agent_choice"],
        "llm_provider": settings.get("llm_provider"),
        "workspace": str(workspace),
        "scene_mode": scene_mode,
        "latest_history": str(_latest_history(conf_uid)[0] or ""),
        "enabled_mcp_servers": enabled,
        "offline_schema_unavailable": unavailable,
        "network_calls": 0,
        "secret_policy": "API keys, OAuth secrets, and environment variables are never exported.",
    }
    return _render(requests, metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "conf.yaml")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(build_report(args.config.resolve(), args.workspace.resolve()))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
