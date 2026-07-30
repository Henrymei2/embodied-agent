"""Deterministic validation for LLM-generated agenda proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .agenda import AgendaEntry, ENTRY_KINDS, REPORT_POLICIES
from .step_policy import capability_requires_approval


@dataclass(frozen=True)
class PolicyVerdict:
    accepted: bool
    reason: str


class AgendaPolicy:
    def __init__(self, maximum_pursuit_seconds: int = 1800) -> None:
        self.maximum_pursuit_seconds = maximum_pursuit_seconds

    def validate(
        self,
        proposal: AgendaEntry,
        *,
        supplied_evidence_refs: set[str],
        capabilities: set[str],
        object_names: set[str],
        comparison_entries: Iterable[AgendaEntry],
    ) -> PolicyVerdict:
        try:
            proposal.validate()
        except ValueError as exc:
            return PolicyVerdict(False, str(exc))
        if proposal.kind not in ENTRY_KINDS or proposal.kind == "user":
            return PolicyVerdict(False, "the generator may not create user entries")
        if proposal.report_policy not in REPORT_POLICIES:
            return PolicyVerdict(False, "unknown reporting policy")
        if proposal.budget.time_budget_seconds > self.maximum_pursuit_seconds:
            return PolicyVerdict(False, "time budget exceeds configured maximum")
        if not set(proposal.motivation.evidence_refs).issubset(supplied_evidence_refs):
            return PolicyVerdict(
                False, "motivation cites evidence outside planner context"
            )
        missing = set(proposal.required_capabilities) - capabilities
        if missing:
            return PolicyVerdict(
                False, f"missing capabilities: {', '.join(sorted(missing))}"
            )
        if proposal.preferred_object and proposal.preferred_object not in object_names:
            return PolicyVerdict(False, "preferred furniture is not present")
        if proposal.requires_approval and proposal.report_policy != "ask_first":
            return PolicyVerdict(False, "consequential work must use ask_first")
        consequential = sorted(
            capability
            for capability in proposal.required_capabilities
            if capability_requires_approval(capability)
        )
        if consequential and not proposal.requires_approval:
            return PolicyVerdict(
                False,
                "consequential capabilities require approval: "
                + ", ".join(consequential),
            )
        if self._is_duplicate(proposal, comparison_entries):
            return PolicyVerdict(False, "semantic duplicate of recent or pending work")
        return PolicyVerdict(True, "accepted")

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    def _is_duplicate(
        self, proposal: AgendaEntry, entries: Iterable[AgendaEntry]
    ) -> bool:
        candidate = self._normalized(f"{proposal.title} {proposal.description}")
        for entry in entries:
            existing = self._normalized(f"{entry.title} {entry.description}")
            if candidate and SequenceMatcher(None, candidate, existing).ratio() >= 0.82:
                return True
        return False
