from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    create_entry,
    utc_now,
)
from src.open_llm_vtuber.autonomy.change_bus import (
    subscribe_agenda_changes,
    unsubscribe_agenda_changes,
)
from src.open_llm_vtuber.autonomy.executive import AgendaExecutive
from src.open_llm_vtuber.autonomy.observation import (
    clear_client_presence,
    update_client_presence,
)
from src.open_llm_vtuber.autonomy.presence import PresenceLedger
from src.open_llm_vtuber.autonomy.reporting import AgendaReporter
from src.open_llm_vtuber.autonomy.runtime import WorkspaceAgendaRuntime
from src.open_llm_vtuber.autonomy.settings import AutonomySettings


def _pursuit(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Review a grounded note",
        "description": "Read one note and summarize it.",
        "origin": "autonomous",
        "evidence_refs": ["memory:today"],
        "completion_condition": "One note was summarized.",
        "time_budget_seconds": 60,
        "status": "queued",
    }
    values.update(overrides)
    return create_entry(**values)


async def _emit(_payload: dict) -> None:
    return None


def _executive(store, planner, settings=None, **kwargs):
    async def run_step(_entry, _checkpoint):
        return "done"

    return AgendaExecutive(
        client_uid="level-two",
        store=store,
        planner=planner,
        settings=settings or AutonomySettings(restart_recovery_grace_seconds=0),
        run_step=run_step,
        emit=_emit,
        capabilities={"read_file"},
        object_names={"computer"},
        capability_objects={"read_file": "computer"},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_runtime_hands_off_between_headless_and_live_owners(tmp_path):
    started: list[str] = []
    stopped: list[str] = []

    async def runner(client_uid, _settings, _presence):
        started.append(client_uid)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(client_uid)

    runtime = WorkspaceAgendaRuntime(tmp_path, runner=runner)
    settings = AutonomySettings()
    await runtime.configure_background("headless", settings, None)
    await asyncio.sleep(0)
    assert runtime.owner_uid == "headless"

    await runtime.attach("live", settings, None)
    await asyncio.sleep(0)
    assert runtime.owner_uid == "live"
    assert stopped == ["headless"]

    await runtime.detach("live")
    await asyncio.sleep(0)
    assert runtime.owner_uid == "headless"
    assert started == ["headless", "live", "headless"]
    assert runtime.snapshot()["mode"] == "headless"
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_restarts_an_unexpectedly_failed_runner(tmp_path):
    attempts = 0
    restarted = asyncio.Event()

    async def runner(_client_uid, _settings, _presence):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient runner failure")
        restarted.set()
        await asyncio.Event().wait()

    runtime = WorkspaceAgendaRuntime(tmp_path, runner=runner)
    settings = AutonomySettings(
        lease_retry_seconds=0.01,
        lease_renew_seconds=1,
    )
    await runtime.attach("live", settings, None)
    await asyncio.wait_for(restarted.wait(), timeout=2)

    assert attempts == 2
    assert runtime.task is not None and not runtime.task.done()
    assert runtime.owner_uid == "live"
    await runtime.close()


@pytest.mark.asyncio
async def test_server_lifespan_registers_and_stops_headless_owner(
    tmp_path,
    monkeypatch,
):
    from src.open_llm_vtuber import autonomy, client_registry, workspace
    from src.open_llm_vtuber.autonomy import presence, runtime
    from src.open_llm_vtuber.conversations import llm_request_queue
    from src.open_llm_vtuber.server import WebSocketServer

    configured: list[tuple] = []
    registered: list[str] = []
    stopped: list[str] = []
    ledger = object()

    async def configure(root, settings, supplied_ledger):
        configured.append((root, settings, supplied_ledger))

    async def reset():
        stopped.append("runtime")

    monkeypatch.setattr(autonomy, "configure_headless_runtime", configure)
    monkeypatch.setattr(runtime, "reset_runtimes", reset)
    monkeypatch.setattr(
        presence,
        "get_or_create_presence_ledger",
        lambda uid, _root, _settings: ledger,
    )
    monkeypatch.setattr(
        presence,
        "unregister_presence_ledger",
        lambda uid: stopped.append(f"presence:{uid}"),
    )
    monkeypatch.setattr(
        client_registry,
        "register_client",
        lambda uid, _context, _send: registered.append(uid),
    )
    monkeypatch.setattr(
        client_registry,
        "unregister_client",
        lambda uid: stopped.append(f"client:{uid}"),
    )
    monkeypatch.setattr(
        llm_request_queue,
        "remove_queue",
        lambda uid: stopped.append(f"queue:{uid}"),
    )
    monkeypatch.setattr(
        workspace,
        "ensure_workspace",
        lambda: SimpleNamespace(root=tmp_path),
    )

    server = object.__new__(WebSocketServer)
    server.default_context_cache = SimpleNamespace(
        system_config=SimpleNamespace(autonomy_config=AutonomySettings())
    )
    server._headless_autonomy_started = False

    await server._start_headless_autonomy()
    assert registered == ["__autonomy_headless__"]
    assert configured[0][0] == tmp_path
    assert configured[0][2] is ledger

    await server._stop_headless_autonomy()
    assert stopped == [
        "runtime",
        "queue:__autonomy_headless__",
        "client:__autonomy_headless__",
        "presence:__autonomy_headless__",
    ]


@pytest.mark.asyncio
async def test_store_mutation_notifies_event_driven_waker(tmp_path):
    store = AgendaStore(tmp_path)
    changed = asyncio.Event()
    token = subscribe_agenda_changes(store.root, changed.set)
    try:
        store.add(_pursuit())
        await asyncio.wait_for(changed.wait(), timeout=1)
    finally:
        unsubscribe_agenda_changes(token)


def test_report_outbox_recovers_and_delivers_once(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="completed")
    store.add(entry)
    report = store.enqueue_report(entry.id, "A durable completion summary.")

    claimed = store.claim_due_report()
    assert claimed is not None
    assert claimed.id == report.id
    assert claimed.attempts == 1

    store.recover_interrupted()
    recovered = store.claim_due_report()
    assert recovered is not None
    assert recovered.id == report.id
    assert recovered.attempts == 2

    store.finish_report(report.id, delivered=True)
    assert store.claim_due_report() is None
    state = store.load()
    assert state.reports[0].status == "delivered"
    assert (
        next(item for item in state.entries if item.id == entry.id).metadata[
            "report_status"
        ]
        == "reported"
    )


@pytest.mark.parametrize("status", ["completed", "failed"])
@pytest.mark.parametrize("kind", ["pursuit", "scheduled"])
def test_business_terminal_transition_always_queues_spoken_report(
    tmp_path, kind, status
):
    store = AgendaStore(tmp_path)
    entry = _pursuit(kind=kind, report_policy="silent")
    store.add(entry)

    store.transition(entry.id, status, progress_summary="Final outcome text.")

    state = store.load()
    finished = next(item for item in state.entries if item.id == entry.id)
    reports = [item for item in state.reports if item.entry_id == entry.id]
    assert finished.metadata["terminal_voice_report"] is True
    assert len(reports) == 1
    assert reports[0].report_key == "completion"
    assert reports[0].summary == "Final outcome text."


@pytest.mark.parametrize(
    ("kind", "status"),
    [("pursuit", "abandoned"), ("idle", "completed"), ("user", "failed")],
)
def test_non_reportable_terminal_transition_stays_silent(tmp_path, kind, status):
    store = AgendaStore(tmp_path)
    entry = _pursuit(kind=kind, report_policy="on_completion")
    store.add(entry)

    store.transition(entry.id, status, progress_summary="Terminal outcome.")

    state = store.load()
    finished = next(item for item in state.entries if item.id == entry.id)
    assert "terminal_voice_report" not in finished.metadata
    assert not [item for item in state.reports if item.entry_id == entry.id]


def test_execution_failure_budget_queues_terminal_voice_report(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(report_policy="silent", status="active")
    store.add(entry)

    store.record_execution_failure(entry.id, error="provider failed", max_attempts=1)

    state = store.load()
    failed = next(item for item in state.entries if item.id == entry.id)
    assert failed.status == "failed"
    assert failed.metadata["terminal_voice_report"] is True
    assert state.reports[0].summary == (
        "I couldn't finish “Review a grounded note” after repeated attempts."
    )
    assert "provider failed" not in state.reports[0].summary


def test_execution_failure_defers_retry_with_exponential_backoff(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="active")
    store.add(entry)

    store.record_execution_failure(
        entry.id,
        error="provider failed",
        max_attempts=3,
        retry_backoff_seconds=2,
        max_retry_backoff_seconds=30,
    )

    current = store.load().entries[0]
    retry_at = current.metadata["reconsider_deferred_until"]
    delay = (datetime.fromisoformat(retry_at) - utc_now()).total_seconds()
    assert current.status == "suspended"
    assert current.metadata["execution_failures"] == 1
    assert 1 <= delay <= 2


@pytest.mark.asyncio
async def test_execution_retry_backoff_prevents_idle_successor(tmp_path):
    class Planner:
        async def propose_intention(self, _context):
            raise AssertionError("deferred work must prevent idle planning")

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="active")
    store.add(entry)
    store.record_execution_failure(
        entry.id,
        error="provider failed",
        max_attempts=3,
        retry_backoff_seconds=30,
        max_retry_backoff_seconds=30,
    )
    executive = _executive(store, Planner())

    assert await executive._advance() is False
    assert [item.kind for item in store.load().entries] == ["pursuit"]


def test_headless_claim_does_not_consume_terminal_voice_report(tmp_path):
    store = AgendaStore(tmp_path)
    spoken = _pursuit(report_policy="silent")
    legacy_silent = _pursuit(title="Legacy silent entry", report_policy="silent")
    store.add(spoken)
    store.add(legacy_silent)
    store.transition(spoken.id, "completed", progress_summary="Speak this result.")
    silent_report = store.enqueue_report(legacy_silent.id, "Keep this silent.")

    claimed = store.claim_due_report(silent_only=True)

    assert claimed is not None
    assert claimed.id == silent_report.id
    spoken_report = next(
        item for item in store.load().reports if item.entry_id == spoken.id
    )
    assert spoken_report.status == "pending"
    assert spoken_report.attempts == 0


def test_headless_report_claim_leaves_spoken_delivery_for_live_client(tmp_path):
    store = AgendaStore(tmp_path)
    spoken = _pursuit(status="completed", report_policy="on_completion")
    silent = _pursuit(
        status="completed",
        title="Silent grounded note",
        report_policy="silent",
    )
    store.add(spoken)
    store.add(silent)
    store.enqueue_report(spoken.id, "Tell the user later.")
    silent_report = store.enqueue_report(silent.id, "Record this silently.")

    claimed = store.claim_due_report(silent_only=True)

    assert claimed is not None
    assert claimed.id == silent_report.id
    pending = next(
        report for report in store.load().reports if report.entry_id == spoken.id
    )
    assert pending.status == "pending"
    assert pending.attempts == 0


@pytest.mark.asyncio
async def test_deferred_report_retries_after_presence_change(tmp_path):
    attempts = 0

    async def report_result(_entry, _summary):
        nonlocal attempts
        attempts += 1
        return attempts == 2

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="completed", report_policy="on_completion")
    store.add(entry)
    store.enqueue_report(entry.id, "Deliver after presence returns.")
    executive = _executive(
        store,
        planner=object(),
        settings=AutonomySettings(
            report_retry_seconds=60,
            restart_recovery_grace_seconds=0,
        ),
        report_result=report_result,
    )

    assert await executive._deliver_due_report() is True
    report = store.load().reports[0]
    assert report.status == "pending"
    assert report.attempts == 1
    assert report.failures == 0

    executive.observation_changed()
    assert await executive._deliver_due_report() is True
    report = store.load().reports[0]
    assert report.status == "delivered"
    assert report.attempts == 2
    assert report.failures == 0


@pytest.mark.asyncio
async def test_cancelled_report_delivery_requeues_without_swallowing_shutdown(tmp_path):
    client_uid = "cancel-report-client"
    delivery_started = asyncio.Event()

    async def run_report(_entry, _summary):
        delivery_started.set()
        await asyncio.Event().wait()

    async def emit(payload):
        if payload["receipt"]["status"] == "canceled":
            raise RuntimeError("client disconnected during handoff")

    settings = AutonomySettings(
        report_retry_seconds=1,
        restart_recovery_grace_seconds=0,
    )
    ledger = PresenceLedger.load(tmp_path, settings)
    reporter = AgendaReporter(client_uid, ledger, emit, run_report)
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="completed", report_policy="on_completion")
    store.add(entry)
    store.enqueue_report(entry.id, "A report interrupted by ownership handoff.")
    executive = _executive(
        store,
        planner=object(),
        settings=settings,
        report_result=reporter.deliver,
    )
    update_client_presence(
        client_uid,
        {"idle_seconds": 0, "user_present": True, "focus_mode": False},
    )

    try:
        delivery = asyncio.create_task(executive._deliver_due_report())
        await asyncio.wait_for(delivery_started.wait(), timeout=1)
        delivery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delivery

        report = store.load().reports[0]
        assert report.status == "pending"
        assert report.failures == 0
        assert ledger.receipts[-1].status == "canceled"
        assert ledger.receipts[-1].error is None
    finally:
        clear_client_presence(client_uid)


@pytest.mark.asyncio
async def test_report_backlog_applies_idle_backpressure(tmp_path):
    class Planner:
        async def propose_intention(self, _context):
            raise AssertionError("planner must wait for report capacity")

    store = AgendaStore(tmp_path)
    completed = _pursuit(status="completed", report_policy="on_completion")
    store.add(completed)
    store.enqueue_report(completed.id, "Pending user-facing summary.")
    executive = _executive(
        store,
        Planner(),
        settings=AutonomySettings(
            report_backlog_limit=1,
            restart_recovery_grace_seconds=0,
        ),
    )

    assert await executive._advance() is True

    queued = next(entry for entry in store.load().entries if entry.status == "queued")
    assert queued.kind == "idle"
    assert queued.metadata["report_backpressure"] is True


@pytest.mark.asyncio
async def test_planner_circuit_breaker_persists_reconsideration(tmp_path):
    class BrokenPlanner:
        calls = 0

        async def propose_intention(self, _context):
            self.calls += 1
            raise ValueError("provider unavailable")

    settings = AutonomySettings(
        planner_retry_limit=1,
        planner_retry_backoff_seconds=0,
        idle_min_seconds=5,
        fallback_reconsideration_seconds=10,
        planner_circuit_failure_threshold=1,
        planner_circuit_max_seconds=40,
        restart_recovery_grace_seconds=0,
    )
    store = AgendaStore(tmp_path)
    planner = BrokenPlanner()
    executive = _executive(store, planner, settings=settings)

    await executive._generate_successor()

    state = store.load()
    assert planner.calls == 1
    assert state.planner_health.consecutive_failures == 1
    assert state.planner_health.circuit_open_until is not None
    assert state.next_reconsideration_at == state.planner_health.circuit_open_until

    recovery = state.entries[0]
    store.transition(recovery.id, "active")
    store.transition(recovery.id, "completed")
    assert await executive._advance() is False
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_headless_mode_parks_object_dependent_work(tmp_path):
    class Planner:
        async def next_step(self, _entry, _context):
            raise AssertionError("headless mode should not plan an object-bound step")

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="active", required_capabilities=["read_file"])
    store.add(entry)
    executive = _executive(store, Planner(), connected_client=False)

    assert await executive._advance() is True
    await asyncio.sleep(0)
    assert await executive._advance() is True

    parked = next(item for item in store.load().entries if item.id == entry.id)
    assert parked.status == "suspended"
    assert parked.metadata["waiting_for_client"] is True


def test_agenda_snapshot_exposes_level_two_health(tmp_path):
    store = AgendaStore(tmp_path)
    completed = _pursuit(status="completed")
    failed = _pursuit(status="failed", title="Failed grounded note")
    store.add(completed)
    store.add(failed)
    store.enqueue_report(completed.id, "Pending summary")
    store.record_planner_failure(
        "provider unavailable",
        circuit_open_until=(utc_now() + timedelta(seconds=30)).isoformat(),
    )

    health = store.snapshot()["health"]

    assert health["terminal"]["completed"] == 1
    assert health["terminal"]["failed"] == 1
    assert health["completion_rate"] == 0.5
    assert health["reports"]["pending"] == 1
    assert health["planner"]["consecutive_failures"] == 1
