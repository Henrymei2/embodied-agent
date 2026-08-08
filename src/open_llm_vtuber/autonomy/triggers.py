"""Typed, content-free references for external agenda triggers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal

from .agenda import AgendaStore, ExternalEvent

TriggerSource = Literal["gmail", "google_calendar", "workspace"]

_RESOURCE_TYPES = {
    "gmail": {"message"},
    "google_calendar": {"calendar_event"},
    "workspace": {"file"},
}
_CHANGE_TYPES = {
    "gmail": {"unread"},
    "google_calendar": {"created", "updated", "deleted"},
    "workspace": {"created", "modified", "deleted"},
}
_SUMMARIES = {
    ("gmail", "unread"): "A new unread Gmail message is available for inspection.",
    ("google_calendar", "created"): "A calendar event was created.",
    ("google_calendar", "updated"): "A calendar event changed.",
    ("google_calendar", "deleted"): "A calendar event was deleted.",
    ("workspace", "created"): "A watched workspace file became available.",
    ("workspace", "modified"): "A watched workspace file changed.",
    ("workspace", "deleted"): "A watched workspace file was removed.",
}


def _token(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} contains control characters")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise ValueError(f"{field} is invalid")
    return cleaned


@dataclass(frozen=True)
class TriggerReference:
    source: TriggerSource
    resource_type: str
    resource_id: str
    revision: str
    change_type: str
    simulated: bool = False

    def validate(self) -> None:
        if self.source not in _RESOURCE_TYPES:
            raise ValueError("unknown trigger source")
        if self.resource_type not in _RESOURCE_TYPES[self.source]:
            raise ValueError("resource_type does not match trigger source")
        if self.change_type not in _CHANGE_TYPES[self.source]:
            raise ValueError("change_type does not match trigger source")
        _token(self.resource_id, "resource_id", 1024)
        _token(self.revision, "revision", 1024)
        if self.source == "workspace":
            path = PurePosixPath(self.resource_id)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("workspace resource_id must be a relative path")
        if not isinstance(self.simulated, bool):
            raise ValueError("simulated must be a boolean")


@dataclass(frozen=True)
class TriggerEnvelope:
    reference: TriggerReference
    schema_version: int = 1

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported trigger schema_version")
        self.reference.validate()

    def as_payload(self) -> dict:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "trigger": asdict(self.reference),
        }


def _identity(envelope: TriggerEnvelope) -> str:
    envelope.validate()
    ref = envelope.reference
    canonical = json.dumps(
        [ref.source, ref.resource_type, ref.resource_id, ref.revision, ref.change_type],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class TriggerPublishResult:
    event: ExternalEvent
    deduplicated: bool


def publish_trigger(
    store: AgendaStore,
    envelope: TriggerEnvelope,
    *,
    backlog_limit: int = 500,
) -> TriggerPublishResult:
    """Validate and publish a trigger without promoting source content to authority."""
    identity = _identity(envelope)
    ref = envelope.reference
    key = f"trigger-v1:{identity}"
    source = f"trigger:{ref.source}"
    existing = next(
        (
            event
            for event in store.load().inbox
            if event.source == source and event.idempotency_key == key
        ),
        None,
    )
    event = store.publish_event(
        idempotency_key=key,
        source=source,
        kind=f"{ref.resource_type}.{ref.change_type}",
        summary=_SUMMARIES[(ref.source, ref.change_type)],
        evidence_ref=f"trigger:{ref.source}:{identity}",
        payload=envelope.as_payload(),
        backlog_limit=backlog_limit,
    )
    return TriggerPublishResult(event=event, deduplicated=existing is not None)


def trigger_from_event(event: ExternalEvent) -> dict | None:
    payload = event.payload
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    trigger = payload.get("trigger")
    return trigger if isinstance(trigger, dict) else None
