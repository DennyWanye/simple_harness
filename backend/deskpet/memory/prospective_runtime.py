"""Owned production registration/time loop over existing durable public contracts.

No model or new ledger; A7 presentation/ACK remains the foreground consumer.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import sqlite3
from pathlib import Path
import uuid

log = logging.getLogger(__name__)

# Our own DDL raises bare snake_case tokens (`RAISE(ABORT,'s5c_cursor_successor_required')`).
# Only a message that is entirely such a token is logged; every other SQLite
# message (which can quote schema text) is reduced to its result code, so the
# log line stays payload-free by construction.
_CONSTRAINT_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_CAUSE_DEPTH = 8


def failure_identity(exc: BaseException) -> str:
    """Stable, payload-free failure identity for an otherwise silent lane error.

    `type=...` alone cost the v55 cursor regression a full corpus rerun: every
    registration failed with a bare `IntegrityError` and the constraint that
    actually fired (`s5c_cursor_successor_required`) only surfaced from a
    hand-run traceback.
    """

    identity = [f"type={type(exc).__name__}"]
    seen: list[int] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < _CAUSE_DEPTH:
        if id(current) in seen:
            break
        seen.append(id(current))
        if isinstance(current, sqlite3.Error):
            name = getattr(current, "sqlite_errorname", None)
            code = getattr(current, "sqlite_errorcode", None)
            if name:
                identity.append(f"sqlite={name}")
            if code is not None:
                identity.append(f"sqlite_code={code}")
            message = str(current).strip()
            if _CONSTRAINT_TOKEN.match(message):
                identity.append(f"constraint={message}")
            break
        current = current.__cause__ or current.__context__
    return " ".join(identity)


REMINDER_CAPABILITY = (
    "One-shot time reminders can be processed by the background memory workflow after this turn. "
    "The absence of a reminder-creation tool does not mean this capability is unavailable. "
    "Before an actual durable registration result is available, say only that the request can be processed; "
    "do not claim a reminder is already scheduled or delivered. An accepted analysis candidate is not a scheduler acknowledgement. "
    "Due reminders are presented through the current conversation while the application is running; "
    "do not promise operating-system notifications or wake-up while the application is closed. "
)


class RuntimeProspectiveSignalAuthority:
    """Route to existing exact, owner-bound journals, never mint an authority."""
    def __init__(self, path, principal):
        self.path, self.principal = Path(path), principal

    async def resolve_prospective_signal_authority(self, reference):
        from simple_harness.runtime import ProspectiveSignalAuthorityRef
        from deskpet.memory.s5c_store import HostProspectiveSignalAuthority
        from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
        if type(reference) is not ProspectiveSignalAuthorityRef:
            raise TypeError("ProspectiveSignalAuthorityRef required")
        if reference.authority_id.startswith("host:time-authority:"):
            return await ProspectiveSignalStore(self.path, self.principal).resolve_prospective_signal_authority(reference)
        if reference.authority_id.startswith("host:prospective-authority:"):
            return await HostProspectiveSignalAuthority(self.path, self.principal).resolve_prospective_signal_authority(reference)
        raise ValueError("prospective_runtime_authority_kind_unknown")


class ProspectiveRuntimeLane:
    def __init__(self, *, path, runtime, clock, poll_seconds=2.0, batch_size=32):
        if (not callable(clock) or type(poll_seconds) not in (int, float)
                or not math.isfinite(poll_seconds) or poll_seconds <= 0
                or type(batch_size) is not int or not 1 <= batch_size <= 100):
            raise ValueError("prospective_runtime_arguments_invalid")
        self.path, self.runtime, self.clock = Path(path), runtime, clock
        self.poll_seconds, self.batch_size = float(poll_seconds), batch_size
        self.owner = "host:prospective-runtime:" + uuid.uuid4().hex
        self._task = None
        self._wake = asyncio.Event()
        self._closing = False
        self._registration = self._timer = None
        self._manager = None
        self.last_registration_error = self.last_timer_error = None
        self.completed_ticks = 0
        self.last_applied_count = 0

    async def _components(self):
        manager = await self.runtime.manager()
        if manager is not self._manager:
            from deskpet.memory.s5c_store import S5cStore
            from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
            from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
            from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
            from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
            from deskpet.memory.prospective_scheduler import ProspectiveScheduler
            registrations = S5cStore(self.path, self.runtime.principal())
            signals = ProspectiveSignalStore(self.path, self.runtime.principal())
            self._registration = ProspectiveRegistrationConsumer(registrations, manager,
                PublicRegistrationAuthoritySource(store=registrations, memory=manager, clock=self.clock))
            self._timer = ProspectiveScheduler(store=signals, memory=manager, clock=self.clock,
                source=PublicTimeAuthoritySource(registrations=registrations, signals=signals))
            self._manager = manager

    async def tick(self):
        await self._components()
        self.last_registration_error = self.last_timer_error = None
        self.last_applied_count = 0
        try:
            await self._registration.run_once(page_size=self.batch_size, max_pages=1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_registration_error = exc
            log.warning("prospective_runtime_registration_failed %s", failure_identity(exc))
        try:
            self.last_applied_count = await self._timer.tick(claim_owner=self.owner, limit=self.batch_size)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_timer_error = exc
            log.warning("prospective_runtime_timer_failed %s", failure_identity(exc))
        self.completed_ticks += 1
        if self.last_applied_count:
            log.info("prospective_runtime_time_applied count=%s", self.last_applied_count)
        return self.last_applied_count

    def wake(self):
        self._wake.set()

    def start(self):
        if self._closing:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="prospective-runtime-lane")

    async def _run(self):
        while not self._closing:
            self._wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_registration_error = exc
                log.warning("prospective_runtime_unavailable %s", failure_identity(exc))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def close(self):
        self._closing = True
        self._wake.set()
        task = self._task
        cancellation = None
        try:
            if task is not None:
                task.cancel()
                # Own the child's cleanup even if our caller is also cancelled.
                join = asyncio.gather(task, return_exceptions=True)
                while not join.done():
                    try:
                        await asyncio.shield(join)
                    except asyncio.CancelledError as exc:
                        cancellation = exc
                join.result()
        finally:
            self._task = None
            self._registration = self._timer = self._manager = None
            self._closing = False
        # Explicit subsequent start (same runtime) is supported, just like the
        # owning MemoryAnalysisLane. Nothing starts itself after close.
        if cancellation is not None:
            raise cancellation
        # Shared Memory manager is owned and closed by application composition.
