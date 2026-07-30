"""Presence-gated delivery and receipts for completed agenda intentions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from .agenda import AgendaEntry
from .observation import build_observation
from .presence import PresenceLedger, PresenceReceipt


@dataclass(frozen=True)
class ReportDelivery:
    delivered: bool
    reason: str | None = None
    counts_as_failure: bool = False


@dataclass
class AgendaReporter:
    client_uid: str
    presence: PresenceLedger
    emit: Callable[[dict], Awaitable[None]]
    run_report: Callable[[AgendaEntry, str], Awaitable[None]]

    async def report(self, entry: AgendaEntry, summary: str) -> bool:
        """Compatibility wrapper returning only whether delivery completed."""
        return (await self.deliver(entry, summary)).delivered

    async def deliver(self, entry: AgendaEntry, summary: str) -> ReportDelivery:
        """Record a completion and speak only when policy and presence allow."""
        action = self._action(entry, summary)
        observation = build_observation(self.client_uid)
        direction = entry.motivation.type or entry.origin
        reason = f"Completed agenda intention: {entry.title}"[:500]
        score = 1.0 + self.presence.affinity(direction)

        try:
            receipt = self.presence.queue_action(
                direction=direction,
                action=action,
                score=score,
                reason=reason,
                observation=observation,
            )
        except ValueError as exc:
            receipt = self.presence.record_blocked_action(
                direction=direction,
                action=action,
                score=score,
                reason=reason,
                observation=observation,
                error=str(exc),
            )
            await self._emit_receipt(receipt)
            return ReportDelivery(
                delivered=action == "silent",
                reason=str(exc),
                counts_as_failure=False,
            )

        await self._emit_receipt(receipt)
        allowed, gate_reason = self.presence.start_action(
            receipt.receipt_id,
            observation=observation,
        )
        if not allowed:
            self.presence.fail_action(receipt.receipt_id, gate_reason)
            await self._emit_receipt(receipt)
            return ReportDelivery(
                delivered=action == "silent",
                reason=gate_reason,
                counts_as_failure=False,
            )

        await self._emit_receipt(receipt)
        try:
            if action == "speak":
                await self.run_report(entry, summary)
        except asyncio.CancelledError:
            self.presence.cancel_action(receipt.receipt_id)
            await self._emit_receipt(receipt)
            return ReportDelivery(False, "delivery owner changed", False)
        except Exception as exc:
            self.presence.fail_action(receipt.receipt_id, str(exc))
            await self._emit_receipt(receipt)
            return ReportDelivery(
                False,
                f"{type(exc).__name__}: {str(exc)[:400]}",
                True,
            )

        self.presence.complete_action(receipt.receipt_id)
        await self._emit_receipt(receipt)
        return ReportDelivery(True)

    async def _emit_receipt(self, receipt: PresenceReceipt) -> None:
        await self.emit(
            {
                "type": "autonomy-receipt",
                "receipt": receipt.to_dict(),
                "attention_budget_remaining": round(
                    self.presence.budget_balance,
                    2,
                ),
            }
        )

    @staticmethod
    def _action(entry: AgendaEntry, summary: str) -> str:
        if entry.report_policy == "silent":
            return "silent"
        if entry.report_policy == "if_noteworthy":
            cleaned = " ".join(summary.split())
            if len(cleaned) < 40 or cleaned.lower().startswith(("no ", "nothing ")):
                return "silent"
        return "speak"
