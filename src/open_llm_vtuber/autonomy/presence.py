"""Consent-aware attention accounting for autonomous companion behavior.

An embodied agent should be able to initiate, but initiative is a scarce user
resource. ``PresenceLedger`` gives proactive actions an explicit cost, keeps a
small inspectable receipt for every action, and learns bounded preferences from
user feedback. The implementation is deterministic, offline, and contains no
raw screen or application content.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from loguru import logger

from .observation import Observation
from .settings import AutonomySettings

_STATE_FILENAME = "presence_ledger.json"
_SCHEMA_VERSION = 1

HELPFUL = "helpful"
DISMISSED = "dismissed"
SNOOZED = "snoozed"
VALID_FEEDBACK = {HELPFUL, DISMISSED, SNOOZED}

_ACTION_COSTS = {
    "silent": 0.25,
    "ask": 1.25,
    "speak": 2.0,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def attention_cost(action: str) -> float:
    """Return the attention cost of a planner delivery mode."""
    return _ACTION_COSTS.get(action, 0.0)


def _idle_band(idle_seconds: Optional[float]) -> str:
    """Coarsen activity so receipts never become an activity-tracking log."""
    if idle_seconds is None:
        return "unknown"
    if idle_seconds < 60:
        return "active"
    if idle_seconds < 300:
        return "recently-active"
    if idle_seconds < 1800:
        return "idle"
    return "away"


@dataclass
class PresenceReceipt:
    """Human-inspectable explanation of one autonomous action."""

    receipt_id: str
    created_at: str
    direction: str
    action: str
    score: float
    attention_cost: float
    reason: str
    signals: dict[str, Any]
    feedback: Optional[str] = None
    feedback_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PresenceLedger:
    """Persistent token bucket, feedback model, and autonomous action log."""

    max_budget: float
    refill_per_hour: float
    receipt_retention: int
    default_snooze_minutes: int
    budget_balance: float
    last_refill_at: str
    snoozed_until: Optional[str] = None
    direction_affinity: dict[str, float] = field(default_factory=dict)
    receipts: list[PresenceReceipt] = field(default_factory=list)
    schema_version: int = _SCHEMA_VERSION
    _path: Optional[Path] = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, workspace_root: Path, settings: AutonomySettings) -> "PresenceLedger":
        path = workspace_root / _STATE_FILENAME
        now = _utc_now()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                receipts = [
                    PresenceReceipt(
                        receipt_id=str(item["receipt_id"]),
                        created_at=str(item["created_at"]),
                        direction=str(item["direction"]),
                        action=str(item["action"]),
                        score=float(item.get("score", 0.0)),
                        attention_cost=float(item.get("attention_cost", 0.0)),
                        reason=str(item.get("reason", "")),
                        signals=dict(item.get("signals", {})),
                        feedback=item.get("feedback"),
                        feedback_at=item.get("feedback_at"),
                    )
                    for item in raw.get("receipts", [])
                    if isinstance(item, dict)
                    and item.get("receipt_id")
                    and item.get("created_at")
                    and item.get("direction")
                    and item.get("action")
                ]
                ledger = cls(
                    max_budget=settings.attention_budget,
                    refill_per_hour=settings.attention_refill_per_hour,
                    receipt_retention=settings.receipt_retention,
                    default_snooze_minutes=settings.default_snooze_minutes,
                    budget_balance=_clamp(
                        float(raw.get("budget_balance", settings.attention_budget)),
                        0.0,
                        settings.attention_budget,
                    ),
                    last_refill_at=str(raw.get("last_refill_at") or now.isoformat()),
                    snoozed_until=raw.get("snoozed_until"),
                    direction_affinity={
                        str(key): _clamp(float(value), -1.0, 1.0)
                        for key, value in raw.get("direction_affinity", {}).items()
                    },
                    receipts=receipts[-settings.receipt_retention :],
                    schema_version=int(raw.get("schema_version", _SCHEMA_VERSION)),
                )
                ledger._path = path
                ledger.refill(now)
                return ledger
            except Exception as exc:  # noqa: BLE001 - corrupt state is recoverable
                logger.warning(
                    f"presence: could not read {path} ({exc}); starting fresh"
                )

        ledger = cls(
            max_budget=settings.attention_budget,
            refill_per_hour=settings.attention_refill_per_hour,
            receipt_retention=settings.receipt_retention,
            default_snooze_minutes=settings.default_snooze_minutes,
            budget_balance=settings.attention_budget,
            last_refill_at=now.isoformat(),
        )
        ledger._path = path
        return ledger

    def save(self) -> None:
        if self._path is None:
            return
        data = {
            "schema_version": self.schema_version,
            "max_budget": self.max_budget,
            "refill_per_hour": self.refill_per_hour,
            "budget_balance": round(self.budget_balance, 4),
            "last_refill_at": self.last_refill_at,
            "snoozed_until": self.snoozed_until,
            "direction_affinity": self.direction_affinity,
            "receipts": [receipt.to_dict() for receipt in self.receipts],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best effort
            logger.warning(f"presence: failed to save state: {exc}")

    def refill(self, now: Optional[datetime] = None) -> float:
        """Refill the token bucket according to elapsed wall time."""
        now = _as_utc(now)
        previous = _parse_time(self.last_refill_at) or now
        elapsed_hours = max(0.0, (now - previous).total_seconds() / 3600.0)
        self.budget_balance = min(
            self.max_budget,
            self.budget_balance + elapsed_hours * self.refill_per_hour,
        )
        self.last_refill_at = now.isoformat()
        return self.budget_balance

    def is_snoozed(self, now: Optional[datetime] = None) -> bool:
        until = _parse_time(self.snoozed_until)
        return until is not None and until > _as_utc(now)

    def can_afford(
        self,
        cost: float,
        *,
        user_facing: bool,
        focus_mode: bool = False,
        now: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """Check the presence contract without consuming budget."""
        now = _as_utc(now)
        self.refill(now)
        if user_facing and focus_mode:
            return False, "focus mode is active"
        if user_facing and self.is_snoozed(now):
            return False, "proactive speech is snoozed"
        if self.budget_balance + 1e-9 < cost:
            return False, "attention budget exhausted"
        return True, "allowed by presence contract"

    def affinity(self, direction: str) -> float:
        """Return bounded learned preference for a behavior direction."""
        return _clamp(float(self.direction_affinity.get(direction, 0.0)), -1.0, 1.0)

    def record_action(
        self,
        *,
        direction: str,
        action: str,
        score: float,
        reason: str,
        observation: Observation,
        now: Optional[datetime] = None,
        habit_id: Optional[str] = None,
    ) -> PresenceReceipt:
        """Consume budget and persist a privacy-preserving action receipt."""
        now = _as_utc(now)
        cost = attention_cost(action)
        allowed, gate_reason = self.can_afford(
            cost,
            user_facing=action in {"ask", "speak"},
            focus_mode=observation.focus_mode,
            now=now,
        )
        if not allowed:
            raise ValueError(gate_reason)

        self.budget_balance = max(0.0, self.budget_balance - cost)
        quiet_minutes = observation.seconds_since_last_spoke
        receipt = PresenceReceipt(
            receipt_id=uuid4().hex,
            created_at=now.isoformat(),
            direction=direction,
            action=action,
            score=round(score, 4),
            attention_cost=cost,
            reason=reason,
            signals={
                "user_present": observation.user_present,
                "activity": _idle_band(observation.idle_seconds),
                "focus_mode": observation.focus_mode,
                "quiet_minutes": (
                    round(quiet_minutes / 60.0, 1)
                    if quiet_minutes is not None
                    else None
                ),
                "hour_of_day": observation.hour_of_day,
                # Traceability back to the learned habit that drove this
                # action (Habit Engine); None for built-in behaviors.
                "habit_id": habit_id,
            },
        )
        self.receipts.append(receipt)
        self.receipts = self.receipts[-self.receipt_retention :]
        self.save()
        return receipt

    def apply_feedback(
        self,
        receipt_id: str,
        feedback: str,
        *,
        snooze_minutes: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """Apply explicit user feedback once and update future direction scores."""
        if feedback not in VALID_FEEDBACK:
            return False, f"feedback must be one of {sorted(VALID_FEEDBACK)}"

        receipt = next(
            (item for item in self.receipts if item.receipt_id == receipt_id), None
        )
        if receipt is None:
            return False, "receipt not found"
        if receipt.feedback is not None:
            return False, "feedback already recorded for this receipt"

        now = _as_utc(now)
        adjustment = {
            HELPFUL: 0.15,
            DISMISSED: -0.25,
            SNOOZED: -0.10,
        }[feedback]
        self.direction_affinity[receipt.direction] = _clamp(
            self.affinity(receipt.direction) + adjustment, -1.0, 1.0
        )
        receipt.feedback = feedback
        receipt.feedback_at = now.isoformat()

        if feedback == SNOOZED:
            minutes = (
                self.default_snooze_minutes
                if snooze_minutes is None
                else int(snooze_minutes)
            )
            minutes = max(5, min(1440, minutes))
            self.snoozed_until = (now + timedelta(minutes=minutes)).isoformat()

        self.save()
        return True, "feedback recorded"

    def cancel_action(self, receipt_id: str) -> None:
        """Roll back a receipt if dispatch fails before the action can run."""
        receipt = next(
            (item for item in self.receipts if item.receipt_id == receipt_id), None
        )
        if receipt is None or receipt.feedback is not None:
            return
        self.budget_balance = min(
            self.max_budget, self.budget_balance + receipt.attention_cost
        )
        self.receipts = [
            item for item in self.receipts if item.receipt_id != receipt_id
        ]
        self.save()

    def snapshot(self, now: Optional[datetime] = None) -> dict[str, Any]:
        """Return frontend-safe state for controls and receipt inspection."""
        now = _as_utc(now)
        self.refill(now)
        return {
            "type": "presence-state",
            "enabled": True,
            "attention_budget": round(self.budget_balance, 2),
            "attention_budget_max": self.max_budget,
            "attention_refill_per_hour": self.refill_per_hour,
            "snoozed": self.is_snoozed(now),
            "snoozed_until": self.snoozed_until,
            "direction_affinity": {
                key: round(value, 3)
                for key, value in sorted(self.direction_affinity.items())
            },
            "receipts": [receipt.to_dict() for receipt in reversed(self.receipts)],
        }


# Live clients sharing the same workspace must also share one token bucket.
_ledgers_by_root: dict[str, PresenceLedger] = {}
_client_roots: dict[str, str] = {}


def get_or_create_presence_ledger(
    client_uid: str, workspace_root: Path, settings: AutonomySettings
) -> PresenceLedger:
    key = str(workspace_root.resolve())
    ledger = _ledgers_by_root.get(key)
    if ledger is None:
        ledger = PresenceLedger.load(workspace_root, settings)
        _ledgers_by_root[key] = ledger
    _client_roots[client_uid] = key
    return ledger


def get_presence_ledger(client_uid: str) -> Optional[PresenceLedger]:
    key = _client_roots.get(client_uid)
    return _ledgers_by_root.get(key) if key else None


def unregister_presence_ledger(client_uid: str) -> None:
    key = _client_roots.pop(client_uid, None)
    if key and key not in _client_roots.values():
        _ledgers_by_root.pop(key, None)
