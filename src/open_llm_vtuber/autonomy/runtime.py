"""Single-owner coordination for the workspace-scoped agenda executive."""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Awaitable, Callable

from loguru import logger

from .agenda import AgendaStore
from .driver import agenda_loop
from .lease import (
    LeaseError,
    LeaseHandle,
    LeaseLostError,
    WorkspaceLease,
    lease_context,
)
from .presence import PresenceLedger
from .plan_state import ResumablePlanStore
from .settings import AutonomySettings

AgendaRunner = Callable[[str, AutonomySettings, PresenceLedger | None], Awaitable[None]]
HEADLESS_CLIENT_UID = "__autonomy_headless__"


@dataclass(frozen=True)
class _ClientBinding:
    settings: AutonomySettings
    presence: PresenceLedger | None


class WorkspaceAgendaRuntime:
    """Own at most one agenda loop while one or more clients are attached."""

    def __init__(
        self,
        workspace_root: Path,
        runner: AgendaRunner = agenda_loop,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runner = runner
        self.clients: OrderedDict[str, _ClientBinding] = OrderedDict()
        self.background_uid: str | None = None
        self.background_binding: _ClientBinding | None = None
        self.owner_uid: str | None = None
        self.task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.instance_id = uuid.uuid4().hex
        self.lease_status = "standby"
        self.lease_handle: LeaseHandle | None = None
        self._lock = asyncio.Lock()

    async def attach(
        self,
        client_uid: str,
        settings: AutonomySettings,
        presence: PresenceLedger | None,
    ) -> None:
        async with self._lock:
            self.clients[client_uid] = _ClientBinding(settings, presence)
            if settings.debug_reset_agenda_on_connect:
                await self._stop_owner_locked()
                self._reset_agenda_for_debug_locked(client_uid)
                self._start_locked(client_uid, self.clients[client_uid])
                await asyncio.sleep(0)
                return
            if self.owner_uid == self.background_uid:
                await self._stop_owner_locked()
                self._start_locked(client_uid, self.clients[client_uid])
                await asyncio.sleep(0)
            elif self.task is None or self.task.done():
                self._start_locked(client_uid)
                await asyncio.sleep(0)

    async def configure_background(
        self,
        client_uid: str,
        settings: AutonomySettings,
        presence: PresenceLedger | None,
    ) -> None:
        """Keep a safe headless owner available whenever no live client exists."""
        async with self._lock:
            self.background_uid = client_uid
            self.background_binding = _ClientBinding(settings, presence)
            if not self.clients and (self.task is None or self.task.done()):
                self._start_locked(client_uid, self.background_binding)
                await asyncio.sleep(0)

    async def detach(self, client_uid: str) -> None:
        async with self._lock:
            self.clients.pop(client_uid, None)
            if self.owner_uid != client_uid:
                return
            await self._stop_owner_locked()
            if self.clients:
                self._start_locked(next(iter(self.clients)))
                await asyncio.sleep(0)
            elif self.background_uid and self.background_binding:
                self._start_locked(self.background_uid, self.background_binding)
                await asyncio.sleep(0)

    async def close(self) -> None:
        async with self._lock:
            self.clients.clear()
            self.background_uid = None
            self.background_binding = None
            await self._stop_owner_locked()

    @property
    def has_background(self) -> bool:
        return self.background_binding is not None

    @property
    def active_settings(self) -> AutonomySettings | None:
        """Return the settings governing the current or standby binding."""
        if self.owner_uid == self.background_uid and self.background_binding:
            return self.background_binding.settings
        if self.owner_uid is not None and self.owner_uid in self.clients:
            return self.clients[self.owner_uid].settings
        if self.background_binding is not None:
            return self.background_binding.settings
        return None

    def snapshot(self) -> dict:
        if self.owner_uid is None:
            mode = "stopped"
        elif self.owner_uid == self.background_uid:
            mode = "headless"
        else:
            mode = "live"
        try:
            workspace_lease = WorkspaceLease(self.workspace_root).snapshot()
        except Exception as exc:  # noqa: BLE001 - health view is best effort
            workspace_lease = {"held": False, "error": str(exc)[:300]}
        return {
            "workspace": str(self.workspace_root),
            "owner_uid": self.owner_uid,
            "clients": list(self.clients),
            "running": self.task is not None and not self.task.done(),
            "mode": mode,
            "headless_enabled": self.has_background,
            "instance_id": self.instance_id,
            "lease_status": self.lease_status,
            "fencing_token": (
                self.lease_handle.fencing_token if self.lease_handle else None
            ),
            "workspace_lease": workspace_lease,
            "last_error": self.last_error,
        }

    async def _stop_owner_locked(self) -> None:
        task = self.task
        self.task = None
        self.owner_uid = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.lease_handle = None
        self.lease_status = "standby"

    def _reset_agenda_for_debug_locked(self, client_uid: str) -> None:
        removed_plans = ResumablePlanStore(self.workspace_root).reset_for_debug()
        removed = AgendaStore(self.workspace_root).reset_for_debug()
        logger.warning(
            "agenda runtime: debug reset on live client connect "
            "client={} entries={} reports={} events={} plans={}",
            client_uid,
            removed["entries"],
            removed["reports"],
            removed["events"] + removed["inbox"],
            removed_plans,
        )

    def _start_locked(
        self,
        client_uid: str,
        binding: _ClientBinding | None = None,
    ) -> None:
        binding = binding or self.clients[client_uid]
        self.owner_uid = client_uid
        self.last_error = None
        task = asyncio.create_task(
            self._run_leased(client_uid, binding),
            name=f"agenda-runtime:{self.workspace_root.name}:{client_uid}",
        )
        self.task = task
        task.add_done_callback(self._record_completion)
        logger.info(
            f"agenda runtime: {client_uid} owns workspace {self.workspace_root}"
        )

    async def _run_leased(
        self,
        client_uid: str,
        binding: _ClientBinding,
    ) -> None:
        settings = binding.settings
        if not settings.enabled:
            self.lease_status = "standby"
            return
        lease = WorkspaceLease(
            self.workspace_root,
            ttl_seconds=settings.lease_ttl_seconds,
        )
        priority = 100 if client_uid != self.background_uid else 10
        while True:
            try:
                handle = lease.acquire(
                    self.instance_id,
                    priority=priority,
                    holder_label=(
                        "live agenda owner"
                        if priority == 100
                        else "headless agenda owner"
                    ),
                )
            except LeaseError as exc:
                self.lease_status = "standby"
                self.lease_handle = None
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"agenda runtime lease unavailable: {exc}")
                await asyncio.sleep(settings.lease_retry_seconds)
                continue
            if handle is None:
                self.lease_status = "standby"
                self.lease_handle = None
                await asyncio.sleep(settings.lease_retry_seconds)
                continue

            self.last_error = None
            self.lease_status = "held"
            self.lease_handle = handle
            runner_task: asyncio.Task | None = None
            lost = False
            restart = False
            try:
                with lease_context(handle):
                    runner_task = asyncio.create_task(
                        self.runner(
                            client_uid,
                            settings,
                            binding.presence,
                        ),
                        name=f"agenda-runner:{self.workspace_root.name}:{client_uid}",
                    )
                    while True:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(runner_task),
                                timeout=settings.lease_renew_seconds,
                            )
                            self.last_error = (
                                "RuntimeError: agenda runner exited unexpectedly"
                            )
                            logger.warning(
                                "agenda runtime runner exited; restarting after "
                                "the lease retry interval"
                            )
                            restart = True
                            break
                        except asyncio.TimeoutError:
                            try:
                                handle = lease.renew(handle)
                                self.lease_handle = handle
                            except LeaseLostError:
                                self.lease_status = "fenced"
                                lost = True
                                runner_task.cancel()
                                await asyncio.gather(
                                    runner_task,
                                    return_exceptions=True,
                                )
                                break
                            except LeaseError as exc:
                                self.lease_status = "standby"
                                self.last_error = f"{type(exc).__name__}: {exc}"
                                lost = True
                                runner_task.cancel()
                                await asyncio.gather(
                                    runner_task,
                                    return_exceptions=True,
                                )
                                break
            except asyncio.CancelledError:
                if runner_task is not None and not runner_task.done():
                    runner_task.cancel()
                    await asyncio.gather(runner_task, return_exceptions=True)
                raise
            except Exception as exc:
                if runner_task is not None and not runner_task.done():
                    runner_task.cancel()
                    await asyncio.gather(runner_task, return_exceptions=True)
                self.lease_status = "standby"
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    f"agenda runtime runner failed; restarting: {self.last_error}"
                )
                restart = True
            finally:
                try:
                    lease.release(handle)
                except LeaseError as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                self.lease_handle = None
                if self.lease_status == "held":
                    self.lease_status = "standby"
            if lost or restart:
                await asyncio.sleep(settings.lease_retry_seconds)

    def _record_completion(self, task: asyncio.Task) -> None:
        error = None
        if not task.cancelled():
            try:
                error = task.exception()
            except asyncio.CancelledError:
                pass
        if error is not None:
            self.last_error = f"{type(error).__name__}: {error}"
            logger.error(f"agenda runtime stopped unexpectedly: {self.last_error}")
        if self.task is task:
            self.task = None
            self.owner_uid = None
            self.lease_handle = None
            if self.lease_status == "held":
                self.lease_status = "standby"


_runtimes: dict[str, WorkspaceAgendaRuntime] = {}
_client_runtime_keys: dict[str, str] = {}
_registry_lock = Lock()


async def attach_autonomy_client(
    client_uid: str,
    workspace_root: Path,
    settings: AutonomySettings,
    presence: PresenceLedger | None,
) -> WorkspaceAgendaRuntime:
    key = str(workspace_root.resolve())
    with _registry_lock:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = WorkspaceAgendaRuntime(workspace_root)
            _runtimes[key] = runtime
        previous_key = _client_runtime_keys.get(client_uid)
        _client_runtime_keys[client_uid] = key
    if previous_key and previous_key != key:
        with _registry_lock:
            previous = _runtimes.get(previous_key)
        if previous is not None:
            await previous.detach(client_uid)
    await runtime.attach(client_uid, settings, presence)
    return runtime


async def configure_headless_runtime(
    workspace_root: Path,
    settings: AutonomySettings,
    presence: PresenceLedger | None,
    *,
    client_uid: str = HEADLESS_CLIENT_UID,
) -> WorkspaceAgendaRuntime:
    """Install the server-lifetime background binding for a workspace."""
    key = str(workspace_root.resolve())
    with _registry_lock:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = WorkspaceAgendaRuntime(workspace_root)
            _runtimes[key] = runtime
    await runtime.configure_background(client_uid, settings, presence)
    return runtime


async def detach_autonomy_client(client_uid: str) -> None:
    with _registry_lock:
        key = _client_runtime_keys.pop(client_uid, None)
        runtime = _runtimes.get(key) if key is not None else None
    if key is None:
        return
    if runtime is None:
        return
    await runtime.detach(client_uid)
    if not runtime.clients and not runtime.has_background:
        await runtime.close()
        with _registry_lock:
            if _runtimes.get(key) is runtime and not runtime.clients:
                _runtimes.pop(key, None)


def get_workspace_runtime(workspace_root: Path) -> WorkspaceAgendaRuntime | None:
    with _registry_lock:
        return _runtimes.get(str(workspace_root.resolve()))


async def reset_runtimes() -> None:
    with _registry_lock:
        runtimes = list(_runtimes.values())
        _runtimes.clear()
        _client_runtime_keys.clear()
    for runtime in runtimes:
        await runtime.close()
