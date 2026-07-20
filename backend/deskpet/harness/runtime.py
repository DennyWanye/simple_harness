"""Stateless driver-candidate interpreter used by the thin RunKernel."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    GrantConsume,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunEvent,
    RunEventCandidate,
    RunRecord,
    RunRef,
    RunStatus,
    TerminalConflict,
)
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.workflows.effects import NormalizedToolOutcome

from .child_runs import ChildRunCoordinator
from .contracts import RegisteredDriver
from .live_index import BoundedLiveIndex
from .ports import (
    DriverEvent,
    DriverTerminalCandidate,
    ToolOutcomesSignal,
)
from .tool_executor import UnifiedToolExecutor


class DriverRuntime:
    """Interpret driver output; durable truth remains owned by the injected UoW."""

    def __init__(
        self,
        *,
        uow: ExecutionUnitOfWork,
        live: BoundedLiveIndex,
        query: Callable[[RunRef, ActorContext], Awaitable[RunRecord | object]],
        finalize: Callable[..., Awaitable[RunEvent]],
        child_runs: ChildRunCoordinator | None = None,
        tool_executor: UnifiedToolExecutor | None = None,
    ) -> None:
        self._uow = uow
        self._live = live
        self._query = query
        self._finalize = finalize
        self._child_runs = child_runs
        self._tool_executor = tool_executor

    @staticmethod
    def is_terminal_event(event: RunEvent) -> bool:
        return event.candidate.kind == "final" and event.candidate.status in {
            OutcomeStatus.SUCCEEDED,
            OutcomeStatus.FAILED,
            OutcomeStatus.CANCELLED,
        }

    @staticmethod
    def _lease_kwargs(lease: RecoveryLease | None) -> dict[str, RecoveryLease]:
        return {} if lease is None else {"recovery_lease": lease}

    async def emit(self, event: RunEvent) -> None:
        terminal = self.is_terminal_event(event)
        async with self._live.lock:
            active = self._live.get(event.run_id)
            if active is None:
                return
            if not any(item.event_id == event.event_id for item in active.events):
                self._live.publish(active, event)
            if terminal:
                release_child = (
                    active.record is not None
                    and active.record.context.parent_run_id is not None
                )
                self._live.finish(event.run_id, active, release=release_child)
                active.subscribers.clear()

    async def emit_live(self, record: RunRecord, candidate: RunEventCandidate) -> None:
        async with self._live.lock:
            active = self._live.get(record.run_id) or self._live.add(
                record.run_id,
                ActorContext(
                    principal_id=record.context.principal_id,
                    session_id=record.context.session_id,
                    auth_epoch=record.context.auth_epoch,
                    root_run_id=record.context.root_run_id,
                ),
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
            try:
                await self.commit_terminal(
                    record,
                    registration.kind,
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
        current = [lease]
        heartbeat_error: list[BaseException] = []

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10.0)
                try:
                    current[0] = await self._uow.renew_recovery(current[0])
                except BaseException as exc:
                    heartbeat_error.append(exc)
                    return

        candidates: AsyncIterator[DriverEvent] | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            current[0] = await self._uow.renew_recovery(current[0])
            candidates = registration.driver.recover(record.run_id, current[0])
            heartbeat_task = asyncio.create_task(
                heartbeat(), name=f"deskpet-recovery-heartbeat:{record.run_id}"
            )
            while True:
                current[0] = await self._uow.renew_recovery(current[0])
                if heartbeat_error:
                    raise heartbeat_error[0]
                try:
                    candidate = await anext(candidates)
                except StopAsyncIteration:
                    break
                await self.consume_candidate(
                    registration, record, candidate, recovery_lease=current[0]
                )
                if heartbeat_error:
                    raise heartbeat_error[0]
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if candidates is not None:
                await candidates.aclose()
            await self._uow.release_recovery(current[0])

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
        if candidate.kind in {"execute_tools", "open_decision", "delegate_run"}:
            if record.persistence_level is PersistenceLevel.EPHEMERAL:
                actor = ActorContext(
                    principal_id=record.context.principal_id,
                    session_id=record.context.session_id,
                    auth_epoch=record.context.auth_epoch,
                    root_run_id=record.context.root_run_id,
                )
                durable = await self._uow.query(
                    RunRef(record.run_id, record.context.session_id), actor
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
            await self.commit_terminal(
                record,
                registration.kind,
                candidate,
                recovery_lease=recovery_lease,
            )
            return False
        if candidate.kind == "execute_tools":
            event = self.event_candidate(registration.kind, candidate)
            assert event is not None
            await self.emit_live(record, event)
            if self._tool_executor is None:
                raise RuntimeError("tool executor is unavailable")
            authorizations = []
            outcome_metadata: list[dict[str, object]] = []
            precomputed = []
            authoritative_statuses: list[OutcomeStatus | None] = []
            refs = candidate.grant_refs or (None,) * len(candidate.calls)
            actor = ActorContext(
                principal_id=record.context.principal_id,
                session_id=record.context.session_id,
                auth_epoch=record.context.auth_epoch,
                root_run_id=record.context.root_run_id,
            )
            for call, context, grant_ref, declared_effectful in zip(
                candidate.calls, candidate.contexts, refs, candidate.effectful
            ):
                if grant_ref is None:
                    consume = None
                else:
                    consume = GrantConsume(
                        grant_id=grant_ref.grant_id,
                        decision_id=grant_ref.decision_id,
                        decision_nonce=grant_ref.decision_nonce,
                        run_id=record.run_id,
                        expected_session_id=record.context.session_id,
                        call_id=call.stable_call_id,
                        effect_id=context.effect_id,
                        tool_name=call.tool_name,
                        args_hash=call.args_hash,
                        capability_hash=context.capability_hash,
                        scope_hash=context.scope_hash,
                        expected_version=grant_ref.version,
                    )
                if declared_effectful:
                    owner = recovery_lease.owner if recovery_lease is not None else "kernel"
                    epoch = recovery_lease.epoch if recovery_lease is not None else 1
                    claim = await self._uow.consume_grant_and_claim_effect(
                        consume,
                        actor,
                        run_id=record.run_id,
                        expected_session_id=record.context.session_id,
                        call_id=call.stable_call_id,
                        effect_id=context.effect_id,
                        tool_name=call.tool_name,
                        args_hash=call.args_hash,
                        capability_hash=context.capability_hash,
                        scope_hash=context.scope_hash,
                        effect_type=call.effect_type,
                        policy={"kind": call.effect_type, "version": call.effect_policy_version},
                        prepared=call.to_dict(),
                        worker_owner=owner,
                        worker_epoch=epoch,
                        recovery_lease=recovery_lease,
                    )
                    authorizations.append(claim.authorization)
                    metadata: dict[str, object] = {"effect_action": claim.action}
                    if claim.action in {"execute", "reconcile"}:
                        metadata["effect_claim"] = {
                            "effect_id": claim.effect_id,
                            "attempt_no": claim.attempt_no,
                            "worker_owner": claim.worker_owner,
                            "worker_epoch": claim.worker_epoch,
                            "effect_version": claim.effect_version,
                        }
                    known = None
                    if claim.action == "reuse":
                        known = await self._uow.read_effect_outcome(
                            run_id=record.run_id, call_id=call.stable_call_id,
                            effect_id=context.effect_id, args_hash=call.args_hash,
                            capability_hash=context.capability_hash,
                            scope_hash=context.scope_hash,
                        )
                    if known is not None:
                        known_status, payload, receipt_ref, artifact_refs = known
                        precomputed.append(NormalizedToolOutcome.from_dict(payload))
                        authoritative_statuses.append(OutcomeStatus(known_status))
                        metadata.update({"receipt_ref": receipt_ref, "artifact_refs": list(artifact_refs)})
                    elif claim.action == "reconcile":
                        late_state, late_outcome = await self._tool_executor.observe_late(
                            context.effect_id
                        )
                        if late_state == "pending":
                            metadata["late_pending"] = True
                            precomputed.append(NormalizedToolOutcome.malformed(
                                "effect is still running under its original physical call"
                            ))
                        else:
                            precomputed.append(late_outcome or NormalizedToolOutcome.malformed(
                                "late effect evidence unavailable after process recovery"
                            ))
                            metadata.update(self._tool_executor.take_metadata(context.effect_id))
                            metadata["reconciliation"] = True
                        authoritative_statuses.append(
                            OutcomeStatus(str(metadata.get("outcome_status") or "unknown"))
                        )
                    elif claim.action == "in_flight":
                        metadata["late_pending"] = True
                        precomputed.append(NormalizedToolOutcome.malformed(
                            "effect is still owned by its original attempt"
                        ))
                        authoritative_statuses.append(OutcomeStatus.UNKNOWN)
                    elif claim.action != "execute":
                        precomputed.append(NormalizedToolOutcome.malformed(
                            f"effect_{claim.action}; canonical reconciliation required"
                        ))
                        authoritative_statuses.append(None)
                    else:
                        precomputed.append(None)
                        authoritative_statuses.append(None)
                    outcome_metadata.append(metadata)
                else:
                    requires_authorization, effectful = self._tool_executor.prepared_policy(call)
                    if effectful:
                        raise ValueError("driver tool policy does not match registry authority")
                    if requires_authorization and consume is None:
                        raise ValueError("authorized tool is missing a grant")
                    authorizations.append(
                        None if consume is None
                        else await self._uow.consume_authorization(consume, actor)
                    )
                    outcome_metadata.append({})
                    precomputed.append(None)
                    authoritative_statuses.append(None)
            for index, (call, declared_effectful) in enumerate(
                zip(candidate.calls, candidate.effectful)
            ):
                action = outcome_metadata[index].get("effect_action")
                if declared_effectful and action == "execute":
                    requires_authorization, effectful = self._tool_executor.prepared_policy(call)
                    if not effectful:
                        raise ValueError("driver tool policy does not match registry authority")
                    if requires_authorization and authorizations[index] is None:
                        raise ValueError("authorized tool is missing a grant")
            outcomes = await self._tool_executor.execute_batch(
                candidate.calls,
                candidate.contexts,
                authorizations=authorizations,
                precomputed=precomputed,
                **self._lease_kwargs(recovery_lease),
            )
            for context, metadata in zip(candidate.contexts, outcome_metadata):
                metadata.update(self._tool_executor.take_metadata(context.effect_id))
            statuses = tuple(
                authoritative or self._tool_executor.outcome_status(call, outcome)
                for call, outcome, authoritative in zip(
                    candidate.calls, outcomes, authoritative_statuses
                )
            )
            ready = tuple(
                index for index, item in enumerate(outcome_metadata)
                if not bool(item.get("late_pending"))
            )
            if not ready:
                return False
            signal_iterator = registration.driver.signal(
                ToolOutcomesSignal(
                    candidate.run_id,
                    candidate.command_id,
                    tuple(outcomes[index] for index in ready),
                    tuple(statuses[index] for index in ready),
                    tuple(candidate.original_indexes[index] for index in ready),
                    tuple(outcome_metadata[index] for index in ready),
                ),
                **self._lease_kwargs(recovery_lease),
            )
            consumed = await self.consume(
                registration,
                record,
                signal_iterator,
                recovery_lease=recovery_lease,
            )
            for index in ready:
                if candidate.effectful[index]:
                    call, context = candidate.calls[index], candidate.contexts[index]
                    settled = await self._uow.read_effect_outcome(
                        run_id=record.run_id, call_id=call.stable_call_id,
                        effect_id=context.effect_id, args_hash=call.args_hash,
                        capability_hash=context.capability_hash,
                        scope_hash=context.scope_hash,
                    )
                    if settled is not None:
                        self._tool_executor.acknowledge_effect(context.effect_id)
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
                payload={"text": candidate.content, "token_kind": candidate.token_kind},
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
                payload={"tools": [call.tool_name for call in candidate.calls]},
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
                payload={"kind": candidate.decision_kind, "prompt": dict(candidate.prompt)},
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
    ) -> None:
        current = await self._query(
            RunRef(record.run_id, record.context.session_id),
            ActorContext(
                principal_id=record.context.principal_id,
                session_id=record.context.session_id,
                auth_epoch=record.context.auth_epoch,
                root_run_id=record.context.root_run_id,
            ),
        )
        if not isinstance(current, RunRecord):
            raise RuntimeError("legacy run cannot be finalized by the new driver")
        status = RunStatus(terminal.status)
        outcome = {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]
        event = await self._finalize(
            current,
            expected_version=current.version,
            status=status,
            event=RunEventCandidate(
                event_key=f"terminal:{record.run_id}",
                kind="final",
                status=outcome,
                driver_kind=driver_kind,
                correlation=dict(terminal.correlation),
                payload={"text": terminal.content},
                error=(
                    None
                    if terminal.error is None
                    else {"code": "driver_failed", "message": terminal.error}
                ),
            ),
            recovery_lease=recovery_lease,
        )
        if current.context.parent_run_id is not None:
            actor = ActorContext(
                principal_id=current.context.principal_id,
                session_id=current.context.session_id,
                auth_epoch=current.context.auth_epoch,
                root_run_id=current.context.root_run_id,
            )
            authoritative = await self._uow.query(
                RunRef(current.run_id, current.context.session_id), actor
            )
            if (
                not isinstance(authoritative, RunRecord)
                or authoritative.status not in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }
            ):
                raise RuntimeError("terminal child did not commit authoritative durable state")
            async with self._live.lock:
                active = self._live.get(current.run_id)
                if active is not None:
                    active.record = authoritative
        await self.emit(event)


__all__ = ["DriverRuntime"]
