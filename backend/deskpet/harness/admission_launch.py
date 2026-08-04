from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from deskpet.execution.contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchClaim,
    AdmissionLaunchUnknownFence,
    AdmissionPhase,
    JsonValue,
    OutcomeStatus,
    PersistenceLevel,
    RunCreate,
    RunEventCandidate,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    fingerprint_json,
)
from deskpet.execution.fences import release_run_execution_fence
from deskpet.execution.uow_ports import AdmissionUnitOfWork
from deskpet.permissions.admission import admission_completion_state

from .contracts import PreparedRunContextV1, RegisteredDriver
from .ports import DriverStart, DriverTerminalCandidate
from .runtime import DriverRuntime
from .user_continuations import UserContinuationCoordinator


def _run_identity(spec: RunCreate) -> tuple[object, ...]:
    return (
        spec.run_id, spec.idempotency_key,
        spec.context.session_id, spec.context.root_run_id,
        spec.context.request_id, spec.context.turn_id,
        spec.driver_kind, spec.profile_key, spec.capability_fingerprint,
    )


def build_driver_start(
    spec: RunCreate,
    *,
    text: str,
    payload: Mapping[str, JsonValue],
    canonical_messages: tuple[Mapping[str, JsonValue], ...] = (),
    capabilities: Sequence[str] = (),
    association_event: RunEventCandidate | None = None,
    run_start_snapshot=None,
    prepared_run_context: PreparedRunContextV1 | None = None,
) -> DriverStart:
    stored_capability_snapshot = None
    if run_start_snapshot is not None:
        stored_spec = RunCreate.from_dict(json.loads(run_start_snapshot.run_spec_json))
        if _run_identity(stored_spec) != _run_identity(spec):
            raise ValueError("RunStart RunCreate identity differs from execution row")
        # The execution row intentionally projects only mutable ledger columns.
        # Host-only Companion owner fields come from the immutable RunStart contract.
        spec = stored_spec
        stored_capability_snapshot = json.loads(run_start_snapshot.capability_snapshot_json)
        if (
            fingerprint_json(stored_capability_snapshot)
            != run_start_snapshot.capability_snapshot_hash
        ):
            raise ValueError("RunStart capability snapshot hash mismatch")
    prepared_capability_snapshot = (
        None
        if prepared_run_context is None or not prepared_run_context.capability_snapshot
        else dict(prepared_run_context.capability_snapshot)
    )
    if (
        stored_capability_snapshot is not None
        and prepared_capability_snapshot is not None
        and stored_capability_snapshot != prepared_capability_snapshot
    ):
        raise ValueError("prepared capability snapshot differs from RunStart")
    capability_snapshot = (
        stored_capability_snapshot
        or prepared_capability_snapshot
        or {
            "capabilities": sorted(capabilities),
            "capability_hash": spec.capability_fingerprint,
        }
    )
    if capability_snapshot.get("capability_hash") != spec.capability_fingerprint:
        raise ValueError("Driver capability snapshot differs from RunCreate")
    return DriverStart(
        run_id=spec.run_id,
        session_id=spec.context.session_id,
        canonical_messages=canonical_messages or ({"role": "user", "content": text},),
        provider_state=dict(spec.context.provider_plan),
        tool_set_snapshot_ref=(
            str(payload["tool_set_snapshot_ref"])
            if payload.get("tool_set_snapshot_ref")
            else None
        ),
        run_context=spec.context,
        run_spec=spec,
        association_event=association_event,
        profile_key=spec.profile_key,
        request_payload={"text": text, **dict(payload)},
        capability_snapshot=capability_snapshot,
        run_start_snapshot=run_start_snapshot,
        prepared_run_context=prepared_run_context,
    )


def admission_from(continuation) -> AdmissionBoundary | None:
    value = None if continuation is None else continuation.payload.get("_admission")
    return AdmissionBoundary.from_dict(value) if isinstance(value, Mapping) else None


def build_admission_start(spec: RunCreate, boundary: AdmissionBoundary,
                          claim: AdmissionLaunchClaim) -> DriverStart:
    return DriverStart(
        run_id=spec.run_id,
        session_id=spec.context.session_id,
        canonical_messages=boundary.canonical_messages,
        session_projection_cursor=boundary.session_projection_cursor,
        prepared_context_ref=boundary.prepared_context_ref,
        tool_set_snapshot_ref=boundary.tool_set_snapshot_ref,
        provider_state=dict(spec.context.provider_plan),
        run_context=spec.context,
        run_spec=spec,
        association_event=(
            RunEventCandidate.from_dict(boundary.association_event)
            if boundary.association_event
            else None
        ),
        profile_key=spec.profile_key,
        request_payload=boundary.request_payload,
        capability_snapshot=boundary.capability_snapshot,
        completion_state=admission_completion_state(boundary.boundary_version, boundary.request_payload),
        launch_operation_id=boundary.launch_operation_id,
        provider_launch_snapshot=boundary.provider_snapshot,
        admission_launch=claim,
    )


@dataclass(slots=True)
class AdmissionLauncher:
    """Owns admission launch mechanics while RunKernel remains the authority."""

    uow: AdmissionUnitOfWork
    live: Any
    lock: Any
    runtime: DriverRuntime
    continuations: UserContinuationCoordinator
    recovery_owner: str
    query: Callable[[RunRef, ActorContext], Awaitable[RunRecord]]
    acquire_run_execution_fence: Callable[[str], Awaitable[Any]]
    terminal_deliveries: Callable[[RunRecord], Awaitable[Sequence[Any]]]
    notify_terminal: Callable[[RunRecord, Any], Awaitable[None]]

    async def start(self, record: RunRecord, registration: RegisteredDriver,
                    actor: ActorContext) -> None:
        async with self.lock:
            active = self.live.get(record.run_id) or self.live.add(record.run_id, actor)
        async with active.start_lock:
            async with self.lock:
                if active.task is not None and not active.task.done():
                    return
            record = await self.uow.query(RunRef(record.run_id, record.context.session_id), actor)
            boundary = admission_from(await self.uow.load_continuation(record.run_id))
            if boundary is None or boundary.consumed:
                return
            if (
                record.status in TERMINAL_RUN_STATUSES
                or record.status is RunStatus.CANCEL_REQUESTED
                or boundary.phase
                not in {
                    AdmissionPhase.ACCEPTED_START_PENDING,
                    AdmissionPhase.LAUNCH_CLAIMED,
                }
            ):
                return
            if (
                boundary.phase is AdmissionPhase.LAUNCH_CLAIMED
                and not boundary.provider_snapshot.supports_idempotent_launch
            ):
                await self.fail_unknown(record, boundary)
                return
            lease = await self.uow.recovery_scope(
                record.run_id, owner=self.recovery_owner
            )
            claim = await self.uow.claim_admission_launch(
                lease,
                expected_boundary_version=boundary.boundary_version
                - (boundary.phase is AdmissionPhase.LAUNCH_CLAIMED),
            )
            start = build_admission_start(record.spec, claim.boundary, claim)
            operation = self.consume(
                registration,
                record,
                lambda current: registration.driver.start(
                    replace(
                        start,
                        admission_launch=replace(claim, recovery_lease=current),
                    )
                ),
                lease,
            )
            async with self.lock:
                active.record = record
                active.task = asyncio.create_task(
                    self.continuations.run_owned(
                        active,
                        operation,
                        RunRef(record.run_id, record.context.session_id),
                        actor,
                    ),
                    name=f"deskpet-admission:{record.run_id}",
                )
            await asyncio.sleep(0)

    async def consume(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        stream,
        lease,
    ) -> None:
        try:
            await self.runtime.consume_fenced(
                registration,
                record,
                stream,
                lease,
                release=True,
                driver_handoff=True,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            current = admission_from(
                await self.uow.load_continuation(record.run_id)
            )
            if current is None or (
                current.consumed and current.phase is not AdmissionPhase.LAUNCHED
            ):
                return
            fresh = await self.query(
                RunRef(record.run_id, record.context.session_id),
                record.context.actor(),
            )
            if current.phase is AdmissionPhase.LAUNCH_CLAIMED:
                if not current.provider_snapshot.supports_idempotent_launch:
                    await self.fail_unknown(fresh, current)
                return
            try:
                terminal_lease = await self.uow.recovery_scope(
                    record.run_id, owner=self.recovery_owner
                )
                await self.runtime.commit_terminal(
                    fresh,
                    registration.kind,
                    DriverTerminalCandidate(
                        fresh.run_id,
                        "failed",
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                    recovery_lease=terminal_lease,
                )
            except TerminalConflict:
                return

    async def fail_unknown(
        self,
        record: RunRecord,
        boundary: AdmissionBoundary,
    ) -> None:
        lease = await self.uow.recovery_scope(
            record.run_id, owner=self.recovery_owner
        )
        event = RunEventCandidate(
            event_key="run:final",
            kind="run.final",
            status=OutcomeStatus.FAILED,
            driver_kind=record.spec.driver_kind,
            payload={
                "error_code": "launch_outcome_unknown",
                "retry_safe": False,
                "launch_operation_id": boundary.launch_operation_id,
            },
            error={
                "code": "launch_outcome_unknown",
                "message": "provider launch outcome is unknown",
            },
        )
        execution_fence = await self.acquire_run_execution_fence(record.run_id)
        try:
            result = await self.uow.commit_run_outcome(
                record.run_id,
                expected_version=record.version,
                terminal_status=RunStatus.FAILED,
                event=event,
                deliveries=await self.terminal_deliveries(record),
                recovery_lease=lease,
                admission_failure=AdmissionLaunchUnknownFence(
                    record.run_id,
                    boundary.decision_id,
                    boundary.launch_operation_id,
                    boundary.boundary_version,
                ),
            )
        finally:
            await release_run_execution_fence(execution_fence)
        await self.notify_terminal(result.record, result.event)
        await self.runtime.emit(result.event)
