"""Product-neutral durable child-run coordination.

Delegate commands are committed before a deterministic child can be created.
The scheduler owns only renewable scheduling leases; durable run ownership
continues to come from ``execution_runs.driver_kind`` and structural links.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from deskpet.execution import (
    AttachmentPolicy,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildSignalRecord,
    ExecutionUnitOfWork,
    PersistenceLevel,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunRecord,
    RunStatus,
    delegate_idempotency_key,
    fingerprint_json,
)
from deskpet.execution.contracts import thaw_json
from deskpet.harness.ports import (
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DriverEvent,
    DriverSignal,
    JoinPolicy,
)


def _stable_digest(kind: str, parent_run_id: str, command_id: str) -> str:
    return hashlib.sha256(
        f"{kind}|{parent_run_id}|{command_id}".encode("utf-8")
    ).hexdigest()


def stable_child_operation_id(parent_run_id: str, command_id: str) -> str:
    return _stable_digest("execution-child-operation", parent_run_id, command_id)


def stable_child_run_id(parent_run_id: str, command_id: str) -> str:
    return f"child-{_stable_digest('execution-child-run', parent_run_id, command_id)[:32]}"


def attachment_for_join_policy(policy: JoinPolicy | str) -> AttachmentPolicy:
    return {
        JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
        JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
        JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
    }[JoinPolicy(policy)]


def join_policy_for_attachment(policy: AttachmentPolicy | str) -> JoinPolicy:
    return {
        AttachmentPolicy.ATTACHED: JoinPolicy.JOIN_BEFORE_FINAL,
        AttachmentPolicy.ROOT_TERMINAL_CHILD: JoinPolicy.ROOT_TERMINAL_CHILD,
        AttachmentPolicy.DETACHED: JoinPolicy.DETACHED,
    }[AttachmentPolicy(policy)]


class ChildLauncher(Protocol):
    async def accept(self, command: ChildCommandRecord) -> None: ...


@dataclass(frozen=True)
class ChildScheduleAttempt:
    command: ChildCommandRecord
    accepted: bool
    error: str | None = None


@dataclass(frozen=True)
class ChildSignalDelivery:
    record: ChildSignalRecord
    signal: DriverSignal


class ChildRunCoordinator:
    def __init__(self, store: ExecutionUnitOfWork) -> None:
        self._store = store

    @staticmethod
    def _intent(parent: RunRecord, command: DriverEvent) -> ChildCommandIntent:
        if parent.run_id != command.run_id:
            raise ValueError("delegate command names another parent run")
        if parent.persistence_level is not PersistenceLevel.DURABLE:
            raise ValueError("delegate parent must cross a durable boundary first")
        attachment = attachment_for_join_policy(command.join_policy)
        if command.attachment_policy.value != attachment.value:
            raise ValueError("delegate attachment and join policy disagree")
        operation_id = stable_child_operation_id(parent.run_id, command.command_id)
        child_run_id = stable_child_run_id(parent.run_id, command.command_id)
        child_request = thaw_json(command.child_request)
        assert isinstance(child_request, dict)
        capability_subset = tuple(sorted(set(command.capability_subset)))
        capability_snapshot = {"tools": list(capability_subset)}
        capability_ref = fingerprint_json(capability_snapshot)
        context = parent.context
        child_spec = RunCreate(
            run_id=child_run_id,
            idempotency_key=delegate_idempotency_key(
                parent.run_id, command.command_id, command.route_hint
            ),
            context=RunContext(
                session_id=context.session_id,
                root_run_id=context.root_run_id,
                parent_run_id=parent.run_id,
                request_id=f"{context.request_id}:child:{command.command_id}",
                turn_id=context.turn_id,
                venue=context.venue,
                workspace=thaw_json(context.workspace),
                capability_hash=capability_ref,
                provider_plan=thaw_json(context.provider_plan),
                trace_id=f"child-trace-{operation_id[:32]}",
                principal_id=context.principal_id,
                auth_epoch=context.auth_epoch,
            ),
            payload_fingerprint=fingerprint_json(child_request),
            capability_fingerprint=capability_ref,
            driver_kind=str(child_request.get("driver_kind") or "react"),
            profile_key=command.route_hint,
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.QUEUED,
        )
        return ChildCommandIntent(
            operation_id=operation_id,
            parent_run_id=parent.run_id,
            command_id=command.command_id,
            child_spec=child_spec,
            child_request=child_request,
            capability_subset=capability_subset,
            attachment_policy=attachment,
            capability_snapshot_ref=capability_ref,
        )

    async def submit(
        self, parent: RunRecord, command: DriverEvent, *, recovery_lease: RecoveryLease | None = None
    ) -> ChildCommandRecord:
        return await self._store.commit_child_command(
            self._intent(parent, command), recovery_lease=recovery_lease
        )

    async def run_scheduler_once(
        self,
        launcher: ChildLauncher,
        *,
        owner: str,
        limit: int = 16,
        lease_seconds: float = 30.0,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildScheduleAttempt, ...]:
        leased = await self._store.lease_child_commands(
            owner=owner, limit=limit, lease_seconds=lease_seconds,
            parent_run_id=(recovery_lease.run_id if recovery_lease is not None else None),
            recovery_lease=recovery_lease,
        )
        attempts: list[ChildScheduleAttempt] = []
        for command in leased:
            scheduled = await self._store.schedule_child_command(
                command.operation_id,
                lease_owner=owner,
                lease_epoch=command.schedule_lease_epoch,
                recovery_lease=recovery_lease,
            )
            try:
                await launcher.accept(scheduled)
            except Exception as exc:  # another sibling must still be schedulable
                attempts.append(
                    ChildScheduleAttempt(
                        scheduled,
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            acknowledged = await self._store.acknowledge_child_command(
                scheduled.operation_id,
                lease_owner=owner,
                lease_epoch=scheduled.schedule_lease_epoch,
                recovery_lease=recovery_lease,
            )
            attempts.append(ChildScheduleAttempt(acknowledged, True))
        return tuple(attempts)

    async def record_terminal(self, operation_id: str, *, status: str,
                              value: Any = None, recovery_lease: RecoveryLease | None = None) -> ChildSignalRecord:
        return await self._store.record_child_terminal(
            operation_id, terminal_status=status, value=value,
            recovery_lease=recovery_lease,
        )

    async def pending_signals(self, parent_run_id: str, *,
                              recovery_lease: RecoveryLease | None = None) -> tuple[ChildSignalDelivery, ...]:
        records = await self._store.list_pending_child_signals(
            parent_run_id, recovery_lease=recovery_lease
        )
        deliveries: list[ChildSignalDelivery] = []
        for record in records:
            if record.kind == "accepted":
                signal: DriverSignal = ChildAcceptedSignal(
                    record.parent_run_id,
                    record.command_id,
                    record.child_run_id,
                    record.signal_id,
                )
            else:
                signal = ChildTerminalSignal(
                    record.parent_run_id,
                    record.command_id,
                    record.child_run_id,
                    str(record.payload["status"]),
                    record.payload.get("value"),
                    record.signal_id,
                )
            deliveries.append(ChildSignalDelivery(record, signal))
        return tuple(deliveries)

    async def acknowledge_signal(self, signal_id: str, *, recovery_lease: RecoveryLease | None = None) -> ChildSignalRecord:
        return await self._store.acknowledge_child_signal(
            signal_id, recovery_lease=recovery_lease
        )


__all__ = [
    "ChildLauncher",
    "ChildRunCoordinator",
    "ChildScheduleAttempt",
    "ChildSignalDelivery",
    "attachment_for_join_policy",
    "join_policy_for_attachment",
    "stable_child_operation_id",
    "stable_child_run_id",
]
