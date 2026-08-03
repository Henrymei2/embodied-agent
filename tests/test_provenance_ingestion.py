from src.open_llm_vtuber.provenance import (
    ConversationProvenanceBridge,
    PrivateContentStore,
    ProvenanceLedger,
)


def test_live_turn_ingestion_keeps_raw_text_out_of_inspectable_ledger(tmp_path):
    bridge = ConversationProvenanceBridge(tmp_path)
    secret = "My private launch phrase is winter violet."
    evidence = bridge.begin_turn(
        secret,
        source_id="client with unsafe / visible identity",
        turn_ref="turn-stable-one",
    )
    outcome_id = bridge.finish_turn(evidence, success=True)

    ledger_text = bridge.ledger.entries_path.read_text(encoding="utf-8")
    assert secret not in ledger_text
    assert "unsafe / visible identity" not in ledger_text
    assert (
        bridge.ledger.content_store.get_text(
            bridge.ledger.get(evidence.assertion_id)["content_ref"]
        )
        == secret
    )
    assert bridge.ledger.get(evidence.intention_id)["evidence_ids"] == [
        evidence.assertion_id
    ]
    assert bridge.ledger.get(outcome_id)["evidence_ids"] == [evidence.intention_id]


def test_turn_ingestion_is_idempotent_for_a_stable_turn_reference(tmp_path):
    bridge = ConversationProvenanceBridge(tmp_path)
    first = bridge.begin_turn(
        "Please check the calendar", source_id="client-a", turn_ref="t1"
    )
    second = bridge.begin_turn(
        "This retry must not overwrite content", source_id="client-a", turn_ref="t1"
    )
    assert first == second
    first_outcome = bridge.finish_turn(first, success=False)
    second_outcome = bridge.finish_turn(first, success=True)
    assert first_outcome == second_outcome
    assert bridge.ledger.sequence == 3


def test_independent_ledger_handles_refresh_before_appending(tmp_path):
    content = PrivateContentStore(tmp_path / "content")
    first = ProvenanceLedger(tmp_path, content_store=content)
    second = ProvenanceLedger(tmp_path, content_store=content)

    one = content.put("content-one", "one", privacy_class="internal")
    first.append_record(
        record_type="observation",
        claim_key="test:one",
        source_class="system",
        source_id="test-runtime",
        authority="observed",
        privacy_class="internal",
        content_ref=one.content_ref,
        content_digest=one.content_digest,
    )
    two = content.put("content-two", "two", privacy_class="internal")
    second.append_record(
        record_type="observation",
        claim_key="test:two",
        source_class="system",
        source_id="test-runtime",
        authority="observed",
        privacy_class="internal",
        content_ref=two.content_ref,
        content_digest=two.content_digest,
    )

    assert first.sequence == second.sequence == 2
    first.verify()
    second.verify()


def test_independent_private_content_handles_do_not_lose_index_updates(tmp_path):
    first = PrivateContentStore(tmp_path / "content")
    second = PrivateContentStore(tmp_path / "content")
    first.put("first-ref", "first", privacy_class="private")
    second.put("second-ref", "second", privacy_class="private")
    assert first.get_text("second-ref") == "second"
    assert second.get_text("first-ref") == "first"
