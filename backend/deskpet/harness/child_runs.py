"""Durable child commands and parent-signal replay."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Protocol

from deskpet.execution.contracts import (
    AttachmentPolicy,
    ChildCommandIntent,
    ChildCommandRecord,
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
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.ports import (
    DriverEvent,
    DriverSignal,
    JoinPolicy,
)


class ChildLauncher(Protocol):
    async def accept(self, command: ChildCommandRecord) -> None: ...

    async def deliver(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> None: ...


class ChildRunCoordinator:
    def __init__(self, store: ExecutionUnitOfWork) -> None:
        self._store = store
        self._wakeup = asyncio.Event()

    @staticmethod
    def _intent(parent: RunRecord, command: DriverEvent) -> ChildCommandIntent:
        if parent.run_id != command.run_id:
            raise ValueError("delegate command names another parent run")
        if parent.persistence_level is not PersistenceLevel.DURABLE:
            raise ValueError("delegate parent must cross a durable boundary first")
        attachment = {
            JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
            JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
            JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
        }[JoinPolicy(command.join_policy)]
        if command.attachment_policy.value != attachment.value:
            raise ValueError("delegate attachment and join policy disagree")
        operation_id = hashlib.sha256(
            f"execution-child-operation|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()
        child_run_id = "child-" + hashlib.sha256(
            f"execution-child-run|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()[:32]
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
        record = await self._store.commit_child_command(
            self._intent(parent, command), recovery_lease=recovery_lease
        )
        self._wakeup.set()
        return record
