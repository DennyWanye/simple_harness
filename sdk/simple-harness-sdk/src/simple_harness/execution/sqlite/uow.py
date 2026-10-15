# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Atomic SQLite command implementation for root execution lifecycle."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, TypeVar

from simple_harness.contracts import JsonValue
from simple_harness.execution.uow import ExecutionLease, RunRecord
from simple_harness.runtime.orchestration import (
    RuntimeStartDispatchRecord,
    RuntimeStartDispatchState,
)
from simple_harness.workflow.execution_ports import WorkflowTransaction
from simple_harness.workflow.native import TerminalProjectionPrepareReceipt

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import RuntimeStartDispatchRecord
from .uow_domains.admission import StartAdmissionMixin
from .uow_domains.audit import RunAuditMixin
from .uow_domains.base_agent import BaseAgentMixin
from .uow_domains.child_runs import ChildRunMixin
from .uow_domains.child_signals import ChildSignalMixin
from .uow_domains.commands import CommandIngressMixin
from .uow_domains.core import _SqliteWorkflowTransaction
from .uow_domains.delivery import RootTerminalDeliveryMixin
from .uow_domains.effects import EffectMixin
from .uow_domains.fork import WorkflowForkMixin
from .uow_domains.provider_invocations import ProviderInvocationMixin
from .uow_domains.recovery import WorkflowRecoveryMixin
from .uow_domains.runtime_start import RuntimeStartMixin
from .uow_domains.runtime_state import RuntimeStateMixin
from .uow_domains.spawn_activation import WorkflowSpawnActivationMixin
from .uow_domains.spawn_issue import WorkflowSpawnIssueMixin
from .uow_domains.spawn_outcome import WorkflowSpawnOutcomeMixin
from .uow_domains.spawn_settle import WorkflowSpawnSettleMixin
from .uow_domains.support import _fault, _run_record
from .uow_domains.workflow_cancel import WorkflowCancelMixin
from .uow_domains.workflow_resume import WorkflowResumeMixin
from .uow_domains.workflow_start import WorkflowStartMixin

_WorkflowResult = TypeVar("_WorkflowResult")


class SqliteExecutionUnitOfWork(
    RunAuditMixin,
    CommandIngressMixin,
    BaseAgentMixin,
    RuntimeStateMixin,
    RootTerminalDeliveryMixin,
    StartAdmissionMixin,
    ChildRunMixin,
    ChildSignalMixin,
    EffectMixin,
    ProviderInvocationMixin,
    WorkflowStartMixin,
    WorkflowResumeMixin,
    WorkflowCancelMixin,
    WorkflowRecoveryMixin,
    WorkflowForkMixin,
    WorkflowSpawnIssueMixin,
    WorkflowSpawnActivationMixin,
    WorkflowSpawnOutcomeMixin,
    WorkflowSpawnSettleMixin,
    RuntimeStartMixin,
):
    __slots__ = ()

    def close(self) -> None:
        """Close the owned database (idempotent)."""
        self.database.close()

    @property
    def transaction_owner(self) -> object:
        return self.database

    async def run_atomic(
        self,
        operation: Callable[[WorkflowTransaction], Awaitable[_WorkflowResult]],
        *,
        fault_label: str,
    ) -> _WorkflowResult:
        _fault(self.workflow_fault, f"{fault_label}.before_begin")
        with self.database.transaction() as connection:
            transaction = _SqliteWorkflowTransaction(self.database, connection, self.workflow_fault)
            try:
                result = await operation(transaction)
                _fault(self.workflow_fault, f"{fault_label}.before_commit")
            finally:
                transaction.is_open = False
        for point in transaction._after_commit_fault_points:
            _fault(self.workflow_fault, point)
        _fault(self.workflow_fault, f"{fault_label}.after_commit")
        return result

    def read_start_snapshot(self, run_id: str) -> Mapping[str, JsonValue] | None:
        row = self.database.connection.execute(
            "SELECT snapshot_json FROM run_start_snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["snapshot_json"]))
        if not isinstance(value, dict):
            raise TypeError("stored start snapshot is not a JSON object")
        return value

    def list_recoverable_root_runs(self) -> tuple[RunRecord, ...]:
        rows = self.database.connection.execute(
            """
            SELECT * FROM runs
            WHERE parent_run_id IS NULL
              AND state IN ('created', 'queued', 'running', 'cancel_requested')
            ORDER BY created_at ASC, run_id ASC
            """
        ).fetchall()
        return tuple(_run_record(row) for row in rows)

    def list_recoverable_child_runs(self) -> tuple[RunRecord, ...]:
        rows = self.database.connection.execute(
            """
            SELECT * FROM runs
            WHERE parent_run_id IS NOT NULL
              AND state IN ('created', 'queued', 'running', 'cancel_requested')
            ORDER BY created_at ASC, run_id ASC
            """
        ).fetchall()
        return tuple(_run_record(row) for row in rows)

    def read_provider_runtime_lease(self, run_id: str) -> ExecutionLease | None:
        """Current canonical Run lease for admission/recovery; grants never renew it."""
        row = self.database.connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases"
            " WHERE run_id=? AND namespace='runtime.kernel'",
            (run_id,),
        ).fetchone()
        return (
            None
            if row is None
            else ExecutionLease(
                run_id,
                "runtime.kernel",
                str(row["owner_id"]),
                int(row["epoch"]),
                float(row["expires_at"]),
            )
        )


def _runtime_start_dispatch_record(
    row: sqlite3.Row,
) -> RuntimeStartDispatchRecord:

    return RuntimeStartDispatchRecord(
        claim_id=str(row["claim_id"]),
        run_id=str(row["run_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        claim_epoch=int(row["claim_epoch"]),
        expires_at=float(row["expires_at"]),
        version=int(row["version"]),
        state=RuntimeStartDispatchState(str(row["state"])),
    )


def _terminal_projection_prepare(row: sqlite3.Row):  # type: ignore[no-untyped-def]

    return TerminalProjectionPrepareReceipt(
        operation_id=str(row["operation_id"]),
        run_id=str(row["run_id"]),
        terminal_checkpoint_id=str(row["terminal_checkpoint_id"]),
        descriptor_digest=str(row["descriptor_digest"]),
        input_hash=str(row["input_hash"]),
        output=json.loads(str(row["output_json"])),
        output_hash=str(row["output_hash"]),
        blob_refs=tuple(json.loads(str(row["blob_refs_json"]))),
    )


__all__ = ("SqliteExecutionUnitOfWork",)
