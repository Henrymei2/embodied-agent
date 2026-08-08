"""Server-lifetime producers that turn external changes into typed triggers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock
from loguru import logger

from .agenda import AgendaStore
from .triggers import TriggerEnvelope, TriggerReference, publish_trigger


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None)
    try:
        return int(getattr(response, "status", 0)) or None
    except (TypeError, ValueError):
        return None


class ProducerStateStore:
    """Small atomic cursor store; it never contains external source content."""

    def __init__(self, workspace_root: Path) -> None:
        self.path = workspace_root / "agenda" / "producer_state.json"
        self.lock = FileLock(str(self.path.with_suffix(".lock")))

    def get(self, key: str) -> dict[str, Any]:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, json.JSONDecodeError, TypeError):
                logger.warning("event producer cursor store was unreadable; rebaselining")
                return {}
            value = raw.get(key, {}) if isinstance(raw, dict) else {}
            return dict(value) if isinstance(value, dict) else {}

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            raw[key] = dict(value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)


class GmailEventProducer:
    name = "gmail"

    def __init__(self, service: Any, store: AgendaStore, cursors: ProducerStateStore):
        self.service = service
        self.store = store
        self.cursors = cursors

    def poll_once(self) -> None:
        gmail = self.service.gmail()
        users = gmail.users()
        cursor = str(self.cursors.get(self.name).get("history_id") or "")
        if not cursor:
            profile = users.getProfile(userId="me").execute()
            self.cursors.put(self.name, {"history_id": str(profile["historyId"])})
            return

        try:
            page_token: str | None = None
            latest = cursor
            message_ids: set[str] = set()
            while True:
                kwargs: dict[str, Any] = {
                    "userId": "me",
                    "startHistoryId": cursor,
                    "historyTypes": ["messageAdded", "labelAdded"],
                    "labelId": "UNREAD",
                    "maxResults": 500,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                response = users.history().list(**kwargs).execute()
                latest = str(response.get("historyId") or latest)
                for history in response.get("history", []):
                    if not isinstance(history, dict):
                        continue
                    for field in ("messagesAdded", "labelsAdded"):
                        for change in history.get(field, []):
                            message = change.get("message") if isinstance(change, dict) else None
                            if isinstance(message, dict) and message.get("id"):
                                message_ids.add(str(message["id"]))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            for message_id in sorted(message_ids):
                message = users.messages().get(
                    userId="me", id=message_id, format="minimal"
                ).execute()
                if "UNREAD" not in message.get("labelIds", []):
                    continue
                publish_trigger(
                    self.store,
                    TriggerEnvelope(
                        TriggerReference(
                            source="gmail",
                            resource_type="message",
                            resource_id=message_id,
                            # A Gmail message is one opportunity even if labels
                            # later toggle; the stable message ID is its revision.
                            revision=message_id,
                            change_type="unread",
                        )
                    ),
                )
            self.cursors.put(self.name, {"history_id": latest})
        except Exception as exc:
            if _http_status(exc) == 404:
                profile = users.getProfile(userId="me").execute()
                self.cursors.put(self.name, {"history_id": str(profile["historyId"])})
                return
            raise


class CalendarEventProducer:
    name = "google_calendar"

    def __init__(self, service: Any, store: AgendaStore, cursors: ProducerStateStore):
        self.service = service
        self.store = store
        self.cursors = cursors

    def _list_all(self, **kwargs) -> tuple[list[dict[str, Any]], str]:
        events_api = self.service.calendar().events()
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        sync_token = ""
        while True:
            request = dict(kwargs)
            if page_token:
                request["pageToken"] = page_token
            response = events_api.list(**request).execute()
            items.extend(item for item in response.get("items", []) if isinstance(item, dict))
            page_token = response.get("nextPageToken")
            sync_token = str(response.get("nextSyncToken") or sync_token)
            if not page_token:
                return items, sync_token

    def _baseline(self) -> None:
        _, token = self._list_all(
            calendarId="primary",
            singleEvents=True,
            showDeleted=True,
            maxResults=2500,
            timeMin=datetime.now(timezone.utc).isoformat(),
        )
        if not token:
            raise RuntimeError("Google Calendar did not return a sync token")
        self.cursors.put(self.name, {"sync_token": token})

    def poll_once(self) -> None:
        token = str(self.cursors.get(self.name).get("sync_token") or "")
        if not token:
            self._baseline()
            return
        try:
            items, next_token = self._list_all(
                calendarId="primary",
                singleEvents=True,
                showDeleted=True,
                maxResults=2500,
                syncToken=token,
            )
        except Exception as exc:
            if _http_status(exc) == 410:
                self._baseline()
                return
            raise

        for item in items:
            event_id = str(item.get("id") or "")
            revision = str(item.get("updated") or item.get("etag") or "")
            if not event_id or not revision:
                continue
            if item.get("status") == "cancelled":
                change_type = "deleted"
            elif item.get("created") and item.get("created") == item.get("updated"):
                change_type = "created"
            else:
                change_type = "updated"
            publish_trigger(
                self.store,
                TriggerEnvelope(
                    TriggerReference(
                        source="google_calendar",
                        resource_type="calendar_event",
                        resource_id=event_id,
                        revision=revision,
                        change_type=change_type,
                    )
                ),
            )
        self.cursors.put(self.name, {"sync_token": next_token or token})


class WorkspaceEventProducer:
    name = "workspace"

    def __init__(
        self,
        workspace_root: Path,
        watch_path: str,
        settle_seconds: float,
        store: AgendaStore,
    ) -> None:
        relative = Path(watch_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("workspace watch path must stay inside the workspace")
        self.workspace_root = workspace_root.resolve()
        unresolved_watch_root = self.workspace_root / relative
        if unresolved_watch_root.is_symlink():
            raise ValueError("workspace watch path cannot be a symbolic link")
        self.watch_root = unresolved_watch_root.resolve()
        if not self.watch_root.is_relative_to(self.workspace_root):
            raise ValueError("workspace watch path escapes the workspace")
        self.relative_root = relative.as_posix()
        self.settle_seconds = max(0.1, float(settle_seconds))
        self.store = store
        self.known: dict[str, str] | None = None
        self.pending: dict[str, tuple[str, float, str]] = {}

    @staticmethod
    def _fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _scan(self) -> dict[str, str]:
        self.watch_root.mkdir(parents=True, exist_ok=True)
        if self.watch_root.is_symlink():
            raise ValueError("workspace watch path cannot be a symbolic link")
        result: dict[str, str] = {}
        for path in self.watch_root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.watch_root):
                continue
            relative = resolved.relative_to(self.workspace_root).as_posix()
            result[relative] = self._fingerprint(resolved)
        return result

    def _publish(self, resource_id: str, revision: str, change_type: str) -> None:
        publish_trigger(
            self.store,
            TriggerEnvelope(
                TriggerReference(
                    source="workspace",
                    resource_type="file",
                    resource_id=resource_id,
                    revision=revision,
                    change_type=change_type,
                )
            ),
        )

    def poll_once(self) -> None:
        current = self._scan()
        if self.known is None:
            self.known = current
            self.pending.clear()
            return
        now = time.monotonic()
        for resource_id in sorted(set(self.known) - set(current)):
            self._publish(resource_id, self.known[resource_id], "deleted")
            self.known.pop(resource_id, None)
            self.pending.pop(resource_id, None)
        for resource_id, fingerprint in current.items():
            previous = self.known.get(resource_id)
            if previous == fingerprint:
                self.pending.pop(resource_id, None)
                continue
            change_type = "created" if previous is None else "modified"
            pending = self.pending.get(resource_id)
            if pending is None or pending[0] != fingerprint or pending[2] != change_type:
                self.pending[resource_id] = (fingerprint, now, change_type)
                continue
            if now - pending[1] < self.settle_seconds:
                continue
            self._publish(resource_id, fingerprint, change_type)
            self.known[resource_id] = fingerprint
            self.pending.pop(resource_id, None)


@dataclass
class ProducerHealth:
    status: str = "disabled"
    last_success_at: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0


class EventProducerManager:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self.health: dict[str, ProducerHealth] = {
            name: ProducerHealth() for name in ("gmail", "google_calendar", "workspace")
        }

    def add(self, name: str, producer: Any, poll_seconds: float) -> None:
        if name in self.tasks:
            raise ValueError(f"event producer already registered: {name}")
        self.health[name] = ProducerHealth(status="running")
        self.tasks[name] = asyncio.create_task(
            self._run(name, producer.poll_once, poll_seconds),
            name=f"event-producer:{name}",
        )

    async def _run(self, name: str, poll: Callable[[], None], interval: float) -> None:
        failures = 0
        while True:
            try:
                await asyncio.to_thread(poll)
                failures = 0
                self.health[name] = ProducerHealth(
                    status="running", last_success_at=_iso_now()
                )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # source failures are isolated by design
                failures += 1
                self.health[name] = ProducerHealth(
                    status="degraded",
                    last_success_at=self.health[name].last_success_at,
                    last_error=f"{type(exc).__name__}: external source unavailable"[:300],
                    consecutive_failures=failures,
                )
                logger.warning(f"event producer {name} failed: {type(exc).__name__}")
                await asyncio.sleep(min(interval * (2 ** min(failures, 6)), 300.0))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: asdict(health) for name, health in self.health.items()}

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_manager: EventProducerManager | None = None


def get_event_producer_manager() -> EventProducerManager | None:
    return _manager


async def start_event_producers(
    *,
    workspace_root: Path,
    store: AgendaStore,
    google_service: Any,
    config: Any,
) -> EventProducerManager:
    global _manager
    if _manager is not None:
        await _manager.close()
    manager = EventProducerManager()
    cursors = ProducerStateStore(workspace_root)
    gmail = getattr(config, "gmail", None)
    if gmail is not None and bool(getattr(gmail, "enabled", False)):
        manager.add(
            "gmail",
            GmailEventProducer(google_service, store, cursors),
            float(getattr(gmail, "poll_seconds", 5.0)),
        )
    calendar = getattr(config, "google_calendar", None)
    if calendar is not None and bool(getattr(calendar, "enabled", False)):
        manager.add(
            "google_calendar",
            CalendarEventProducer(google_service, store, cursors),
            float(getattr(calendar, "poll_seconds", 5.0)),
        )
    workspace = getattr(config, "workspace", None)
    if workspace is not None and bool(getattr(workspace, "enabled", False)):
        manager.add(
            "workspace",
            WorkspaceEventProducer(
                workspace_root,
                str(getattr(workspace, "watch_path", "demo-inbox")),
                float(getattr(workspace, "settle_seconds", 1.0)),
                store,
            ),
            float(getattr(workspace, "poll_seconds", 5.0)),
        )
    _manager = manager
    return manager


async def stop_event_producers() -> None:
    global _manager
    manager = _manager
    _manager = None
    if manager is not None:
        await manager.close()
