"""Persistent, VR-owned demo scenario sessions.

The authored catalogue is intentionally separate from the benchmark suite.  A
demo scenario drives the live product; a benchmark scenario is an offline,
scored research input.  Keeping the two contracts apart prevents a headset UI
from accidentally exposing held-out benchmark material.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock, Timeout

from .autonomy.agenda import (
    TERMINAL_STATUSES,
    AgendaStore,
    agenda_workspace_root,
    create_entry,
)
from .autonomy.triggers import TriggerEnvelope, TriggerReference, publish_trigger
from .scene_state import reset_scene_state

SCENARIO_PROTOCOL_VERSION = 1
RUN_FILENAME = "vr_demo_scenario.json"
NONTERMINAL_PHASES = {
    "preparing",
    "waiting_event",
    "planning",
    "running",
    "awaiting_approval",
    "cancelling",
}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    title: str
    summary: str
    description: str
    setup: tuple[str, ...]
    sequence: tuple[str, ...]
    demonstrates: tuple[str, ...]
    simulated: bool
    reset_notice: str
    runner: str

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("runner", None)
        return value


EMAIL_CHANGES_PLAN = ScenarioDefinition(
    id="email-changes-agent-plan",
    title="An Email Changes the Agent's Plan",
    summary="A simulated meeting update interrupts background work and changes the agent's plan.",
    description=(
        "The agent notices a newly unread email, inspects the changed meeting, "
        "checks the calendar conflict, and prepares a possible response at the "
        "existing approval boundary."
    ),
    setup=(
        "Reset the current VR room to its default layout.",
        "End unfinished autonomous work and begin a safe bookshelf review.",
        "Load local simulated Gmail and Calendar data; real Google data is untouched.",
    ),
    sequence=(
        "A simulated unread email arrives while the agent is occupied.",
        "The agent suspends interruptible background work and investigates at the mailbox/computer.",
        "The agent checks the conflicting calendar review and prepares a proposed reply.",
        "Any external write remains behind the normal approval boundary.",
    ),
    demonstrates=(
        "event-driven behavior",
        "autonomous intention formation",
        "cross-tool reasoning",
        "persistent tasks",
        "environment to intention to action",
    ),
    simulated=True,
    reset_notice=(
        "Starting resets this character's VR room and ends unfinished autonomous "
        "agenda work. Chat, memory, furniture data, and Google data are preserved."
    ),
    runner="simulated_email_change",
)

SCENARIOS = {EMAIL_CHANGES_PLAN.id: EMAIL_CHANGES_PLAN}


@dataclass
class ScenarioRun:
    run_id: str
    scenario_id: str
    conf_uid: str
    phase: str
    progress_summary: str
    start_request_id: str
    started_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)
    background_entry_id: str | None = None
    event_id: str | None = None
    linked_entry_id: str | None = None
    cancel_request_id: str | None = None
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.phase in NONTERMINAL_PHASES


class ScenarioConflictError(RuntimeError):
    pass


class ScenarioStore:
    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root
        self.path = workspace_root / "agenda" / RUN_FILENAME
        self.lock = FileLock(
            str(workspace_root / "agenda" / f"{RUN_FILENAME}.lock"), timeout=5
        )

    def load(self) -> ScenarioRun | None:
        if not self.path.exists():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("scenario_id") not in SCENARIOS:
            raise ValueError("scenario run store is invalid")
        return ScenarioRun(**raw)

    def save(self, run: ScenarioRun) -> ScenarioRun:
        run.updated_at = _iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(asdict(run), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return run

    def locked(self):
        return self.lock


def _find_event(agenda: AgendaStore, event_id: str | None):
    if not event_id:
        return None
    return next((item for item in agenda.load().inbox if item.id == event_id), None)


def refresh_run(store: ScenarioStore, agenda: AgendaStore) -> ScenarioRun | None:
    """Derive presentation state from the authoritative agenda records."""
    try:
        with store.locked():
            run = store.load()
            if run is None or run.phase in {"cancelled", "completed", "failed"}:
                return run
            state = agenda.load()
            event = next(
                (item for item in state.inbox if item.id == run.event_id), None
            )
            if event is not None:
                run.linked_entry_id = event.linked_entry_id or run.linked_entry_id
                if event.status == "pending":
                    run.phase = "waiting_event"
                    run.progress_summary = (
                        "The simulated unread email is waiting to be noticed."
                    )
                elif event.status == "claimed":
                    run.phase = "planning"
                    run.progress_summary = (
                        "The agent is forming an intention from the email event."
                    )
                elif event.status == "dead_letter":
                    run.phase = "failed"
                    run.error = (
                        event.error or "The simulated event could not be processed."
                    )
            entry = next(
                (item for item in state.entries if item.id == run.linked_entry_id), None
            )
            if entry is not None:
                run.progress_summary = entry.progress_summary or entry.title
                if entry.status == "blocked_approval":
                    run.phase = "awaiting_approval"
                elif entry.status in {"queued", "active", "suspended", "proposed"}:
                    run.phase = "running"
                elif entry.status == "completed":
                    run.phase = "completed"
                elif entry.status == "failed":
                    run.phase = "failed"
                    run.error = entry.progress_summary or "The agenda task failed."
                elif entry.status == "abandoned":
                    run.phase = "cancelled" if run.cancel_request_id else "completed"
            return store.save(run)
    except Timeout as exc:
        raise OSError("scenario run store is busy") from exc


async def start_scenario(
    *,
    scenario_id: str,
    request_id: str,
    conf_uid: str,
    agenda: AgendaStore,
    executive: Any,
) -> ScenarioRun:
    definition = SCENARIOS.get(scenario_id)
    if definition is None:
        raise ValueError("unknown VR scenario")
    if not request_id or len(request_id) > 128:
        raise ValueError("invalid scenario request id")
    if executive is None:
        raise ScenarioConflictError("The autonomy runtime is not available.")

    store = ScenarioStore(agenda.root)
    try:
        with store.locked():
            existing = store.load()
            if existing and existing.start_request_id == request_id:
                return existing
            if existing and existing.active:
                raise ScenarioConflictError("Another VR scenario is already running.")

            state = agenda.load()
            if any(
                entry.kind == "user" and entry.status not in TERMINAL_STATUSES
                for entry in state.entries
            ):
                raise ScenarioConflictError(
                    "Finish the current user conversation before starting the scenario."
                )
            run = ScenarioRun(
                run_id=f"vr-scenario-{uuid4().hex}",
                scenario_id=scenario_id,
                conf_uid=conf_uid,
                phase="preparing",
                progress_summary="Resetting the VR room and preparing simulated data.",
                start_request_id=request_id,
            )
            store.save(run)
    except Timeout as exc:
        raise OSError("scenario run store is busy") from exc

    # Abandon through the executive so resumable plans retain their normal
    # reconciliation guarantees.  Refuse partial reset on an unsafe stop.
    state = agenda.load()
    try:
        for entry in state.entries:
            if entry.kind != "user" and entry.status not in TERMINAL_STATUSES:
                await executive.abandon(entry.id)
    except Exception as exc:
        run.phase = "failed"
        run.error = "Existing autonomous work could not be stopped safely."
        run.progress_summary = run.error
        store.save(run)
        raise ScenarioConflictError(run.error) from exc

    reset_scene_state(conf_uid, "vr")
    background = create_entry(
        kind="pursuit",
        title="Review project notes at the bookshelf",
        description="Inspect the current project reading material without changing external state.",
        origin="demo_scenario",
        evidence_refs=[f"scenario:{run.run_id}"],
        completion_condition="Summarize the available project notes.",
        time_budget_seconds=180,
        status="active",
        preferred_object="bookshelf",
        required_capabilities=["get_library"],
        interruptibility="high",
        report_policy="silent",
        metadata={"scenario_run_id": run.run_id, "queue_priority": 30},
    )
    agenda.add(background)
    try:
        trigger = publish_trigger(
            agenda,
            TriggerEnvelope(
                TriggerReference(
                    source="gmail",
                    resource_type="message",
                    resource_id="scenario-meeting-moved",
                    revision=run.run_id,
                    change_type="unread",
                    simulated=True,
                )
            ),
        )
    except Exception as exc:
        await executive.abandon(background.id)
        run.phase = "failed"
        run.error = "The simulated email event could not be created."
        run.progress_summary = run.error
        store.save(run)
        raise ScenarioConflictError(run.error) from exc
    run.background_entry_id = background.id
    run.event_id = trigger.event.id
    run.phase = "waiting_event"
    run.progress_summary = (
        "The simulated email arrived while background work was active."
    )
    store.save(run)
    executive.wake()
    return run


async def cancel_scenario(
    *, run_id: str, request_id: str, agenda: AgendaStore, executive: Any
) -> ScenarioRun:
    store = ScenarioStore(agenda.root)
    refresh_run(store, agenda)
    try:
        with store.locked():
            run = store.load()
            if run is None or run.run_id != run_id:
                raise ValueError("unknown scenario run")
            if run.cancel_request_id == request_id or run.phase == "cancelled":
                return run
            if not run.active:
                raise ScenarioConflictError("The scenario has already ended.")
            run.cancel_request_id = request_id
            run.phase = "cancelling"
            run.progress_summary = "Stopping scenario-owned work."
            store.save(run)
    except Timeout as exc:
        raise OSError("scenario run store is busy") from exc

    if run.event_id:
        agenda.withdraw_event(run.event_id)
    for entry_id in (run.linked_entry_id, run.background_entry_id):
        if not entry_id:
            continue
        current = next(
            (item for item in agenda.load().entries if item.id == entry_id), None
        )
        if current is not None and current.status not in TERMINAL_STATUSES:
            await executive.abandon(entry_id)
    run.phase = "cancelled"
    run.progress_summary = (
        "The scenario was cancelled. The reset VR room remains active."
    )
    return store.save(run)


def scenario_state_payload(
    agenda: AgendaStore, *, error: str | None = None
) -> dict[str, Any]:
    run = refresh_run(ScenarioStore(agenda.root), agenda)
    revision = agenda.load().revision
    return {
        "type": "vr-scenario-state",
        "version": SCENARIO_PROTOCOL_VERSION,
        "revision": revision,
        "scenarios": [definition.public() for definition in SCENARIOS.values()],
        "active_run": asdict(run) if run is not None else None,
        **({"error": error} if error else {}),
    }


def simulated_fixture(workspace_root: Path | None = None) -> dict[str, Any] | None:
    """Return local tool data only while the simulated scenario is active."""
    root = workspace_root or agenda_workspace_root()
    run = ScenarioStore(root).load()
    if run is None or not run.active or run.scenario_id != EMAIL_CHANGES_PLAN.id:
        return None
    return {
        "run_id": run.run_id,
        "simulated": True,
        "email": {
            "message_id": "scenario-meeting-moved",
            "thread_id": "scenario-meeting-thread",
            "from": "Jordan Lee <jordan@example.test>",
            "to": "You <you@example.test>",
            "subject": "Project sync moved to 3:00 PM",
            "date": "2026-08-10T15:00:00-05:00",
            "snippet": "The project sync moved earlier and now overlaps your review.",
            "labels": ["INBOX", "UNREAD", "SIMULATED"],
            "body": (
                "The project sync has moved to 3:00 PM tomorrow. I noticed your "
                "review is already scheduled then. Would 3:30 PM work instead?"
            ),
            "simulated": True,
        },
        "events": [
            {
                "event_id": "scenario-review-conflict",
                "title": "Project review",
                "start": "2026-08-10T15:00:00-05:00",
                "end": "2026-08-10T15:30:00-05:00",
                "all_day": False,
                "notes": "Existing review that conflicts with the moved project sync.",
                "created_at": None,
                "updated_at": None,
                "html_link": None,
                "simulated": True,
            }
        ],
    }


def record_simulated_write(
    kind: Literal["gmail", "calendar"], payload: dict[str, Any]
) -> dict[str, Any]:
    root = agenda_workspace_root()
    fixture = simulated_fixture(root)
    if fixture is None:
        raise ValueError("no active simulated scenario")
    path = root / "agenda" / "vr_demo_scenario_writes.json"
    records: list[dict[str, Any]] = []
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            records = value
    record = {
        "id": f"simulated-{uuid4().hex}",
        "kind": kind,
        "payload": payload,
        "simulated": True,
        "created_at": _iso(),
    }
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(records[-100:], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    return record
