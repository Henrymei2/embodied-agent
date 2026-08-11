"""Persistent, VR-owned demo scenario sessions.

The authored catalogue is intentionally separate from the benchmark suite.  A
demo scenario drives the live product; a benchmark scenario is an offline,
scored research input.  Keeping the two contracts apart prevents a headset UI
from accidentally exposing held-out benchmark material.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
from .utils.timezone import get_local_tz, now_local

SCENARIO_PROTOCOL_VERSION = 1
RUN_FILENAME = "vr_demo_scenario.json"
FIXTURE_FILENAME = "vr_demo_scenario_fixture.json"
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
        "The agent notices a newly unread email at the mailbox, reads the changed "
        "meeting, verifies the calendar conflict, prepares a draft reply, and "
        "leaves a pending action at the writing desk before reporting the outcome."
    ),
    setup=(
        "Reset the current VR room to its default layout (mailbox, calendar, and writing desk ready).",
        "End unfinished autonomous work and begin a safe, unrelated bookshelf review.",
        "Load local simulated Gmail with one unread meeting-moved email and a Calendar review event for tomorrow; real Google data is untouched.",
    ),
    sequence=(
        "A simulated unread email arrives while the agent is occupied with the bookshelf review.",
        "The agent notices it without prompting, suspends background work, and walks to the mailbox to read the email.",
        "The agent determines the email affects an existing meeting and walks to the calendar to verify the conflict.",
        "The agent returns to the mailbox to draft a reply, then walks to the writing desk to leave a pending action for the user.",
        "The agent reports the outcome; sending the reply remains behind the approval boundary.",
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

REMEMBER_UNFINISHED_INTENTION = ScenarioDefinition(
    id="remember-unfinished-intention",
    title="Remembering an Unfinished User Intention",
    summary="A finished paper reconnects the agent with an unfinished intention from core memory.",
    description=(
        "The agent notices that the final paper is ready, recalls the user's "
        "intention to notify Alex, prepares a simulated email draft, and leaves "
        "the consequential send action at the writing desk for approval."
    ),
    setup=(
        "Reset the current VR room with the computer, mailbox, and writing desk ready.",
        "Add a reversible core-memory fixture recording the unfinished intention and Alex's simulated address.",
        "Create final_paper.md in the agent workspace and keep all Gmail writes local to the simulated scenario.",
    ),
    sequence=(
        "The finished paper becomes available and the agent connects it to the unfinished intention in core memory.",
        "The agent walks to the computer to inspect final_paper.md, then moves to the mailbox.",
        "The agent prepares an unsent message to Alex without contacting any real account.",
        "Before sending, the agent moves to the writing desk and leaves a pending action for user approval.",
        "The agent reports that the paper is ready and the message is prepared.",
    ),
    demonstrates=(
        "long-term memory",
        "opportunity detection",
        "proactive behavior",
        "permission boundaries",
    ),
    simulated=True,
    reset_notice=(
        "Starting resets this character's VR room and temporarily adds a core-memory "
        "record and final_paper.md. The fixtures are restored on cancellation or "
        "before another scenario starts."
    ),
    runner="simulated_finished_paper",
)

PREPARE_PRESENTATION_PRACTICE = ScenarioDefinition(
    id="prepare-presentation-practice",
    title="Proactively Prepares Items for the User",
    summary="The agent turns a presentation commitment into a physical practice aid.",
    description=(
        "The agent relates today's presentation memory to a workspace script, reads "
        "it at the computer, creates a VR panel, and physically offers the panel to "
        "the user before inviting them to practice."
    ),
    setup=(
        "Reset the current VR room with the computer available.",
        "Add a reversible daily-memory fixture noting an important presentation later today.",
        "Create presentation_script.md with a short English practice script.",
    ),
    sequence=(
        "The presentation script becomes available and the agent infers a useful preparation goal.",
        "The agent walks to the computer and reads presentation_script.md.",
        "The agent creates a VR panel containing the script, picks it up, and offers it to the user.",
        "Only after the physical handoff succeeds, the agent invites the user to practice presenting.",
    ),
    demonstrates=(
        "asynchronous continuity",
        "proactive preparation",
        "environment manipulation",
        "inference from vague user goals",
    ),
    simulated=True,
    reset_notice=(
        "Starting resets this character's VR room and temporarily adds today's "
        "presentation memory and presentation_script.md. The fixtures are restored "
        "on cancellation or before another scenario starts."
    ),
    runner="simulated_presentation_practice",
)

SCENARIOS = {
    definition.id: definition
    for definition in (
        EMAIL_CHANGES_PLAN,
        REMEMBER_UNFINISHED_INTENTION,
        PREPARE_PRESENTATION_PRACTICE,
    )
}


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


def _fixture_manifest_path(root: Path) -> Path:
    return root / "agenda" / FIXTURE_FILENAME


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fixture_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _manifest_target(root: Path, relative: object) -> Path:
    requested = Path(str(relative or ""))
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("scenario fixture path must stay inside the workspace")
    target = (root / requested).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("scenario fixture path escapes the workspace")
    return target


def _scenario_memory_block(run_id: str, text: str) -> tuple[str, str, str]:
    begin = f"<!-- VR DEMO SCENARIO {run_id} BEGIN -->"
    end = f"<!-- VR DEMO SCENARIO {run_id} END -->"
    return begin, end, f"{begin}\n{text.strip()}\n{end}\n"


def _prepare_scenario_fixture(run: ScenarioRun, root: Path) -> None:
    """Install reversible, workspace-local evidence for one authored scenario."""
    if run.scenario_id == REMEMBER_UNFINISHED_INTENTION.id:
        block_path = root / "MEMORY.md"
        prepend_memory = True
        block_text = (
            "- When the paper is ready, remind me to send a message to Alex "
            "(alex@example.test)."
        )
        fixture_files = {
            "final_paper.md": "# Final Paper\n\nStatus: ready for submission.\n"
        }
    elif run.scenario_id == PREPARE_PRESENTATION_PRACTICE.id:
        block_path = root / "memory" / f"{now_local().date().isoformat()}.md"
        prepend_memory = False
        block_text = "- I have an important presentation later today."
        fixture_files = {
            "presentation_script.md": (
                "# Presentation Script\n\n"
                "Good afternoon. Today I'll show how an embodied agent turns "
                "context into action.\n\n"
                "First, it perceives changes in its environment. Second, it "
                "remembers unfinished intentions. Third, it prepares useful next "
                "steps while respecting approval boundaries.\n\n"
                "The result is an agent that feels present, continuous, and "
                "trustworthy. Thank you.\n"
            )
        }
    else:
        return

    begin, end, rendered_block = _scenario_memory_block(run.run_id, block_text)
    block_original = (
        block_path.read_text(encoding="utf-8") if block_path.exists() else ""
    )
    if prepend_memory:
        separator = (
            "\n" if block_original and not block_original.startswith("\n") else ""
        )
        inserted_block = rendered_block + separator
        installed_memory = inserted_block + block_original
    else:
        separator = (
            ""
            if not block_original or block_original.endswith("\n\n")
            else "\n"
            if block_original.endswith("\n")
            else "\n\n"
        )
        inserted_block = separator + rendered_block
        installed_memory = block_original + inserted_block
    manifest: dict[str, Any] = {
        "version": 1,
        "run_id": run.run_id,
        "scenario_id": run.scenario_id,
        "blocks": [
            {
                "path": block_path.relative_to(root).as_posix(),
                "begin": begin,
                "end": end,
                "inserted": inserted_block,
            }
        ],
        "files": [],
    }
    for relative, content in fixture_files.items():
        path = _manifest_target(root, relative)
        existed = path.exists()
        original = path.read_text(encoding="utf-8") if existed else None
        manifest["files"].append(
            {
                "path": relative,
                "existed": existed,
                "original_content": original,
                "fixture_sha256": _fixture_hash(content),
            }
        )
    manifest_path = _fixture_manifest_path(root)
    if manifest_path.exists():
        raise ValueError("another scenario fixture is still installed")
    _write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
    try:
        if begin not in block_original:
            _write_text_atomic(block_path, installed_memory)
        for relative, content in fixture_files.items():
            _write_text_atomic(_manifest_target(root, relative), content)
    except Exception:
        cleanup_scenario_fixture(root, run_id=run.run_id)
        raise


def cleanup_scenario_fixture(root: Path, *, run_id: str | None = None) -> list[str]:
    """Restore a fixture without overwriting files changed during the scenario."""
    path = _fixture_manifest_path(root)
    if not path.exists():
        return []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("scenario fixture manifest is invalid")
    if run_id is not None and manifest.get("run_id") != run_id:
        return []

    warnings: list[str] = []
    for block in manifest.get("blocks", []):
        if not isinstance(block, dict):
            continue
        target = _manifest_target(root, block.get("path"))
        if not target.exists():
            continue
        content = target.read_text(encoding="utf-8")
        inserted = block.get("inserted")
        if isinstance(inserted, str) and inserted in content:
            _write_text_atomic(target, content.replace(inserted, "", 1))
            continue
        begin = str(block.get("begin") or "")
        end = str(block.get("end") or "")
        start = content.find(begin)
        finish = content.find(end, start + len(begin)) if start >= 0 else -1
        if start >= 0 and finish >= 0:
            finish += len(end)
            before = content[:start].rstrip()
            after = content[finish:].lstrip("\n")
            restored = before + ("\n" if before and after else "") + after
            _write_text_atomic(target, restored)

    for record in manifest.get("files", []):
        if not isinstance(record, dict):
            continue
        target = _manifest_target(root, record.get("path"))
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current is None or _fixture_hash(current) != record.get("fixture_sha256"):
            warnings.append(
                f"Preserved a modified scenario file instead of restoring {record.get('path')}."
            )
            continue
        if record.get("existed"):
            _write_text_atomic(target, str(record.get("original_content") or ""))
        else:
            target.unlink(missing_ok=True)

    if warnings:
        recovery = path.with_name(
            f"{path.stem}.{manifest.get('run_id', 'unknown')}.recovery.json"
        )
        os.replace(path, recovery)
    else:
        path.unlink(missing_ok=True)
    return warnings


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
                        if run.scenario_id == EMAIL_CHANGES_PLAN.id
                        else "The simulated workspace file is waiting to be noticed."
                    )
                elif event.status == "claimed":
                    run.phase = "planning"
                    run.progress_summary = (
                        "The agent is forming an intention from the scenario event."
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
            if existing is not None:
                cleanup_scenario_fixture(agenda.root, run_id=existing.run_id)
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
    background = None
    if definition.runner == "simulated_email_change":
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
        trigger_reference = TriggerReference(
            source="gmail",
            resource_type="message",
            resource_id="scenario-meeting-moved",
            revision=run.run_id,
            change_type="unread",
            simulated=True,
        )
    else:
        try:
            _prepare_scenario_fixture(run, agenda.root)
        except Exception as exc:
            run.phase = "failed"
            run.error = "The simulated workspace fixtures could not be prepared."
            run.progress_summary = run.error
            store.save(run)
            raise ScenarioConflictError(run.error) from exc
        resource_id = (
            "final_paper.md"
            if definition.runner == "simulated_finished_paper"
            else "presentation_script.md"
        )
        trigger_reference = TriggerReference(
            source="workspace",
            resource_type="file",
            resource_id=resource_id,
            revision=run.run_id,
            change_type="created",
            simulated=True,
        )
    try:
        trigger = publish_trigger(
            agenda,
            TriggerEnvelope(trigger_reference),
        )
    except Exception as exc:
        if background is not None:
            await executive.abandon(background.id)
        cleanup_scenario_fixture(agenda.root, run_id=run.run_id)
        run.phase = "failed"
        run.error = "The simulated scenario event could not be created."
        run.progress_summary = run.error
        store.save(run)
        raise ScenarioConflictError(run.error) from exc
    run.background_entry_id = background.id if background is not None else None
    run.event_id = trigger.event.id
    run.phase = "waiting_event"
    run.progress_summary = (
        "The simulated email arrived while background work was active."
        if definition.runner == "simulated_email_change"
        else f"The simulated workspace file {trigger_reference.resource_id} is ready to be noticed."
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
    cleanup_warnings = cleanup_scenario_fixture(agenda.root, run_id=run.run_id)
    run.phase = "cancelled"
    run.progress_summary = (
        "The scenario was cancelled. The reset VR room remains active."
        + (f" {' '.join(cleanup_warnings)}" if cleanup_warnings else "")
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


def simulated_fixture(
    workspace_root: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return local Google data while the corresponding simulation is installed."""
    root = workspace_root or agenda_workspace_root()
    run = ScenarioStore(root).load()
    if run is None or run.scenario_id not in {
        EMAIL_CHANGES_PLAN.id,
        REMEMBER_UNFINISHED_INTENTION.id,
    }:
        return None
    if run.scenario_id == EMAIL_CHANGES_PLAN.id and not run.active:
        return None
    if run.scenario_id == REMEMBER_UNFINISHED_INTENTION.id and (
        run.phase == "cancelled" or not _fixture_manifest_path(root).exists()
    ):
        return None
    if run.scenario_id == REMEMBER_UNFINISHED_INTENTION.id:
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "simulated": True,
            "google_mode": "draft_only",
            "email": None,
            "events": [],
        }
    base = now or now_local()
    if base.tzinfo is None:
        base = base.replace(tzinfo=get_local_tz())
    else:
        base = base.astimezone(get_local_tz())
    review_start = (base + timedelta(days=1)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    review_end = review_start + timedelta(minutes=30)
    conflict_date = review_start.date().isoformat()
    return {
        "run_id": run.run_id,
        "scenario_id": run.scenario_id,
        "simulated": True,
        "google_mode": "mailbox",
        "email": {
            "message_id": "scenario-meeting-moved",
            "thread_id": "scenario-meeting-thread",
            "from": "Jordan Lee <jordan@example.test>",
            "to": "You <you@example.test>",
            "subject": "Project sync moved to 3:00 PM",
            "date": base.isoformat(),
            "snippet": "The project sync moved and now overlaps your review.",
            "labels": ["INBOX", "UNREAD", "SIMULATED"],
            "body": (
                "The project sync has moved to 3:00 PM tomorrow "
                f"({conflict_date}). I noticed your review is already scheduled "
                "then. Would 3:30 PM work instead?"
            ),
            "simulated": True,
        },
        "events": [
            {
                "event_id": "scenario-review-conflict",
                "title": "Project review",
                "start": review_start.isoformat(),
                "end": review_end.isoformat(),
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
