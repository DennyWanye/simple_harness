# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Settlement of workflow spawn continuations (stale catalog, graph, parent terminal)."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from simple_harness.contracts import CallId, JsonValue, canonical_json
from simple_harness.execution.effects import EffectState
from simple_harness.execution.uow import FaultHook, UnitOfWorkConflict
from simple_harness.runtime.orchestration import (
    VerifiedWorkflowGraphUnavailable,
    WorkflowLaunchRequest,
    WorkflowLaunchTicket,
    WorkflowSpawnContinuationClaim,
    WorkflowSpawnContinuationReady,
)
from simple_harness.tools.contracts import ToolResult
from simple_harness.workflow.execution_ports import WorkflowTerminalOutcome, WorkflowTransaction

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import (
        VerifiedWorkflowGraphUnavailable,
        WorkflowLaunchRequest,
        WorkflowLaunchTicket,
        WorkflowSpawnContinuationClaim,
        WorkflowSpawnContinuationReady,
    )
    from simple_harness.workflow.execution_ports import WorkflowTerminalOutcome
from .core import ExecutionUnitOfWorkCore, _SqliteWorkflowTransaction
from .support import (
    _fault,
    _time,
    _tool_result_json,
    _workflow_launch_ticket,
    _workflow_spawn_activation_hash,
    _workflow_spawn_completion_hash,
    _workflow_spawn_continuation_claim,
    _workflow_spawn_continuation_ready,
    _workflow_spawn_failure_evidence,
    _workflow_spawn_ready_activation,
)


class WorkflowSpawnSettleMixin(ExecutionUnitOfWorkCore):
    """Settlement of workflow spawn continuations (stale catalog, graph, parent terminal)."""

    __slots__ = ()

    if TYPE_CHECKING:

        @classmethod
        def _verify_workflow_terminal(
            cls, connection: sqlite3.Connection, outcome: object
        ) -> bool: ...
        async def read_issued(
            self, transaction: WorkflowTransaction, request_key: str
        ) -> tuple[WorkflowLaunchTicket, WorkflowLaunchRequest] | None: ...
        async def read_spawn_continuation_outcome(
            self, transaction: WorkflowTransaction, spawn_operation_id: str
        ) -> ToolResult | None: ...

    async def settle_spawn_continuation_catalog_stale(
        self,
        transaction: WorkflowTransaction,
        continuation: WorkflowSpawnContinuationClaim,
        ready: WorkflowSpawnContinuationReady | None,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ToolResult:
        tx = self._assert_open_workflow_transaction(transaction)
        existing = await self.read_spawn_continuation_outcome(
            transaction, continuation.spawn_operation_id
        )
        if existing is not None:
            return existing
        now = _time(now)
        row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (continuation.spawn_operation_id,),
        ).fetchone()
        if (
            row is None
            or str(row["state"]) != "claimed"
            or _workflow_spawn_continuation_claim(row) != continuation
        ):
            raise UnitOfWorkConflict("workflow spawn continuation claim differs")
        activation: sqlite3.Row | None = None
        path_kind = "direct"
        if ready is not None:
            ready_row = tx.connection.execute(
                "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
                (ready.ready_receipt_id,),
            ).fetchone()
            activation = tx.connection.execute(
                """
                SELECT * FROM workflow_spawn_ready_activations
                WHERE ready_receipt_id=? AND state='active'
                """,
                (ready.ready_receipt_id,),
            ).fetchone()
            if (
                ready_row is None
                or _workflow_spawn_continuation_ready(ready_row) != ready
                or ready_row["consumed_at"] is None
                or activation is None
                or _workflow_spawn_ready_activation(tx.connection, activation).continuation_claim
                != continuation
            ):
                raise UnitOfWorkConflict("workflow spawn recovery settlement authority differs")
            path_kind = "ready_recovery"
        ticket = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (continuation.ticket_receipt_id,),
        ).fetchone()
        catalog = tx.connection.execute(
            "SELECT * FROM workflow_catalog_authorities LIMIT 1"
        ).fetchone()
        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(row["effect_id"]),),
        ).fetchone()
        if ticket is None or catalog is None or effect is None:
            raise UnitOfWorkConflict("workflow spawn settlement authority is missing")
        ticket_catalog = (
            int(ticket["catalog_generation"]),
            int(ticket["catalog_authority_version"]),
            str(ticket["catalog_hash"]),
        )
        observed_catalog = (
            int(catalog["generation"]),
            int(catalog["version"]),
            str(catalog["catalog_hash"]),
        )
        if ticket_catalog == observed_catalog:
            raise UnitOfWorkConflict("workflow launch catalog is not stale")
        if (
            str(effect["state"]) not in {EffectState.HANDED_OFF.value, EffectState.UNKNOWN.value}
            or str(effect["run_id"]) != continuation.parent_run_id
            or str(effect["request_hash"]) != str(row["effect_request_hash"])
            or int(effect["handoff_attempt"]) != int(row["handoff_attempt"])
        ):
            raise UnitOfWorkConflict("workflow spawn Effect settlement authority differs")
        result = ToolResult.failed(
            CallId(str(effect["call_id"])),
            "workflow_catalog_stale",
            "The workflow catalog changed before the child Run was admitted.",
        )
        result_json = _tool_result_json(result)
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        evidence: dict[str, JsonValue] = {
            "kind": "catalog_stale",
            "ticket_catalog_generation": ticket_catalog[0],
            "ticket_catalog_version": ticket_catalog[1],
            "ticket_catalog_hash": ticket_catalog[2],
            "observed_catalog_authority_id": str(catalog["authority_id"]),
            "observed_catalog_generation": observed_catalog[0],
            "observed_catalog_version": observed_catalog[1],
            "observed_catalog_hash": observed_catalog[2],
        }
        evidence_json = canonical_json(evidence)
        evidence_hash = hashlib.sha256(evidence_json.encode()).hexdigest()
        completion_receipt_id = self._derived_id(
            "workflow-spawn/completion/v1", continuation.spawn_operation_id
        )
        completion_values: dict[str, object] = {
            "completion_receipt_id": completion_receipt_id,
            "spawn_operation_id": continuation.spawn_operation_id,
            "ticket_receipt_id": continuation.ticket_receipt_id,
            "parent_run_id": continuation.parent_run_id,
            "path_kind": path_kind,
            "effect_id": str(row["effect_id"]),
            "handoff_attempt": int(row["handoff_attempt"]),
            "effect_request_hash": str(row["effect_request_hash"]),
            "issue_authority_hash": str(row["issue_authority_hash"]),
            "tool_result_json": result_json,
            "tool_result_hash": result_hash,
            "child_runtime_start_receipt_id": None,
            "failure_evidence_kind": "catalog_stale",
            "failure_evidence_id": self._derived_id(
                "workflow-spawn/failure-evidence/v1", evidence_hash
            ),
            "failure_evidence_json": evidence_json,
            "failure_evidence_hash": evidence_hash,
            "activation_chain_head_id": (
                None if activation is None else str(activation["activation_receipt_id"])
            ),
            "child_wait_receipt_id": None,
            "created_at": now,
        }
        completion_hash = _workflow_spawn_completion_hash(completion_values)
        _fault(fault, "workflow:spawn_catalog_stale:before_effect_write")
        changed = tx.connection.execute(
            """
            UPDATE execution_effects
            SET state='failed',result_json=?,evidence_ref=?,settled_at=?,version=version+1
            WHERE effect_id=? AND state IN ('handed_off','unknown') AND version=?
            """,
            (
                result_json,
                str(completion_values["failure_evidence_id"]),
                now,
                str(row["effect_id"]),
                int(effect["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn Effect settlement CAS failed")
        _fault(fault, "workflow:spawn_catalog_stale:after_effect_write")
        if activation is not None:
            consumed_version = int(activation["version"]) + 1
            consumed_hash = _workflow_spawn_activation_hash(
                activation_receipt_id=str(activation["activation_receipt_id"]),
                ready_receipt_id=str(activation["ready_receipt_id"]),
                spawn_operation_id=str(activation["spawn_operation_id"]),
                parent_run_id=str(activation["parent_run_id"]),
                effect_id=str(activation["effect_id"]),
                owner_id=str(activation["owner_id"]),
                runtime_lease_epoch=int(activation["runtime_lease_epoch"]),
                run_fence_epoch=int(activation["run_fence_epoch"]),
                workflow_lease_epoch=(
                    None
                    if activation["workflow_lease_epoch"] is None
                    else int(activation["workflow_lease_epoch"])
                ),
                continuation_claim_epoch=int(activation["continuation_claim_epoch"]),
                predecessor_activation_receipt_id=(
                    None
                    if activation["predecessor_activation_receipt_id"] is None
                    else str(activation["predecessor_activation_receipt_id"])
                ),
                version=consumed_version,
            )
            changed = tx.connection.execute(
                """
                UPDATE workflow_spawn_ready_activations
                SET state='consumed',version=?,canonical_hash=?,consumed_at=?
                WHERE activation_receipt_id=? AND state='active' AND version=?
                """,
                (
                    consumed_version,
                    consumed_hash,
                    now,
                    str(activation["activation_receipt_id"]),
                    int(activation["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn recovery activation consume CAS failed")
            _fault(fault, "workflow:spawn_catalog_stale:after_activation_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_completion_receipts(
                completion_receipt_id,spawn_operation_id,ticket_receipt_id,
                parent_run_id,path_kind,effect_id,handoff_attempt,
                effect_request_hash,issue_authority_hash,tool_result_json,
                tool_result_hash,child_runtime_start_receipt_id,
                failure_evidence_kind,failure_evidence_id,failure_evidence_json,
                failure_evidence_hash,activation_chain_head_id,
                child_wait_receipt_id,canonical_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,NULL,?,?)
            """,
            (
                completion_receipt_id,
                continuation.spawn_operation_id,
                continuation.ticket_receipt_id,
                continuation.parent_run_id,
                path_kind,
                str(row["effect_id"]),
                int(row["handoff_attempt"]),
                str(row["effect_request_hash"]),
                str(row["issue_authority_hash"]),
                result_json,
                result_hash,
                "catalog_stale",
                str(completion_values["failure_evidence_id"]),
                evidence_json,
                evidence_hash,
                (None if activation is None else str(activation["activation_receipt_id"])),
                completion_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_catalog_stale:after_completion_write")
        changed = tx.connection.execute(
            """
            UPDATE workflow_spawn_continuations
            SET state='completed',completion_receipt_id=?,
                completion_path_kind=?,version=version+1,updated_at=?
            WHERE operation_id=? AND state='claimed' AND version=?
            """,
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
        _fault(fault, "workflow:spawn_catalog_stale:after_continuation_write")
        tx.register_after_commit_fault("workflow:spawn_catalog_stale:after_commit")
        return result

    async def settle_spawn_continuation_graph_unavailable(
        self,
        transaction: WorkflowTransaction,
        continuation: WorkflowSpawnContinuationClaim,
        ready: WorkflowSpawnContinuationReady | None,
        evidence: VerifiedWorkflowGraphUnavailable,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ToolResult:

        tx = self._assert_open_workflow_transaction(transaction)
        existing = await self.read_spawn_continuation_outcome(
            transaction, continuation.spawn_operation_id
        )
        if existing is not None:
            return existing
        if (
            type(evidence) is not VerifiedWorkflowGraphUnavailable
            or not evidence._is_sdk_verified()
        ):
            raise TypeError("graph-unavailable evidence is not SDK verified")
        if ready is None:
            raise UnitOfWorkConflict(
                "graph-unavailable settlement requires ready recovery authority"
            )
        now = _time(now)
        row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (continuation.spawn_operation_id,),
        ).fetchone()
        ready_row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
            (ready.ready_receipt_id,),
        ).fetchone()
        activation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations "
            "WHERE ready_receipt_id=? AND state='active'",
            (ready.ready_receipt_id,),
        ).fetchone()
        if (
            row is None
            or str(row["state"]) != "claimed"
            or _workflow_spawn_continuation_claim(row) != continuation
            or ready_row is None
            or _workflow_spawn_continuation_ready(ready_row) != ready
            or ready_row["consumed_at"] is None
            or activation is None
            or _workflow_spawn_ready_activation(tx.connection, activation).continuation_claim
            != continuation
        ):
            raise UnitOfWorkConflict("workflow spawn graph settlement authority differs")
        ticket = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (continuation.ticket_receipt_id,),
        ).fetchone()
        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(row["effect_id"]),),
        ).fetchone()
        if ticket is None or effect is None:
            raise UnitOfWorkConflict("workflow spawn graph settlement facts are missing")
        workflow_epoch = (
            None
            if activation["workflow_lease_epoch"] is None
            else int(activation["workflow_lease_epoch"])
        )
        proof_fields = (
            evidence.ticket_receipt_id,
            evidence.profile_key,
            evidence.workflow_name,
            evidence.workflow_version,
            evidence.expected_implementation_hash,
            evidence.activation_receipt_id,
            evidence.parent_run_id,
            evidence.owner_id,
            evidence.runtime_lease_epoch,
            evidence.run_fence_epoch,
            evidence.workflow_lease_epoch,
            evidence.continuation_claim_epoch,
        )
        durable_fields = (
            str(ticket["ticket_receipt_id"]),
            str(ticket["profile_key"]),
            str(ticket["workflow_name"]),
            str(ticket["workflow_version"]),
            str(ticket["implementation_fingerprint"]),
            str(activation["activation_receipt_id"]),
            str(activation["parent_run_id"]),
            str(activation["owner_id"]),
            int(activation["runtime_lease_epoch"]),
            int(activation["run_fence_epoch"]),
            workflow_epoch,
            int(activation["continuation_claim_epoch"]),
        )
        if (
            proof_fields != durable_fields
            or evidence.observed_kind not in {"missing", "drift"}
            or (
                evidence.observed_kind == "missing"
                and evidence.observed_implementation_hash is not None
            )
            or (
                evidence.observed_kind == "drift"
                and (
                    evidence.observed_implementation_hash is None
                    or evidence.observed_implementation_hash
                    == evidence.expected_implementation_hash
                )
            )
            or str(effect["state"]) not in {EffectState.HANDED_OFF.value, EffectState.UNKNOWN.value}
            or str(effect["run_id"]) != continuation.parent_run_id
            or str(effect["request_hash"]) != str(row["effect_request_hash"])
            or int(effect["handoff_attempt"]) != int(row["handoff_attempt"])
        ):
            raise UnitOfWorkConflict("workflow graph-unavailable evidence differs")

        result = ToolResult.failed(
            CallId(str(effect["call_id"])),
            "graph_version_unavailable",
            "The pinned workflow graph version is unavailable.",
        )
        result_json = _tool_result_json(result)
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        evidence_value: dict[str, JsonValue] = {
            "kind": "graph_version_unavailable",
            "ticket_receipt_id": evidence.ticket_receipt_id,
            "profile_key": evidence.profile_key,
            "workflow_name": evidence.workflow_name,
            "workflow_version": evidence.workflow_version,
            "expected_implementation_hash": evidence.expected_implementation_hash,
            "registry_content_digest": evidence.registry_content_digest,
            "activation_receipt_id": evidence.activation_receipt_id,
            "parent_run_id": evidence.parent_run_id,
            "owner_id": evidence.owner_id,
            "runtime_lease_epoch": evidence.runtime_lease_epoch,
            "run_fence_epoch": evidence.run_fence_epoch,
            "workflow_lease_epoch": evidence.workflow_lease_epoch,
            "continuation_claim_epoch": evidence.continuation_claim_epoch,
            "observed_kind": evidence.observed_kind,
            "observed_implementation_hash": evidence.observed_implementation_hash,
        }
        evidence_json = canonical_json(evidence_value)
        evidence_hash = hashlib.sha256(evidence_json.encode()).hexdigest()
        completion_receipt_id = self._derived_id(
            "workflow-spawn/completion/v1", continuation.spawn_operation_id
        )
        completion_values: dict[str, object] = {
            "completion_receipt_id": completion_receipt_id,
            "spawn_operation_id": continuation.spawn_operation_id,
            "ticket_receipt_id": continuation.ticket_receipt_id,
            "parent_run_id": continuation.parent_run_id,
            "path_kind": "ready_recovery",
            "effect_id": str(row["effect_id"]),
            "handoff_attempt": int(row["handoff_attempt"]),
            "effect_request_hash": str(row["effect_request_hash"]),
            "issue_authority_hash": str(row["issue_authority_hash"]),
            "tool_result_json": result_json,
            "tool_result_hash": result_hash,
            "child_runtime_start_receipt_id": None,
            "failure_evidence_kind": "graph_version_unavailable",
            "failure_evidence_id": self._derived_id(
                "workflow-spawn/failure-evidence/v1", evidence_hash
            ),
            "failure_evidence_json": evidence_json,
            "failure_evidence_hash": evidence_hash,
            "activation_chain_head_id": str(activation["activation_receipt_id"]),
            "child_wait_receipt_id": None,
            "created_at": now,
        }
        completion_hash = _workflow_spawn_completion_hash(completion_values)
        _fault(fault, "workflow:spawn_graph_unavailable:before_effect_write")
        changed = tx.connection.execute(
            "UPDATE execution_effects SET state='failed',result_json=?,evidence_ref=?,"
            "settled_at=?,version=version+1 WHERE effect_id=? "
            "AND state IN ('handed_off','unknown') AND version=?",
            (
                result_json,
                str(completion_values["failure_evidence_id"]),
                now,
                str(row["effect_id"]),
                int(effect["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn Effect settlement CAS failed")
        _fault(fault, "workflow:spawn_graph_unavailable:after_effect_write")
        consumed_version = int(activation["version"]) + 1
        consumed_hash = _workflow_spawn_activation_hash(
            activation_receipt_id=str(activation["activation_receipt_id"]),
            ready_receipt_id=str(activation["ready_receipt_id"]),
            spawn_operation_id=str(activation["spawn_operation_id"]),
            parent_run_id=str(activation["parent_run_id"]),
            effect_id=str(activation["effect_id"]),
            owner_id=str(activation["owner_id"]),
            runtime_lease_epoch=int(activation["runtime_lease_epoch"]),
            run_fence_epoch=int(activation["run_fence_epoch"]),
            workflow_lease_epoch=workflow_epoch,
            continuation_claim_epoch=int(activation["continuation_claim_epoch"]),
            predecessor_activation_receipt_id=(
                None
                if activation["predecessor_activation_receipt_id"] is None
                else str(activation["predecessor_activation_receipt_id"])
            ),
            version=consumed_version,
        )
        changed = tx.connection.execute(
            "UPDATE workflow_spawn_ready_activations SET state='consumed',version=?,"
            "canonical_hash=?,consumed_at=? WHERE activation_receipt_id=? "
            "AND state='active' AND version=?",
            (
                consumed_version,
                consumed_hash,
                now,
                str(activation["activation_receipt_id"]),
                int(activation["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn recovery activation consume CAS failed")
        _fault(fault, "workflow:spawn_graph_unavailable:after_activation_write")
        tx.connection.execute(
            "INSERT INTO workflow_spawn_completion_receipts("
            "completion_receipt_id,spawn_operation_id,ticket_receipt_id,parent_run_id,"
            "path_kind,effect_id,handoff_attempt,effect_request_hash,"
            "issue_authority_hash,tool_result_json,tool_result_hash,"
            "child_runtime_start_receipt_id,failure_evidence_kind,"
            "failure_evidence_id,failure_evidence_json,failure_evidence_hash,"
            "activation_chain_head_id,child_wait_receipt_id,canonical_hash,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,NULL,?,?)",
            (
                completion_receipt_id,
                continuation.spawn_operation_id,
                continuation.ticket_receipt_id,
                continuation.parent_run_id,
                "ready_recovery",
                str(row["effect_id"]),
                int(row["handoff_attempt"]),
                str(row["effect_request_hash"]),
                str(row["issue_authority_hash"]),
                result_json,
                result_hash,
                "graph_version_unavailable",
                str(completion_values["failure_evidence_id"]),
                evidence_json,
                evidence_hash,
                str(activation["activation_receipt_id"]),
                completion_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_graph_unavailable:after_completion_write")
        changed = tx.connection.execute(
            "UPDATE workflow_spawn_continuations SET state='completed',"
            "completion_receipt_id=?,completion_path_kind='ready_recovery',"
            "version=version+1,updated_at=? WHERE operation_id=? "
            "AND state='claimed' AND version=?",
            (
                completion_receipt_id,
                now,
                continuation.spawn_operation_id,
                continuation.version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn completion CAS failed")
        _fault(fault, "workflow:spawn_graph_unavailable:after_continuation_write")
        tx.register_after_commit_fault("workflow:spawn_graph_unavailable:after_commit")
        return result

    async def settle_spawn_continuation_for_parent_terminal(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        ready_or_continuation: WorkflowSpawnContinuationReady | WorkflowSpawnContinuationClaim,
        parent_terminal_snapshot: WorkflowTerminalOutcome,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ToolResult:

        tx = self._assert_open_workflow_transaction(transaction)
        if not isinstance(
            ready_or_continuation,
            (WorkflowSpawnContinuationClaim, WorkflowSpawnContinuationReady),
        ):
            raise TypeError("ready_or_continuation must be durable workflow spawn evidence")
        if not isinstance(parent_terminal_snapshot, WorkflowTerminalOutcome):
            raise TypeError("parent_terminal_snapshot must be a WorkflowTerminalOutcome")
        operation_id = ready_or_continuation.spawn_operation_id
        existing = await self.read_spawn_continuation_outcome(transaction, operation_id)
        if existing is not None:
            return self._verify_parent_terminal_replay(
                tx,
                operation_id=operation_id,
                ticket=ticket,
                ready_or_continuation=ready_or_continuation,
                parent_terminal_snapshot=parent_terminal_snapshot,
                existing=existing,
            )

        now = _time(now)
        if not self._verify_workflow_terminal(tx.connection, parent_terminal_snapshot):
            raise UnitOfWorkConflict("workflow spawn parent terminal receipt is forged or stale")
        continuation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        issued = await self.read_issued(transaction, operation_id)
        if continuation is None or issued is None or issued[0] != ticket:
            raise UnitOfWorkConflict("workflow spawn parent-terminal authority is missing")
        request = issued[1]
        if (
            str(continuation["state"]) not in {"pending", "claimed"}
            or str(continuation["ticket_receipt_id"]) != ticket.ticket_receipt_id
            or str(continuation["parent_run_id"]) != parent_terminal_snapshot.run_id
            or request.spawn_origin.parent_run_id != parent_terminal_snapshot.run_id
            or request.request_key != operation_id
        ):
            raise UnitOfWorkConflict("workflow spawn parent-terminal identity differs")

        path_kind, ready_row, blocker, activation = self._parent_terminal_recovery_path(
            tx,
            operation_id=operation_id,
            ticket=ticket,
            ready_or_continuation=ready_or_continuation,
            parent_terminal_snapshot=parent_terminal_snapshot,
            continuation=continuation,
        )

        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(continuation["effect_id"]),),
        ).fetchone()
        if (
            effect is None
            or str(effect["state"]) not in {EffectState.HANDED_OFF.value, EffectState.UNKNOWN.value}
            or str(effect["run_id"]) != parent_terminal_snapshot.run_id
            or str(effect["request_hash"]) != str(continuation["effect_request_hash"])
            or int(effect["handoff_attempt"]) != int(continuation["handoff_attempt"])
        ):
            raise UnitOfWorkConflict("workflow spawn parent-terminal Effect authority differs")

        result = ToolResult.failed(
            CallId(str(effect["call_id"])),
            "workflow_parent_terminal_before_spawn",
            "The parent Run became terminal before the child Run was admitted.",
        )
        result_json = _tool_result_json(result)
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        failure_evidence: dict[str, JsonValue] = {
            "kind": "workflow_parent_terminal_before_spawn",
            "terminal_receipt_id": parent_terminal_snapshot.receipt_id,
            "terminal_state": parent_terminal_snapshot.state,
            "terminal_outcome_hash": parent_terminal_snapshot.outcome_hash,
        }
        evidence_json = canonical_json(failure_evidence)
        evidence_hash = hashlib.sha256(evidence_json.encode()).hexdigest()
        evidence_id = self._derived_id("workflow-spawn/failure-evidence/v1", evidence_hash)
        completion_receipt_id = self._derived_id("workflow-spawn/completion/v1", operation_id)
        completion_values: dict[str, object] = {
            "completion_receipt_id": completion_receipt_id,
            "spawn_operation_id": operation_id,
            "ticket_receipt_id": ticket.ticket_receipt_id,
            "parent_run_id": parent_terminal_snapshot.run_id,
            "path_kind": path_kind,
            "effect_id": str(continuation["effect_id"]),
            "handoff_attempt": int(continuation["handoff_attempt"]),
            "effect_request_hash": str(continuation["effect_request_hash"]),
            "issue_authority_hash": str(continuation["issue_authority_hash"]),
            "tool_result_json": result_json,
            "tool_result_hash": result_hash,
            "child_runtime_start_receipt_id": None,
            "failure_evidence_kind": failure_evidence["kind"],
            "failure_evidence_id": evidence_id,
            "failure_evidence_json": evidence_json,
            "failure_evidence_hash": evidence_hash,
            "activation_chain_head_id": (
                None if activation is None else str(activation["activation_receipt_id"])
            ),
            "child_wait_receipt_id": None,
            "created_at": now,
        }
        completion_hash = _workflow_spawn_completion_hash(completion_values)

        _fault(fault, "workflow:spawn_parent_terminal:before_effect_write")
        changed = tx.connection.execute(
            "UPDATE execution_effects SET state='failed',result_json=?,"
            "evidence_ref=?,settled_at=?,version=version+1 WHERE effect_id=? "
            "AND state IN ('handed_off','unknown') AND version=?",
            (
                result_json,
                evidence_id,
                now,
                str(continuation["effect_id"]),
                int(effect["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn parent-terminal Effect CAS failed")
        _fault(fault, "workflow:spawn_parent_terminal:after_effect_write")

        self._release_parent_terminal_recovery_rows(
            tx, ready_row=ready_row, blocker=blocker, activation=activation, now=now, fault=fault
        )

        _fault(fault, "workflow:spawn_parent_terminal:before_completion_write")
        tx.connection.execute(
            "INSERT INTO workflow_spawn_completion_receipts("
            "completion_receipt_id,spawn_operation_id,ticket_receipt_id,parent_run_id,"
            "path_kind,effect_id,handoff_attempt,effect_request_hash,"
            "issue_authority_hash,tool_result_json,tool_result_hash,"
            "child_runtime_start_receipt_id,failure_evidence_kind,"
            "failure_evidence_id,failure_evidence_json,failure_evidence_hash,"
            "activation_chain_head_id,child_wait_receipt_id,canonical_hash,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,NULL,?,?)",
            (
                completion_receipt_id,
                operation_id,
                ticket.ticket_receipt_id,
                parent_terminal_snapshot.run_id,
                path_kind,
                str(continuation["effect_id"]),
                int(continuation["handoff_attempt"]),
                str(continuation["effect_request_hash"]),
                str(continuation["issue_authority_hash"]),
                result_json,
                result_hash,
                str(failure_evidence["kind"]),
                evidence_id,
                evidence_json,
                evidence_hash,
                completion_values["activation_chain_head_id"],
                completion_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_parent_terminal:after_completion_write")
        _fault(fault, "workflow:spawn_parent_terminal:before_continuation_write")
        changed = tx.connection.execute(
            "UPDATE workflow_spawn_continuations SET state='completed',"
            "completion_receipt_id=?,completion_path_kind=?,version=version+1,"
            "updated_at=? WHERE operation_id=? AND state IN ('pending','claimed') "
            "AND version=?",
            (
                completion_receipt_id,
                path_kind,
                now,
                operation_id,
                int(continuation["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn parent-terminal continuation CAS failed")
        _fault(fault, "workflow:spawn_parent_terminal:after_continuation_write")
        tx.register_after_commit_fault("workflow:spawn_parent_terminal:after_commit")
        return result

    def _verify_parent_terminal_replay(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        operation_id: str,
        ticket: WorkflowLaunchTicket,
        ready_or_continuation: WorkflowSpawnContinuationReady | WorkflowSpawnContinuationClaim,
        parent_terminal_snapshot: WorkflowTerminalOutcome,
        existing: ToolResult,
    ) -> ToolResult:
        completion = tx.connection.execute(
            "SELECT * FROM workflow_spawn_completion_receipts WHERE spawn_operation_id=?",
            (operation_id,),
        ).fetchone()
        if completion is None:
            raise UnitOfWorkConflict("workflow spawn terminal completion disappeared")
        path_kind = str(completion["path_kind"])
        if path_kind.startswith("parent_terminal_"):
            evidence = _workflow_spawn_failure_evidence(completion)
            replay_identity = (
                evidence.get("terminal_receipt_id"),
                evidence.get("terminal_state"),
                evidence.get("terminal_outcome_hash"),
            )
            requested_identity = (
                parent_terminal_snapshot.receipt_id,
                parent_terminal_snapshot.state,
                parent_terminal_snapshot.outcome_hash,
            )
            durable_ticket = tx.connection.execute(
                "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
                (ticket.ticket_receipt_id,),
            ).fetchone()
            if (
                replay_identity != requested_identity
                or durable_ticket is None
                or _workflow_launch_ticket(durable_ticket) != ticket
                or ticket.ticket_receipt_id != str(completion["ticket_receipt_id"])
            ):
                raise UnitOfWorkConflict("workflow spawn parent-terminal replay evidence differs")
            replay_ready_row = tx.connection.execute(
                "SELECT * FROM workflow_spawn_continuation_ready WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if path_kind == "parent_terminal_ticket_only":
                continuation_row = tx.connection.execute(
                    "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if (
                    not isinstance(
                        ready_or_continuation,
                        WorkflowSpawnContinuationClaim,
                    )
                    or continuation_row is None
                    or ready_or_continuation.version + 1 != int(continuation_row["version"])
                    or (
                        ready_or_continuation.spawn_operation_id,
                        ready_or_continuation.ticket_receipt_id,
                        ready_or_continuation.parent_run_id,
                        ready_or_continuation.owner_id,
                        ready_or_continuation.runtime_lease_epoch,
                        ready_or_continuation.run_fence_epoch,
                        ready_or_continuation.workflow_lease_epoch,
                        ready_or_continuation.claim_epoch,
                        ready_or_continuation.expires_at,
                    )
                    != (
                        str(continuation_row["operation_id"]),
                        str(continuation_row["ticket_receipt_id"]),
                        str(continuation_row["parent_run_id"]),
                        str(continuation_row["owner_id"]),
                        int(continuation_row["runtime_lease_epoch"]),
                        int(continuation_row["run_fence_epoch"]),
                        (
                            None
                            if continuation_row["workflow_lease_epoch"] is None
                            else int(continuation_row["workflow_lease_epoch"])
                        ),
                        int(continuation_row["claim_epoch"]),
                        float(continuation_row["expires_at"]),
                    )
                ):
                    raise UnitOfWorkConflict(
                        "workflow spawn parent-terminal replay continuation differs"
                    )
            elif (
                not isinstance(
                    ready_or_continuation,
                    WorkflowSpawnContinuationReady,
                )
                or replay_ready_row is None
                or _workflow_spawn_continuation_ready(replay_ready_row) != ready_or_continuation
            ):
                raise UnitOfWorkConflict("workflow spawn parent-terminal replay ready differs")
        return existing

    def _parent_terminal_recovery_path(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        operation_id: str,
        ticket: WorkflowLaunchTicket,
        ready_or_continuation: WorkflowSpawnContinuationReady | WorkflowSpawnContinuationClaim,
        parent_terminal_snapshot: WorkflowTerminalOutcome,
        continuation: sqlite3.Row,
    ) -> tuple[str, sqlite3.Row | None, sqlite3.Row | None, sqlite3.Row | None]:
        ready_rows = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready "
            "WHERE operation_id=? ORDER BY ready_receipt_id",
            (operation_id,),
        ).fetchall()
        activation_rows = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations "
            "WHERE spawn_operation_id=? ORDER BY created_at,activation_receipt_id",
            (operation_id,),
        ).fetchall()
        active_rows = [row for row in activation_rows if str(row["state"]) == "active"]
        ready_row: sqlite3.Row | None = None
        blocker: sqlite3.Row | None = None
        activation: sqlite3.Row | None = None
        if isinstance(ready_or_continuation, WorkflowSpawnContinuationClaim):
            if (
                str(continuation["state"]) != "claimed"
                or _workflow_spawn_continuation_claim(continuation) != ready_or_continuation
                or ready_rows
                or activation_rows
            ):
                raise UnitOfWorkConflict("workflow spawn ticket-only terminal authority differs")
            path_kind = "parent_terminal_ticket_only"
        else:
            if len(ready_rows) != 1:
                raise UnitOfWorkConflict("workflow spawn parent-terminal ready shape differs")
            ready_row = ready_rows[0]
            assert ready_row is not None
            if (
                _workflow_spawn_continuation_ready(ready_row) != ready_or_continuation
                or str(ready_row["ticket_receipt_id"]) != ticket.ticket_receipt_id
                or str(ready_row["effect_id"]) != str(continuation["effect_id"])
                or int(ready_row["handoff_attempt"]) != int(continuation["handoff_attempt"])
            ):
                raise UnitOfWorkConflict("workflow spawn parent-terminal ready evidence differs")
            if not activation_rows:
                if ready_row["consumed_at"] is not None or active_rows:
                    raise UnitOfWorkConflict("workflow spawn unactivated terminal shape differs")
                blockers = tx.connection.execute(
                    "SELECT * FROM run_wait_blockers WHERE run_id=? "
                    "AND kind='tool' AND ledger_identity=? AND handoff_attempt=? "
                    "AND wake_consumed=0 AND superseded_by IS NULL "
                    "ORDER BY blocker_id",
                    (
                        parent_terminal_snapshot.run_id,
                        str(ready_row["effect_id"]),
                        int(ready_row["handoff_attempt"]),
                    ),
                ).fetchall()
                if len(blockers) != 1:
                    raise UnitOfWorkConflict("workflow spawn parent-terminal blocker differs")
                blocker = blockers[0]
                path_kind = "parent_terminal_ready_unactivated"
            else:
                if (
                    ready_row["consumed_at"] is None
                    or len(active_rows) != 1
                    or active_rows[0] is not activation_rows[-1]
                    or str(continuation["state"]) != "claimed"
                ):
                    raise UnitOfWorkConflict("workflow spawn activated terminal shape differs")
                predecessor: str | None = None
                for index, row in enumerate(activation_rows):
                    expected_state = "active" if index == len(activation_rows) - 1 else "superseded"
                    if (
                        str(row["ready_receipt_id"]) != str(ready_row["ready_receipt_id"])
                        or str(row["parent_run_id"]) != parent_terminal_snapshot.run_id
                        or str(row["effect_id"]) != str(continuation["effect_id"])
                        or row["predecessor_activation_receipt_id"] != predecessor
                        or str(row["state"]) != expected_state
                        or str(row["canonical_hash"])
                        != _workflow_spawn_activation_hash(
                            activation_receipt_id=str(row["activation_receipt_id"]),
                            ready_receipt_id=str(row["ready_receipt_id"]),
                            spawn_operation_id=str(row["spawn_operation_id"]),
                            parent_run_id=str(row["parent_run_id"]),
                            effect_id=str(row["effect_id"]),
                            owner_id=str(row["owner_id"]),
                            runtime_lease_epoch=int(row["runtime_lease_epoch"]),
                            run_fence_epoch=int(row["run_fence_epoch"]),
                            workflow_lease_epoch=(
                                None
                                if row["workflow_lease_epoch"] is None
                                else int(row["workflow_lease_epoch"])
                            ),
                            continuation_claim_epoch=int(row["continuation_claim_epoch"]),
                            predecessor_activation_receipt_id=predecessor,
                            version=int(row["version"]),
                        )
                    ):
                        raise UnitOfWorkConflict(
                            "workflow spawn parent-terminal activation chain differs"
                        )
                    predecessor = str(row["activation_receipt_id"])
                activation = active_rows[0]
                assert activation is not None
                if (
                    str(activation["owner_id"]) != str(continuation["owner_id"])
                    or int(activation["runtime_lease_epoch"])
                    != int(continuation["runtime_lease_epoch"])
                    or int(activation["run_fence_epoch"]) != int(continuation["run_fence_epoch"])
                    or activation["workflow_lease_epoch"] != continuation["workflow_lease_epoch"]
                    or int(activation["continuation_claim_epoch"])
                    != int(continuation["claim_epoch"])
                ):
                    raise UnitOfWorkConflict("workflow spawn active terminal claim differs")
                path_kind = "parent_terminal_activated"
        return path_kind, ready_row, blocker, activation

    def _release_parent_terminal_recovery_rows(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        ready_row: sqlite3.Row | None,
        blocker: sqlite3.Row | None,
        activation: sqlite3.Row | None,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        if activation is not None:
            _fault(fault, "workflow:spawn_parent_terminal:before_activation_write")
            consumed_version = int(activation["version"]) + 1
            consumed_hash = _workflow_spawn_activation_hash(
                activation_receipt_id=str(activation["activation_receipt_id"]),
                ready_receipt_id=str(activation["ready_receipt_id"]),
                spawn_operation_id=str(activation["spawn_operation_id"]),
                parent_run_id=str(activation["parent_run_id"]),
                effect_id=str(activation["effect_id"]),
                owner_id=str(activation["owner_id"]),
                runtime_lease_epoch=int(activation["runtime_lease_epoch"]),
                run_fence_epoch=int(activation["run_fence_epoch"]),
                workflow_lease_epoch=(
                    None
                    if activation["workflow_lease_epoch"] is None
                    else int(activation["workflow_lease_epoch"])
                ),
                continuation_claim_epoch=int(activation["continuation_claim_epoch"]),
                predecessor_activation_receipt_id=(
                    None
                    if activation["predecessor_activation_receipt_id"] is None
                    else str(activation["predecessor_activation_receipt_id"])
                ),
                version=consumed_version,
            )
            changed = tx.connection.execute(
                "UPDATE workflow_spawn_ready_activations SET state='consumed',"
                "version=?,canonical_hash=?,consumed_at=? "
                "WHERE activation_receipt_id=? AND state='active' AND version=?",
                (
                    consumed_version,
                    consumed_hash,
                    now,
                    str(activation["activation_receipt_id"]),
                    int(activation["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn parent-terminal activation CAS failed")
            _fault(fault, "workflow:spawn_parent_terminal:after_activation_write")

        if ready_row is not None and activation is None:
            _fault(fault, "workflow:spawn_parent_terminal:before_ready_write")
            changed = tx.connection.execute(
                "UPDATE workflow_spawn_continuation_ready SET consumed_at=? "
                "WHERE ready_receipt_id=? AND consumed_at IS NULL",
                (now, str(ready_row["ready_receipt_id"])),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn parent-terminal ready CAS failed")
            _fault(fault, "workflow:spawn_parent_terminal:after_ready_write")
        if blocker is not None:
            assert ready_row is not None
            _fault(fault, "workflow:spawn_parent_terminal:before_blocker_write")
            changed = tx.connection.execute(
                "UPDATE run_wait_blockers SET wake_consumed=1,consumed_at=?,"
                "superseded_by=?,version=version+1 WHERE blocker_id=? "
                "AND wake_consumed=0 AND superseded_by IS NULL AND version=?",
                (
                    now,
                    str(ready_row["ready_receipt_id"]),
                    str(blocker["blocker_id"]),
                    int(blocker["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn parent-terminal blocker CAS failed")
            _fault(fault, "workflow:spawn_parent_terminal:after_blocker_write")
