"""Structured LLM roles for agenda intention, activity, and recovery planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .agenda import ExecutionCheckpoint, create_entry, new_entry_id
from .agenda_context import AgendaContext
from .idle_relocation import IdlePlacement, IdleScene

LLMComplete = Callable[[str], Awaitable[str]]

_PURSUIT_KIND_ALIASES = {
    "activity",
    "intention",
    "object_arrangement",
    "plan",
    "project",
    "research",
    "system_optimization",
    "task",
    "work",
}


def normalize_generated_kind(value: Any) -> str:
    """Map generic model vocabulary onto the agenda's semantic entry kinds."""
    kind = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if kind in _PURSUIT_KIND_ALIASES:
        return "pursuit"
    if kind in {"pursuit", "idle", "scheduled", "user"}:
        return kind
    # The intention generator is not authorized to create scheduled or user
    # work. Any other model-created category is therefore a pursuit subtype,
    # not a new persisted enum value.
    return "pursuit"


def _json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = (
        fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    )
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("planner output must be a JSON object")
    return value


@dataclass
class StructuredAgendaPlanner:
    complete: LLMComplete

    async def choose_idle_placement(
        self,
        context: AgendaContext,
        scene: IdleScene,
    ) -> IdlePlacement:
        """Choose exactly one destination from a live, bounded room snapshot."""
        objects = [
            {
                "object_id": obj.object_id,
                "label": obj.label,
                "position": {"x": obj.x, "y": obj.y},
                "distance_from_agent": obj.distance,
                "posture_on_arrival": obj.posture,
            }
            for obj in scene.objects
        ]
        raw = _json_object(
            await self.complete(
                "You choose where an embodied agent should wait while deciding what "
                "to do next. Return JSON only with object_id and reason. Choose "
                "object_id verbatim from the supplied room objects; never invent an "
                "object. Prefer a place that makes semantic sense given the recently "
                "finished work, time, presence, and room labels. A currently suitable "
                "place may be chosen again. Known seats will apply the posture shown; "
                "all other destinations remain standing. "
                f"Current position: {json.dumps(scene.current_position)}\n"
                f"Room objects: {json.dumps(objects, ensure_ascii=False)}\n"
                f"Agenda context: {json.dumps(context.payload, ensure_ascii=False)}"
            )
        )
        object_id = raw.get("object_id")
        return IdlePlacement(
            object_id=str(object_id) if object_id is not None else None,
            reason=str(raw.get("reason", ""))[:200],
        )

    async def propose_intention(self, context: AgendaContext):
        prompt = (
            "You are the intention generator for an embodied agent. Propose exactly "
            "one novel, grounded, bounded high-level intention. Return JSON only with "
            "kind, title, description, motivation_type, evidence_refs, novelty, "
            "completion_condition, time_budget_seconds, preferred_object, "
            "required_capabilities, report_policy, risk, requires_approval. "
            'The "kind" field MUST be exactly "pursuit" or "idle"; never use '
            '"task", "plan", "intention", or another synonym. Never create a user '
            "or scheduled entry. Use only evidence_refs and required_capabilities "
            "listed verbatim in the supplied context. Context:\n"
            + json.dumps(context.payload, ensure_ascii=False)
        )
        raw = _json_object(await self.complete(prompt))
        return create_entry(
            kind=normalize_generated_kind(raw["kind"]),
            title=str(raw["title"])[:160],
            description=str(raw["description"])[:1000],
            origin="autonomous",
            evidence_refs=[str(value) for value in raw["evidence_refs"]],
            completion_condition=str(raw["completion_condition"])[:1000],
            time_budget_seconds=int(raw["time_budget_seconds"]),
            status="proposed",
            preferred_object=raw.get("preferred_object"),
            required_capabilities=[
                str(value) for value in raw.get("required_capabilities", [])
            ],
            report_policy=str(raw.get("report_policy", "silent")),
            risk=str(raw.get("risk", "low")),
            requires_approval=bool(raw.get("requires_approval", False)),
            metadata={"novelty": str(raw.get("novelty", ""))[:500]},
        )

    async def next_step(self, entry: dict[str, Any], context: AgendaContext):
        prompt = (
            "You are an interruptible activity planner. Return exactly one JSON step "
            "with kind, purpose, required_object, required_capability, arguments, "
            "timeout_seconds, plausible_duration_seconds, interruptible, "
            "side_effecting. Allowed kinds: move_to_object, invoke_tool, wait, "
            "read_result, write_artifact, reflect, evaluate_completion, "
            "report_result, request_approval. Do not claim execution occurred. "
            f"Entry: {json.dumps(entry, ensure_ascii=False)}\n"
            f"Context: {json.dumps(context.payload, ensure_ascii=False)}"
        )
        raw = _json_object(await self.complete(prompt))
        checkpoint = ExecutionCheckpoint(
            step_id=new_entry_id("step"),
            kind=str(raw["kind"]),
            state="planned",
            purpose=str(raw.get("purpose", ""))[:1000],
            required_object=raw.get("required_object"),
            required_capability=raw.get("required_capability"),
            arguments=dict(raw.get("arguments", {})),
            timeout_seconds=max(1, int(raw.get("timeout_seconds", 120))),
            plausible_duration_seconds=max(
                0.0, float(raw.get("plausible_duration_seconds", 0))
            ),
            interruptible=bool(raw.get("interruptible", True)),
            side_effecting=bool(raw.get("side_effecting", False)),
        )
        checkpoint.validate()
        return checkpoint

    async def evaluate_completion(
        self, entry: dict[str, Any], evidence: list[str]
    ) -> tuple[bool, str]:
        raw = _json_object(
            await self.complete(
                "Determine whether the declared completion condition is met. "
                'Return JSON only: {"complete": boolean, "summary": string}. '
                f"Entry: {json.dumps(entry, ensure_ascii=False)} Evidence: "
                f"{json.dumps(evidence[-10:], ensure_ascii=False)}"
            )
        )
        return bool(raw.get("complete", False)), str(raw.get("summary", ""))[:1000]

    async def reconsider(self, entry: dict[str, Any], context: AgendaContext) -> str:
        raw = _json_object(
            await self.complete(
                "Reconsider interrupted work. Return JSON only with decision equal to "
                "resume, queue, or abandon. Never resume an uncertain side-effecting "
                f"step. Entry: {json.dumps(entry, ensure_ascii=False)} Context: "
                f"{json.dumps(context.payload, ensure_ascii=False)}"
            )
        )
        decision = str(raw.get("decision", "queue"))
        return decision if decision in {"resume", "queue", "abandon"} else "queue"
