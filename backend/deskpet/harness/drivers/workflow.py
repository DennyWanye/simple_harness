"""Canonical Driver adapter for durable Native workflows."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from deskpet.execution.contracts import OutcomeStatus, RecoveryLease, RunEvent
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DriverEvent,
    DriverSignal,
    DriverStart,
    PersistedEventCandidate,
)
from deskpet.harness.profiles import ProfileRegistry


class WorkflowLauncherPort(Protocol):
    async def launch_precreated(self, **kwargs: Any) -> Mapping[str, Any]: ...
    async def recover_pending(self, *, only_run_ids: set[str] | None = None, recovery_lease: RecoveryLease | None = None) -> list[str]: ...
    async def cancel_precreated(self, run_id: str, reason: str = "user") -> Mapping[str, Any]: ...


class WorkflowEventReader(Protocol):
    async def get_event(self, event_id: str) -> RunEvent: ...
    async def list_events(
        self, run_id: str, *, after_durable_seq: int = 0
    ) -> tuple[RunEvent, ...]: ...


class WorkflowSignalResumer(Protocol):
    async def resume(
        self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None
    ) -> None: ...


class WorkflowResumeLauncher(Protocol):
    async def resume_precreated(
        self,
        run_id: str,
        responses: Mapping[str, Any],
    ) -> Any: ...


class LauncherWorkflowSignalResumer:
    """Resume a Native interrupt after Kernel resolved its durable decision.

    ``RunKernel.signal`` performs the authenticated decision CAS first.  The
    workflow engine therefore receives only the already-authorized response,
    keyed by the immutable interrupt nonce captured when the checkpoint opened
    the decision.  It never re-resolves a legacy HumanStore decision.
    """

    def __init__(
        self,
        launcher: WorkflowResumeLauncher,
        *,
        unit_of_work: ExecutionUnitOfWork | None = None,
    ) -> None:
        self._launcher = launcher
        self._unit_of_work = unit_of_work

    async def resume(
        self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None
    ) -> None:
        if signal.kind in {"child_accepted", "child_terminal"}:
            if self._unit_of_work is None or recovery_lease is None:
                raise RuntimeError("workflow child resume requires its fenced unit of work")
            signal_id = str(getattr(signal, "signal_id", "") or "").strip()
            if not signal_id:
                raise ValueError("workflow child signal requires its stable signal id")
            decision = await self._unit_of_work.prepare_workflow_child_resume(
                signal_id, recovery_lease=recovery_lease
            )
            if decision.request.run_id != signal.run_id:
                raise ValueError("workflow child decision run binding mismatch")
            payload = dict((decision.response or {})["child_signal"])
            await self._launcher.resume_precreated(
                signal.run_id, {decision.request.nonce: payload}
            )
            return
        if signal.kind != "decision":
            raise TypeError("unsupported workflow signal")
        nonce = str(getattr(signal, "nonce", "") or "").strip()
        if not nonce:
            raise ValueError("workflow decision signal requires its interrupt nonce")
        response = getattr(signal, "response", None)
        if not isinstance(response, Mapping):
            raise ValueError("workflow decision signal requires a response mapping")
        await self._launcher.resume_precreated(signal.run_id, {nonce: dict(response)})


class WorkflowDriver:
    """Map registered profiles to Native workflow definitions without product branches."""

    def __init__(
        self,
        launcher: WorkflowLauncherPort,
        events: WorkflowEventReader,
        profiles: ProfileRegistry,
        *,
        signal_resumer: WorkflowSignalResumer | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        if not profiles.workflow_specs:
            raise ValueError("at least one workflow profile is required")
        self._launcher = launcher
        self._events = events
        self._profiles = profiles.workflow_specs
        self._signal_resumer = signal_resumer
        self._poll_interval = max(0.001, float(poll_interval))

    @property
    def profile_keys(self) -> frozenset[str]:
        return frozenset(self._profiles)

    @staticmethod
    def _terminal(event: RunEvent) -> bool:
        return event.status in {
            OutcomeStatus.SUCCEEDED,
            OutcomeStatus.FAILED,
            OutcomeStatus.CANCELLED,
        } and (
            event.kind == "final"
            or event.kind.endswith(".final")
            or str(event.candidate.payload.get("kind") or "") == "final"
        )

    async def _follow(
        self,
        run_id: str,
        *,
        after: int,
    ) -> AsyncIterator[DriverEvent]:
        cursor = after
        while True:
            events = await self._events.list_events(
                run_id, after_durable_seq=cursor
            )
            for event in events:
                cursor = max(cursor, int(event.durable_seq or 0))
                yield PersistedEventCandidate(event)
                if self._terminal(event):
                    return
            await asyncio.sleep(self._poll_interval)

    def start(self, request: DriverStart) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            context = request.run_context
            if context is None:
                raise ValueError("workflow driver requires a trusted run context")
            profile = self._profiles.get(request.profile_key)
            if profile is None:
                raise ValueError(f"workflow profile is not registered: {request.profile_key}")
            accepted = await self._launcher.launch_precreated(
                workflow_name=str(profile.workflow_name),
                workflow_version=str(profile.workflow_version),
                session_id=context.session_id,
                request_id=context.request_id,
                turn_id=context.turn_id,
                start_payload=(
                    dict(profile.request_factory(request))
                    if profile.request_factory is not None
                    else dict(request.request_payload)
                ),
                capability_snapshot=dict(request.capability_snapshot),
                state_factory=profile.state_factory,
                context_factory=profile.context_factory,
                venue=context.venue,
                base_epoch=context.auth_epoch,
                logical_slot=profile.logical_slot,
                principal_id=context.principal_id,
                run_id=request.run_id,
                trace_id=context.trace_id,
                association_event=request.association_event,
            )
            accepted_event = await self._events.get_event(
                str(accepted["accepted_event_id"])
            )
            yield PersistedEventCandidate(accepted_event)
            async for candidate in self._follow(
                request.run_id,
                after=int(accepted_event.durable_seq or 0),
            ):
                yield candidate

        return iterator()

    def signal(self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            if self._signal_resumer is None:
                raise RuntimeError("workflow signal resumer is not configured")
            await self._signal_resumer.resume(signal, recovery_lease)
            if False:
                yield CancelAcknowledgedCandidate(signal.run_id, "unreachable")

        return iterator()

    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            await self._launcher.cancel_precreated(run_id, reason)
            yield CancelAcknowledgedCandidate(run_id, reason)

        return iterator()

    def recover(self, run_id: str, recovery_lease: RecoveryLease) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            existing = await self._events.list_events(run_id, after_durable_seq=0)
            cursor = 0
            for event in existing:
                cursor = max(cursor, int(event.durable_seq or 0))
                yield PersistedEventCandidate(event)
                if self._terminal(event):
                    return
            await self._launcher.recover_pending(
                only_run_ids={run_id}, recovery_lease=recovery_lease
            )
            async for candidate in self._follow(run_id, after=cursor):
                yield candidate

        return iterator()

    async def close(self) -> None:
        return None
