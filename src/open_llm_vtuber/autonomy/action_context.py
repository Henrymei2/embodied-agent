"""Per-task bridge from exact action authorization back to the agenda."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass
class AgendaActionScope:
    entry_id: str
    step_id: str
    pending_transaction_id: str | None = None
    capability: str | None = None
    summary: str = ""


_CURRENT: ContextVar[AgendaActionScope | None] = ContextVar(
    "agenda_action_scope", default=None
)


class AgendaApprovalRequired(RuntimeError):
    def __init__(self, scope: AgendaActionScope) -> None:
        super().__init__(scope.summary or "Exact external action approval is required.")
        self.scope = scope


@contextmanager
def agenda_action_scope(entry_id: str, step_id: str) -> Iterator[AgendaActionScope]:
    scope = AgendaActionScope(entry_id=entry_id, step_id=step_id)
    token = _CURRENT.set(scope)
    try:
        yield scope
    finally:
        _CURRENT.reset(token)


def current_action_scope() -> AgendaActionScope | None:
    """Return the current agenda action scope for chain-level coordination."""
    return _CURRENT.get()


def observe_action_transaction(
    metadata: dict, *, capability: str, summary: str
) -> None:
    scope = _CURRENT.get()
    transaction = metadata.get("action_transaction") if isinstance(metadata, dict) else None
    if scope is None or not isinstance(transaction, dict):
        return
    if transaction.get("state") != "pending_confirmation":
        return
    transaction_id = transaction.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        return
    scope.pending_transaction_id = transaction_id
    scope.capability = capability
    scope.summary = (
        f"I inspected the email and prepared an external action using "
        f"{capability}. Your approval is required before it can be committed. "
        f"{summary}"
    )[:2000]
