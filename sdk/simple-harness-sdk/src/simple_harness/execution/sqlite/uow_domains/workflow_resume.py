# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow resume admission, claims and settlement."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING

from simple_harness.contracts import JsonValue, canonical_json, freeze_json
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    DecisionState,
    ExecutionLease,
    FaultHook,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.workflow.execution_ports import (
    ResumeAdmissionReceipt,
    ResumeAdmissionRequest,
    ResumeCommitBinding,
    ResumePhase,
    StartMode,
    WorkflowActivation,
    WorkflowTransaction,
)
from simple_harness.workflow.lease import WorkflowLease

from .core import ExecutionUnitOfWorkCore
from .support import _fault, _object_json, _required, _thaw, _time


class WorkflowResumeMixin(ExecutionUnitOfWorkCore):
    """Workflow resume admission, claims and settlement."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _acquire_activation_rows(
            self,
            connection: sqlite3.Connection,
            *,
            run_id: str,
            namespace: str,
            owner_id: str,
            now: float,
            ttl_seconds: float,
        ) -> WorkflowActivation: ...
        @staticmethod
        def _require_runtime_and_fence(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            execution_lease: ExecutionLease,
            run_fence: RunFenceLease,
            now: float,
            require_exact_expiry: bool = True,
        ) -> None: ...
        async def release_activation(
            self,
            transaction: WorkflowTransaction,
            activation: WorkflowActivation,
            expected_run_version: int,
            outcome: Mapping[str, JsonValue],
            *,
            now: float,
            fault: FaultHook | None = None,
        ) -> None: ...

    @staticmethod
    def _resume_payload(request: ResumeAdmissionRequest) -> dict[str, JsonValue]:
        return {
            "receipt_id": request.receipt_id,
            "run_id": request.run_id,
            "expected_run_version": request.expected_run_version,
            "expected_checkpoint_head": request.expected_checkpoint_head,
            "pending_interrupts": [list(item) for item in request.pending_interrupts],
            "responses": _thaw(request.responses),
            "responses_hash": request.responses_hash,
            "mode": request.mode.value,
        }

    @classmethod
    def _resume_fingerprint(cls, request: ResumeAdmissionRequest) -> tuple[str, str]:
        payload = cls._resume_payload(request)
        payload_json = canonical_json(payload)
        actual_responses_hash = hashlib.sha256(
            canonical_json(payload["responses"]).encode()
        ).hexdigest()
        if actual_responses_hash != request.responses_hash:
            raise UnitOfWorkConflict("resume response hash does not match content")
        return payload_json, hashlib.sha256(payload_json.encode()).hexdigest()

    def _resume_receipt(
        self,
        row: sqlite3.Row,
        *,
        connection: sqlite3.Connection | None = None,
        include_activation: bool = True,
    ) -> ResumeAdmissionReceipt:
        authority = self.database.connection if connection is None else connection
        payload = json.loads(str(row["request_json"]))
        pending = tuple((str(item[0]), str(item[1])) for item in payload["pending_interrupts"])
        request = ResumeAdmissionRequest(
            receipt_id=str(payload["receipt_id"]),
            run_id=str(payload["run_id"]),
            expected_run_version=int(payload["expected_run_version"]),
            expected_checkpoint_head=str(payload["expected_checkpoint_head"]),
            pending_interrupts=pending,
            responses=payload["responses"],
            responses_hash=str(payload["responses_hash"]),
            mode=StartMode(str(payload["mode"])),
        )
        payload_json, fingerprint = self._resume_fingerprint(request)
        if (
            str(row["request_json"]) != payload_json
            or str(row["request_fingerprint"]) != fingerprint
            or str(row["run_id"]) != request.run_id
            or str(row["mode"]) != request.mode.value
            or int(row["expected_run_version"]) != request.expected_run_version
            or str(row["expected_checkpoint_head"]) != request.expected_checkpoint_head
        ):
            raise UnitOfWorkConflict("resume receipt durable binding changed")
        activation = None
        if (
            include_activation
            and row["claim_owner"] is not None
            and str(row["phase"]) == ResumePhase.CLAIMED.value
        ):
            start = authority.execute(
                "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
                (request.run_id,),
            ).fetchone()
            if start is not None:
                activation = self._read_workflow_activation(
                    request.run_id,
                    namespace=str(json.loads(str(start["request_json"]))["checkpoint_namespace"]),
                    owner_id=str(row["claim_owner"]),
                    connection=authority,
                )
        outcome = (
            None
            if row["outcome_json"] is None
            else freeze_json(json.loads(str(row["outcome_json"])))
        )
        return ResumeAdmissionReceipt(
            request=request,
            request_fingerprint=str(row["request_fingerprint"]),
            phase=ResumePhase(str(row["phase"])),
            version=int(row["version"]),
            claim_owner=None if row["claim_owner"] is None else str(row["claim_owner"]),
            claim_epoch=None if row["claim_epoch"] is None else int(row["claim_epoch"]),
            claim_expires_at=(
                None if row["claim_expires_at"] is None else float(row["claim_expires_at"])
            ),
            activation=activation,
            serialized_outcome=outcome,
            next_attempt_at=(
                None if row["next_attempt_at"] is None else float(row["next_attempt_at"])
            ),
        )

    async def admit_resume(
        self,
        transaction: WorkflowTransaction,
        request: ResumeAdmissionRequest,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        payload_json, fingerprint = self._resume_fingerprint(request)
        existing = tx.connection.execute(
            "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
            (request.receipt_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["request_fingerprint"]) != fingerprint:
                raise UnitOfWorkConflict("resume receipt reused with different request")
            return self._resume_receipt(existing, connection=tx.connection)
        run = tx.connection.execute(
            "SELECT state,version FROM runs WHERE run_id=?", (request.run_id,)
        ).fetchone()
        head = tx.connection.execute(
            "SELECT checkpoint_id,checkpoint_json FROM workflow_checkpoints WHERE run_id=? ORDER BY"
            " version DESC LIMIT 1",
            (request.run_id,),
        ).fetchone()
        if (
            run is None
            or str(run["state"]) not in {"waiting", "queued"}
            or int(run["version"]) != request.expected_run_version
            or head is None
            or str(head["checkpoint_id"]) != request.expected_checkpoint_head
        ):
            raise UnitOfWorkConflict("resume authority snapshot changed")
        pending = tuple(
            sorted(
                (
                    str(row["decision_id"]),
                    hashlib.sha256(str(row["request_json"]).encode()).hexdigest(),
                )
                for row in tx.connection.execute(
                    "SELECT decision_id,request_json FROM decisions WHERE run_id=? AND"
                    " state!='open'",
                    (request.run_id,),
                ).fetchall()
            )
        )
        if pending != request.pending_interrupts:
            raise UnitOfWorkConflict("resume pending interrupts changed")
        _fault(fault, "workflow:admit_resume:before_workflow_resume_admissions_write")
        tx.connection.execute(
            "INSERT INTO"
            " workflow_resume_admissions(receipt_id,run_id,request_fingerprint,request_json,mode,expected_run_version,expected_checkpoint_head,phase,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,'admitted',0,?,?)",
            (
                request.receipt_id,
                request.run_id,
                fingerprint,
                payload_json,
                request.mode.value,
                request.expected_run_version,
                request.expected_checkpoint_head,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:admit_resume:after_workflow_resume_admissions_write")
        return self._resume_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
                (request.receipt_id,),
            ).fetchone(),
            connection=tx.connection,
        )

    async def resolve_decision_and_admit_resume(
        self,
        transaction: WorkflowTransaction,
        request: ResumeAdmissionRequest,
        *,
        decision_id: str,
        nonce: str,
        expected_decision_version: int,
        response: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        """Resolve one open Workflow interrupt and admit its resume atomically."""

        tx = self._assert_open_workflow_transaction(transaction)
        decision_id = _required(decision_id, "decision_id")
        nonce = _required(nonce, "nonce")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        if isinstance(expected_decision_version, bool) or expected_decision_version < 0:
            raise ValueError("expected_decision_version must be non-negative")
        response_json = _object_json(response, "response")
        row = tx.connection.execute(
            "SELECT run_id,kind,state,request_json,version FROM decisions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise UnitOfWorkNotFound(decision_id)
        durable_request = json.loads(str(row["request_json"]))
        if (
            str(row["run_id"]) != request.run_id
            or str(row["state"]) != DecisionState.OPEN.value
            or int(row["version"]) != expected_decision_version
            or str(durable_request.get("nonce") or decision_id) != nonce
        ):
            raise UnitOfWorkConflict("workflow decision resume authority changed")
        _fault(fault, "workflow:resolve_resume:before_decision_write")
        changed = tx.connection.execute(
            "UPDATE decisions SET state='allowed',response_json=?,version=version+1,resolved_at=? "
            "WHERE decision_id=? AND run_id=? AND state='open' AND version=?",
            (response_json, now, decision_id, request.run_id, expected_decision_version),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow decision resolution CAS failed")
        _fault(fault, "workflow:resolve_resume:after_decision_write")
        changed = tx.connection.execute(
            "UPDATE runs SET state='queued',version=version+1,updated_at=? "
            "WHERE run_id=? AND state='waiting' AND version=?",
            (now, request.run_id, request.expected_run_version - 1),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow decision Run resolution CAS failed")
        self._insert_event(
            tx.connection,
            event_id=event_id,
            run_id=request.run_id,
            kind="decision.allowed",
            payload={"decision_id": decision_id, "kind": str(row["kind"])},
            now=now,
        )
        _fault(fault, "workflow:resolve_resume:before_admit")
        receipt = await self.admit_resume(transaction, request, now=now, fault=fault)
        _fault(fault, "workflow:resolve_resume:after_admit")
        return receipt

    async def _claim_resume(
        self,
        transaction: WorkflowTransaction,
        receipt_id: str,
        expected_receipt_version: int,
        *,
        owner_id: str,
        now: float,
        ttl_seconds: float,
        execution_lease: ExecutionLease | None,
        run_fence: RunFenceLease | None,
        expected_mode: StartMode,
        fault: FaultHook | None,
    ) -> ResumeAdmissionReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?", (receipt_id,)
        ).fetchone()
        if (
            row is None
            or int(row["version"]) != expected_receipt_version
            or str(row["phase"]) not in {"admitted", "retry_wait", "claimed"}
            or str(row["mode"]) != expected_mode.value
        ):
            raise UnitOfWorkConflict("resume receipt is not claimable")
        if str(row["phase"]) == ResumePhase.RETRY_WAIT.value and (
            row["next_attempt_at"] is None or float(row["next_attempt_at"]) > now
        ):
            raise UnitOfWorkConflict("resume retry is not due")
        if (
            str(row["phase"]) == "claimed"
            and row["claim_expires_at"] is not None
            and float(row["claim_expires_at"]) > now
        ):
            raise UnitOfWorkConflict("resume receipt has an active claimant")
        run_id = str(row["run_id"])
        start = tx.connection.execute(
            "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if start is None:
            raise UnitOfWorkConflict("resume start authority is missing")
        namespace = str(json.loads(str(start["request_json"]))["checkpoint_namespace"])
        if execution_lease is None:
            activation = self._acquire_activation_rows(
                tx.connection,
                run_id=run_id,
                namespace=namespace,
                owner_id=owner_id,
                now=now,
                ttl_seconds=ttl_seconds,
            )
        else:
            assert run_fence is not None
            self._require_runtime_and_fence(
                tx.connection,
                run_id=run_id,
                execution_lease=execution_lease,
                run_fence=run_fence,
                now=now,
            )
            activation = WorkflowActivation(
                execution_lease,
                run_fence,
                WorkflowLease(
                    run_id,
                    owner_id,
                    execution_lease.epoch,
                    execution_lease.expires_at,
                    execution_lease.epoch,
                    namespace,
                ),
            )
            workflow = tx.connection.execute(
                "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND"
                " namespace=?",
                (run_id, namespace),
            ).fetchone()
            if workflow is None or tuple(workflow) != (
                owner_id,
                execution_lease.epoch,
                execution_lease.expires_at,
            ):
                raise UnitOfWorkConflict("precreated resume workflow projection changed")
        _fault(fault, "workflow:claim_resume:before_workflow_resume_admissions_write")
        changed = tx.connection.execute(
            "UPDATE workflow_resume_admissions SET phase='claimed',version=version+1,"
            "claim_owner=?,claim_epoch=?,claim_expires_at=?,next_attempt_at=NULL,"
            "updated_at=? WHERE receipt_id=? AND version=?",
            (
                owner_id,
                activation.execution_lease.epoch,
                activation.execution_lease.expires_at,
                now,
                receipt_id,
                expected_receipt_version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("resume claim CAS failed")
        _fault(fault, "workflow:claim_resume:after_workflow_resume_admissions_write")
        return self._resume_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone(),
            connection=tx.connection,
        )

    async def claim_resume_standalone(
        self,
        transaction: WorkflowTransaction,
        receipt_id: str,
        expected_receipt_version: int,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        return await self._claim_resume(
            transaction,
            receipt_id,
            expected_receipt_version,
            owner_id=owner_id,
            now=now,
            ttl_seconds=ttl_seconds,
            execution_lease=None,
            run_fence=None,
            expected_mode=StartMode.STANDALONE,
            fault=fault,
        )

    async def claim_resume_precreated(
        self,
        transaction: WorkflowTransaction,
        receipt_id: str,
        expected_receipt_version: int,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        return await self._claim_resume(
            transaction,
            receipt_id,
            expected_receipt_version,
            owner_id=execution_lease.owner_id,
            now=now,
            ttl_seconds=ttl_seconds,
            execution_lease=execution_lease,
            run_fence=run_fence,
            expected_mode=StartMode.PRECREATED,
            fault=fault,
        )

    async def settle_resume(
        self,
        transaction: WorkflowTransaction,
        binding: ResumeCommitBinding,
        activation: WorkflowActivation,
        committed_checkpoint: str,
        outcome: Mapping[str, JsonValue],
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
            (binding.receipt_id,),
        ).fetchone()
        if (
            row is None
            or int(row["version"]) != binding.expected_receipt_version
            or str(row["request_fingerprint"]) != binding.request_fingerprint
            or str(row["phase"]) != "claimed"
        ):
            raise UnitOfWorkConflict("resume settlement binding changed")
        self._require_runtime_and_fence(
            tx.connection,
            run_id=str(row["run_id"]),
            execution_lease=activation.execution_lease,
            run_fence=activation.run_fence,
            now=now,
            require_exact_expiry=False,
        )
        head = tx.connection.execute(
            "SELECT checkpoint_id FROM workflow_checkpoints WHERE run_id=? ORDER BY version DESC"
            " LIMIT 1",
            (str(row["run_id"]),),
        ).fetchone()
        if head is None or str(head["checkpoint_id"]) != committed_checkpoint:
            raise UnitOfWorkConflict("resume committed checkpoint changed")
        outcome_json = canonical_json(dict(outcome))
        _fault(fault, "workflow:settle_resume:before_workflow_resume_admissions_write")
        changed = tx.connection.execute(
            "UPDATE workflow_resume_admissions SET"
            " phase='settled',version=version+1,committed_checkpoint=?,outcome_json=?,updated_at=?"
            " WHERE receipt_id=? AND version=? AND phase='claimed'",
            (
                committed_checkpoint,
                outcome_json,
                now,
                binding.receipt_id,
                binding.expected_receipt_version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("resume settlement CAS failed")
        _fault(fault, "workflow:settle_resume:after_workflow_resume_admissions_write")
        current_run = tx.connection.execute(
            "SELECT version FROM runs WHERE run_id=?", (str(row["run_id"]),)
        ).fetchone()
        if current_run is None:
            raise UnitOfWorkConflict("resume Run disappeared before release")
        await self.release_activation(
            transaction,
            activation,
            int(current_run["version"]),
            outcome,
            now=now,
            fault=fault,
        )
        return self._resume_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
                (binding.receipt_id,),
            ).fetchone(),
            connection=tx.connection,
        )

    async def defer_resume_retry(
        self,
        transaction: WorkflowTransaction,
        binding: ResumeCommitBinding,
        activation: WorkflowActivation,
        retry_operation_id: str,
        retry_attempt: int,
        next_attempt_at: float,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ResumeAdmissionReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
            (binding.receipt_id,),
        ).fetchone()
        if (
            row is None
            or int(row["version"]) != binding.expected_receipt_version
            or str(row["request_fingerprint"]) != binding.request_fingerprint
            or str(row["phase"]) != ResumePhase.CLAIMED.value
        ):
            raise UnitOfWorkConflict("resume retry binding changed")
        self._require_runtime_and_fence(
            tx.connection,
            run_id=str(row["run_id"]),
            execution_lease=activation.execution_lease,
            run_fence=activation.run_fence,
            now=now,
            require_exact_expiry=False,
        )
        outcome_json = canonical_json(
            {
                "status": "retryable",
                "retry_operation_id": retry_operation_id,
                "retry_attempt": retry_attempt,
                "next_attempt_at": next_attempt_at,
            }
        )
        _fault(fault, "workflow:defer_resume_retry:before_workflow_resume_admissions_write")
        changed = tx.connection.execute(
            "UPDATE workflow_resume_admissions SET"
            " phase='retry_wait',version=version+1,retry_attempt=?,next_attempt_at=?,outcome_json=?,claim_owner=NULL,claim_epoch=NULL,claim_expires_at=NULL,updated_at=?"  # noqa: E501
            " WHERE receipt_id=? AND version=? AND phase='claimed'",
            (
                retry_attempt,
                next_attempt_at,
                outcome_json,
                now,
                binding.receipt_id,
                binding.expected_receipt_version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("resume retry CAS failed")
        _fault(fault, "workflow:defer_resume_retry:after_workflow_resume_admissions_write")
        tx.connection.execute(
            "DELETE FROM workflow_leases WHERE run_id=? AND namespace IN (?,?) AND owner_id=? AND"
            " epoch=?",
            (
                activation.execution_lease.run_id,
                RUNTIME_LEASE_NAMESPACE,
                activation.workflow_lease.namespace,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
            ),
        )
        tx.connection.execute(
            "UPDATE run_fences SET state='released',released_at=? WHERE run_id=? AND epoch=? AND"
            " state='active'",
            (now, activation.execution_lease.run_id, activation.run_fence.epoch),
        )
        return self._resume_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_resume_admissions WHERE receipt_id=?",
                (binding.receipt_id,),
            ).fetchone(),
            connection=tx.connection,
        )
