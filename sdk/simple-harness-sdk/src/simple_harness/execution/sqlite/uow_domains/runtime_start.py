# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Runtime start admission, retry wakes and workflow terminal verification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, RunId, canonical_json
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.orchestration import (
    RuntimeStartActivation,
    RuntimeStartAdmission,
    RuntimeStartDispatchClaim,
    RuntimeStartDisposition,
    RuntimeStartReceipt,
    WorkflowLaunchTicket,
)
from simple_harness.runtime.start_snapshot import StartSnapshot
from simple_harness.tools.schema import (
    ArgumentsValidationError,
    SchemaDefinitionError,
    validate_arguments,
)
from simple_harness.workflow.execution_ports import (
    ResumeAdmissionReceipt,
    ResumePhase,
    StartAdmissionRequest,
    StartMode,
    StartPhase,
    WorkflowRecoveryReceiptKind,
    WorkflowRecoveryWork,
    WorkflowRetryWake,
    WorkflowTerminalOutcome,
    WorkflowTransaction,
    start_admission_request_from_json,
)

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import (
        RuntimeActivationClaim,
        RuntimeStartActivation,
        RuntimeStartAdmission,
        RuntimeStartDispatchClaim,
        RuntimeStartReceipt,
        VerifiedWorkflowLaunchTicket,
        WorkflowLaunchTicket,
    )
    from simple_harness.runtime.start_snapshot import RunStart, StartSnapshot
    from simple_harness.workflow.execution_ports import WorkflowTerminalOutcome
from .core import ExecutionUnitOfWorkCore
from .support import (
    _fault,
    _json_object,
    _required,
    _thaw,
    _thaw_json,
    _time,
    _verified_workflow_launch_ticket,
    _workflow_launch_ticket,
)


def _runtime_start_receipt(row: sqlite3.Row) -> RuntimeStartReceipt:

    return RuntimeStartReceipt(
        ticket_receipt_id=str(row["ticket_receipt_id"]),
        run_id=str(row["run_id"]),
        trace_id=str(row["trace_id"]),
        thread_id=str(row["thread_id"]),
        committed_run_version=int(row["committed_run_version"]),
        start_snapshot_hash=str(row["start_snapshot_hash"]),
        workflow_request_hash=str(row["workflow_request_hash"]),
        created_at=float(row["created_at"]),
    )


def _runtime_start_dispatch_claim(
    row: sqlite3.Row,
) -> RuntimeStartDispatchClaim:

    return RuntimeStartDispatchClaim(
        claim_id=str(row["claim_id"]),
        run_id=str(row["run_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        claim_epoch=int(row["claim_epoch"]),
    )


def _runtime_start_activation(runtime: sqlite3.Row, fence: sqlite3.Row) -> RuntimeStartActivation:

    run_id = str(runtime["run_id"])
    return RuntimeStartActivation(
        execution_lease=ExecutionLease(
            run_id=run_id,
            namespace=str(runtime["namespace"]),
            owner_id=str(runtime["owner_id"]),
            epoch=int(runtime["epoch"]),
            expires_at=float(runtime["expires_at"]),
        ),
        run_fence=RunFenceLease(
            run_id=RunId(run_id),
            epoch=int(fence["epoch"]),
            owner_id=str(fence["owner_id"]),
            runtime_lease_epoch=int(fence["runtime_lease_epoch"]),
        ),
    )


def _require_runtime_start_receipt_identity(
    row: sqlite3.Row,
    *,
    verified: VerifiedWorkflowLaunchTicket,
    snapshot_hash: str,
    workflow_request_hash: str,
) -> None:
    expected = (
        verified.ticket_receipt_id,
        verified.resolved_run_id,
        verified.resolved_trace_id,
        verified.resolved_thread_id,
        snapshot_hash,
        workflow_request_hash,
    )
    actual = (
        str(row["ticket_receipt_id"]),
        str(row["run_id"]),
        str(row["trace_id"]),
        str(row["thread_id"]),
        str(row["start_snapshot_hash"]),
        str(row["workflow_request_hash"]),
    )
    if actual != expected:
        raise UnitOfWorkConflict("runtime start receipt differs")


def _validate_runtime_start_binding(
    verified: VerifiedWorkflowLaunchTicket,
    start: RunStart,
    request: StartAdmissionRequest,
    snapshot: StartSnapshot,
) -> None:

    if not verified._is_sdk_verified():
        raise UnitOfWorkConflict("workflow launch ticket verification was forged")
    if not isinstance(snapshot, StartSnapshot):
        raise TypeError("snapshot must be a StartSnapshot")
    start_input_hash = hashlib.sha256(canonical_json(_thaw(start.input)).encode()).hexdigest()
    try:
        validate_arguments(_thaw(start.input), verified.start_input_schema.canonical_schema)
    except (ArgumentsValidationError, SchemaDefinitionError) as exc:
        raise UnitOfWorkConflict("workflow start input violates durable schema") from exc
    expected = (
        verified.session_id,
        verified.request_id,
        verified.resolved_run_id,
        verified.tool_catalog_generation,
        verified.ticket_receipt_id,
        StartMode.PRECREATED,
        verified.session_id,
        verified.request_id,
        verified.turn_id,
        verified.profile_key,
        "workflow",
        verified.tool_catalog_generation,
        verified.workflow_name,
        verified.workflow_version,
        verified.requested_run_id,
        verified.requested_trace_id,
        verified.requested_thread_id,
        verified.resolved_run_id,
        verified.resolved_trace_id,
        verified.resolved_thread_id,
        verified.checkpoint_namespace,
        verified.manifest_hash,
        verified.implementation_fingerprint,
        verified.state_schema_version,
        verified.start_input_schema.schema_ref,
        verified.start_input_schema.schema_hash,
        (
            None
            if verified.terminal_projection_descriptor is None
            else _thaw(verified.terminal_projection_descriptor)
        ),
        verified.terminal_request_factory_hash,
        start_input_hash,
        _thaw(verified.capability_snapshot),
    )
    actual = (
        start.execution_session_id.value,
        start.request_id.value,
        start.run_id.value,
        start.tool_catalog_generation,
        request.request_key,
        request.mode,
        request.session_id,
        request.request_id,
        request.turn_id,
        request.profile_key,
        request.driver_kind,
        request.tool_catalog_generation,
        request.workflow_name,
        request.workflow_version,
        request.requested_run_id,
        request.requested_trace_id,
        request.requested_thread_id,
        request.resolved_run_id,
        request.resolved_trace_id,
        request.resolved_thread_id,
        request.checkpoint_namespace,
        request.manifest_hash,
        request.implementation_hash,
        request.state_schema_version,
        request.start_input_schema_ref,
        request.start_input_schema_hash,
        (
            None
            if request.terminal_projection_descriptor is None
            else _thaw(request.terminal_projection_descriptor)
        ),
        request.terminal_request_factory_hash,
        hashlib.sha256(canonical_json(_thaw(request.start_input)).encode()).hexdigest(),
        _thaw(request.capability_snapshot),
    )
    if actual != expected or start_input_hash != verified.start_input_hash:
        raise UnitOfWorkConflict("Runtime start differs from verified launch ticket")
    if snapshot.workflow_admission != request:
        raise UnitOfWorkConflict("start snapshot workflow admission differs")
    if (
        snapshot.profile_key != verified.profile_key
        or snapshot.driver_kind != "workflow"
        or snapshot.tool_catalog_generation != verified.tool_catalog_generation
        or _thaw_json(snapshot.input) != _thaw(start.input)
    ):
        raise UnitOfWorkConflict("start snapshot differs from verified launch ticket")


class RuntimeStartMixin(ExecutionUnitOfWorkCore):
    """Runtime start admission, retry wakes and workflow terminal verification."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _resume_receipt(
            self,
            row: sqlite3.Row,
            *,
            connection: sqlite3.Connection | None = None,
            include_activation: bool = True,
        ) -> ResumeAdmissionReceipt: ...
        async def verify(
            self, transaction: WorkflowTransaction, ticket: WorkflowLaunchTicket
        ) -> VerifiedWorkflowLaunchTicket: ...

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
    ) -> RuntimeStartAdmission:

        tx = self._assert_open_workflow_transaction(transaction)
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if ticket_row is None or _workflow_launch_ticket(ticket_row) != ticket:
            raise UnitOfWorkConflict("workflow launch ticket is forged or stale")
        verified = _verified_workflow_launch_ticket(ticket_row)
        _validate_runtime_start_binding(verified, start, request, snapshot)
        snapshot_json = canonical_json(snapshot.to_json())
        snapshot_hash = hashlib.sha256(snapshot_json.encode()).hexdigest()
        request_json = canonical_json(self._workflow_request_payload(request))
        workflow_request_hash = hashlib.sha256(request_json.encode()).hexdigest()
        existing_receipt = tx.connection.execute(
            "SELECT * FROM runtime_start_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if existing_receipt is not None:
            _require_runtime_start_receipt_identity(
                existing_receipt,
                verified=verified,
                snapshot_hash=snapshot_hash,
                workflow_request_hash=workflow_request_hash,
            )
            return self._classify_runtime_start_admission(
                tx.connection,
                existing_receipt,
                claim=claim,
                now=now,
                fault=fault,
            )
        # A new generic Run may only be created while the ticket's durable
        # catalog/profile binding is still current.  Existing admissions above
        # are immutable facts and intentionally replay before this current-view
        # check.
        verified = await self.verify(transaction, ticket)
        expires_at = now + float(claim.lease_ttl_seconds)
        _fault(fault, "workflow:runtime_start:before_session_write")
        tx.connection.execute(
            "INSERT OR IGNORE INTO execution_sessions(session_id,user_id,created_at) "
            "VALUES(?,'harness-system',?)",
            (verified.session_id, now),
        )
        _fault(fault, "workflow:runtime_start:after_session_write")
        _fault(fault, "workflow:runtime_start:before_run_write")
        tx.connection.execute(
            "INSERT INTO"
            " runs(run_id,execution_session_id,request_id,root_run_id,parent_run_id,profile_key,driver_kind,state,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,'workflow','running',1,?,?)",
            (
                verified.resolved_run_id,
                verified.session_id,
                verified.request_id,
                verified.root_run_id,
                verified.parent_run_id,
                verified.profile_key,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_run_write")
        _fault(fault, "workflow:runtime_start:before_snapshot_write")
        tx.connection.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json,snapshot_hash,created_at)"
            " VALUES(?,?,?,?)",
            (verified.resolved_run_id, snapshot_json, snapshot_hash, now),
        )
        _fault(fault, "workflow:runtime_start:after_snapshot_write")
        _fault(fault, "workflow:runtime_start:before_child_link_write")
        tx.connection.execute(
            "INSERT INTO run_links(parent_run_id,child_run_id,attachment_policy,created_at)"
            " VALUES(?,?,?,?)",
            (
                verified.parent_run_id,
                verified.resolved_run_id,
                verified.attachment_policy.value,
                now,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_child_link_write")
        _fault(fault, "workflow:runtime_start:before_child_command_write")
        tx.connection.execute(
            "INSERT INTO"
            " child_commands(command_id,parent_run_id,child_run_id,ticket_id,workflow_ticket_receipt_id,state,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,NULL,?,'pending',?,?)",
            (
                verified.child_command_id,
                verified.parent_run_id,
                verified.resolved_run_id,
                verified.ticket_receipt_id,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_child_command_write")
        event_id = f"{verified.resolved_run_id}:workflow-start-created"
        _fault(fault, "workflow:runtime_start:before_event_write")
        self._insert_event(
            tx.connection,
            event_id=event_id,
            run_id=verified.resolved_run_id,
            kind="run.created",
            payload={"profile_key": verified.profile_key, "driver_kind": "workflow"},
            now=now,
        )
        _fault(fault, "workflow:runtime_start:after_event_write")
        _fault(fault, "workflow:runtime_start:before_runtime_lease_write")
        tx.connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?)",
            (
                verified.resolved_run_id,
                RUNTIME_LEASE_NAMESPACE,
                claim.owner_id,
                1,
                expires_at,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_runtime_lease_write")
        _fault(fault, "workflow:runtime_start:before_run_fence_write")
        tx.connection.execute(
            "INSERT INTO"
            " run_fences(run_id,owner_id,runtime_lease_epoch,epoch,state,acquired_at,released_at)"
            " VALUES(?,?,?,?, 'active',?,NULL)",
            (verified.resolved_run_id, claim.owner_id, 1, 1, now),
        )
        _fault(fault, "workflow:runtime_start:after_run_fence_write")
        _fault(fault, "workflow:runtime_start:before_receipt_write")
        tx.connection.execute(
            "INSERT INTO"
            " runtime_start_receipts(ticket_receipt_id,run_id,trace_id,thread_id,committed_run_version,start_snapshot_hash,workflow_request_hash,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,1,?,?,?)",
            (
                ticket.ticket_receipt_id,
                verified.resolved_run_id,
                verified.resolved_trace_id,
                verified.resolved_thread_id,
                snapshot_hash,
                workflow_request_hash,
                now,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_receipt_write")
        claim_id = self._derived_id("runtime-start-dispatch/v1", ticket.ticket_receipt_id)
        _fault(fault, "workflow:runtime_start:before_dispatch_claim_write")
        tx.connection.execute(
            "INSERT INTO"
            " runtime_start_dispatch_claims(claim_id,ticket_receipt_id,run_id,owner_id,runtime_lease_epoch,claim_epoch,expires_at,version,state,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,1,1,?,0,'claimed',?,?)",
            (
                claim_id,
                ticket.ticket_receipt_id,
                verified.resolved_run_id,
                claim.owner_id,
                expires_at,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:runtime_start:after_dispatch_claim_write")
        tx.register_after_commit_fault("workflow:runtime_start:after_commit")
        receipt_row = tx.connection.execute(
            "SELECT * FROM runtime_start_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        assert receipt_row is not None
        execution_lease = ExecutionLease(
            verified.resolved_run_id,
            RUNTIME_LEASE_NAMESPACE,
            claim.owner_id,
            1,
            expires_at,
        )
        fence = RunFenceLease(
            run_id=RunId(verified.resolved_run_id),
            epoch=1,
            owner_id=claim.owner_id,
            runtime_lease_epoch=1,
        )
        return RuntimeStartAdmission(
            receipt=_runtime_start_receipt(receipt_row),
            disposition=RuntimeStartDisposition.START_NEW,
            activation=RuntimeStartActivation(execution_lease, fence),
            dispatch_claim=RuntimeStartDispatchClaim(
                claim_id, verified.resolved_run_id, claim.owner_id, 1, 1
            ),
        )

    async def resume_admitted_runtime_start(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        claim: RuntimeActivationClaim,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> RuntimeStartAdmission:
        tx = self._assert_open_workflow_transaction(transaction)
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        receipt_row = tx.connection.execute(
            "SELECT * FROM runtime_start_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if (
            ticket_row is None
            or _workflow_launch_ticket(ticket_row) != ticket
            or receipt_row is None
        ):
            raise UnitOfWorkConflict("workflow child Runtime admission receipt is missing")
        return self._classify_runtime_start_admission(
            tx.connection,
            receipt_row,
            claim=claim,
            now=_time(now),
            fault=fault,
        )

    async def resume_spawn_child_start(
        self,
        transaction: WorkflowTransaction,
        child_run_id: str,
        claim: RuntimeActivationClaim,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> RuntimeStartAdmission:
        tx = self._assert_open_workflow_transaction(transaction)
        child_run_id = _required(child_run_id, "child_run_id")
        command = tx.connection.execute(
            "SELECT workflow_ticket_receipt_id FROM child_commands WHERE child_run_id=?",
            (child_run_id,),
        ).fetchone()
        if command is None or command["workflow_ticket_receipt_id"] is None:
            raise UnitOfWorkConflict("workflow child has no durable workflow launch ticket")
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (str(command["workflow_ticket_receipt_id"]),),
        ).fetchone()
        if ticket_row is None:
            raise UnitOfWorkConflict("workflow child launch ticket disappeared")
        return await self.resume_admitted_runtime_start(
            transaction,
            _workflow_launch_ticket(ticket_row),
            claim,
            now=now,
            fault=fault,
        )

    def _runtime_retry_wake(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        run_version: int,
        resume_row: sqlite3.Row | None,
    ):

        if resume_row is None or str(resume_row["phase"]) != "retry_wait":
            return None
        receipt = self._resume_receipt(resume_row, connection=connection, include_activation=False)
        if receipt.next_attempt_at is None:
            raise UnitOfWorkConflict("retry wait receipt has no due time")
        try:
            outcome = _json_object(
                json.loads(str(resume_row["outcome_json"])), "retry wait outcome"
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise UnitOfWorkConflict("retry wait outcome is invalid") from exc
        receipt_id = receipt.request.receipt_id
        receipt_version = receipt.version
        mode = receipt.request.mode
        due_at = receipt.next_attempt_at
        try:
            wait_event_id = _required(outcome.get("wait_event_id"), "wait_event_id")
        except ValueError as exc:
            raise UnitOfWorkConflict("retry wait outcome is invalid") from exc
        start = connection.execute(
            "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if start is None:
            raise UnitOfWorkConflict("retry wait lacks pinned start authority")
        start_request = start_admission_request_from_json(
            _json_object(json.loads(str(start["request_json"])), "start request")
        )
        head = connection.execute(
            "SELECT checkpoint_id,checkpoint_json,checkpoint_hash FROM "
            "workflow_checkpoints WHERE run_id=? AND namespace=? "
            "ORDER BY version DESC LIMIT 1",
            (run_id, start_request.checkpoint_namespace),
        ).fetchone()
        if (
            head is None
            or str(head["checkpoint_id"]) != receipt.request.expected_checkpoint_head
            or hashlib.sha256(str(head["checkpoint_json"]).encode()).hexdigest()
            != str(head["checkpoint_hash"])
        ):
            raise UnitOfWorkConflict("retry wait checkpoint authority changed")
        durable_pending = tuple(
            sorted(
                (
                    str(item["decision_id"]),
                    hashlib.sha256(str(item["request_json"]).encode()).hexdigest(),
                )
                for item in connection.execute(
                    "SELECT decision_id,request_json FROM decisions "
                    "WHERE run_id=? AND state!='open'",
                    (run_id,),
                ).fetchall()
            )
        )
        if durable_pending != receipt.request.pending_interrupts:
            raise UnitOfWorkConflict("retry wait interrupt authority changed")
        core: dict[str, JsonValue] = {
            "run_id": run_id,
            "receipt_id": receipt_id,
            "receipt_version": receipt_version,
            "mode": mode.value,
            "due_at": due_at,
            "wait_event_id": wait_event_id,
            "generic_run_version": run_version,
            "request_fingerprint": receipt.request_fingerprint,
            "responses_hash": receipt.request.responses_hash,
            "expected_checkpoint_head": receipt.request.expected_checkpoint_head,
        }
        outcome_hash = hashlib.sha256(canonical_json(core).encode()).hexdigest()
        if (
            outcome.get("status") != "retryable"
            or outcome.get("generic_run_version") != run_version
            or outcome.get("outcome_hash") != outcome_hash
        ):
            raise UnitOfWorkConflict("retry wait outcome binding changed")
        event = connection.execute(
            "SELECT kind,payload_json FROM run_events WHERE run_id=? AND event_id=?",
            (run_id, wait_event_id),
        ).fetchone()
        if (
            event is None
            or str(event["kind"]) != "workflow.retry_waiting"
            or canonical_json(json.loads(str(event["payload_json"]))) != canonical_json(core)
        ):
            raise UnitOfWorkConflict("retry wait event binding changed")
        if (
            connection.execute(
                "SELECT 1 FROM workflow_leases WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone()
            is not None
        ):
            raise UnitOfWorkConflict("retry wait retained an activation lease")
        fence = connection.execute(
            "SELECT state FROM run_fences WHERE run_id=?", (run_id,)
        ).fetchone()
        if fence is None or str(fence["state"]) != "released":
            raise UnitOfWorkConflict("retry wait retained an active Run fence")
        return WorkflowRetryWake(
            run_id,
            receipt_id,
            receipt_version,
            mode,
            due_at,
            wait_event_id,
            run_version,
            outcome_hash,
        )

    @staticmethod
    def _read_workflow_terminal_outcome(connection: sqlite3.Connection, run_id: str):

        row = connection.execute(
            "SELECT * FROM workflow_terminal_receipts WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        delivery_ids_raw = json.loads(str(row["delivery_ids_json"]))
        if not isinstance(delivery_ids_raw, list) or not all(
            isinstance(item, str) for item in delivery_ids_raw
        ):
            raise UnitOfWorkConflict("terminal delivery identity is invalid")
        return WorkflowTerminalOutcome(
            str(row["receipt_id"]),
            run_id,
            str(row["checkpoint_id"]),
            str(row["state"]),
            str(row["event_id"]),
            tuple(delivery_ids_raw),
            str(row["outcome_hash"]),
        )

    def read_workflow_terminal_outcome(self, run_id: str):
        return self._read_workflow_terminal_outcome(self.database.connection, run_id)

    @staticmethod
    def _verify_workflow_terminal_unchecked(
        connection: sqlite3.Connection, outcome: object
    ) -> bool:

        if not isinstance(outcome, WorkflowTerminalOutcome):
            return False
        row = connection.execute(
            "SELECT * FROM workflow_terminal_receipts WHERE receipt_id=? AND run_id=?",
            (outcome.receipt_id, outcome.run_id),
        ).fetchone()
        if row is None:
            return False
        delivery_ids = tuple(json.loads(str(row["delivery_ids_json"])))
        durable = WorkflowTerminalOutcome(
            str(row["receipt_id"]),
            str(row["run_id"]),
            str(row["checkpoint_id"]),
            str(row["state"]),
            str(row["event_id"]),
            delivery_ids,
            str(row["outcome_hash"]),
        )
        if durable != outcome:
            return False
        terminal_payload = json.loads(str(row["terminal_payload_json"]))
        delivery_facts = json.loads(str(row["delivery_facts_json"]))
        if not isinstance(delivery_facts, list):
            return False
        canonical_outcome: dict[str, JsonValue] = {
            "receipt_id": outcome.receipt_id,
            "run_id": outcome.run_id,
            "checkpoint_id": outcome.checkpoint_id,
            "state": outcome.state,
            "event_id": outcome.event_id,
            "delivery_ids": list(outcome.delivery_ids),
            "terminal_payload": terminal_payload,
            "delivery_facts": delivery_facts,
        }
        if (
            hashlib.sha256(canonical_json(canonical_outcome).encode()).hexdigest()
            != outcome.outcome_hash
        ):
            return False
        run = connection.execute(
            "SELECT state,version FROM runs WHERE run_id=?", (outcome.run_id,)
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT checkpoint_id,checkpoint_json,checkpoint_hash,namespace,version "
            "FROM workflow_checkpoints WHERE run_id=? AND namespace=? "
            "ORDER BY version DESC LIMIT 1",
            (outcome.run_id, str(row["checkpoint_namespace"])),
        ).fetchone()
        event = connection.execute(
            "SELECT kind,payload_json FROM run_events WHERE run_id=? AND event_id=?",
            (outcome.run_id, outcome.event_id),
        ).fetchone()
        fence_receipt = connection.execute(
            "SELECT owner_id,runtime_lease_epoch,run_fence_epoch "
            "FROM workflow_terminal_fence_receipts "
            "WHERE receipt_id=? AND run_id=?",
            (str(row["terminal_fence_receipt_id"]), outcome.run_id),
        ).fetchone()
        fence = connection.execute(
            "SELECT owner_id,runtime_lease_epoch,epoch,state FROM run_fences WHERE run_id=?",
            (outcome.run_id,),
        ).fetchone()
        start = connection.execute(
            "SELECT json_extract(request_json,'$.checkpoint_namespace') AS namespace "
            "FROM workflow_start_admissions WHERE run_id=?",
            (outcome.run_id,),
        ).fetchone()
        if (
            run is None
            or str(run["state"]) != outcome.state
            or int(run["version"]) != int(row["run_version"])
            or checkpoint is None
            or str(checkpoint["checkpoint_id"]) != outcome.checkpoint_id
            or str(checkpoint["namespace"]) != str(row["checkpoint_namespace"])
            or int(checkpoint["version"]) != int(row["checkpoint_version"])
            or str(checkpoint["checkpoint_hash"]) != str(row["checkpoint_hash"])
            or hashlib.sha256(str(checkpoint["checkpoint_json"]).encode()).hexdigest()
            != str(checkpoint["checkpoint_hash"])
            or not isinstance(
                checkpoint_payload := json.loads(str(checkpoint["checkpoint_json"])),
                dict,
            )
            or checkpoint_payload.get("checkpoint_id") != outcome.checkpoint_id
            or event is None
            or str(event["kind"])
            != {
                "completed": "run.completed",
                "failed": "run.failed",
                "cancelled": "run.cancelled",
            }.get(outcome.state)
            or hashlib.sha256(str(event["payload_json"]).encode()).hexdigest()
            != str(row["event_payload_hash"])
            or fence_receipt is None
            or fence is None
            or str(fence["state"]) != "released"
            or start is None
            or str(start["namespace"]) != str(row["checkpoint_namespace"])
            or tuple(fence_receipt)
            != (
                str(fence["owner_id"]),
                int(fence["runtime_lease_epoch"]),
                int(fence["epoch"]),
            )
            or connection.execute(
                "SELECT 1 FROM workflow_leases WHERE run_id=? LIMIT 1",
                (outcome.run_id,),
            ).fetchone()
            is not None
        ):
            return False
        if tuple(sorted(outcome.delivery_ids)) != outcome.delivery_ids:
            return False
        actual_delivery_ids = tuple(
            str(item["delivery_id"])
            for item in connection.execute(
                "SELECT delivery_id FROM delivery_outbox WHERE run_id=? ORDER BY delivery_id",
                (outcome.run_id,),
            ).fetchall()
        )
        if actual_delivery_ids != outcome.delivery_ids:
            return False
        actual_facts: list[dict[str, JsonValue]] = []
        for delivery_id in outcome.delivery_ids:
            delivery = connection.execute(
                "SELECT delivery_id,sink_kind,idempotency_key,payload_json,state,created_at "
                "FROM delivery_outbox WHERE run_id=? AND delivery_id=?",
                (outcome.run_id, delivery_id),
            ).fetchone()
            if delivery is None or str(delivery["state"]) not in {
                "pending",
                "claimed",
                "delivered",
                "failed",
                "released",
            }:
                return False
            actual_facts.append(
                {
                    "delivery_id": str(delivery["delivery_id"]),
                    "sink_kind": str(delivery["sink_kind"]),
                    "idempotency_key": str(delivery["idempotency_key"]),
                    "payload": json.loads(str(delivery["payload_json"])),
                    "created_at": float(delivery["created_at"]),
                }
            )
        return canonical_json(cast(JsonValue, actual_facts)) == canonical_json(
            cast(JsonValue, delivery_facts)
        )

    @classmethod
    def _verify_workflow_terminal(cls, connection: sqlite3.Connection, outcome: object) -> bool:
        try:
            return cls._verify_workflow_terminal_unchecked(connection, outcome)
        except (
            json.JSONDecodeError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            return False

    def verify_workflow_terminal(self, outcome: object) -> bool:
        return self._verify_workflow_terminal(self.database.connection, outcome)

    def _require_runtime_start_shape(
        self,
        connection: sqlite3.Connection,
        *,
        receipt_row: sqlite3.Row,
        run: sqlite3.Row,
        dispatch: sqlite3.Row,
        start_receipt: sqlite3.Row | None,
        resume_receipt: sqlite3.Row | None,
    ) -> None:
        """Validate immutable generic/workflow admission cross-authority."""

        run_id = str(receipt_row["run_id"])
        ticket_row = connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (str(receipt_row["ticket_receipt_id"]),),
        ).fetchone()
        if ticket_row is None:
            raise UnitOfWorkConflict("generic Run identity lacks launch ticket")
        verified = _verified_workflow_launch_ticket(ticket_row)
        expected_run = (
            verified.resolved_run_id,
            verified.session_id,
            verified.request_id,
            verified.root_run_id,
            verified.parent_run_id,
            verified.profile_key,
            "workflow",
        )
        actual_run = (
            str(run["run_id"]),
            str(run["execution_session_id"]),
            str(run["request_id"]),
            str(run["root_run_id"]),
            None if run["parent_run_id"] is None else str(run["parent_run_id"]),
            str(run["profile_key"]),
            str(run["driver_kind"]),
        )
        if actual_run != expected_run or (
            str(receipt_row["trace_id"]),
            str(receipt_row["thread_id"]),
        ) != (verified.resolved_trace_id, verified.resolved_thread_id):
            raise UnitOfWorkConflict("generic Run identity differs from launch ticket")
        expected_claim_id = self._derived_id(
            "runtime-start-dispatch/v1", verified.ticket_receipt_id
        )
        if (
            str(dispatch["claim_id"]) != expected_claim_id
            or str(dispatch["ticket_receipt_id"]) != verified.ticket_receipt_id
            or str(dispatch["run_id"]) != run_id
            or str(dispatch["state"]) not in {"claimed", "consumed"}
        ):
            raise UnitOfWorkConflict("Runtime start dispatch identity differs")

        run_state = str(run["state"])
        if run_state not in {
            RunState.RUNNING.value,
            RunState.WAITING.value,
            RunState.CANCEL_REQUESTED.value,
            RunState.COMPLETED.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
        }:
            raise UnitOfWorkConflict("workflow Runtime admission requires RUNNING state algebra")
        if start_receipt is None:
            if resume_receipt is not None or str(dispatch["state"]) != "claimed":
                raise UnitOfWorkConflict("Runtime start phase matrix differs")
            if run_state not in {
                RunState.RUNNING.value,
                RunState.CANCEL_REQUESTED.value,
            }:
                raise UnitOfWorkConflict("orphan Runtime start must remain RUNNING")
            return

        if str(dispatch["state"]) != "consumed":
            raise UnitOfWorkConflict("bound workflow retained a claimed dispatch")
        start_request_value = json.loads(str(start_receipt["request_json"]))
        if not isinstance(start_request_value, dict):
            raise UnitOfWorkConflict("stored workflow start request is invalid")
        start_request = start_admission_request_from_json(start_request_value)
        payload_json, fingerprint, start_run, trace_id, thread_id = self._start_identity(
            start_request
        )
        if (
            start_request.mode is not StartMode.PRECREATED
            or str(start_receipt["request_key"]) != verified.ticket_receipt_id
            or str(start_receipt["request_id"]) != verified.request_id
            or str(start_receipt["run_id"]) != run_id
            or start_run != run_id
            or str(start_receipt["trace_id"]) != trace_id
            or str(start_receipt["thread_id"]) != thread_id
            or str(start_receipt["request_fingerprint"]) != fingerprint
            or str(start_receipt["request_json"]) != payload_json
        ):
            raise UnitOfWorkConflict("workflow start receipt identity differs")
        start_phase = str(start_receipt["phase"])
        resume_phase = None if resume_receipt is None else str(resume_receipt["phase"])
        if run_state == RunState.RUNNING.value:
            if start_phase not in {StartPhase.CLAIMED.value, StartPhase.RUNNING.value}:
                raise UnitOfWorkConflict("RUNNING workflow start phase differs")
            if resume_phase not in {None, ResumePhase.ADMITTED.value, ResumePhase.CLAIMED.value}:
                raise UnitOfWorkConflict("RUNNING workflow resume phase differs")
        elif run_state == RunState.WAITING.value:
            if start_phase != StartPhase.RUNNING.value or resume_phase not in {
                None,
                ResumePhase.RETRY_WAIT.value,
            }:
                raise UnitOfWorkConflict("WAITING workflow phase differs")
        elif run_state == RunState.CANCEL_REQUESTED.value:
            if start_phase not in {
                StartPhase.CLAIMED.value,
                StartPhase.RUNNING.value,
                StartPhase.SETTLED.value,
            } or resume_phase not in {
                None,
                ResumePhase.ADMITTED.value,
                ResumePhase.CLAIMED.value,
                ResumePhase.RETRY_WAIT.value,
                ResumePhase.SETTLED.value,
            }:
                raise UnitOfWorkConflict("cancel workflow phase differs")
        elif start_phase not in {StartPhase.RUNNING.value, StartPhase.SETTLED.value}:
            raise UnitOfWorkConflict("terminal/cancel workflow phase differs")

    @staticmethod
    def _require_live_runtime_start_cofence(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        runtime: sqlite3.Row,
        fence: sqlite3.Row,
        dispatch: sqlite3.Row,
        start_receipt: sqlite3.Row | None,
        resume_receipt: sqlite3.Row | None,
        now: float,
    ) -> None:
        owner_id = str(runtime["owner_id"])
        runtime_epoch = int(runtime["epoch"])
        expires_at = float(runtime["expires_at"])
        if (
            expires_at <= now
            or str(fence["state"]) != "active"
            or str(fence["owner_id"]) != owner_id
            or int(fence["runtime_lease_epoch"]) != runtime_epoch
        ):
            raise UnitOfWorkConflict("live Runtime start authorities are split-brain")
        if start_receipt is None:
            if (
                str(dispatch["state"]) != "claimed"
                or str(dispatch["owner_id"]) != owner_id
                or int(dispatch["runtime_lease_epoch"]) != runtime_epoch
                or float(dispatch["expires_at"]) <= now
                or float(dispatch["expires_at"]) != expires_at
            ):
                raise UnitOfWorkConflict("live Runtime dispatch is not claimable")
            return
        if str(dispatch["state"]) != "consumed":
            raise UnitOfWorkConflict("bound workflow retained a claimed dispatch")
        request_payload = json.loads(str(start_receipt["request_json"]))
        namespace = _required(request_payload.get("checkpoint_namespace"), "checkpoint_namespace")
        projection = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, namespace),
        ).fetchone()
        receipt = resume_receipt if resume_receipt is not None else start_receipt
        if (
            projection is None
            or str(projection["owner_id"]) != owner_id
            or int(projection["epoch"]) != runtime_epoch
            or float(projection["expires_at"]) != expires_at
            or receipt["claim_owner"] is None
            or str(receipt["claim_owner"]) != owner_id
            or receipt["claim_epoch"] is None
            or int(receipt["claim_epoch"]) != runtime_epoch
        ):
            raise UnitOfWorkConflict("live workflow projection is split-brain")

    @classmethod
    def _require_waiting_authority_shape(
        cls,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        runtime: sqlite3.Row | None,
        fence: sqlite3.Row | None,
        dispatch: sqlite3.Row,
        start_receipt: sqlite3.Row,
        resume_receipt: sqlite3.Row | None,
        now: float,
    ) -> None:
        request_payload = json.loads(str(start_receipt["request_json"]))
        namespace = _required(request_payload.get("checkpoint_namespace"), "checkpoint_namespace")
        workflow = connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, namespace),
        ).fetchone()
        if runtime is None and workflow is None:
            if fence is None or str(fence["state"]) != "released":
                raise UnitOfWorkConflict("released WAITING authority is incomplete")
            return
        if runtime is None or workflow is None or fence is None:
            raise UnitOfWorkConflict("WAITING authority is partially released")
        cls._require_live_runtime_start_cofence(
            connection,
            run_id=run_id,
            runtime=runtime,
            fence=fence,
            dispatch=dispatch,
            start_receipt=start_receipt,
            resume_receipt=resume_receipt,
            now=now,
        )

    def _classify_runtime_start_admission(
        self,
        connection: sqlite3.Connection,
        receipt_row: sqlite3.Row,
        *,
        claim: RuntimeActivationClaim,
        now: float,
        fault: FaultHook | None,
    ) -> RuntimeStartAdmission:

        run_id = str(receipt_row["run_id"])
        receipt = _runtime_start_receipt(receipt_row)
        run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        dispatch = connection.execute(
            "SELECT * FROM runtime_start_dispatch_claims WHERE run_id=?", (run_id,)
        ).fetchone()
        runtime = connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence = connection.execute("SELECT * FROM run_fences WHERE run_id=?", (run_id,)).fetchone()
        start_receipt_row = connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE run_id=?", (run_id,)
        ).fetchone()
        resume_rows = connection.execute(
            "SELECT * FROM workflow_resume_admissions "
            "WHERE run_id=? AND phase IN ('admitted','claimed','retry_wait') "
            "ORDER BY created_at,receipt_id LIMIT 2",
            (run_id,),
        ).fetchall()
        if len(resume_rows) > 1:
            raise UnitOfWorkConflict("workflow has multiple unsettled resume receipts")
        resume_receipt_row = None if not resume_rows else resume_rows[0]
        if run is None or dispatch is None:
            raise UnitOfWorkConflict("runtime start authority is incomplete")
        self._require_runtime_start_shape(
            connection,
            receipt_row=receipt_row,
            run=run,
            dispatch=dispatch,
            start_receipt=start_receipt_row,
            resume_receipt=resume_receipt_row,
        )
        run_state = str(run["state"])
        if run_state == RunState.WAITING.value:
            return self._classify_waiting_runtime_start(
                connection,
                receipt=receipt,
                run_id=run_id,
                run=run,
                dispatch=dispatch,
                runtime=runtime,
                fence=fence,
                start_receipt_row=start_receipt_row,
                resume_receipt_row=resume_receipt_row,
                now=now,
            )
        if run_state == RunState.CANCEL_REQUESTED.value:
            cancel = connection.execute(
                "SELECT phase FROM workflow_cancel_receipts WHERE run_id=? "
                "ORDER BY generation DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if cancel is None or str(cancel["phase"]) not in {
                "requested",
                "cancelling",
                "blocked",
            }:
                raise UnitOfWorkConflict("generic cancel state lacks workflow cancel authority")
            return RuntimeStartAdmission(receipt, RuntimeStartDisposition.CANCEL_PENDING)
        if run_state in {"completed", "failed", "cancelled"}:
            terminal = self._read_workflow_terminal_outcome(connection, run_id)
            if terminal is None or not self._verify_workflow_terminal(connection, terminal):
                raise UnitOfWorkConflict("terminal workflow outcome is incomplete")
            return RuntimeStartAdmission(
                receipt,
                RuntimeStartDisposition.TERMINAL,
                workflow_terminal=terminal,
            )
        live = (
            runtime is not None
            and fence is not None
            and str(fence["state"]) == "active"
            and float(runtime["expires_at"]) > now
        )
        if live:
            assert runtime is not None and fence is not None
            self._require_live_runtime_start_cofence(
                connection,
                run_id=run_id,
                runtime=runtime,
                fence=fence,
                dispatch=dispatch,
                start_receipt=start_receipt_row,
                resume_receipt=resume_receipt_row,
                now=now,
            )
            if str(runtime["owner_id"]) != claim.owner_id:
                return RuntimeStartAdmission(receipt, RuntimeStartDisposition.FOREIGN_ACTIVE)
            if start_receipt_row is not None or resume_receipt_row is not None:
                return RuntimeStartAdmission(receipt, RuntimeStartDisposition.ATTACH_CURRENT)
            if str(dispatch["state"]) != "claimed":
                raise UnitOfWorkConflict("consumed dispatch has no workflow receipt")
            activation = _runtime_start_activation(runtime, fence)
            return RuntimeStartAdmission(
                receipt,
                RuntimeStartDisposition.START_ORPHAN,
                activation=activation,
                dispatch_claim=_runtime_start_dispatch_claim(dispatch),
            )
        prior_start_receipt = (
            None
            if start_receipt_row is None or resume_receipt_row is not None
            else self._start_receipt(
                start_receipt_row,
                connection=connection,
            )
        )
        old_runtime_epoch = 0 if runtime is None else int(runtime["epoch"])
        old_fence_runtime_epoch = 0 if fence is None else int(fence["runtime_lease_epoch"])
        workflow_epoch = 0
        workflow_namespace: str | None = None
        if start_receipt_row is not None:
            workflow_namespace = json.loads(str(start_receipt_row["request_json"]))[
                "checkpoint_namespace"
            ]
            projection = connection.execute(
                "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                "WHERE run_id=? AND namespace=?",
                (run_id, workflow_namespace),
            ).fetchone()
            if projection is not None and float(projection["expires_at"]) > now:
                raise UnitOfWorkConflict("live foreign workflow projection blocks Runtime takeover")
            workflow_epoch = 0 if projection is None else int(projection["epoch"])
        next_epoch = max(old_runtime_epoch, old_fence_runtime_epoch, workflow_epoch) + 1
        expires_at = now + float(claim.lease_ttl_seconds)
        _fault(fault, "workflow:runtime_start:before_runtime_takeover_write")
        connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(run_id,namespace) DO UPDATE SET"
            " owner_id=excluded.owner_id,epoch=excluded.epoch,expires_at=excluded.expires_at",
            (run_id, RUNTIME_LEASE_NAMESPACE, claim.owner_id, next_epoch, expires_at),
        )
        _fault(fault, "workflow:runtime_start:after_runtime_takeover_write")
        if workflow_namespace is not None and resume_receipt_row is not None:
            _fault(
                fault,
                "workflow:runtime_start:before_workflow_takeover_write",
            )
            connection.execute(
                "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(run_id,namespace) DO UPDATE SET "
                "owner_id=excluded.owner_id,epoch=excluded.epoch,"
                "expires_at=excluded.expires_at",
                (
                    run_id,
                    workflow_namespace,
                    claim.owner_id,
                    next_epoch,
                    expires_at,
                ),
            )
            _fault(
                fault,
                "workflow:runtime_start:after_workflow_takeover_write",
            )
        fence_epoch = 1 if fence is None else int(fence["epoch"]) + 1
        _fault(fault, "workflow:runtime_start:before_fence_takeover_write")
        connection.execute(
            "INSERT INTO"
            " run_fences(run_id,owner_id,runtime_lease_epoch,epoch,state,acquired_at,released_at)"
            " VALUES(?,?,?,?, 'active',?,NULL) ON CONFLICT(run_id) DO UPDATE SET"
            " owner_id=excluded.owner_id,runtime_lease_epoch=excluded.runtime_lease_epoch,epoch=excluded.epoch,state='active',acquired_at=excluded.acquired_at,released_at=NULL",  # noqa: E501
            (run_id, claim.owner_id, next_epoch, fence_epoch, now),
        )
        _fault(fault, "workflow:runtime_start:after_fence_takeover_write")
        activation = RuntimeStartActivation(
            ExecutionLease(
                run_id,
                RUNTIME_LEASE_NAMESPACE,
                claim.owner_id,
                next_epoch,
                expires_at,
            ),
            RunFenceLease(
                run_id=RunId(run_id),
                epoch=fence_epoch,
                owner_id=claim.owner_id,
                runtime_lease_epoch=next_epoch,
            ),
        )
        if start_receipt_row is None:
            if str(dispatch["state"]) != "claimed":
                raise UnitOfWorkConflict("consumed dispatch has no workflow receipt")
            _fault(fault, "workflow:runtime_start:before_dispatch_takeover_write")
            connection.execute(
                "UPDATE runtime_start_dispatch_claims SET"
                " owner_id=?,runtime_lease_epoch=?,claim_epoch=claim_epoch+1,expires_at=?,version=version+1,updated_at=?"  # noqa: E501
                " WHERE claim_id=?",
                (claim.owner_id, next_epoch, expires_at, now, str(dispatch["claim_id"])),
            )
            _fault(fault, "workflow:runtime_start:after_dispatch_takeover_write")
            updated_dispatch = connection.execute(
                "SELECT * FROM runtime_start_dispatch_claims WHERE claim_id=?",
                (str(dispatch["claim_id"]),),
            ).fetchone()
            assert updated_dispatch is not None
            return RuntimeStartAdmission(
                receipt,
                RuntimeStartDisposition.START_ORPHAN,
                activation=activation,
                dispatch_claim=_runtime_start_dispatch_claim(updated_dispatch),
            )
        if resume_receipt_row is not None:
            resume_receipt = self._resume_receipt(
                resume_receipt_row,
                connection=connection,
                include_activation=False,
            )
            work = WorkflowRecoveryWork(
                run_id=run_id,
                receipt_kind=WorkflowRecoveryReceiptKind.RESUME,
                receipt_id=resume_receipt.request.receipt_id,
                receipt_version=resume_receipt.version,
                mode=resume_receipt.request.mode,
                due_at=(
                    None
                    if resume_receipt_row["next_attempt_at"] is None
                    else float(resume_receipt_row["next_attempt_at"])
                ),
                request_fingerprint=resume_receipt.request_fingerprint,
                receipt_snapshot=resume_receipt,
            )
            return RuntimeStartAdmission(
                receipt,
                RuntimeStartDisposition.RECOVER_RESUME,
                activation=activation,
                recovery_work=work,
            )
        assert prior_start_receipt is not None
        start_receipt = prior_start_receipt
        if start_receipt.request.mode is not StartMode.PRECREATED:
            raise UnitOfWorkConflict("standalone start cannot be Runtime-recovered")
        work = WorkflowRecoveryWork(
            run_id=run_id,
            receipt_kind=WorkflowRecoveryReceiptKind.START,
            receipt_id=start_receipt.request_key,
            receipt_version=start_receipt.version,
            mode=start_receipt.request.mode,
            due_at=None,
            request_fingerprint=start_receipt.request_fingerprint,
            receipt_snapshot=start_receipt,
        )
        return RuntimeStartAdmission(
            receipt,
            RuntimeStartDisposition.RECOVER_START,
            activation=activation,
            recovery_work=work,
        )

    def _classify_waiting_runtime_start(
        self,
        connection: sqlite3.Connection,
        *,
        receipt: RuntimeStartReceipt,
        run_id: str,
        run: sqlite3.Row,
        dispatch: sqlite3.Row,
        runtime: sqlite3.Row | None,
        fence: sqlite3.Row | None,
        start_receipt_row: sqlite3.Row | None,
        resume_receipt_row: sqlite3.Row | None,
        now: float,
    ) -> RuntimeStartAdmission:
        retry_wake = self._runtime_retry_wake(
            connection,
            run_id=run_id,
            run_version=int(run["version"]),
            resume_row=resume_receipt_row,
        )
        if retry_wake is None:
            if start_receipt_row is None:
                raise UnitOfWorkConflict("generic WAITING Run lacks pinned workflow admission")
            request_payload = json.loads(str(start_receipt_row["request_json"]))
            namespace = _required(
                request_payload.get("checkpoint_namespace"),
                "checkpoint_namespace",
            )
            head = connection.execute(
                "SELECT checkpoint_id,checkpoint_json,checkpoint_hash "
                "FROM workflow_checkpoints "
                "WHERE run_id=? AND namespace=? ORDER BY version DESC LIMIT 1",
                (run_id, namespace),
            ).fetchone()
            open_decision = None
            blocker = None
            if head is not None:
                if hashlib.sha256(str(head["checkpoint_json"]).encode()).hexdigest() != str(
                    head["checkpoint_hash"]
                ):
                    raise UnitOfWorkConflict("pinned workflow checkpoint self-hash changed")
                checkpoint_payload = json.loads(str(head["checkpoint_json"]))
                interrupt = (
                    checkpoint_payload.get("interrupt")
                    if isinstance(checkpoint_payload, dict)
                    else None
                )
                if interrupt is None:
                    interrupt_operation = connection.execute(
                        "SELECT payload_json FROM workflow_native_operations "
                        "WHERE run_id=? AND namespace=? AND base_checkpoint_id=? "
                        "AND operation_kind='interrupt' "
                        "ORDER BY created_at DESC,operation_id DESC LIMIT 1",
                        (run_id, namespace, str(head["checkpoint_id"])),
                    ).fetchone()
                    if interrupt_operation is not None:
                        operation_payload = json.loads(str(interrupt_operation["payload_json"]))
                        interrupt = (
                            operation_payload.get("interrupt")
                            if isinstance(operation_payload, dict)
                            else None
                        )
                interrupt_id = (
                    interrupt.get("interrupt_id") if isinstance(interrupt, dict) else None
                )
                if isinstance(interrupt_id, str) and interrupt_id:
                    open_decision = connection.execute(
                        "SELECT 1 FROM decisions WHERE run_id=? "
                        "AND decision_id=? AND state='open' LIMIT 1",
                        (run_id, interrupt_id),
                    ).fetchone()
                if open_decision is None:
                    open_decision = connection.execute(
                        "SELECT 1 FROM decisions WHERE run_id=? AND state='open' "
                        "AND json_extract(request_json,'$.checkpoint_id')=? "
                        "AND json_extract(request_json,'$.checkpoint_namespace')=? LIMIT 1",
                        (run_id, str(head["checkpoint_id"]), namespace),
                    ).fetchone()
                linked_blockers = (
                    checkpoint_payload.get("wait_blocker_ids", [])
                    if isinstance(checkpoint_payload, dict)
                    else []
                )
                if isinstance(linked_blockers, list) and linked_blockers:
                    placeholders = ",".join("?" for _ in linked_blockers)
                    blocker = connection.execute(
                        "SELECT 1 FROM run_wait_blockers WHERE run_id=? "
                        "AND resolution_id IS NULL AND blocker_id IN ("
                        + placeholders
                        + ") LIMIT 1",
                        (run_id, *linked_blockers),
                    ).fetchone()
            if head is None or (open_decision is None and blocker is None):
                raise UnitOfWorkConflict("generic WAITING Run lacks matching workflow authority")
            self._require_waiting_authority_shape(
                connection,
                run_id=run_id,
                runtime=runtime,
                fence=fence,
                dispatch=dispatch,
                start_receipt=start_receipt_row,
                resume_receipt=resume_receipt_row,
                now=now,
            )
        return RuntimeStartAdmission(
            receipt, RuntimeStartDisposition.WAITING, retry_wake=retry_wake
        )
