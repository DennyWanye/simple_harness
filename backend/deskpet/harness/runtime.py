"""Stateless driver-candidate interpreter used by the thin RunKernel."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from deskpet.execution.contracts import (
    ActorContext,
    ContractValidationError,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunEvent,
    RunEventCandidate,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
)
from deskpet.execution.uow_ports import DriverRuntimeUnitOfWork
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolArgumentsProjectionError,
)

from .child_runs import ChildRunCoordinator
from .contracts import RegisteredDriver
from .live_index import BoundedLiveIndex
from .ports import (
    DriverEvent,
    DriverTerminalCandidate,
    ToolOutcomesSignal,
)
from .tool_executor import EffectBatchExecutor

logger = logging.getLogger(__name__)

_PERMANENT_RECOVERY_FAILURES = frozenset(
    {
        "prepared_call_stale",
        "tool_catalog_stale",
        "workflow_implementation_stale",
    }
)


class DriverRuntime:
    """Interpret driver output; durable truth remains owned by the injected UoW."""

    def __init__(
        self,
        *,
        uow: DriverRuntimeUnitOfWork,
        live: BoundedLiveIndex,
        query: Callable[[RunRef, ActorContext], Awaitable[RunRecord | object]],
        finalize: Callable[..., Awaitable[RunEvent]],
        terminal_handler: Callable[..., Awaitable[None]] | None = None,
        child_runs: ChildRunCoordinator | None = None,
        tool_executor: EffectBatchExecutor | None = None,
    ) -> None:
        self._uow = uow
        self._live = live
        self._query = query
        self._finalize = finalize
        self._terminal_handler = terminal_handler
        self._child_runs = child_runs
        self._tool_executor = tool_executor

    @staticmethod
    def is_terminal_event(event: RunEvent) -> bool:
        return event.candidate.is_terminal

    @staticmethod
    def _lease_kwargs(lease: RecoveryLease | None) -> dict[str, RecoveryLease]:
        return {} if lease is None else {"recovery_lease": lease}

    async def emit(self, event: RunEvent) -> None:
        terminal = self.is_terminal_event(event)
        async with self._live.lock:
            active = self._live.get(event.run_id)
            if active is None:
                if terminal and self._child_runs is not None:
                    self._child_runs.trigger_reconciliation()
                return
            if (terminal and event.durable_seq is not None and
                    (active.record is None
                     or active.record.status not in TERMINAL_RUN_STATUSES)):
                current = await self._uow.query(
                    RunRef(event.run_id, event.session_id), active.actor)
                if isinstance(current, RunRecord):
                    self._live.adopt(current)
            if not any(item.event_id == event.event_id for item in active.events):
                self._live.publish(active, event)
            if terminal:
                release_child = (
                    active.record is not None
                    and active.record.context.parent_run_id is not None
                )
                self._live.finish(event.run_id, active, release=release_child)
                active.subscribers.clear()
        if terminal and self._child_runs is not None:
            # Native Workflow checkpoints can terminalize a child before the
            # generic Driver sees the persisted final event. Every terminal
            # event therefore wakes parent-signal reconciliation, including
            # events that bypass commit_terminal(). The reconciler coalesces
            # duplicate wakeups and finds work from durable state.
            self._child_runs.trigger_reconciliation()

    async def emit_live(self, record: RunRecord, candidate: RunEventCandidate) -> None:
        async with self._live.lock:
            active = self._live.get(record.run_id) or self._live.add(
                record.run_id, record.context.actor(),
            )
            live_seq = active.next_live_seq
            active.next_live_seq += 1
            event = RunEvent(
                event_id=f"live:{record.run_id}:{live_seq}",
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(f"kernel:{record.run_id}", live_seq),
                candidate=candidate,
                created_at=time.time(),
            )
            self._live.publish(active, event)

    async def drive(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverEvent],
    ) -> None:
        try:
            async for candidate in candidates:
                await self.consume_candidate(registration, record, candidate)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if getattr(exc, "code", None) in {
                "write_cancelled_before_commit",
                "committed_after_cancel",
                "write_outcome_unknown",
            }:
                # The execution write lane converted shutdown cancellation
                # into a typed durable boundary result: rolled back, committed,
                # or commit-unknown.  All three require startup reconciliation;
                # terminalizing here would hide that Run from recovery.
                return
            logger.exception(
                "driver_runtime_failed run_id=%s driver_kind=%s",
                record.run_id,
                registration.kind,
            )
            try:
                await self.consume_candidate(
                    registration,
                    record,
                    DriverTerminalCandidate(
                        run_id=record.run_id,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
            except TerminalConflict:
                return

    async def drive_recovery(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        lease: RecoveryLease,
    ) -> None:
        try:
            await self.consume_fenced(
                registration, record,
                lambda current: registration.driver.recover(
                    record.run_id, current
                ),
                lease,
                release=True,
            )
        except Exception as exc:
            logger.exception(
                "driver_recovery_failed run_id=%s driver_kind=%s",
                record.run_id,
                registration.kind,
            )
            if await self.terminalize_permanent_recovery_failure(
                registration, record, exc
            ):
                return
            raise

    async def terminalize_permanent_recovery_failure(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        exc: BaseException,
    ) -> bool:
        """Settle an incompatible durable snapshot instead of retrying forever."""
        failure_code = self._permanent_recovery_failure_code(exc)
        if failure_code is None:
            return False
        try:
            await self.consume_candidate(
                registration,
                record,
                DriverTerminalCandidate(
                    run_id=record.run_id,
                    status="failed",
                    error=(
                        f"{failure_code}: the saved execution snapshot is "
                        "incompatible with the current runtime; send "
                        "the request again so it can be rebuilt"
                    ),
                    correlation={
                        "failure_code": failure_code,
                        "recovery_terminalization": True,
                    },
                ),
            )
        except TerminalConflict:
            return True
        logger.error(
            "driver_recovery_permanently_terminalized "
            "run_id=%s driver_kind=%s failure_code=%s",
            record.run_id,
            registration.kind,
            failure_code,
        )
        return True

    @staticmethod
    def _permanent_recovery_failure_code(exc: BaseException) -> str | None:
        """Return a stable code only for known non-retriable recovery faults."""
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            code = str(getattr(current, "code", "") or "").strip()
            message = str(current).strip()
            for candidate in (code, message):
                if candidate in _PERMANENT_RECOVERY_FAILURES:
                    return candidate
            current = current.__cause__ or current.__context__
        return None

    async def consume_fenced(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        stream: Callable[[RecoveryLease], AsyncIterator[DriverEvent]],
        lease: RecoveryLease,
        *,
        heartbeat_interval: float = 10.0,
        release: bool = False,
        driver_handoff: bool = False,
    ) -> None:
        handoff = getattr(registration.driver, "consume_admitted", None)
        if driver_handoff and handoff is not None:
            await handoff(self, registration, record, stream, lease)
            return
        current, candidates, pending, failure = lease, None, None, None

        async def await_with_renewal(operation):
            nonlocal current, pending
            pending = asyncio.create_task(operation)
            while not pending.done():
                done, _ = await asyncio.wait(
                    (pending,), timeout=max(0.001, heartbeat_interval)
                )
                if not done:
                    current = await self._uow.recovery_scope(current)
            result = pending.result()
            pending = None
            return result

        try:
            current = await self._uow.recovery_scope(current)
            candidates = stream(current)
            while True:
                current = await self._uow.recovery_scope(current)
                try:
                    candidate = await await_with_renewal(anext(candidates))
                except StopAsyncIteration:
                    return
                consume = self.consume_candidate(
                    registration, record, candidate, recovery_lease=current
                )
                if candidate.kind == "terminal":
                    await consume
                    return
                await await_with_renewal(consume)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            cleanup_error = None
            if candidates is not None:
                try:
                    await candidates.aclose()
                except BaseException as exc:
                    cleanup_error = exc
            if release:
                try:
                    await self._uow.recovery_scope(current, lease_seconds=None)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if failure is None and cleanup_error is not None:
                raise cleanup_error

    async def consume(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverEvent],
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> bool:
        acknowledged = False
        async for candidate in candidates:
            acknowledged = (
                await self.consume_candidate(
                    registration, record, candidate, recovery_lease=recovery_lease
                )
                or acknowledged
            )
        return acknowledged

    async def consume_candidate(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidate: DriverEvent,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> bool:
        if candidate.run_id != record.run_id:
            raise ValueError("driver candidate run binding mismatch")
        if recovery_lease is not None:
            await self._uow.assert_recovery_fence(recovery_lease)
        requires_durable_run = candidate.kind in {"open_decision", "delegate_run"} or (
            candidate.kind == "execute_tools"
            and (any(candidate.effectful) or bool(candidate.grant_refs))
        )
        if requires_durable_run:
            if record.persistence_level is PersistenceLevel.EPHEMERAL:
                durable = await self._uow.query(
                    RunRef(record.run_id, record.context.session_id),
                    record.context.actor(),
                )
                if not isinstance(durable, RunRecord):
                    raise RuntimeError("durable boundary did not promote its execution run")
                async with self._live.lock:
                    active = self._live.get(record.run_id)
                    if active is not None:
                        active.record = None
                record = durable
        if candidate.kind == "persisted_event":
            await self.emit(candidate.event)
            return False
        if candidate.kind == "terminal":
            if self._terminal_handler is None:
                await self.commit_terminal(
                    record,
                    registration.kind,
                    candidate,
                    recovery_lease=recovery_lease,
                )
            else:
                await self._terminal_handler(
                    registration,
                    record,
                    candidate,
                    recovery_lease=recovery_lease,
                )
            return False
        if candidate.kind == "execute_tools":
            try:
                event = self.event_candidate(registration.kind, candidate)
                assert event is not None
            except (
                ContractValidationError,
                PreparedToolArgumentsProjectionError,
            ) as exc:
                logger.error(
                    "tool_argument_projection_invalid run_id=%s command_id=%s "
                    "driver_kind=%s error_type=%s",
                    record.run_id,
                    candidate.command_id,
                    registration.kind,
                    type(exc).__name__,
                )
                outcomes = tuple(
                    NormalizedToolOutcome.failure(
                        "tool_argument_projection_invalid",
                        (
                            "prepared tool arguments could not be projected "
                            "to canonical JSON"
                        ),
                    )
                    for _call in candidate.calls
                )
                metadata = tuple(
                    {
                        "replan": True,
                        "retriable": False,
                        "source_layer": "tool_argument_projection",
                    }
                    for _call in candidate.calls
                )
                signal_iterator = registration.driver.signal(
                    ToolOutcomesSignal(
                        candidate.run_id,
                        candidate.command_id,
                        outcomes,
                        tuple(OutcomeStatus.FAILED for _call in candidate.calls),
                        candidate.original_indexes,
                        metadata,
                    ),
                    **self._lease_kwargs(recovery_lease),
                )
                return await self.consume(
                    registration,
                    record,
                    signal_iterator,
                    recovery_lease=recovery_lease,
                )
            await self.emit_live(record, event)
            if self._tool_executor is None:
                raise RuntimeError("tool executor is unavailable")
            batch = await self._tool_executor.execute(
                record, record.context.actor(), candidate,
                **self._lease_kwargs(recovery_lease),
            )
            if batch is None:
                return False
            signal_iterator = registration.driver.signal(
                batch.signal,
                **self._lease_kwargs(recovery_lease),
            )
            consumed = await self.consume(
                registration,
                record,
                signal_iterator,
                recovery_lease=recovery_lease,
            )
            await self._tool_executor.acknowledge_committed(batch.ready_refs)
            return consumed
        if candidate.kind == "delegate_run" and self._child_runs is not None:
            await self._child_runs.submit(
                record, candidate, **self._lease_kwargs(recovery_lease)
            )
        acknowledged = candidate.kind == "cancel_acknowledged"
        event = self.event_candidate(registration.kind, candidate)
        if event is not None:
            await self.emit_live(record, event)
        return acknowledged

    @staticmethod
    def event_candidate(
        driver_kind: str, candidate: DriverEvent
    ) -> RunEventCandidate | None:
        if candidate.kind == "token":
            return RunEventCandidate(
                event_key=f"token:{uuid.uuid4().hex}",
                kind="transcript",
                status=OutcomeStatus.SUCCEEDED,
                driver_kind=driver_kind,
                payload={
                    "text": candidate.content,
                    "token_kind": candidate.token_kind,
                    "invocation_id": candidate.invocation_id,
                    "stream_epoch": candidate.stream_epoch,
                    "provisional": candidate.provisional,
                    "retract_provisional": candidate.retract_provisional,
                },
            )
        if candidate.kind == "provider_fallback":
            return RunEventCandidate(
                event_key=f"fallback:{candidate.from_provider}:{candidate.to_provider}",
                kind="provider_fallback",
                status=OutcomeStatus.ACCEPTED,
                driver_kind=driver_kind,
                payload={
                    "from_provider": candidate.from_provider,
                    "to_provider": candidate.to_provider,
                    "reason": candidate.reason,
                },
            )
        if candidate.kind == "execute_tools":
            return RunEventCandidate(
                event_key=f"tools:{candidate.command_id}",
                kind="tool_requested",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={"command_id": candidate.command_id},
                payload={
                    "tools": [call.tool_name for call in candidate.calls],
                    "calls": [
                        {
                            "id": call.stable_call_id,
                            "name": call.tool_name,
                            "arguments": call.arguments_json(),
                        }
                        for call in candidate.calls
                    ],
                },
            )
        if candidate.kind == "open_decision":
            return RunEventCandidate(
                event_key=f"decision:{candidate.decision_id}",
                kind="decision",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={
                    "command_id": candidate.command_id,
                    "decision_id": candidate.decision_id,
                },
                payload={
                    "run_id": candidate.run_id,
                    "command_id": candidate.command_id,
                    "request_id": candidate.decision_id,
                    "decision_id": candidate.decision_id,
                    "nonce": candidate.nonce,
                    "version": 0,
                    "kind": candidate.decision_kind,
                    "prompt": dict(candidate.prompt),
                    "prompt_schema_version": candidate.prompt_schema_version,
                    "expires_at": candidate.expires_at,
                    "domain_kind": candidate.domain_kind,
                    "domain_id": candidate.domain_id,
                    "call_id": candidate.call_id,
                    "effect_id": candidate.effect_id,
                    "tool_name": candidate.tool_name,
                    "args_hash": candidate.args_hash,
                    "capability_hash": candidate.capability_hash,
                    "scope_hash": candidate.scope_hash,
                },
            )
        if candidate.kind == "context_compacted":
            return RunEventCandidate(
                event_key=(
                    candidate.source_event_id
                    if candidate.source_event_id.startswith("context-compaction:")
                    else f"context-compaction:{candidate.source_event_id}"
                ),
                kind="context_compacted",
                status=OutcomeStatus.SUCCEEDED,
                driver_kind=driver_kind,
                correlation={
                    "iteration": candidate.iteration,
                    "source_event_id": candidate.source_event_id,
                },
                payload={
                    "reduction": candidate.reduction,
                    "tokens_in": candidate.tokens_in,
                    "tokens_out": candidate.tokens_out,
                    "model": candidate.model,
                    "source_event_id": candidate.source_event_id,
                    "based_on_sample_id": candidate.based_on_sample_id,
                    "occurred_at": candidate.occurred_at,
                },
            )
        if candidate.kind == "delegate_run":
            return RunEventCandidate(
                event_key=f"delegate:{candidate.command_id}",
                kind="delegate_requested",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={"command_id": candidate.command_id},
                payload={
                    "route_hint": candidate.route_hint,
                    "join_policy": candidate.join_policy.value,
                    "attachment_policy": candidate.attachment_policy.value,
                },
            )
        if candidate.kind == "child_accepted":
            return RunEventCandidate(
                event_key=f"child:{candidate.command_id}:{candidate.child_run_id}",
                kind="child_accepted",
                status=OutcomeStatus.ACCEPTED,
                driver_kind=driver_kind,
                correlation={
                    "command_id": candidate.command_id,
                    "child_run_id": candidate.child_run_id,
                },
                payload={"join_policy": candidate.join_policy.value},
            )
        if candidate.kind == "cancel_acknowledged":
            return RunEventCandidate(
                event_key=f"cancel-ack:{candidate.run_id}",
                kind="cancel_acknowledged",
                status=OutcomeStatus.CANCELLED,
                driver_kind=driver_kind,
                payload={"reason": candidate.reason},
            )
        return None

    async def commit_terminal(
        self,
        record: RunRecord,
        driver_kind: str,
        terminal: DriverEvent,
        *,
        recovery_lease: RecoveryLease | None = None,
        current: RunRecord | None = None,
    ) -> None:
        current = current or await self._query(
            RunRef(record.run_id, record.context.session_id),
            record.context.actor(),
        )
        if not isinstance(current, RunRecord):
            raise RuntimeError("legacy run cannot be finalized by the new driver")
        status = RunStatus(terminal.status)
        outcome = {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]
        failure_code = str(
            terminal.correlation.get("failure_code") or "driver_failed"
        )
        event = await self._finalize(
            current,
            expected_version=current.version,
            status=status,
            event=RunEventCandidate(
                event_key=f"terminal:{record.run_id}",
                kind="run.final",
                status=outcome,
                driver_kind=driver_kind,
                correlation=dict(terminal.correlation),
                payload={"text": terminal.content},
                error=(
                    None
                    if terminal.error is None
                    else {"code": failure_code, "message": terminal.error}
                ),
            ),
            recovery_lease=recovery_lease,
        )
        if current.context.parent_run_id is not None:
            authoritative = await self._uow.query(
                RunRef(current.run_id, current.context.session_id),
                current.context.actor(),
            )
            if (
                not isinstance(authoritative, RunRecord)
                or authoritative.status not in TERMINAL_RUN_STATUSES
            ):
                raise RuntimeError("terminal child did not commit authoritative durable state")
            async with self._live.lock:
                self._live.adopt(authoritative)
        await self.emit(event)


__all__ = ["DriverRuntime"]
