"""Cross-process workspace lease with monotonically increasing fencing tokens."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout


class LeaseError(RuntimeError):
    """Base error for lease persistence or ownership failures."""


class LeaseLostError(LeaseError):
    """Raised when a stale owner attempts fenced work."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class LeaseHandle:
    workspace_root: Path
    owner_id: str
    fencing_token: int
    priority: int
    holder_label: str
    expires_at: str


@dataclass
class LeaseRecord:
    owner_id: str
    fencing_token: int
    priority: int
    holder_label: str
    acquired_at: str
    renewed_at: str
    expires_at: str

    def validate(self) -> None:
        if not self.owner_id or len(self.owner_id) > 200:
            raise LeaseError("lease owner id is invalid")
        if self.fencing_token < 1:
            raise LeaseError("lease fencing token must be positive")
        if not 0 <= self.priority <= 1000:
            raise LeaseError("lease priority is invalid")
        for value in (self.acquired_at, self.renewed_at, self.expires_at):
            try:
                datetime.fromisoformat(value)
            except (TypeError, ValueError) as exc:
                raise LeaseError("lease timestamp is invalid") from exc


_CURRENT_LEASE: ContextVar[LeaseHandle | None] = ContextVar(
    "autonomy_workspace_lease",
    default=None,
)


@contextmanager
def lease_context(handle: LeaseHandle) -> Iterator[None]:
    token = _CURRENT_LEASE.set(handle)
    try:
        yield
    finally:
        _CURRENT_LEASE.reset(token)


def current_lease() -> LeaseHandle | None:
    return _CURRENT_LEASE.get()


class WorkspaceLease:
    """Atomic lease persisted beside the agenda store.

    Higher-priority live owners may fence a headless owner immediately. Equal
    priority owners wait for expiry, preventing two live server processes from
    executing the same workspace concurrently.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        ttl_seconds: float = 30.0,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.root = workspace_root.resolve()
        self.path = self.root / "agenda" / "lease.json"
        self.lock = FileLock(
            str(self.root / "agenda" / "lease.lock"),
            timeout=lock_timeout_seconds,
        )
        self.ttl_seconds = max(2.0, float(ttl_seconds))

    def acquire(
        self,
        owner_id: str,
        *,
        priority: int,
        holder_label: str,
        now: datetime | None = None,
    ) -> LeaseHandle | None:
        requested_now = now
        owner_id = str(owner_id).strip()
        if not owner_id or len(owner_id) > 200:
            raise ValueError("lease owner id is invalid")
        priority = max(0, min(1000, int(priority)))
        holder_label = str(holder_label).strip()[:200] or "autonomy"
        try:
            with self.lock:
                now = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                )
                current = self._read_unlocked()
                if current is not None:
                    expires = _as_utc(datetime.fromisoformat(current.expires_at))
                    same_owner = current.owner_id == owner_id
                    may_preempt = priority > current.priority
                    if expires > now and not same_owner and not may_preempt:
                        return None
                    fencing_token = (
                        current.fencing_token
                        if same_owner and expires > now
                        else current.fencing_token + 1
                    )
                    acquired_at = (
                        current.acquired_at
                        if same_owner and expires > now
                        else now.isoformat()
                    )
                else:
                    fencing_token = 1
                    acquired_at = now.isoformat()
                expires_at = now + timedelta(seconds=self.ttl_seconds)
                record = LeaseRecord(
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    priority=priority,
                    holder_label=holder_label,
                    acquired_at=acquired_at,
                    renewed_at=now.isoformat(),
                    expires_at=expires_at.isoformat(),
                )
                self._write_unlocked(record)
                return self._handle(record)
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc

    def renew(
        self,
        handle: LeaseHandle,
        *,
        now: datetime | None = None,
    ) -> LeaseHandle:
        requested_now = now
        self._assert_workspace(handle)
        try:
            with self.lock:
                now = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                )
                current = self._read_unlocked()
                self._assert_record(current, handle, now=now)
                current.renewed_at = now.isoformat()
                current.expires_at = (
                    now + timedelta(seconds=self.ttl_seconds)
                ).isoformat()
                self._write_unlocked(current)
                return self._handle(current)
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc

    def release(
        self,
        handle: LeaseHandle,
        *,
        now: datetime | None = None,
    ) -> bool:
        requested_now = now
        self._assert_workspace(handle)
        try:
            with self.lock:
                now = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                )
                current = self._read_unlocked()
                if (
                    current is None
                    or current.owner_id != handle.owner_id
                    or current.fencing_token != handle.fencing_token
                ):
                    return False
                current.renewed_at = now.isoformat()
                current.expires_at = now.isoformat()
                self._write_unlocked(current)
                return True
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc

    def assert_current(
        self,
        handle: LeaseHandle,
        *,
        now: datetime | None = None,
        minimum_remaining_seconds: float = 0.0,
    ) -> None:
        requested_now = now
        self._assert_workspace(handle)
        try:
            with self.lock:
                checked_at = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                ) + timedelta(seconds=max(0.0, float(minimum_remaining_seconds)))
                self._assert_record(self._read_unlocked(), handle, now=checked_at)
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc

    @contextmanager
    def guard_current(
        self,
        handle: LeaseHandle,
        *,
        now: datetime | None = None,
    ) -> Iterator[None]:
        """Hold ownership stable across one short fenced commit."""
        requested_now = now
        self._assert_workspace(handle)
        try:
            with self.lock:
                checked_at = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                )
                self._assert_record(self._read_unlocked(), handle, now=checked_at)
                yield
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc

    def snapshot(self, *, now: datetime | None = None) -> dict:
        requested_now = now
        try:
            with self.lock:
                now = (
                    _as_utc(requested_now) if requested_now is not None else _utc_now()
                )
                record = self._read_unlocked()
        except Timeout as exc:
            raise LeaseError("workspace lease is busy") from exc
        if record is None:
            return {"held": False, "fencing_token": 0}
        expires = _as_utc(datetime.fromisoformat(record.expires_at))
        return {
            **asdict(record),
            "held": expires > now,
            "remaining_seconds": max(0.0, (expires - now).total_seconds()),
        }

    def _read_unlocked(self) -> LeaseRecord | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise LeaseError("workspace lease root must be an object")
            record = LeaseRecord(**raw)
            record.validate()
            return record
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LeaseError(f"workspace lease is unreadable: {exc}") from exc

    def _write_unlocked(self, record: LeaseRecord) -> None:
        record.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(asdict(record), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _assert_workspace(self, handle: LeaseHandle) -> None:
        if handle.workspace_root.resolve() != self.root:
            raise LeaseLostError("lease belongs to a different workspace")

    @staticmethod
    def _assert_record(
        record: LeaseRecord | None,
        handle: LeaseHandle,
        *,
        now: datetime,
    ) -> None:
        if record is None:
            raise LeaseLostError("workspace lease no longer exists")
        if (
            record.owner_id != handle.owner_id
            or record.fencing_token != handle.fencing_token
        ):
            raise LeaseLostError("workspace lease was fenced by another owner")
        if _as_utc(datetime.fromisoformat(record.expires_at)) <= now:
            raise LeaseLostError("workspace lease expired")

    def _handle(self, record: LeaseRecord) -> LeaseHandle:
        return LeaseHandle(
            workspace_root=self.root,
            owner_id=record.owner_id,
            fencing_token=record.fencing_token,
            priority=record.priority,
            holder_label=record.holder_label,
            expires_at=record.expires_at,
        )
