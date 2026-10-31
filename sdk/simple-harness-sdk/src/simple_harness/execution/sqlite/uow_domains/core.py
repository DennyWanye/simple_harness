# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Connection state and run/start/continuation primitives shared by all domains."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import cast

from simple_harness.contracts import JsonValue, RunId, canonical_json, freeze_json
from simple_harness.execution.contracts.children import (
    ChildSignalRecord,
    ChildTerminalReceipt,
    ChildTerminalResult,
)
from simple_harness.execution.delivery import TerminalCommitResult
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    AdmissionRecord,
    AdmissionState,
    ContinuationProgressReceipt,
    ContinuationProgressResult,
    ContinuationRecord,
    ContinuationState,
    ContinuationTerminalResult,
    DecisionRecord,
    DecisionState,
    ExecutionLease,
    FaultHook,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.workflow.execution_ports import (
    StartAdmissionReceipt,
    StartAdmissionRequest,
    StartClaimAction,
    StartPhase,
    WorkflowActivation,
    WorkflowOperationConflict,
    WorkflowOperationReceipt,
    WorkflowTransaction,
    start_admission_request_from_json,
    start_admission_request_to_json,
)
from simple_harness.workflow.lease import WorkflowLease

from ..audit_witness import record_event_witness
from ..database import Database
from .support import (
    _child_signal_record,
    _child_terminal_receipt,
    _continuation_progress_receipt,
    _delivery_record,
    _fault,
    _required,
    _run_record,
    _time,
)


class _SqliteWorkflowTransaction:
    __slots__ = (
        "_after_commit_fault_points",
        "_fault",
        "connection",
        "is_open",
        "transaction_owner",
    )

    def __init__(
        self, owner: Database, connection: sqlite3.Connection, fault: FaultHook | None
    ) -> None:
        self.transaction_owner = owner
        self.connection = connection
        self.is_open = True
        self._fault = fault
        self._after_commit_fault_points: list[str] = []

    def register_after_commit_fault(self, point: str) -> None:
        if point not in self._after_commit_fault_points:
            self._after_commit_fault_points.append(point)

    async def read_workflow_operation(self, operation_id: str) -> WorkflowOperationReceipt | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        identity = json.loads(str(row["identity_json"]))
        outcome = json.loads(str(row["outcome_json"]))
        if not isinstance(identity, list):
            raise WorkflowOperationConflict("corrupt workflow operation identity")
        return WorkflowOperationReceipt(
            operation_id=str(row["operation_id"]),
            adapter_method=str(row["adapter_method"]),
            identity=tuple(str(value) for value in identity),
            payload_hash=str(row["payload_hash"]),
            outcome=cast(JsonValue, outcome),
            run_id=str(row["run_id"]),
            namespace=str(row["namespace"]),
            checkpoint_id=(None if row["checkpoint_id"] is None else str(row["checkpoint_id"])),
            lease_epoch=int(row["lease_epoch"]),
            created_at=float(row["created_at"]),
        )

    async def apply_workflow_operation(
        self,
        *,
        adapter_method: str,
        identity: tuple[str, ...],
        payload: Mapping[str, JsonValue],
    ) -> JsonValue:
        del identity
        _fault(self._fault, f"workflow_adapter.{adapter_method}.before_ledger")
        run_id = _required(payload.get("run_id"), "run_id")
        now_value = payload.get("now", 0.0)
        now = _time(now_value)
        if adapter_method == "mark_running_on_claim":
            changed = self.connection.execute(
                """
                UPDATE runs SET state='running', version=version+1, updated_at=?
                WHERE run_id=? AND state IN ('created','queued','waiting')
                """,
                (now, run_id),
            ).rowcount
            row = self.connection.execute(
                "SELECT state,version FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise UnitOfWorkNotFound(run_id)
            receipt = self.connection.execute(
                "SELECT phase,version FROM workflow_start_admissions WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if receipt is not None and str(receipt["phase"]) == StartPhase.CLAIMED.value:
                self.connection.execute(
                    "UPDATE workflow_start_admissions SET"
                    " phase='running',version=version+1,updated_at=? WHERE run_id=? AND"
                    " phase='claimed' AND version=?",
                    (now, run_id, int(receipt["version"])),
                )
            outcome: JsonValue = {
                "changed": bool(changed),
                "state": str(row["state"]),
                "version": int(row["version"]),
            }
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        if adapter_method == "consume_decisions":
            checkpoint_id = _required(payload.get("checkpoint_id"), "checkpoint_id")
            decision_ids = payload.get("decision_ids")
            responses = payload.get("responses")
            if not isinstance(decision_ids, list) or not isinstance(responses, dict):
                raise WorkflowOperationConflict("invalid decision consumption payload")
            for decision_id in decision_ids:
                resolved = _required(decision_id, "decision_id")
                if resolved not in responses:
                    raise WorkflowOperationConflict("decision response is missing")
                row = self.connection.execute(
                    "SELECT state,response_json FROM decisions WHERE decision_id=? AND run_id=?",
                    (resolved, run_id),
                ).fetchone()
                if row is None or str(row["state"]) == "open":
                    raise WorkflowOperationConflict("decision is not durably resolved")
                response_json = canonical_json(cast(JsonValue, responses[resolved]))
                self.connection.execute(
                    """
                    INSERT INTO workflow_decision_consumptions(
                        run_id,checkpoint_id,decision_id,response_json,consumed_at
                    ) VALUES (?,?,?,?,?)
                    """,
                    (run_id, checkpoint_id, resolved, response_json, now),
                )
            outcome = {
                "decision_ids": list(decision_ids),
                "checkpoint_id": checkpoint_id,
            }
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        if adapter_method == "open_decision":
            interrupt_id = _required(payload.get("interrupt_id"), "interrupt_id")
            request = payload.get("request")
            if not isinstance(request, dict):
                raise WorkflowOperationConflict("decision request must be an object")
            existing = self.connection.execute(
                "SELECT request_json,state FROM decisions WHERE decision_id=?",
                (interrupt_id,),
            ).fetchone()
            request_json = canonical_json(request)
            if existing is not None:
                if str(existing["request_json"]) != request_json:
                    raise WorkflowOperationConflict("decision request changed")
                return {"decision_id": interrupt_id, "state": str(existing["state"])}
            self.connection.execute(
                """
                INSERT INTO decisions(
                    decision_id,run_id,kind,state,request_json,version,created_at
                ) VALUES (?,?,?,'open',?,0,?)
                """,
                (
                    interrupt_id,
                    run_id,
                    str(request.get("kind", "workflow_interrupt")),
                    request_json,
                    now,
                ),
            )
            changed = self.connection.execute(
                """
                UPDATE runs SET state='waiting',version=version+1,updated_at=?
                WHERE run_id=? AND state='running'
                """,
                (now, run_id),
            ).rowcount
            if changed != 1:
                raise WorkflowOperationConflict("workflow interrupt requires running Run")
            event_id = hashlib.sha256(f"{run_id}|decision.open|{interrupt_id}".encode()).hexdigest()
            sequence = int(
                self.connection.execute(
                    "SELECT COALESCE(MAX(durable_seq),0)+1 FROM run_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            self.connection.execute(
                """
                INSERT INTO run_events(
                    event_id,run_id,durable_seq,kind,payload_json,created_at
                ) VALUES(?,?,?,'decision.open',?,?)
                """,
                (
                    event_id,
                    run_id,
                    sequence,
                    canonical_json(
                        {
                            "decision_id": interrupt_id,
                            "kind": str(request.get("kind", "workflow_interrupt")),
                        }
                    ),
                    now,
                ),
            )

            record_event_witness(self.connection, event_id)
            outcome = cast(
                JsonValue,
                {"decision_id": interrupt_id, "state": "open", "event_id": event_id},
            )
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        if adapter_method == "materialize_intent":
            intent_id = _required(payload.get("intent_id"), "intent_id")
            intent = payload.get("intent")
            if not isinstance(intent, dict):
                raise WorkflowOperationConflict("workflow intent must be an object")
            event_id = hashlib.sha256(f"{run_id}|{intent_id}".encode()).hexdigest()
            sequence = int(
                self.connection.execute(
                    "SELECT COALESCE(MAX(durable_seq),0)+1 FROM run_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            self.connection.execute(
                "INSERT INTO run_events(event_id,run_id,durable_seq,kind,payload_json,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    event_id,
                    run_id,
                    sequence,
                    str(intent.get("event_type", "workflow.event")),
                    canonical_json(intent),
                    now,
                ),
            )

            record_event_witness(self.connection, event_id)
            outcome = {"event_id": event_id, "durable_seq": sequence}
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        if adapter_method == "link_effects":
            namespace = _required(payload.get("checkpoint_namespace"), "checkpoint_namespace")
            checkpoint_id = _required(payload.get("checkpoint_id"), "checkpoint_id")
            effect_ids = payload.get("effect_ids")
            if not isinstance(effect_ids, list):
                raise WorkflowOperationConflict("effect ids must be a list")
            for effect_id in effect_ids:
                self.connection.execute(
                    "INSERT INTO"
                    " workflow_checkpoint_effect_links(run_id,namespace,checkpoint_id,effect_id,created_at)"  # noqa: E501
                    " VALUES(?,?,?,?,?)",
                    (
                        run_id,
                        namespace,
                        checkpoint_id,
                        _required(effect_id, "effect_id"),
                        now,
                    ),
                )
            outcome = {"effect_ids": list(effect_ids), "checkpoint_id": checkpoint_id}
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        if adapter_method == "finalize_run":
            status = _required(payload.get("status"), "status")
            if status not in {"completed", "failed", "cancelled"}:
                raise WorkflowOperationConflict("invalid terminal workflow status")
            changed = self.connection.execute(
                """
                UPDATE runs SET state=?,version=version+1,updated_at=?
                WHERE run_id=? AND state NOT IN ('completed','failed','cancelled')
                """,
                (status, now, run_id),
            ).rowcount
            row = self.connection.execute(
                "SELECT state,version FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise UnitOfWorkNotFound(run_id)
            if not changed and str(row["state"]) != status:
                raise WorkflowOperationConflict("Run already has another terminal state")
            outcome = {"state": str(row["state"]), "version": int(row["version"])}
            _fault(self._fault, f"workflow_adapter.{adapter_method}.after_ledger")
            return outcome
        raise WorkflowOperationConflict(f"unknown workflow adapter method: {adapter_method}")

    async def write_workflow_operation(self, receipt: WorkflowOperationReceipt) -> None:
        _fault(self._fault, f"workflow_adapter.{receipt.adapter_method}.before_receipt")
        self.connection.execute(
            """
            INSERT INTO workflow_operation_receipts(
                operation_id,adapter_method,identity_json,payload_hash,outcome_json,
                run_id,namespace,checkpoint_id,lease_epoch,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                receipt.operation_id,
                receipt.adapter_method,
                canonical_json(list(receipt.identity)),
                receipt.payload_hash,
                canonical_json(receipt.outcome),
                receipt.run_id,
                receipt.namespace,
                receipt.checkpoint_id,
                receipt.lease_epoch,
                receipt.created_at,
            ),
        )
        _fault(self._fault, f"workflow_adapter.{receipt.adapter_method}.after_receipt")


def _admission_record(row: sqlite3.Row) -> AdmissionRecord:
    prompt = freeze_json(json.loads(str(row["prompt_json"])))
    response = (
        None if row["response_json"] is None else freeze_json(json.loads(str(row["response_json"])))
    )
    return AdmissionRecord(
        admission_id=str(row["admission_id"]),
        run_id=str(row["run_id"]),
        state=AdmissionState(row["state"]),
        prompt=prompt,
        response=response,
        expires_at=None if row["expires_at"] is None else float(row["expires_at"]),
        version=int(row["version"]),
    )


def _decision_record(row: sqlite3.Row) -> DecisionRecord:
    return DecisionRecord(
        decision_id=str(row["decision_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        state=DecisionState(row["state"]),
        request=freeze_json(json.loads(str(row["request_json"]))),
        response=(
            None
            if row["response_json"] is None
            else freeze_json(json.loads(str(row["response_json"])))
        ),
        version=int(row["version"]),
    )


def _continuation_record(row: sqlite3.Row) -> ContinuationRecord:
    return ContinuationRecord(
        continuation_id=str(row["continuation_id"]),
        run_id=str(row["run_id"]),
        fifo_seq=int(row["fifo_seq"]),
        payload=freeze_json(json.loads(str(row["payload_json"]))),
        state=ContinuationState(row["state"]),
        version=int(row["version"]),
        claimed_by=None if row["claimed_by"] is None else str(row["claimed_by"]),
        runtime_lease_epoch=(
            None if row["runtime_lease_epoch"] is None else int(row["runtime_lease_epoch"])
        ),
        claim_epoch=int(row["claim_epoch"]),
        ack_receipt_id=(None if row["ack_receipt_id"] is None else str(row["ack_receipt_id"])),
    )


class ExecutionUnitOfWorkCore:
    """Connection state and run/start/continuation primitives shared by all domains."""

    __slots__ = (
        "database",
        "workflow_fault",
        "_context_use_scope",
        "_context_use_configured",
        "_budget_cache",
    )

    def __init__(self, database: Database, *, workflow_fault: FaultHook | None = None) -> None:
        self.database = database
        self.workflow_fault = workflow_fault
        self._context_use_scope = None
        self._context_use_configured = False
        # 每个 Run 的预算快照按行缓存：invocation_id -> (version, 费用微元或 None, 是否在途)
        self._budget_cache: dict[str, dict[str, tuple[int, int | None, bool]]] = {}

    def read_child_signal(self, signal_id: str) -> ChildSignalRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        return None if row is None else _child_signal_record(row)

    def read_run(self, run_id: str) -> RunRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else _run_record(row)

    def read_admission(self, admission_id: str) -> AdmissionRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM run_admissions WHERE admission_id = ?", (admission_id,)
        ).fetchone()
        return None if row is None else _admission_record(row)

    def read_decision(self, decision_id: str) -> DecisionRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return None if row is None else _decision_record(row)

    def read_continuation(self, continuation_id: str) -> ContinuationRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM continuations WHERE continuation_id = ?",
            (continuation_id,),
        ).fetchone()
        return None if row is None else _continuation_record(row)

    def _run_by_request(self, session_id: str, request_id: str) -> RunRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM runs WHERE execution_session_id = ? AND request_id = ?",
            (session_id, request_id),
        ).fetchone()
        return None if row is None else _run_record(row)

    def _require_runtime_lease(
        self,
        connection: sqlite3.Connection,
        lease: ExecutionLease,
        *,
        now: float,
    ) -> None:
        row = connection.execute(
            """
            SELECT owner_id, epoch, expires_at FROM workflow_leases
            WHERE run_id = ? AND namespace = ?
            """,
            (lease.run_id, lease.namespace),
        ).fetchone()
        if (
            row is None
            or str(row["owner_id"]) != lease.owner_id
            or int(row["epoch"]) != lease.epoch
            or float(row["expires_at"]) <= now
        ):
            raise UnitOfWorkConflict("runtime lease is stale or expired")

    def _require_continuation_claim(
        self,
        connection: sqlite3.Connection,
        claim: ContinuationRecord,
        lease: ExecutionLease,
    ) -> None:
        if claim.run_id != lease.run_id:
            raise UnitOfWorkConflict("continuation claim belongs to another Run")
        row = connection.execute(
            "SELECT state,version,claimed_by,runtime_lease_epoch,claim_epoch "
            "FROM continuations WHERE continuation_id=?",
            (claim.continuation_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            "claimed",
            claim.version,
            lease.owner_id,
            lease.epoch,
            claim.claim_epoch,
        ):
            raise UnitOfWorkConflict("continuation claim is stale")

    def _read_continuation_progress_receipt(
        self, receipt_id: str
    ) -> ContinuationProgressReceipt | None:
        row = self.database.connection.execute(
            "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        return None if row is None else _continuation_progress_receipt(row)

    def _read_child_terminal_receipt(self, receipt_id: str) -> ChildTerminalReceipt | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_terminal_receipts WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        return None if row is None else _child_terminal_receipt(row)

    def _read_child_terminal_receipt_for_command(
        self, command_id: str
    ) -> ChildTerminalReceipt | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_terminal_receipts WHERE command_id=?",
            (command_id,),
        ).fetchone()
        return None if row is None else _child_terminal_receipt(row)

    def _replay_child_terminal(
        self,
        receipt: ChildTerminalReceipt,
        *,
        command_id: str,
        terminal_state: RunState,
        outcome_hash: str,
        signal_id: str | None,
        event_id: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
    ) -> ChildTerminalResult:
        if (
            run_fence.owner_id != execution_lease.owner_id
            or run_fence.runtime_lease_epoch != execution_lease.epoch
        ):
            raise UnitOfWorkConflict("child terminal authorities differ")
        expected = (
            command_id,
            terminal_state.value,
            outcome_hash,
            signal_id,
            event_id,
            execution_lease.owner_id,
            execution_lease.epoch,
            run_fence.epoch,
        )
        actual = (
            receipt.command_id,
            receipt.terminal_state,
            receipt.outcome_hash,
            receipt.signal_id,
            receipt.event_id,
            receipt.owner_id,
            receipt.runtime_lease_epoch,
            receipt.fence_epoch,
        )
        if actual != expected or run_fence.run_id.value != receipt.child_run_id:
            raise UnitOfWorkConflict("child terminal receipt differs")
        return ChildTerminalResult(
            receipt.child_run_id,
            receipt.terminal_state,
            receipt,
            None if signal_id is None else self.read_child_signal(signal_id),
        )

    def _replay_continuation_progress(
        self,
        receipt: ContinuationProgressReceipt,
        *,
        continuation_claim: ContinuationRecord,
        execution_lease: ExecutionLease,
        outcome_hash: str,
    ) -> ContinuationProgressResult:
        expected = (
            continuation_claim.continuation_id,
            continuation_claim.run_id,
            execution_lease.owner_id,
            execution_lease.epoch,
            continuation_claim.claim_epoch,
            outcome_hash,
        )
        actual = (
            receipt.continuation_id,
            receipt.run_id,
            receipt.owner_id,
            receipt.runtime_lease_epoch,
            receipt.claim_epoch,
            receipt.outcome_hash,
        )
        if actual != expected:
            raise UnitOfWorkConflict("continuation progress receipt differs")
        run = self.read_run(receipt.run_id)
        continuation = self.read_continuation(receipt.continuation_id)
        if run is None or continuation is None:
            raise UnitOfWorkConflict("continuation progress receipt is orphaned")
        return ContinuationProgressResult(run, continuation, receipt)

    def _replay_continuation_terminal(
        self,
        receipt: ContinuationProgressReceipt,
        *,
        continuation_claim: ContinuationRecord,
        execution_lease: ExecutionLease,
        outcome_hash: str,
    ) -> ContinuationTerminalResult:
        progress = self._replay_continuation_progress(
            receipt,
            continuation_claim=continuation_claim,
            execution_lease=execution_lease,
            outcome_hash=outcome_hash,
        )
        deliveries = tuple(
            _delivery_record(row)
            for row in self.database.connection.execute(
                "SELECT * FROM delivery_outbox WHERE run_id=? ORDER BY delivery_id",
                (receipt.run_id,),
            ).fetchall()
        )
        return ContinuationTerminalResult(
            TerminalCommitResult(progress.run, deliveries),
            progress.continuation,
            progress.receipt,
        )

    def _require_run_fence(
        self,
        connection: sqlite3.Connection,
        fence: RunFenceLease,
        *,
        execution_lease: ExecutionLease,
    ) -> None:
        row = connection.execute(
            "SELECT owner_id, runtime_lease_epoch, epoch, state FROM run_fences WHERE run_id = ?",
            (fence.run_id.value,),
        ).fetchone()
        if (
            row is None
            or tuple(row)
            != (
                fence.owner_id,
                fence.runtime_lease_epoch,
                fence.epoch,
                "active",
            )
            or fence.runtime_lease_epoch != execution_lease.epoch
        ):
            raise UnitOfWorkConflict("Run fence is stale or inactive")

    def _verify_existing_start(
        self,
        record: RunRecord,
        *,
        run_id: str,
        profile_key: str,
        driver_kind: str,
        snapshot_hash: str,
    ) -> None:
        row = self.database.connection.execute(
            "SELECT snapshot_hash FROM run_start_snapshots WHERE run_id = ?",
            (record.run_id,),
        ).fetchone()
        if (
            record.run_id != run_id
            or record.profile_key != profile_key
            or record.driver_kind != driver_kind
            or row is None
            or row[0] != snapshot_hash
        ):
            raise UnitOfWorkConflict("request identity reused with different root intent")

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        run_id: str,
        kind: str,
        payload: dict[str, JsonValue],
        now: float,
    ) -> None:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(durable_seq), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO run_events(event_id, run_id, durable_seq, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, run_id, sequence, kind, canonical_json(payload), now),
        )

        record_event_witness(connection, event_id)

    @staticmethod
    def _workflow_request_payload(
        request: StartAdmissionRequest,
    ) -> dict[str, JsonValue]:
        return start_admission_request_to_json(request)

    @staticmethod
    def _derived_id(domain: str, fingerprint: str) -> str:
        return hashlib.sha256(
            f"simple-harness.workflow.{domain}|{fingerprint}".encode()
        ).hexdigest()

    @classmethod
    def _start_identity(cls, request: StartAdmissionRequest) -> tuple[str, str, str, str, str]:
        payload_json = canonical_json(cls._workflow_request_payload(request))
        fingerprint = hashlib.sha256(payload_json.encode()).hexdigest()
        run_id = (
            request.resolved_run_id
            or request.requested_run_id
            or cls._derived_id("run", fingerprint)
        )
        trace_id = (
            request.resolved_trace_id
            or request.requested_trace_id
            or cls._derived_id("trace", fingerprint)
        )
        thread_id = (
            request.resolved_thread_id
            or request.requested_thread_id
            or cls._derived_id("thread", fingerprint)
        )
        return payload_json, fingerprint, run_id, trace_id, thread_id

    def _start_receipt(
        self,
        row: sqlite3.Row,
        *,
        connection: sqlite3.Connection | None = None,
        include_activation: bool = True,
        activation_override: WorkflowActivation | None = None,
    ) -> StartAdmissionReceipt:
        authority = self.database.connection if connection is None else connection
        activation = None
        if row["claim_owner"] is not None and str(row["phase"]) in {
            StartPhase.CLAIMED.value,
            StartPhase.RUNNING.value,
        }:
            run_id = str(row["run_id"])
            namespace = str(json.loads(str(row["request_json"]))["checkpoint_namespace"])
            if activation_override is not None:
                activation = activation_override
            elif include_activation:
                fence = authority.execute(
                    "SELECT epoch,owner_id,runtime_lease_epoch,state FROM run_fences "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if (
                    fence is None
                    or str(fence["state"]) != "active"
                    or str(fence["owner_id"]) != str(row["claim_owner"])
                    or int(fence["runtime_lease_epoch"]) != int(row["claim_epoch"])
                ):
                    raise UnitOfWorkConflict("start receipt claim fence authority changed")
                claim_epoch = int(row["claim_epoch"])
                claim_expiry = float(row["claim_expires_at"])
                owner_id = str(row["claim_owner"])
                activation = WorkflowActivation(
                    ExecutionLease(
                        run_id,
                        RUNTIME_LEASE_NAMESPACE,
                        owner_id,
                        claim_epoch,
                        claim_expiry,
                    ),
                    RunFenceLease(
                        RunId(run_id),
                        int(fence["epoch"]),
                        owner_id,
                        claim_epoch,
                    ),
                    WorkflowLease(
                        run_id,
                        owner_id,
                        claim_epoch,
                        claim_expiry,
                        claim_epoch,
                        namespace,
                    ),
                )
        outcome = (
            None
            if row["outcome_json"] is None
            else freeze_json(json.loads(str(row["outcome_json"])))
        )
        request_value = json.loads(str(row["request_json"]))
        if not isinstance(request_value, dict):
            raise UnitOfWorkConflict("stored workflow start request is invalid")
        return StartAdmissionReceipt(
            request=start_admission_request_from_json(request_value),
            request_id=str(row["request_id"]),
            request_key=str(row["request_key"]),
            request_fingerprint=str(row["request_fingerprint"]),
            run_id=str(row["run_id"]),
            trace_id=str(row["trace_id"]),
            thread_id=str(row["thread_id"]),
            phase=StartPhase(str(row["phase"])),
            version=int(row["version"]),
            claim_action=(
                None if row["claim_action"] is None else StartClaimAction(str(row["claim_action"]))
            ),
            claim_owner=None if row["claim_owner"] is None else str(row["claim_owner"]),
            claim_epoch=(None if row["claim_epoch"] is None else int(row["claim_epoch"])),
            claim_expires_at=(
                None if row["claim_expires_at"] is None else float(row["claim_expires_at"])
            ),
            activation=activation,
            serialized_outcome=outcome,
        )

    def _read_workflow_activation(
        self,
        run_id: str,
        *,
        namespace: str,
        owner_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> WorkflowActivation:
        authority = self.database.connection if connection is None else connection
        runtime = authority.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        workflow = authority.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, namespace),
        ).fetchone()
        fence = authority.execute(
            "SELECT epoch,owner_id,runtime_lease_epoch,state FROM run_fences WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if runtime is None or workflow is None or fence is None or str(fence["state"]) != "active":
            raise UnitOfWorkConflict("workflow activation is incomplete")
        execution_lease = ExecutionLease(
            run_id,
            RUNTIME_LEASE_NAMESPACE,
            str(runtime["owner_id"]),
            int(runtime["epoch"]),
            float(runtime["expires_at"]),
        )
        run_fence = RunFenceLease(
            RunId(run_id),
            int(fence["epoch"]),
            str(fence["owner_id"]),
            int(fence["runtime_lease_epoch"]),
        )
        workflow_lease = WorkflowLease(
            run_id,
            str(workflow["owner_id"]),
            int(workflow["epoch"]),
            float(workflow["expires_at"]),
            int(runtime["epoch"]),
            namespace,
        )
        if execution_lease.owner_id != owner_id:
            raise UnitOfWorkConflict("workflow activation owner changed")
        return WorkflowActivation(execution_lease, run_fence, workflow_lease)

    @staticmethod
    def _assert_open_workflow_transaction(
        transaction: WorkflowTransaction,
    ) -> _SqliteWorkflowTransaction:
        if not isinstance(transaction, _SqliteWorkflowTransaction) or not transaction.is_open:
            raise UnitOfWorkConflict("workflow lifecycle requires an open canonical transaction")
        return transaction
