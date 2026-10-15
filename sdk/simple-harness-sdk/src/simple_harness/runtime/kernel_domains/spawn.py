# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow spawn control: accepting spawns and registering recovered and started children."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from simple_harness.execution.uow import RunState, UnitOfWorkConflict
from simple_harness.providers import CancelToken

from ..orchestration import RuntimeStartAdmission, RuntimeStartDisposition
from ..workflow_spawn import WorkflowSpawnChildControlKind

if TYPE_CHECKING:
    from simple_harness.runtime.workflow_spawn import WorkflowSpawnToolOutcome
from .core import RuntimeCore


class WorkflowSpawnControlMixin(RuntimeCore):
    """Workflow spawn control: accepting spawns and registering recovered and started children."""

    __slots__ = ()

    async def _accept_workflow_spawn_control(
        self, parent_run_id: str, outcome: WorkflowSpawnToolOutcome
    ) -> None:

        child_control = outcome.child_control
        admission = child_control.admission
        child_run_id = admission.receipt.run_id
        if child_run_id != outcome.child_start_ref.child_run_id:
            raise UnitOfWorkConflict("workflow spawn child control identity differs")
        self._drop_local_authority(parent_run_id)
        if child_control.kind is WorkflowSpawnChildControlKind.START:
            self._register_workflow_child_start(admission)
        elif child_control.kind is WorkflowSpawnChildControlKind.RECOVER:
            self._register_workflow_child_recovery(admission)
        elif child_control.kind is WorkflowSpawnChildControlKind.CANCEL:
            child = self._uow.read_run(child_run_id)
            if child is None or child.state is not RunState.CANCEL_REQUESTED:
                raise UnitOfWorkConflict(
                    "workflow spawn CANCEL control lacks a cancel-pending child"
                )
            await self._terminalize_cancelled(child, reason="workflow_spawn_child_control")
            return
        elif child_control.kind is WorkflowSpawnChildControlKind.TERMINAL:
            if self._uow.read_child_terminal_result_for_run(child_run_id) is None:
                raise UnitOfWorkConflict("workflow spawn TERMINAL control lacks terminal receipt")
            return
        elif child_control.kind in {
            WorkflowSpawnChildControlKind.ATTACH,
            WorkflowSpawnChildControlKind.WAITING,
        }:
            return
        else:  # pragma: no cover - closed enum exhaustiveness
            raise AssertionError(child_control.kind)
        if child_run_id not in self._live.active_run_ids():
            self._schedule(child_run_id)

    def _register_recovered_spawn_child(self, admission: RuntimeStartAdmission) -> bool:
        if admission.disposition in {
            RuntimeStartDisposition.START_NEW,
            RuntimeStartDisposition.START_ORPHAN,
        }:
            self._register_workflow_child_start(admission)
            return True
        if admission.disposition in {
            RuntimeStartDisposition.RECOVER_START,
            RuntimeStartDisposition.RECOVER_RESUME,
        }:
            self._register_workflow_child_recovery(admission)
            return True
        if admission.disposition in {
            RuntimeStartDisposition.FOREIGN_ACTIVE,
            RuntimeStartDisposition.ATTACH_CURRENT,
            RuntimeStartDisposition.WAITING,
            RuntimeStartDisposition.TERMINAL,
        }:
            return False
        if admission.disposition is RuntimeStartDisposition.CANCEL_PENDING:
            # No Driver authority is returned for this disposition.  Let the
            # canonical cancellation coordinator reacquire and converge it.
            return True
        raise UnitOfWorkConflict("workflow child recovery control is not implemented")

    def _register_workflow_child_recovery(self, admission: RuntimeStartAdmission) -> None:
        activation = admission.activation
        recovery_work = admission.recovery_work
        if activation is None or recovery_work is None:
            raise UnitOfWorkConflict("workflow spawn RECOVER control lacks child authority")
        child_run_id = activation.execution_lease.run_id
        self._leases[child_run_id] = activation.execution_lease
        self._fences[child_run_id] = activation.run_fence
        self._workflow_recovery_work[child_run_id] = recovery_work
        self._cancels.setdefault(child_run_id, CancelToken())
        heartbeat = self._heartbeats.get(child_run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[child_run_id] = asyncio.create_task(
                self._heartbeat(child_run_id),
                name=f"simple-harness-heartbeat:{child_run_id}",
            )

    def _register_workflow_child_start(self, admission: RuntimeStartAdmission) -> None:
        activation = admission.activation
        dispatch_claim = admission.dispatch_claim
        if activation is None or dispatch_claim is None:
            raise UnitOfWorkConflict("workflow spawn START control lacks child authority")
        child_run_id = activation.execution_lease.run_id
        self._leases[child_run_id] = activation.execution_lease
        self._fences[child_run_id] = activation.run_fence
        self._workflow_start_dispatches[child_run_id] = dispatch_claim
        self._cancels.setdefault(child_run_id, CancelToken())
        heartbeat = self._heartbeats.get(child_run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[child_run_id] = asyncio.create_task(
                self._heartbeat(child_run_id),
                name=f"simple-harness-heartbeat:{child_run_id}",
            )
