from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.provenance import (
    PolicyContext,
    ProvenanceLedger,
    ProvenanceValidationError,
    RetrievalRequest,
    correct_memory,
    delete_memory,
    export_memory,
    ingest_record,
    inspect_memory,
    render_context_block,
    retrieve_context,
)
from src.open_llm_vtuber.workspace.embedding_cache import EmbeddingCache

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class Clock:
    """One clock for the ledger and the policy, because two would drift."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Session:
    def __init__(self, tmp_path) -> None:
        counter = {"value": 0}

        def ids() -> str:
            counter["value"] += 1
            return f"{counter['value']:06d}"

        self.clock = Clock()
        self.ledger = ProvenanceLedger(
            tmp_path / "ledger", clock=self.clock, id_factory=ids
        )
        self.offset = 0

    @property
    def now(self) -> datetime:
        return self.clock.now

    def ingest(self, claim, content, **fields):
        self.offset += 1
        stored = self.ledger.content_store.put(f"blob-{self.offset}", content)
        defaults = {
            "record_type": "assertion",
            "source_class": "user",
            "source_id": "participant-1",
            "authority": "reported",
            "privacy_class": "private",
        }
        defaults.update(fields)
        return ingest_record(
            self.ledger,
            context=PolicyContext(now=self.now),
            claim_key=claim,
            content_ref=stored.content_ref,
            content_digest=stored.content_digest,
            **defaults,
        )

    def request(self, *claims, **overrides) -> RetrievalRequest:
        return RetrievalRequest(claim_keys=list(claims), now=self.now, **overrides)

    def retrieve(self, *claims, **overrides):
        return retrieve_context(
            self.ledger.records(), self.request(*claims, **overrides)
        )


def test_retrieval_returns_one_claim_and_leaves_the_rest_of_memory_alone(tmp_path):
    session = Session(tmp_path)
    session.ingest("pet.name", "Her cat is called Pep.")
    session.ingest("home.city", "She lives in Madison.")
    session.ingest("work.role", "She teaches.")

    context = session.retrieve("pet.name")

    assert [entry.claim_key for entry in context.entries] == ["pet.name"]
    assert not context.truncated


def test_a_contested_claim_is_returned_as_contested_not_resolved(tmp_path):
    session = Session(tmp_path)
    observation = {
        "record_type": "observation",
        "source_class": "sensor",
        "authority": "observed",
        "privacy_class": "internal",
    }
    session.ingest("lamp.state", "on", source_id="camera-1", **observation)
    session.ingest("lamp.state", "off", source_id="camera-2", **observation)

    context = session.retrieve("lamp.state")

    assert len(context.entries) == 2
    assert all(entry.contested for entry in context.entries)
    assert context.contested_claims == ["lamp.state"]
    block = render_context_block(context, session.ledger.content_store)
    assert "contested" in block
    assert "rather than choosing a side" in block


def test_a_belief_whose_evidence_was_deleted_is_never_retrieved(tmp_path):
    """The gate: a retrieved belief must still be able to point at its evidence."""

    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    concluded = session.ingest(
        "pet.mood",
        "The cat is content.",
        record_type="belief",
        source_class="model",
        source_id="llm-1",
        authority="inferred",
        privacy_class="internal",
        confidence=0.8,
        evidence_ids=[stated.record["record_id"]],
    )
    assert session.retrieve("pet.mood").entries

    session.ledger.tombstone(stated.record["record_id"])
    context = session.retrieve("pet.mood")

    assert context.entries == []
    assert context.skipped == [
        {"record_id": concluded.record["record_id"], "reason": "evidence_deleted"}
    ]


def test_retrieval_respects_a_privacy_ceiling_and_a_record_budget(tmp_path):
    session = Session(tmp_path)
    session.ingest("pet.name", "Her cat is called Pep.", privacy_class="restricted")
    for index in range(4):
        session.ingest(f"topic.{index}", f"Fact {index}.", privacy_class="internal")

    restricted = session.retrieve("pet.name", privacy_ceiling="internal")
    assert restricted.entries == []
    assert restricted.skipped[0]["reason"] == "privacy_ceiling"

    budgeted = session.retrieve(
        *[f"topic.{index}" for index in range(4)], max_records=2
    )
    assert len(budgeted.entries) == 2
    assert budgeted.truncated


def test_deleted_content_cannot_come_back_through_a_summary_or_an_embedding(tmp_path):
    """The gate: deletion reaches the text, the summary of it, and its vector."""

    session = Session(tmp_path)
    store = session.ledger.content_store
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    summary = session.ingest(
        "pet.summary",
        "She has a cat named Pep.",
        record_type="belief",
        source_class="model",
        source_id="llm-1",
        authority="inferred",
        privacy_class="internal",
        confidence=0.9,
        evidence_ids=[stated.record["record_id"]],
    )
    cache = EmbeddingCache(tmp_path / "workspace")
    for record in (stated.record, summary.record):
        cache.put(f"vec-{record['record_id']}", [0.1, 0.2, 0.3])
        store.register_derived(record["content_ref"], f"vec-{record['record_id']}")
    cache.save()

    result = delete_memory(
        session.ledger, stated.record["record_id"], purge=cache.purge
    )

    assert sorted(result.tombstoned_ids) == sorted(
        [stated.record["record_id"], summary.record["record_id"]]
    )
    assert result.purged_artifacts == 2
    assert cache.get(f"vec-{stated.record['record_id']}") is None
    # The summary went with it: it is the deleted sentence in another form.
    assert cache.get(f"vec-{summary.record['record_id']}") is None
    assert (
        EmbeddingCache(tmp_path / "workspace").get(f"vec-{summary.record['record_id']}")
        is None
    )

    context = session.retrieve("pet.name", "pet.summary")
    assert context.entries == []
    assert render_context_block(context, store) == ""


def test_a_stale_retrieval_result_still_cannot_render_deleted_content(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    context = session.retrieve("pet.name")
    assert "Pep" in render_context_block(context, session.ledger.content_store)

    delete_memory(session.ledger, stated.record["record_id"])

    assert render_context_block(context, session.ledger.content_store) == ""


def test_the_user_can_see_correct_and_export_what_is_remembered(tmp_path):
    session = Session(tmp_path)
    store = session.ledger.content_store
    stated = session.ingest("pet.name", "Her cat is called Pep.")

    visible = inspect_memory(session.ledger, store=store)
    assert [entry.content for entry in visible] == ["Her cat is called Pep."]

    corrected = correct_memory(
        session.ledger,
        stated.record["record_id"],
        content="The cat is called Bo.",
        source_id="participant-1",
    )
    assert corrected["supersedes_id"] == stated.record["record_id"]
    assert session.ledger.get(stated.record["record_id"])["status"] == "superseded"
    assert [entry.content for entry in inspect_memory(session.ledger, store=store)] == [
        "The cat is called Bo."
    ]

    exported = export_memory(session.ledger, store=store)
    assert exported["entry_count"] == 2
    encoded = json.dumps(exported)
    assert "Pep" in encoded and "Bo" in encoded
    assert len(inspect_memory(session.ledger, include_history=True)) == 2
    # An operator view of the same ledger stays content-free.
    assert all(entry.content is None for entry in inspect_memory(session.ledger))


def test_correcting_a_model_conclusion_retires_it_rather_than_replacing_it(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    concluded = session.ingest(
        "pet.mood",
        "The cat is anxious.",
        record_type="belief",
        source_class="model",
        source_id="llm-1",
        authority="inferred",
        privacy_class="internal",
        confidence=0.9,
        evidence_ids=[stated.record["record_id"]],
    )

    correct_memory(
        session.ledger,
        concluded.record["record_id"],
        content="She is loud, not anxious.",
        source_id="participant-1",
    )

    assert session.ledger.get(concluded.record["record_id"])["status"] == "retracted"
    entries = session.retrieve("pet.mood").entries
    assert [entry.record_type for entry in entries] == ["assertion"]


def test_a_deleted_memory_cannot_be_corrected_back_into_existence(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    delete_memory(session.ledger, stated.record["record_id"])

    with pytest.raises(ProvenanceValidationError, match="deleted"):
        correct_memory(
            session.ledger,
            stated.record["record_id"],
            content="Her cat is called Pep.",
            source_id="participant-1",
        )


def test_retrieval_reports_calibrated_confidence_and_flags_the_uncalibrated(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    session.ingest(
        "pet.mood",
        "The cat is content.",
        record_type="belief",
        source_class="model",
        source_id="llm-1",
        authority="inferred",
        privacy_class="internal",
        confidence=0.95,
        evidence_ids=[stated.record["record_id"]],
    )

    class Calibrator:
        def calibrate(self, source_class, confidence):
            return 0.4 if source_class == "model" else None

    context = retrieve_context(
        session.ledger.records(),
        session.request("pet.mood"),
        calibrator=Calibrator(),
    )
    assert context.entries[0].calibrated_confidence == 0.4
    assert "calibrated 0.40" in render_context_block(
        context, session.ledger.content_store
    )

    uncalibrated = session.retrieve("pet.mood")
    assert uncalibrated.entries[0].calibrated_confidence is None
    assert "uncalibrated" in render_context_block(
        uncalibrated, session.ledger.content_store
    )


def test_the_memory_ablation_removes_evidence_recall_but_not_the_character(
    monkeypatch, tmp_path
):
    """The gate: ablating memory must remove retrieval, and only retrieval."""

    from src.open_llm_vtuber import study
    from src.open_llm_vtuber.workspace import build_workspace_prompt_context

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "AGENT.md").write_text("You are Mao.", encoding="utf-8")
    (workspace / "RULE.md").write_text("Never speak in list form.", encoding="utf-8")

    # Wall-clock here on purpose: the prompt builder opens the ledger itself,
    # and a fixture clock set in the future would look like a claim that has
    # not started being true yet.
    ledger = ProvenanceLedger(workspace / "provenance")
    stored = ledger.content_store.put("blob-1", "Her cat is called Pep.")
    ingest_record(
        ledger,
        context=PolicyContext(now=ledger.now()),
        record_type="assertion",
        claim_key="pet.name",
        source_class="user",
        source_id="participant-1",
        authority="reported",
        privacy_class="private",
        content_ref=stored.content_ref,
        content_digest=stored.content_digest,
    )

    monkeypatch.setenv("EMBODIED_AGENT_EMBEDDINGS", "off")
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    study.reset_for_tests()
    full = build_workspace_prompt_context(
        "the cat", workspace_dir=workspace, claim_keys=["pet.name"]
    )
    assert "You are Mao." in full
    assert "Never speak in list form." in full
    assert "Pep" in full

    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memory=off")
    study.reset_for_tests()
    ablated = build_workspace_prompt_context(
        "the cat", workspace_dir=workspace, claim_keys=["pet.name"]
    )
    study.reset_for_tests()

    # Same character, same rules, nothing recalled.
    assert "You are Mao." in ablated
    assert "Never speak in list form." in ablated
    assert "Pep" not in ablated


def test_a_workspace_without_a_ledger_adds_nothing_and_fails_quietly(tmp_path):
    from src.open_llm_vtuber import study
    from src.open_llm_vtuber.workspace import build_evidence_context

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    study.reset_for_tests()

    assert build_evidence_context(["pet.name"], workspace_dir=workspace) == ""
    assert build_evidence_context([], workspace_dir=workspace) == ""

    # A ledger that cannot be read costs context, not the conversation.
    (workspace / "provenance").mkdir()
    (workspace / "provenance" / "ledger.jsonl").write_text("{ broken", encoding="utf-8")
    assert build_evidence_context(["pet.name"], workspace_dir=workspace) == ""
