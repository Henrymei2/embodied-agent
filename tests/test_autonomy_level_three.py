from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.open_llm_vtuber.autonomy.agenda import AgendaStore, create_entry
from src.open_llm_vtuber.autonomy.lease import (
    LeaseLostError,
    WorkspaceLease,
    lease_context,
)
from src.open_llm_vtuber.autonomy.runtime import WorkspaceAgendaRuntime
from src.open_llm_vtuber.autonomy.settings import AutonomySettings
from src.open_llm_vtuber.autonomy.telemetry import (
    AutonomyTracer,
    build_chain_report,
)


def _pursuit(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Handle a durable event",
        "description": "Inspect the event and record one bounded outcome.",
        "origin": "autonomous",
        "evidence_refs": ["external:test:event-1"],
        "completion_condition": "The event has one recorded outcome.",
        "time_budget_seconds": 60,
        "status": "queued",
    }
    values.update(overrides)
    return create_entry(**values)


def _lease_process(
    root: str,
    owner: str,
    priority: int,
    results,
) -> None:
    lease = WorkspaceLease(Path(root), ttl_seconds=60)
    handle = lease.acquire(
        owner,
        priority=priority,
        holder_label=owner,
    )
    results.put(handle.fencing_token if handle else None)


def test_workspace_lease_fences_stale_agenda_mutations(tmp_path):
    lease = WorkspaceLease(tmp_path, ttl_seconds=30)
    headless = lease.acquire(
        "server-a",
        priority=10,
        holder_label="headless",
    )
    assert headless is not None
    assert lease.acquire("server-b", priority=10, holder_label="headless") is None

    live = lease.acquire("server-b", priority=100, holder_label="live")
    assert live is not None
    assert live.fencing_token == headless.fencing_token + 1
    with pytest.raises(LeaseLostError):
        lease.assert_current(headless)

    store = AgendaStore(tmp_path)
    with lease_context(headless), pytest.raises(LeaseLostError):
        store.add(_pursuit(title="Stale owner work"))
    with lease_context(live):
        store.add(_pursuit(title="Current owner work"))
    assert store.load().fencing_token == live.fencing_token


def test_workspace_lease_takeover_is_cross_process(tmp_path):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    first = context.Process(
        target=_lease_process,
        args=(str(tmp_path), "process-a", 10, results),
    )
    first.start()
    first.join(timeout=15)
    assert first.exitcode == 0
    first_token = results.get(timeout=2)

    second = context.Process(
        target=_lease_process,
        args=(str(tmp_path), "process-b", 100, results),
    )
    second.start()
    second.join(timeout=15)
    assert second.exitcode == 0
    second_token = results.get(timeout=2)

    assert second_token == first_token + 1
    snapshot = WorkspaceLease(tmp_path).snapshot()
    assert snapshot["owner_id"] == "process-b"
    assert snapshot["fencing_token"] == second_token


def test_workspace_lease_recovers_after_crashed_owner_expiry(tmp_path):
    lease = WorkspaceLease(tmp_path, ttl_seconds=5)
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    crashed = lease.acquire(
        "crashed-process",
        priority=100,
        holder_label="live",
        now=started,
    )
    assert crashed is not None
    assert (
        lease.acquire(
            "standby-process",
            priority=100,
            holder_label="live",
            now=started + timedelta(seconds=4),
        )
        is None
    )

    recovered = lease.acquire(
        "standby-process",
        priority=100,
        holder_label="live",
        now=started + timedelta(seconds=6),
    )
    assert recovered is not None
    assert recovered.fencing_token == crashed.fencing_token + 1


def test_fenced_commit_cannot_race_a_priority_takeover(tmp_path):
    store = AgendaStore(tmp_path)
    lease = WorkspaceLease(tmp_path)
    headless = lease.acquire("headless", priority=10, holder_label="headless")
    assert headless is not None
    mutation_entered = threading.Event()
    finish_mutation = threading.Event()
    takeover_finished = threading.Event()
    takeover: list = []

    def mutate_as_headless():
        with lease_context(headless):
            store.mutate(
                lambda state: (
                    mutation_entered.set(),
                    finish_mutation.wait(timeout=2),
                    setattr(state, "paused", True),
                )
            )

    def acquire_as_live():
        takeover.append(lease.acquire("live", priority=100, holder_label="live"))
        takeover_finished.set()

    old_thread = threading.Thread(target=mutate_as_headless)
    old_thread.start()
    assert mutation_entered.wait(timeout=2)
    new_thread = threading.Thread(target=acquire_as_live)
    new_thread.start()
    assert not takeover_finished.wait(timeout=0.1)
    finish_mutation.set()
    old_thread.join(timeout=2)
    new_thread.join(timeout=2)

    assert not old_thread.is_alive()
    assert not new_thread.is_alive()
    assert takeover[0] is not None
    assert takeover[0].fencing_token == headless.fencing_token + 1
    with lease_context(takeover[0]):
        store.pause(False)
    assert store.load().fencing_token == takeover[0].fencing_token


@pytest.mark.asyncio
async def test_live_runtime_preempts_and_fences_remote_headless_owner(tmp_path):
    first_started = asyncio.Event()
    first_stopped = asyncio.Event()
    second_started = asyncio.Event()

    async def first_runner(_uid, _settings, _presence):
        first_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            first_stopped.set()

    async def second_runner(_uid, _settings, _presence):
        second_started.set()
        await asyncio.Event().wait()

    settings = AutonomySettings(
        lease_ttl_seconds=5,
        lease_renew_seconds=1,
        lease_retry_seconds=0.25,
    )
    first = WorkspaceAgendaRuntime(tmp_path, runner=first_runner)
    second = WorkspaceAgendaRuntime(tmp_path, runner=second_runner)
    await first.configure_background("headless", settings, None)
    await asyncio.wait_for(first_started.wait(), timeout=2)

    await second.attach("live", settings, None)
    await asyncio.wait_for(second_started.wait(), timeout=2)
    await asyncio.wait_for(first_stopped.wait(), timeout=3)

    assert first.snapshot()["lease_status"] in {"fenced", "standby"}
    assert second.snapshot()["lease_status"] == "held"
    await first.close()
    await second.close()


def test_external_event_is_idempotent_and_atomically_acknowledged(tmp_path):
    store = AgendaStore(tmp_path)
    first = store.publish_event(
        idempotency_key="mail:42",
        source="mail",
        kind="message_arrived",
        summary="A new message needs bounded review.",
        evidence_ref="external:mail:42",
        payload={"importance": "normal"},
    )
    replay = store.publish_event(
        idempotency_key="mail:42",
        source="mail",
        kind="message_arrived",
        summary="This replay must not create another event.",
        evidence_ref="external:mail:42",
    )
    assert replay.id == first.id
    assert len(store.load().inbox) == 1
    independent_source = store.publish_event(
        idempotency_key="mail:42",
        source="calendar",
        kind="event_changed",
        summary="The same producer key from another source is independent.",
        evidence_ref="external:calendar:42",
    )
    assert independent_source.id != first.id
    assert len(store.load().inbox) == 2

    lease = WorkspaceLease(tmp_path)
    handle = lease.acquire("event-owner", priority=100, holder_label="live")
    assert handle is not None
    with lease_context(handle):
        claimed = store.claim_next_event(
            owner_id=handle.owner_id,
            fencing_token=handle.fencing_token,
        )
        assert claimed is not None
        entry = _pursuit(evidence_refs=[claimed.evidence_ref])
        store.add_from_event(
            entry,
            event_id=claimed.id,
            fencing_token=handle.fencing_token,
        )

    state = store.load()
    event = state.inbox[0]
    assert event.status == "acknowledged"
    assert event.linked_entry_id == entry.id
    assert sum(item.id == entry.id for item in state.entries) == 1


def test_failed_event_and_activity_enter_dead_letter_store(tmp_path):
    store = AgendaStore(tmp_path)
    event = store.publish_event(
        idempotency_key="webhook:bad",
        source="webhook",
        kind="invalid_payload",
        summary="A malformed integration event needs review.",
        evidence_ref="external:webhook:bad",
    )
    lease = WorkspaceLease(tmp_path)
    handle = lease.acquire("worker", priority=100, holder_label="live")
    assert handle is not None
    with lease_context(handle):
        claimed = store.claim_next_event(
            owner_id=handle.owner_id,
            fencing_token=handle.fencing_token,
        )
        assert claimed is not None
        store.fail_event(
            event.id,
            fencing_token=handle.fencing_token,
            error="schema mismatch",
            retry_seconds=1,
            max_attempts=1,
        )

    entry = _pursuit(status="suspended")
    store.add(entry)
    store.record_execution_failure(
        entry.id,
        error="tool provider failed",
        max_attempts=1,
    )

    state = store.load()
    assert state.inbox[0].status == "dead_letter"
    assert {item.category for item in state.dead_letters} == {
        "external_event",
        "activity_step",
    }
    assert (
        next(item for item in state.entries if item.id == entry.id).status == "failed"
    )


def test_fenced_takeover_recovers_an_abandoned_event_claim(tmp_path):
    store = AgendaStore(tmp_path)
    event = store.publish_event(
        idempotency_key="calendar:recovery",
        source="calendar",
        kind="event_changed",
        summary="A claimed event must survive its worker disappearing.",
        evidence_ref="external:calendar:recovery",
    )
    lease = WorkspaceLease(tmp_path)
    headless = lease.acquire("headless", priority=10, holder_label="headless")
    assert headless is not None
    with lease_context(headless):
        claimed = store.claim_next_event(
            owner_id=headless.owner_id,
            fencing_token=headless.fencing_token,
        )
    assert claimed is not None

    live = lease.acquire("live", priority=100, holder_label="live")
    assert live is not None
    with lease_context(live):
        recovered = store.claim_next_event(
            owner_id=live.owner_id,
            fencing_token=live.fencing_token,
        )
        assert recovered is not None
        assert recovered.id == event.id
        assert recovered.claimed_by == "live"
        assert recovered.attempts == 2
        store.acknowledge_event(
            recovered.id,
            fencing_token=live.fencing_token,
        )

    assert store.load().inbox[0].status == "acknowledged"


def test_planner_and_capability_quotas_reset_by_window(tmp_path):
    store = AgendaStore(tmp_path)
    start = datetime.now(timezone.utc)

    assert store.consume_quota(
        category="planner",
        limit=1,
        window_seconds=60,
        now=start,
    ).allowed
    denied = store.consume_quota(
        category="planner",
        limit=1,
        window_seconds=60,
        now=start + timedelta(seconds=10),
    )
    assert not denied.allowed

    assert store.consume_quota(
        category="capability",
        key="read_file",
        limit=1,
        window_seconds=60,
        now=start,
    ).allowed
    assert not store.consume_quota(
        category="capability",
        key="read_file",
        limit=1,
        window_seconds=60,
        now=start + timedelta(seconds=10),
    ).allowed

    reset = store.consume_quota(
        category="planner",
        limit=1,
        window_seconds=60,
        now=start + timedelta(seconds=61),
    )
    assert reset.allowed
    assert store.load().quota.capability_calls == {}


def test_telemetry_is_content_free_and_chain_report_is_bounded(tmp_path):
    tracer = AutonomyTracer(tmp_path)
    with tracer.span(
        "agenda.test",
        attributes={
            "agenda.entry_id": "entry-1",
            "agenda.capability": "read_file",
        },
    ):
        pass
    record = json.loads(
        (tmp_path / "agenda" / "traces.jsonl").read_text(encoding="utf-8")
    )
    assert len(record["traceId"]) == 32
    assert len(record["spanId"]) == 16
    assert record["status"]["code"] == "STATUS_CODE_OK"
    assert "prompt" not in json.dumps(record).lower()

    store = AgendaStore(tmp_path)
    store.add(_pursuit(status="completed"))
    store.add(_pursuit(status="failed", title="Different failed work"))
    report = build_chain_report(store, horizon_hours=24)
    assert report["entries"]["total"] == 2
    assert report["entries"]["completion_rate"] == 0.5
    assert report["entries"]["grounded_rate"] == 1.0
    assert report["dead_letters"]["total"] == 0


def test_level_three_http_surface_publishes_and_reports(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.open_llm_vtuber.autonomy import agenda as agenda_module
    from src.open_llm_vtuber.request_security import build_trusted_hosts
    from src.open_llm_vtuber.routes import init_agenda_routes

    store = AgendaStore(tmp_path)
    monkeypatch.setattr(agenda_module, "_default_store", store)
    monkeypatch.setattr(agenda_module, "agenda_workspace_root", lambda: tmp_path)
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("127.0.0.1")
    app.include_router(init_agenda_routes())
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )
    payload = {
        "idempotency_key": "mail:http-1",
        "source": "mail",
        "kind": "message_arrived",
        "summary": "A local integration published an event.",
        "evidence_ref": "external:mail:http-1",
    }

    published = client.post("/api/agenda/events", json=payload)
    replay = client.post("/api/agenda/events", json=payload)
    snapshot = client.get("/api/agenda")
    report = client.get("/api/agenda/report", params={"hours": 24})

    assert published.status_code == 200
    assert replay.json()["id"] == published.json()["id"]
    assert snapshot.json()["health"]["events"]["pending"] == 1
    assert snapshot.json()["runtime"]["lease_status"] == "standby"
    assert report.json()["horizon_hours"] == 24


def test_idempotent_event_replay_soak_stays_constant_size(tmp_path):
    store = AgendaStore(tmp_path)
    for _ in range(1000):
        store.publish_event(
            idempotency_key="integration:stable-key",
            source="integration",
            kind="heartbeat",
            summary="A content-free integration heartbeat.",
            evidence_ref="external:integration:stable-key",
        )

    state = store.load()
    assert len(state.inbox) == 1
    assert state.revision == 1
