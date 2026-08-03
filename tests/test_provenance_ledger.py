from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.provenance import (
    ContentDeletedError,
    ContentStoreError,
    LedgerIntegrityError,
    PrivateContentStore,
    ProvenanceLedger,
    ProvenanceValidationError,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class Clock:
    """A clock the test moves on purpose, so ordering is never accidental."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def make_ledger(tmp_path, name: str = "ledger") -> ProvenanceLedger:
    counter = {"value": 0}

    def ids() -> str:
        counter["value"] += 1
        return f"{counter['value']:06d}"

    return ProvenanceLedger(tmp_path / name, clock=Clock(), id_factory=ids)


def say(ledger: ProvenanceLedger, claim: str, content: str, **overrides):
    """Store private content and record the user asserting it."""

    ref = overrides.pop("content_ref", f"blob-{ledger.sequence}-{claim}")
    stored = ledger.content_store.put(ref, content)
    fields = {
        "record_type": "assertion",
        "claim_key": claim,
        "source_class": "user",
        "source_id": "participant-1",
        "authority": "reported",
        "privacy_class": "private",
        "content_ref": stored.content_ref,
        "content_digest": stored.content_digest,
    }
    fields.update(overrides)
    return ledger.append_record(**fields)


def test_a_reloaded_ledger_is_the_same_ledger(tmp_path):
    ledger = make_ledger(tmp_path)
    first = say(ledger, "pet.name", "Her cat is called Pep.")
    say(ledger, "pet.breed", "It is a tabby.")
    before = ledger.records()

    reloaded = ProvenanceLedger(tmp_path / "ledger")

    assert reloaded.records() == before
    assert reloaded.sequence == ledger.sequence
    assert reloaded.get(first["record_id"])["claim_key"] == "pet.name"


def test_supersession_retires_the_prior_record_in_one_entry(tmp_path):
    ledger = make_ledger(tmp_path)
    original = say(ledger, "pet.name", "Her cat is called Pep.")
    replacement = say(
        ledger,
        "pet.name",
        "Actually the cat is called Bo.",
        supersedes_id=original["record_id"],
    )

    assert ledger.get(original["record_id"])["status"] == "superseded"
    assert replacement["supersedes_id"] == original["record_id"]
    # One entry, so a crash cannot leave a successor without a retired parent.
    entries = _entries(tmp_path)
    assert len(entries) == 2
    assert entries[1]["status_changes"] == [
        {"record_id": original["record_id"], "status": "superseded"}
    ]


def test_a_resolved_record_cannot_be_superseded_twice(tmp_path):
    ledger = make_ledger(tmp_path)
    original = say(ledger, "pet.name", "Pep.")
    say(ledger, "pet.name", "Bo.", supersedes_id=original["record_id"])

    with pytest.raises(ProvenanceValidationError, match="cannot be superseded"):
        say(ledger, "pet.name", "Mo.", supersedes_id=original["record_id"])


def test_a_rejected_operation_leaves_no_trace(tmp_path):
    ledger = make_ledger(tmp_path)
    say(ledger, "pet.name", "Pep.")
    before_records = ledger.records()
    before_bytes = (tmp_path / "ledger" / "ledger.jsonl").read_bytes()

    with pytest.raises(ProvenanceValidationError):
        ledger.append_record(
            record_type="belief",
            claim_key="pet.mood",
            source_class="model",
            source_id="llm-1",
            authority="inferred",
            privacy_class="internal",
            content_ref="blob-belief",
            content_digest="a" * 64,
            confidence=0.7,
            evidence_ids=["rec-does-not-exist"],
        )

    assert ledger.records() == before_records
    assert (tmp_path / "ledger" / "ledger.jsonl").read_bytes() == before_bytes
    ledger.verify()


@pytest.mark.parametrize("damage", ["truncate", "drop", "reorder", "edit"])
def test_a_damaged_ledger_is_not_a_shorter_history(tmp_path, damage):
    ledger = make_ledger(tmp_path)
    say(ledger, "pet.name", "Pep.")
    say(ledger, "pet.breed", "Tabby.")
    say(ledger, "home.city", "Madison.")
    path = tmp_path / "ledger" / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()

    if damage == "truncate":
        # A *complete* line that is not valid JSON is damage, not a torn write.
        lines = lines[:-1] + [lines[-1][: len(lines[-1]) // 2]]
    elif damage == "drop":
        lines = [lines[0], lines[2]]
    elif damage == "reorder":
        lines = [lines[0], lines[2], lines[1]]
    else:
        entry = json.loads(lines[1])
        entry["record"]["confidence"] = 0.99
        lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError):
        ProvenanceLedger(tmp_path / "ledger")


def test_an_append_cut_short_by_a_crash_costs_one_operation_not_the_memory(tmp_path):
    """The half-written entry was never acknowledged; the rest still stands."""

    ledger = make_ledger(tmp_path)
    say(ledger, "pet.name", "Pep.")
    say(ledger, "pet.breed", "Tabby.")
    path = tmp_path / "ledger" / "ledger.jsonl"

    intact = path.read_bytes()
    fragment = json.dumps(_entries(tmp_path)[-1], separators=(",", ":")).encode("utf-8")
    path.write_bytes(intact + fragment[: len(fragment) // 2])

    recovered = ProvenanceLedger(tmp_path / "ledger")

    assert recovered.sequence == 2
    assert len(recovered.records()) == 2
    # The file is repaired, so the next append cannot splice onto the fragment.
    assert path.read_bytes() == intact
    say(recovered, "home.city", "Madison.")
    assert ProvenanceLedger(tmp_path / "ledger").sequence == 3


def test_references_that_differ_only_in_case_or_punctuation_stay_separate(tmp_path):
    """Identifier rules and filename rules are not the same rules."""

    store = PrivateContentStore(tmp_path / "store")
    store.put("blob-A", "upper")
    store.put("blob-a", "lower")
    store.put("blob:colon", "colon")

    assert store.get_text("blob-A") == "upper"
    assert store.get_text("blob-a") == "lower"
    assert store.get_text("blob:colon") == "colon"

    store.delete("blob-A")
    assert store.get_text("blob-a") == "lower"


def test_deleting_content_purges_what_was_derived_from_it(tmp_path):
    ledger = make_ledger(tmp_path)
    stated = say(ledger, "pet.name", "Her cat is called Pep.")
    ref = stated["content_ref"]
    ledger.content_store.register_derived(ref, "embedding-abc123")
    ledger.content_store.register_derived(ref, "summary-2026-08-03")

    purge_keys = ledger.tombstone(stated["record_id"], reason="user_request")

    assert purge_keys == frozenset({"embedding-abc123", "summary-2026-08-03"})
    assert ledger.get(stated["record_id"])["status"] == "tombstoned"
    assert ledger.get(stated["record_id"])["content_ref"] is None
    with pytest.raises(ContentDeletedError):
        ledger.content_store.get_text(ref)
    assert not (tmp_path / "ledger" / "content" / "blobs" / f"{ref}.bin").exists()


def test_deleted_content_cannot_be_written_back_under_the_same_reference(tmp_path):
    store = PrivateContentStore(tmp_path / "store")
    store.put("blob-1", "Her cat is called Pep.")
    store.delete("blob-1")

    with pytest.raises(ContentDeletedError):
        store.put("blob-1", "Her cat is called Pep.")
    with pytest.raises(ContentDeletedError):
        store.register_derived("blob-1", "embedding-abc")
    # And the deletion survives a restart.
    assert PrivateContentStore(tmp_path / "store").is_deleted("blob-1")


def test_content_digests_are_keyed_so_a_tombstone_cannot_confirm_a_guess(tmp_path):
    import hashlib

    first = PrivateContentStore(tmp_path / "a")
    second = PrivateContentStore(tmp_path / "b")
    secret = "Her cat is called Pep."

    assert first.digest(secret) != hashlib.sha256(secret.encode()).hexdigest()
    assert first.digest(secret) != second.digest(secret)
    assert first.digest(secret) == first.digest(secret)


def test_the_store_refuses_to_overwrite_and_bounds_its_items(tmp_path):
    store = PrivateContentStore(tmp_path / "store", max_items=2)
    store.put("blob-1", "one")

    with pytest.raises(ContentStoreError, match="already exists"):
        store.put("blob-1", "two")

    store.put("blob-2", "two")
    with pytest.raises(ContentStoreError, match="item limit"):
        store.put("blob-3", "three")

    with pytest.raises(ProvenanceValidationError):
        store.put("../escape", "traversal")


def test_the_ledger_budget_is_finite(tmp_path):
    ledger = ProvenanceLedger(tmp_path / "small", clock=Clock(), max_entries=1)
    say(ledger, "pet.name", "Pep.")

    with pytest.raises(LedgerIntegrityError, match="budget exhausted"):
        say(ledger, "pet.breed", "Tabby.")


def _entries(tmp_path) -> list[dict]:
    lines = (tmp_path / "ledger" / "ledger.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in lines.splitlines() if line.strip()]
