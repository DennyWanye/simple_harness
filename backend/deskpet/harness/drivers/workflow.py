"""Canonical Driver adapter for durable Native workflows."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Protocol

from deskpet.execution.dispatch import dispatch_with_run_fence
from deskpet.execution.contracts import (
    RecoveryLease,
    RunEvent,
    RunRef,
    StaleRecoveryLease,
)
from deskpet.execution.uow_ports import WorkflowDriverUnitOfWork
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DriverEvent,
    DriverRecoveryDeferred,
    DriverSignal,
    DriverStart,
    PersistedEventCandidate,
)
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.start_snapshot import activate_after_start_commit


class WorkflowLauncherPort(Protocol):
    async def launch_precreated(self, **kwargs: Any) -> Mapping[str, Any]: ...
    async def recover_pending(self, *, only_run_ids: set[str] | None = None, recovery_lease: RecoveryLease | None = None) -> list[str]: ...
    async def cancel_precreated(self, run_id: str, reason: str = "user") -> Mapping[str, Any]: ...
    async def resume_precreated(self, run_id: str, responses: Mapping[str, Any]) -> Any: ...
    async def schedule_resume(self, run_id: str, responses: Mapping[str, Any]) -> Mapping[str, Any]: ...


class WorkflowEventReader(Protocol):
    async def get_event(self, event_id: str) -> RunEvent: ...
    async def list_events(
        self, run_id: str, *, after_durable_seq: int = 0
    ) -> tuple[RunEvent, ...]: ...


class WorkflowDriver:
    """Map registered profiles to Native workflow definitions without product branches."""

    def __init__(
        self,
        launcher: WorkflowLauncherPort,
        events: WorkflowEventReader,
        profiles: ProfileRegistry,
        *,
        unit_of_work: WorkflowDriverUnitOfWork | None = None,
        run_execution_fence_acquirer: (
            Callable[[str], Awaitable[Any]] | None
        ) = None,
        poll_interval: float = 0.05,
    ) -> None:
        if not profiles.workflow_specs:
            raise ValueError("at least one workflow profile is required")
        self._launcher = launcher
        self._events = events
        self._profiles = profiles.workflow_specs
        self._unit_of_work = unit_of_work
        self._run_execution_fence_acquirer = (
            run_execution_fence_acquirer
        )
        self._poll_interval = max(0.001, float(poll_interval))

    @property
    def profile_keys(self) -> frozenset[str]:
        return frozenset(self._profiles)

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
                if event.candidate.is_terminal:
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
            if request.run_spec is None:
                raise ValueError("workflow driver requires the Kernel-frozen run spec")
            accepted = await dispatch_with_run_fence(
                acquire_fence=self._run_execution_fence_acquirer,
                run_id=request.run_id,
                operation_kind="workflow.node.launch_precreated",
                operation_id=request.run_id,
                invoke=lambda: self._launcher.launch_precreated(
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
                    admission_launch=request.admission_launch,
                    execution_spec=request.run_spec,
                    run_start_snapshot=request.run_start_snapshot,
                    start_commit_extensions=(
                        ()
                        if request.prepared_run_context is None
                        else request.prepared_run_context.start_commit_extensions
                    ),
                    after_start_commit=(
                        None
                        if (
                            request.prepared_run_context is None
                            or request.run_start_snapshot is None
                        )
                        else self._after_start_commit(request)
                    ),
                ),
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

    def _after_start_commit(self, request: DriverStart):
        async def activate(_accepted: Mapping[str, Any]) -> None:
            assert request.run_context is not None
            assert request.run_start_snapshot is not None
            assert request.prepared_run_context is not None
            stored = await self._unit_of_work.read_run_start_snapshot(
                request.run_id
            )
            if stored is None:
                raise RuntimeError("workflow start snapshot is missing after commit")
            record = await self._unit_of_work.query(
                RunRef(request.run_id, request.session_id),
                request.run_context.actor(),
            )
            await activate_after_start_commit(
                record=record,
                snapshot=stored,
                prepared=request.prepared_run_context,
            )

        if self._unit_of_work is None:
            raise RuntimeError("workflow start handshake requires its unit of work")
        return activate

    def signal(self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
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
                await dispatch_with_run_fence(
                    acquire_fence=self._run_execution_fence_acquirer,
                    run_id=signal.run_id,
                    operation_kind="workflow.node.resume_child",
                    operation_id=signal_id,
                    invoke=lambda: self._launcher.resume_precreated(
                        signal.run_id,
                        {
                            decision.request.nonce: dict(
                                (decision.response or {})["child_signal"]
                            )
                        },
                    ),
                )
            elif signal.kind == "decision":
                nonce = str(getattr(signal, "nonce", "") or "").strip()
                response = getattr(signal, "response", None)
                if not nonce:
                    raise ValueError("workflow decision signal requires its interrupt nonce")
                if not isinstance(response, Mapping):
                    raise ValueError("workflow decision signal requires a response mapping")
                await dispatch_with_run_fence(
                    acquire_fence=self._run_execution_fence_acquirer,
                    run_id=signal.run_id,
                    operation_kind="workflow.node.resume_decision",
                    operation_id=nonce,
                    invoke=lambda: self._launcher.schedule_resume(
                        signal.run_id, {nonce: dict(response)}
                    ),
                )
            else:
                raise TypeError("unsupported workflow signal")
            if False:
                yield CancelAcknowledgedCandidate(signal.run_id, "unreachable")

        return iterator()

    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            await self._launcher.cancel_precreated(run_id, reason)
            yield CancelAcknowledgedCandidate(run_id, reason)

        return iterator()

    async def prepare_recovery(
        self, run_id: str, recovery_lease: RecoveryLease
    ) -> None:
        """Wait until the Native launcher has rebuilt its trusted context."""
        if self._unit_of_work is None:
            raise RuntimeError("workflow recovery requires its fenced unit of work")
        recovered = await dispatch_with_run_fence(
            acquire_fence=self._run_execution_fence_acquirer,
            run_id=run_id,
            operation_kind="workflow.node.recover",
            operation_id=run_id,
            invoke=lambda: self._launcher.recover_pending(
                only_run_ids={run_id},
                recovery_lease=recovery_lease,
            ),
        )
        if run_id not in recovered:
            raise DriverRecoveryDeferred(
                "workflow recovery is waiting for a durable resume condition"
            )

    async def consume_admitted(
        self, runtime, registration, record, stream, lease
    ) -> None:
        """Release Kernel recovery ownership after Native accepted the run."""
        candidates = stream(lease)
        try:
            accepted = await anext(candidates)
            if (accepted.kind != "persisted_event"
                    or accepted.event.candidate.kind != "workflow.accepted"):
                raise RuntimeError("workflow admission lacked a durable accepted handoff")
            await runtime.consume_candidate(registration, record, accepted)
            try:
                await self._events.recovery_scope(lease, lease_seconds=None)
            except StaleRecoveryLease:
                pass
            await runtime.consume(registration, record, candidates)
        finally:
            await candidates.aclose()

    def recover(self, run_id: str, recovery_lease: RecoveryLease) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            existing = await self._events.list_events(run_id, after_durable_seq=0)
            cursor = 0
            for event in existing:
                cursor = max(cursor, int(event.durable_seq or 0))
                yield PersistedEventCandidate(event)
                if event.candidate.is_terminal:
                    return
            async for candidate in self._follow(run_id, after=cursor):
                yield candidate

        return iterator()

    async def close(self) -> None:
        return None
