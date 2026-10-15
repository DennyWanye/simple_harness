# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Spawn admission: reading and continuing an admitted child spawn, parent suspension and completion records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import CallId, JsonValue, RunId, canonical_json, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.contracts.children import AttachmentPolicy
from simple_harness.execution.effects import EffectState
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.orchestration import (
    RuntimeStartAdmission,
    RuntimeStartDisposition,
    WorkflowLaunchTicket,
    WorkflowSpawnContinuationClaim,
)
from simple_harness.runtime.start_snapshot import StartSnapshot
from simple_harness.runtime.workflow_spawn import (
    ChildStartDispatchRef,
    WorkflowChildWaitBinding,
    WorkflowSpawnChildControlKind,
    _create_workflow_spawn_admission_outcome,
    _create_workflow_spawn_child_control,
    _create_workflow_spawn_result,
    _create_workflow_spawn_tool_outcome,
)
from simple_harness.tools.contracts import ToolOutcome, ToolResult
from simple_harness.workflow.execution_ports import StartAdmissionRequest, WorkflowTransaction

from .core import ExecutionUnitOfWorkCore, _SqliteWorkflowTransaction
from .support import (
    _fault,
    _time,
    _tool_result_json,
    _workflow_spawn_activation_hash,
    _workflow_spawn_completion_hash,
    _workflow_spawn_continuation_claim,
    _workflow_spawn_ready_activation,
)

if TYPE_CHECKING:
    from simple_harness.runtime.context import ContextSnapshot
    from simple_harness.runtime.orchestration import (
        RuntimeActivationClaim,
        RuntimeStartAdmission,
        WorkflowLaunchTicket,
        WorkflowSpawnContinuationClaim,
    )
    from simple_harness.runtime.start_snapshot import RunStart, StartSnapshot
    from simple_harness.runtime.workflow_spawn import (
        WorkflowSpawnAdmissionOutcome,
        WorkflowSpawnToolOutcome,
    )


class WorkflowSpawnAdmissionMixin(ExecutionUnitOfWorkCore):
    """Spawn admission: reading and continuing an admitted child spawn, parent suspension and completion records."""

    __slots__ = ()

    if TYPE_CHECKING:

        async def admit_runtime_start(
            self,
            transaction: WorkflowTransaction,
            ticket: WorkflowLaunchTicket,
            start: RunStart,
            request: StartAdmissionRequest,
            snapshot: StartSnapshot,
            claim: RuntimeActivationClaim,
            *,
            now: float,
            fault: FaultHook | None = None,
        ) -> RuntimeStartAdmission: ...
        async def read_spawn_continuation_outcome(
            self, transaction: WorkflowTransaction, spawn_operation_id: str
        ) -> ToolResult | None: ...

    async def read_spawn_admission_outcome(
        self,
        transaction: WorkflowTransaction,
        spawn_operation_id: str,
    ) -> WorkflowSpawnAdmissionOutcome | None:

        tx = self._assert_open_workflow_transaction(transaction)
        result = await self.read_spawn_continuation_outcome(transaction, spawn_operation_id)
        if result is None:
            return None
        completion = tx.connection.execute(
            "SELECT * FROM workflow_spawn_completion_receipts WHERE spawn_operation_id=?",
            (spawn_operation_id,),
        ).fetchone()
        if (
            completion is None
            or completion["child_runtime_start_receipt_id"] is None
            or completion["child_wait_receipt_id"] is None
            or result.outcome is not ToolOutcome.SUCCEEDED
            or not isinstance(result.value, Mapping)
        ):
            return None
        wait = tx.connection.execute(
            "SELECT * FROM workflow_spawn_child_wait_receipts WHERE parent_wait_receipt_id=?",
            (str(completion["child_wait_receipt_id"]),),
        ).fetchone()
        if wait is None:
            raise UnitOfWorkConflict("workflow spawn child-wait receipt is missing")
        value = thaw_json(result.value)
        if not isinstance(value, dict):
            raise UnitOfWorkConflict("workflow spawn result payload is malformed")
        spawn_result = _create_workflow_spawn_result(
            schema_version=value.get("schema_version"),
            child_run_id=value.get("child_run_id"),
            child_request_id=value.get("child_request_id"),
            parent_run_id=value.get("parent_run_id"),
            root_run_id=value.get("root_run_id"),
            ticket_receipt_id=value.get("ticket_receipt_id"),
            runtime_start_receipt_id=value.get("runtime_start_receipt_id"),
            child_command_id=value.get("child_command_id"),
            attachment_policy=AttachmentPolicy(cast(str, value.get("attachment_policy"))),
        )
        if (
            spawn_result.child_run_id != str(wait["child_run_id"])
            or spawn_result.parent_run_id != str(wait["parent_run_id"])
            or spawn_result.child_command_id != str(wait["child_command_id"])
            or spawn_result.runtime_start_receipt_id
            != str(completion["child_runtime_start_receipt_id"])
        ):
            raise UnitOfWorkConflict("workflow spawn admission result differs")
        child_start_ref = ChildStartDispatchRef(
            child_start_receipt_id=str(wait["child_start_receipt_id"]),
            child_dispatch_claim_id=str(wait["child_dispatch_claim_id"]),
            child_run_id=str(wait["child_run_id"]),
        )
        suspension = WorkflowChildWaitBinding(
            parent_run_id=str(wait["parent_run_id"]),
            child_run_id=str(wait["child_run_id"]),
            child_command_id=str(wait["child_command_id"]),
            parent_wait_receipt_id=str(wait["parent_wait_receipt_id"]),
            expected_parent_version=int(wait["parent_waiting_version"]),
            react_checkpoint_revision=int(wait["react_checkpoint_revision"]),
            expected_signal_domain=str(wait["expected_signal_domain"]),
            source_phase=str(wait["source_phase"]),
            batch_digest=str(wait["batch_digest"]),
            spawn_ordinal=int(wait["spawn_ordinal"]),
            next_tool_ordinal=int(wait["next_tool_ordinal"]),
            spawn_result_append_receipt_id=str(wait["spawn_result_append_receipt_id"]),
            context_revision=int(wait["context_post_revision"]),
            termination_started_at=float(wait["termination_started_at"]),
            termination_last_observed_at=float(wait["termination_last_observed_at"]),
            wall_deadline=(None if wait["wall_deadline"] is None else float(wait["wall_deadline"])),
            termination_policy_snapshot_hash=str(wait["termination_policy_snapshot_hash"]),
        )
        return _create_workflow_spawn_admission_outcome(
            child_start_ref=child_start_ref,
            result=spawn_result,
            suspension=suspension,
        )

    async def continue_spawn_admission(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        continuation: WorkflowSpawnContinuationClaim,
        start: RunStart,
        request: StartAdmissionRequest,
        snapshot: StartSnapshot,
        claim: RuntimeActivationClaim,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnToolOutcome:
        # 按需导入：循环依赖（uow ↔ context）
        from simple_harness.runtime.context import _append_context_in_transaction

        tx = self._assert_open_workflow_transaction(transaction)
        if (
            await self.read_spawn_admission_outcome(transaction, continuation.spawn_operation_id)
            is not None
        ):
            raise UnitOfWorkConflict(
                "workflow spawn admission already committed; use receipt reader"
            )
        now = _time(now)
        (
            continuation_row,
            parent,
            runtime,
            active_activation,
            path_kind,
            activation_chain_head_id,
        ) = self._require_spawn_admission_parent(tx, ticket, continuation, now)

        admission = await self.admit_runtime_start(
            transaction,
            ticket,
            start,
            request,
            snapshot,
            claim,
            now=now,
            fault=fault,
        )
        if (
            admission.disposition
            not in {
                RuntimeStartDisposition.START_NEW,
                RuntimeStartDisposition.START_ORPHAN,
            }
            or admission.dispatch_claim is None
        ):
            raise UnitOfWorkConflict("direct workflow spawn requires a fresh child start authority")
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(continuation_row["effect_id"]),),
        ).fetchone()
        checkpoint = tx.connection.execute(
            "SELECT * FROM workflow_checkpoints WHERE run_id=? AND namespace='react.termination.v1'"
            " ORDER BY version DESC LIMIT 1",
            (continuation.parent_run_id,),
        ).fetchone()
        if ticket_row is None or effect is None or checkpoint is None:
            raise UnitOfWorkConflict("workflow spawn success authority is missing")
        checkpoint_json = str(checkpoint["checkpoint_json"])
        checkpoint_value = json.loads(checkpoint_json)
        if (
            not isinstance(checkpoint_value, dict)
            or checkpoint_value.get("phase") != "tool_batch_reserved"
            or int(checkpoint["version"]) != 0
            or str(effect["state"]) not in {EffectState.HANDED_OFF.value, EffectState.UNKNOWN.value}
            or str(effect["run_id"]) != continuation.parent_run_id
        ):
            raise UnitOfWorkConflict("workflow spawn success phase differs")
        spawn_result = _create_workflow_spawn_result(
            schema_version="workflow_spawn.result.v1",
            child_run_id=admission.receipt.run_id,
            child_request_id=start.request_id.value,
            parent_run_id=continuation.parent_run_id,
            root_run_id=str(parent["root_run_id"]),
            ticket_receipt_id=ticket.ticket_receipt_id,
            runtime_start_receipt_id=admission.receipt.ticket_receipt_id,
            child_command_id=str(ticket_row["child_command_id"]),
            attachment_policy=AttachmentPolicy.ATTACHED,
        )
        tool_result = ToolResult.succeeded(CallId(str(effect["call_id"])), spawn_result.to_json())
        tool_result_json = _tool_result_json(tool_result)
        tool_result_hash = hashlib.sha256(tool_result_json.encode()).hexdigest()
        append_id = self._derived_id(
            "workflow-spawn/context-append/v1", continuation.spawn_operation_id
        )
        context_pre_revision = checkpoint_value.get("context_revision")
        if isinstance(context_pre_revision, bool) or not isinstance(context_pre_revision, int):
            raise UnitOfWorkConflict("workflow spawn Context revision is missing")
        parent_lease = ExecutionLease(
            continuation.parent_run_id,
            RUNTIME_LEASE_NAMESPACE,
            continuation.owner_id,
            continuation.runtime_lease_epoch,
            float(runtime["expires_at"]),
        )
        context = _append_context_in_transaction(
            tx.connection,
            RunId(continuation.parent_run_id),
            parent_lease,
            context_pre_revision,
            append_id,
            (
                Message(
                    MessageRole.TOOL,
                    canonical_json(spawn_result.to_json()),
                    name="workflow_spawn",
                    call_id=CallId(str(effect["raw_call_id"])),
                ),
            ),
            now=now,
        )
        _fault(fault, "workflow:spawn_admission:after_context_write")
        batch_digest = hashlib.sha256(checkpoint_json.encode()).hexdigest()
        next_checkpoint_hash, next_checkpoint_version = self._write_spawn_child_wait_checkpoint(
            tx,
            continuation=continuation,
            admission=admission,
            checkpoint=checkpoint,
            checkpoint_value=checkpoint_value,
            context=context,
            effect=effect,
            now=now,
            fault=fault,
        )
        parent_waiting_version = self._suspend_parent_for_spawn_child(
            tx,
            continuation=continuation,
            parent=parent,
            active_activation=active_activation,
            now=now,
            fault=fault,
        )
        parent_wait_receipt_id = self._derived_id(
            "workflow-spawn/child-wait/v1", continuation.spawn_operation_id
        )
        termination_started_at = float(checkpoint_value["started_at"])
        termination_last_observed_at = float(checkpoint_value["last_observed_at"])
        termination_policy_snapshot_hash = hashlib.sha256(
            canonical_json(
                {
                    "started_at": termination_started_at,
                    "last_observed_at": termination_last_observed_at,
                }
            ).encode()
        ).hexdigest()
        wait_identity: dict[str, JsonValue] = {
            "spawn_operation_id": continuation.spawn_operation_id,
            "parent_run_id": continuation.parent_run_id,
            "child_run_id": admission.receipt.run_id,
            "child_command_id": str(ticket_row["child_command_id"]),
            "parent_wait_receipt_id": parent_wait_receipt_id,
        }
        identity_hash = hashlib.sha256(canonical_json(wait_identity).encode()).hexdigest()
        lifecycle_hash = hashlib.sha256(
            canonical_json(
                {
                    **wait_identity,
                    "state": "unconsumed",
                    "parent_waiting_version": parent_waiting_version,
                    "react_checkpoint_hash": next_checkpoint_hash,
                    "context_post_revision": context.revision,
                }
            ).encode()
        ).hexdigest()
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_child_wait_receipts(
                parent_wait_receipt_id,spawn_operation_id,parent_run_id,child_run_id,
                child_command_id,parent_pre_version,parent_waiting_version,
                react_checkpoint_revision,react_checkpoint_hash,expected_signal_domain,
                source_phase,batch_digest,spawn_ordinal,next_tool_ordinal,
                prior_result_append_receipts_json,raw_tool_call_id,
                spawn_result_append_id,spawn_result_append_receipt_id,
                spawn_tool_message_hash,context_pre_revision,context_post_revision,
                released_runtime_lease_epoch,released_workflow_lease_epoch,
                termination_started_at,termination_last_observed_at,wall_deadline,
                termination_policy_snapshot_hash,released_run_fence_epoch,
                child_start_receipt_id,child_dispatch_claim_id,
                child_runtime_lease_epoch,wake_activation_receipt_id,
                state,version,identity_hash,lifecycle_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'tool_batch_reserved',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unconsumed',0,?,?,?)
            """,  # noqa: E501
            (
                parent_wait_receipt_id,
                continuation.spawn_operation_id,
                continuation.parent_run_id,
                admission.receipt.run_id,
                str(ticket_row["child_command_id"]),
                int(parent["version"]),
                parent_waiting_version,
                next_checkpoint_version,
                next_checkpoint_hash,
                "child.terminal.v1",
                batch_digest,
                int(effect["call_ordinal"]),
                int(effect["call_ordinal"]) + 1,
                "[]",
                str(effect["raw_call_id"]),
                append_id,
                append_id,
                tool_result_hash,
                context_pre_revision,
                context.revision,
                continuation.runtime_lease_epoch,
                continuation.workflow_lease_epoch,
                termination_started_at,
                termination_last_observed_at,
                None,
                termination_policy_snapshot_hash,
                continuation.run_fence_epoch,
                admission.receipt.ticket_receipt_id,
                admission.dispatch_claim.claim_id,
                admission.activation.execution_lease.epoch,  # type: ignore[union-attr]
                activation_chain_head_id,
                identity_hash,
                lifecycle_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_admission:after_child_wait_write")
        self._record_spawn_admission_completion(
            tx,
            continuation=continuation,
            continuation_row=continuation_row,
            admission=admission,
            effect=effect,
            tool_result_json=tool_result_json,
            tool_result_hash=tool_result_hash,
            parent_wait_receipt_id=parent_wait_receipt_id,
            path_kind=path_kind,
            activation_chain_head_id=activation_chain_head_id,
            now=now,
            fault=fault,
        )
        tx.register_after_commit_fault("workflow:spawn_admission:after_commit")
        child_start_ref = ChildStartDispatchRef(
            admission.receipt.ticket_receipt_id,
            admission.dispatch_claim.claim_id,
            admission.receipt.run_id,
        )
        suspension = WorkflowChildWaitBinding(
            parent_run_id=continuation.parent_run_id,
            child_run_id=admission.receipt.run_id,
            child_command_id=str(ticket_row["child_command_id"]),
            parent_wait_receipt_id=parent_wait_receipt_id,
            expected_parent_version=parent_waiting_version,
            react_checkpoint_revision=next_checkpoint_version,
            expected_signal_domain="child.terminal.v1",
            source_phase="tool_batch_reserved",
            batch_digest=batch_digest,
            spawn_ordinal=int(effect["call_ordinal"]),
            next_tool_ordinal=int(effect["call_ordinal"]) + 1,
            spawn_result_append_receipt_id=append_id,
            context_revision=context.revision,
            termination_started_at=termination_started_at,
            termination_last_observed_at=termination_last_observed_at,
            wall_deadline=None,
            termination_policy_snapshot_hash=termination_policy_snapshot_hash,
        )
        control_kind = {
            RuntimeStartDisposition.START_NEW: WorkflowSpawnChildControlKind.START,
            RuntimeStartDisposition.START_ORPHAN: WorkflowSpawnChildControlKind.START,
            RuntimeStartDisposition.RECOVER_START: WorkflowSpawnChildControlKind.RECOVER,
            RuntimeStartDisposition.RECOVER_RESUME: WorkflowSpawnChildControlKind.RECOVER,
            RuntimeStartDisposition.ATTACH_CURRENT: WorkflowSpawnChildControlKind.ATTACH,
            RuntimeStartDisposition.FOREIGN_ACTIVE: WorkflowSpawnChildControlKind.WAITING,
            RuntimeStartDisposition.WAITING: WorkflowSpawnChildControlKind.WAITING,
            RuntimeStartDisposition.CANCEL_PENDING: WorkflowSpawnChildControlKind.CANCEL,
            RuntimeStartDisposition.TERMINAL: WorkflowSpawnChildControlKind.TERMINAL,
        }[admission.disposition]
        child_control = _create_workflow_spawn_child_control(kind=control_kind, admission=admission)
        return _create_workflow_spawn_tool_outcome(
            tool_result=tool_result,
            child_control=child_control,
            child_start_ref=child_start_ref,
            suspension=suspension,
        )

    def _write_spawn_child_wait_checkpoint(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        admission: RuntimeStartAdmission,
        checkpoint: sqlite3.Row,
        checkpoint_value: dict[str, JsonValue],
        context: ContextSnapshot,
        effect: sqlite3.Row,
        now: float,
        fault: FaultHook | None,
    ) -> tuple[str, int]:
        next_checkpoint = dict(checkpoint_value)
        next_checkpoint.update(
            {
                "phase": "child_wait",
                "context_revision": context.revision,
                "tool_result_progress": int(effect["call_ordinal"]) + 1,
                "workflow_spawn_operation_id": continuation.spawn_operation_id,
                "workflow_spawn_child_run_id": admission.receipt.run_id,
            }
        )
        next_checkpoint_json = canonical_json(cast(JsonValue, next_checkpoint))
        next_checkpoint_hash = hashlib.sha256(next_checkpoint_json.encode()).hexdigest()
        next_checkpoint_version = int(checkpoint["version"]) + 1
        tx.connection.execute(
            "INSERT INTO"
            " workflow_checkpoints(checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,lease_epoch,version,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                f"{continuation.parent_run_id}:react.termination.v1:{next_checkpoint_version}",
                continuation.parent_run_id,
                "react.termination.v1",
                next_checkpoint_json,
                next_checkpoint_hash,
                continuation.runtime_lease_epoch,
                next_checkpoint_version,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_admission:after_checkpoint_write")
        return next_checkpoint_hash, next_checkpoint_version

    def _require_spawn_admission_parent(
        self,
        tx: _SqliteWorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        continuation: WorkflowSpawnContinuationClaim,
        now: float,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row | None, str, str | None]:
        continuation_row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (continuation.spawn_operation_id,),
        ).fetchone()
        if (
            continuation_row is None
            or str(continuation_row["state"]) != "claimed"
            or _workflow_spawn_continuation_claim(continuation_row) != continuation
            or continuation.expires_at <= now
            or continuation.ticket_receipt_id != ticket.ticket_receipt_id
        ):
            raise UnitOfWorkConflict("workflow spawn continuation claim differs")
        parent = tx.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (continuation.parent_run_id,),
        ).fetchone()
        runtime = tx.connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (continuation.parent_run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence = tx.connection.execute(
            "SELECT * FROM run_fences WHERE run_id=?",
            (continuation.parent_run_id,),
        ).fetchone()
        if (
            parent is None
            or str(parent["state"]) != RunState.RUNNING.value
            or runtime is None
            or fence is None
            or str(runtime["owner_id"]) != continuation.owner_id
            or int(runtime["epoch"]) != continuation.runtime_lease_epoch
            or float(runtime["expires_at"]) <= now
            or str(fence["state"]) != "active"
            or str(fence["owner_id"]) != continuation.owner_id
            or int(fence["runtime_lease_epoch"]) != continuation.runtime_lease_epoch
            or int(fence["epoch"]) != continuation.run_fence_epoch
        ):
            raise UnitOfWorkConflict("workflow spawn parent authority differs")
        active_activation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations "
            "WHERE spawn_operation_id=? AND state='active'",
            (continuation.spawn_operation_id,),
        ).fetchone()
        path_kind = "direct"
        activation_chain_head_id: str | None = None
        if active_activation is not None:
            durable_activation = _workflow_spawn_ready_activation(tx.connection, active_activation)
            if durable_activation.continuation_claim != continuation:
                raise UnitOfWorkConflict("workflow spawn recovery activation claim differs")
            ready = tx.connection.execute(
                "SELECT consumed_at FROM workflow_spawn_continuation_ready "
                "WHERE ready_receipt_id=?",
                (durable_activation.ready_receipt.ready_receipt_id,),
            ).fetchone()
            if ready is None or ready["consumed_at"] is None:
                raise UnitOfWorkConflict("workflow spawn recovery ready receipt is unconsumed")
            path_kind = "ready_recovery"
            activation_chain_head_id = durable_activation.activation_receipt_id
        return (
            continuation_row,
            parent,
            runtime,
            active_activation,
            path_kind,
            activation_chain_head_id,
        )

    def _suspend_parent_for_spawn_child(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        parent: sqlite3.Row,
        active_activation: sqlite3.Row | None,
        now: float,
        fault: FaultHook | None,
    ) -> int:
        parent_waiting_version = int(parent["version"]) + 1
        changed = tx.connection.execute(
            "UPDATE runs SET state='waiting',version=version+1,updated_at=? WHERE run_id=? AND"
            " state='running' AND version=?",
            (now, continuation.parent_run_id, int(parent["version"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn parent WAITING CAS failed")
        _fault(fault, "workflow:spawn_admission:after_parent_write")
        runtime_deleted = tx.connection.execute(
            "DELETE FROM workflow_leases WHERE run_id=? AND namespace=? AND owner_id=? AND epoch=?",
            (
                continuation.parent_run_id,
                RUNTIME_LEASE_NAMESPACE,
                continuation.owner_id,
                continuation.runtime_lease_epoch,
            ),
        ).rowcount
        if runtime_deleted != 1:
            raise UnitOfWorkConflict("workflow spawn Runtime release CAS failed")
        if continuation.workflow_lease_epoch is not None:
            workflow_deleted = tx.connection.execute(
                "DELETE FROM workflow_leases WHERE run_id=? AND namespace<>? AND owner_id=? AND"
                " epoch=?",
                (
                    continuation.parent_run_id,
                    RUNTIME_LEASE_NAMESPACE,
                    continuation.owner_id,
                    continuation.workflow_lease_epoch,
                ),
            ).rowcount
            if workflow_deleted != 1:
                raise UnitOfWorkConflict("workflow spawn Workflow release CAS failed")
        changed = tx.connection.execute(
            "UPDATE run_fences SET state='released',released_at=? WHERE run_id=? AND owner_id=? AND"
            " runtime_lease_epoch=? AND epoch=? AND state='active'",
            (
                now,
                continuation.parent_run_id,
                continuation.owner_id,
                continuation.runtime_lease_epoch,
                continuation.run_fence_epoch,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn RunFence release CAS failed")
        _fault(fault, "workflow:spawn_admission:after_authority_release")
        if active_activation is not None:
            consumed_version = int(active_activation["version"]) + 1
            consumed_hash = _workflow_spawn_activation_hash(
                activation_receipt_id=str(active_activation["activation_receipt_id"]),
                ready_receipt_id=str(active_activation["ready_receipt_id"]),
                spawn_operation_id=str(active_activation["spawn_operation_id"]),
                parent_run_id=str(active_activation["parent_run_id"]),
                effect_id=str(active_activation["effect_id"]),
                owner_id=str(active_activation["owner_id"]),
                runtime_lease_epoch=int(active_activation["runtime_lease_epoch"]),
                run_fence_epoch=int(active_activation["run_fence_epoch"]),
                workflow_lease_epoch=(
                    None
                    if active_activation["workflow_lease_epoch"] is None
                    else int(active_activation["workflow_lease_epoch"])
                ),
                continuation_claim_epoch=int(active_activation["continuation_claim_epoch"]),
                predecessor_activation_receipt_id=(
                    None
                    if active_activation["predecessor_activation_receipt_id"] is None
                    else str(active_activation["predecessor_activation_receipt_id"])
                ),
                version=consumed_version,
            )
            consumed = tx.connection.execute(
                "UPDATE workflow_spawn_ready_activations "
                "SET state='consumed',version=?,canonical_hash=?,consumed_at=? "
                "WHERE activation_receipt_id=? AND state='active' AND version=?",
                (
                    consumed_version,
                    consumed_hash,
                    now,
                    str(active_activation["activation_receipt_id"]),
                    int(active_activation["version"]),
                ),
            ).rowcount
            if consumed != 1:
                raise UnitOfWorkConflict("workflow spawn recovery activation consume CAS failed")
            _fault(fault, "workflow:spawn_admission:after_activation_write")
        return parent_waiting_version

    def _record_spawn_admission_completion(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        continuation_row: sqlite3.Row,
        admission: RuntimeStartAdmission,
        effect: sqlite3.Row,
        tool_result_json: str,
        tool_result_hash: str,
        parent_wait_receipt_id: str,
        path_kind: str,
        activation_chain_head_id: str | None,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        changed = tx.connection.execute(
            "UPDATE execution_effects SET"
            " state='succeeded',result_json=?,settled_at=?,version=version+1 WHERE effect_id=? AND"
            " state IN ('handed_off','unknown') AND version=?",
            (tool_result_json, now, str(effect["effect_id"]), int(effect["version"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn Effect settlement CAS failed")
        completion_receipt_id = self._derived_id(
            "workflow-spawn/completion/v1", continuation.spawn_operation_id
        )
        completion_values: dict[str, object] = {
            "completion_receipt_id": completion_receipt_id,
            "spawn_operation_id": continuation.spawn_operation_id,
            "ticket_receipt_id": continuation.ticket_receipt_id,
            "parent_run_id": continuation.parent_run_id,
            "path_kind": path_kind,
            "effect_id": str(effect["effect_id"]),
            "handoff_attempt": int(effect["handoff_attempt"]),
            "effect_request_hash": str(effect["request_hash"]),
            "issue_authority_hash": str(continuation_row["issue_authority_hash"]),
            "tool_result_json": tool_result_json,
            "tool_result_hash": tool_result_hash,
            "child_runtime_start_receipt_id": admission.receipt.ticket_receipt_id,
            "failure_evidence_kind": None,
            "failure_evidence_id": None,
            "failure_evidence_json": None,
            "failure_evidence_hash": None,
            "activation_chain_head_id": activation_chain_head_id,
            "child_wait_receipt_id": parent_wait_receipt_id,
            "created_at": now,
        }
        completion_hash = _workflow_spawn_completion_hash(completion_values)
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_completion_receipts(
                completion_receipt_id,spawn_operation_id,ticket_receipt_id,parent_run_id,
                path_kind,effect_id,handoff_attempt,effect_request_hash,
                issue_authority_hash,tool_result_json,tool_result_hash,
                child_runtime_start_receipt_id,failure_evidence_kind,
                failure_evidence_id,failure_evidence_json,failure_evidence_hash,
                activation_chain_head_id,child_wait_receipt_id,canonical_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?,?,?)
            """,
            (
                completion_receipt_id,
                continuation.spawn_operation_id,
                continuation.ticket_receipt_id,
                continuation.parent_run_id,
                path_kind,
                str(effect["effect_id"]),
                int(effect["handoff_attempt"]),
                str(effect["request_hash"]),
                str(continuation_row["issue_authority_hash"]),
                tool_result_json,
                tool_result_hash,
                admission.receipt.ticket_receipt_id,
                activation_chain_head_id,
                parent_wait_receipt_id,
                completion_hash,
                now,
            ),
        )
        changed = tx.connection.execute(
            "UPDATE workflow_spawn_continuations SET"
            " state='completed',completion_receipt_id=?,completion_path_kind=?,version=version+1,updated_at=?"  # noqa: E501
            " WHERE operation_id=? AND state='claimed' AND version=?",
            (
                completion_receipt_id,
                path_kind,
                now,
                continuation.spawn_operation_id,
                continuation.version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn completion CAS failed")
        _fault(fault, "workflow:spawn_admission:after_completion_write")
