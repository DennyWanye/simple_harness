# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow start admission, precreated starts and activation leases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, RunId, canonical_json
from simple_harness.execution.contracts.children import AttachmentPolicy
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.orchestration import RuntimeStartDispatchClaim
from simple_harness.runtime.start_snapshot import StartSnapshot
from simple_harness.workflow.execution_ports import (
    PrecreatedStartAction,
    PrecreatedStartDispatch,
    ResumeAdmissionReceipt,
    StartAdmissionReceipt,
    StartAdmissionRequest,
    StartClaimAction,
    StartMode,
    StartPhase,
    WorkflowActivation,
    WorkflowRecoveryReceiptKind,
    WorkflowRecoveryWork,
    WorkflowTransaction,
)
from simple_harness.workflow.lease import WorkflowLease
from simple_harness.workflow.native import NativeWorkflowExecutable

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import RuntimeStartDispatchClaim
    from simple_harness.runtime.start_snapshot import StartSnapshot
from .core import ExecutionUnitOfWorkCore
from .support import _child_terminal_receipt, _fault, _object_json


def _validate_workflow_page_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("workflow page limit must be between 1 and 50")


class WorkflowStartMixin(ExecutionUnitOfWorkCore):
    """Workflow start admission, precreated starts and activation leases."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _resume_receipt(
            self,
            row: sqlite3.Row,
            *,
            connection: sqlite3.Connection | None = None,
            include_activation: bool = True,
        ) -> ResumeAdmissionReceipt: ...

    async def admit_start_standalone(
        self,
        transaction: WorkflowTransaction,
        request: StartAdmissionRequest,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> StartAdmissionReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        if request.mode is not StartMode.STANDALONE:
            raise UnitOfWorkConflict("standalone admission rejects precreated mode")
        payload_json, fingerprint, run_id, trace_id, thread_id = self._start_identity(request)
        existing = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE request_key=?",
            (request.request_key,),
        ).fetchone()
        if existing is not None:
            if str(existing["request_fingerprint"]) != fingerprint:
                raise UnitOfWorkConflict("start request key reused with different payload")
            return self._start_receipt(existing, connection=tx.connection)
        snapshot = json.loads(payload_json)
        snapshot.update({"resolved_run_id": run_id, "trace_id": trace_id, "thread_id": thread_id})
        snapshot_json = canonical_json(snapshot)
        _fault(fault, "workflow:admit_start_standalone:before_runs_write")
        tx.connection.execute(
            "INSERT OR IGNORE INTO execution_sessions(session_id,user_id,created_at) "
            "VALUES(?,'harness-system',?)",
            (request.session_id, now),
        )
        tx.connection.execute(
            "INSERT INTO"
            " runs(run_id,execution_session_id,request_id,root_run_id,parent_run_id,profile_key,driver_kind,state,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,NULL,?,?,'created',0,?,?)",
            (
                run_id,
                request.session_id,
                request.request_id,
                run_id,
                request.profile_key,
                "workflow",
                now,
                now,
            ),
        )
        _fault(fault, "workflow:admit_start_standalone:after_runs_write")
        _fault(fault, "workflow:admit_start_standalone:before_run_start_snapshots_write")
        tx.connection.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json,snapshot_hash,created_at)"
            " VALUES(?,?,?,?)",
            (
                run_id,
                snapshot_json,
                hashlib.sha256(snapshot_json.encode()).hexdigest(),
                now,
            ),
        )
        _fault(fault, "workflow:admit_start_standalone:after_run_start_snapshots_write")
        _fault(
            fault,
            "workflow:admit_start_standalone:before_workflow_start_admissions_write",
        )
        tx.connection.execute(
            "INSERT INTO"
            " workflow_start_admissions(request_key,request_id,request_fingerprint,request_json,mode,run_id,trace_id,thread_id,phase,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?, 'admitted',0,?,?)",
            (
                request.request_key,
                request.request_id,
                fingerprint,
                payload_json,
                request.mode.value,
                run_id,
                trace_id,
                thread_id,
                now,
                now,
            ),
        )
        _fault(
            fault,
            "workflow:admit_start_standalone:after_workflow_start_admissions_write",
        )
        return self._start_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_start_admissions WHERE request_key=?",
                (request.request_key,),
            ).fetchone(),
            connection=tx.connection,
        )

    @staticmethod
    def _require_runtime_and_fence(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
        require_exact_expiry: bool = True,
    ) -> None:
        runtime = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence = connection.execute(
            "SELECT owner_id,epoch,runtime_lease_epoch,state FROM run_fences WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            execution_lease.run_id != run_id
            or execution_lease.namespace != RUNTIME_LEASE_NAMESPACE
            or (require_exact_expiry and execution_lease.expires_at <= now)
            or runtime is None
            or str(runtime["owner_id"]) != execution_lease.owner_id
            or int(runtime["epoch"]) != execution_lease.epoch
            or float(runtime["expires_at"]) <= now
            or (require_exact_expiry and float(runtime["expires_at"]) != execution_lease.expires_at)
            or (
                not require_exact_expiry
                and float(runtime["expires_at"]) < execution_lease.expires_at
            )
            or fence is None
            or tuple(fence)
            != (
                run_fence.owner_id,
                run_fence.epoch,
                run_fence.runtime_lease_epoch,
                "active",
            )
            or run_fence.run_id.value != run_id
            or run_fence.owner_id != execution_lease.owner_id
            or run_fence.runtime_lease_epoch != execution_lease.epoch
        ):
            raise UnitOfWorkConflict("precreated workflow authority is stale")

    @classmethod
    def _require_current_start_claim(
        cls,
        connection: sqlite3.Connection,
        *,
        receipt: StartAdmissionReceipt,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
    ) -> None:
        cls._require_runtime_and_fence(
            connection,
            run_id=receipt.run_id,
            execution_lease=execution_lease,
            run_fence=run_fence,
            now=now,
            require_exact_expiry=False,
        )
        runtime = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (receipt.run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        projection = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (receipt.run_id, receipt.request.checkpoint_namespace),
        ).fetchone()
        if (
            runtime is None
            or projection is None
            or receipt.claim_owner != execution_lease.owner_id
            or receipt.claim_epoch != execution_lease.epoch
            or receipt.claim_expires_at is None
            or receipt.claim_expires_at > float(runtime["expires_at"])
            or tuple(projection[:2]) != (execution_lease.owner_id, execution_lease.epoch)
            or float(projection["expires_at"]) != float(runtime["expires_at"])
            or float(projection["expires_at"]) <= now
        ):
            raise UnitOfWorkConflict("start claim authority is stale")

    @staticmethod
    def _require_workflow_handoff_authority(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        execution_lease: ExecutionLease,
        workflow_lease: WorkflowLease | None,
        now: float,
    ) -> None:
        run = connection.execute(
            "SELECT driver_kind FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise UnitOfWorkConflict("handoff Run does not exist")
        if str(run["driver_kind"]) != "workflow":
            if workflow_lease is not None:
                raise UnitOfWorkConflict("non-workflow handoff rejects workflow authority")
            return
        if workflow_lease is None:
            raise UnitOfWorkConflict("workflow handoff requires workflow lease")
        projection = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, workflow_lease.namespace),
        ).fetchone()
        runtime = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        admission = connection.execute(
            "SELECT json_extract(request_json,'$.checkpoint_namespace') AS namespace "
            "FROM workflow_start_admissions WHERE run_id=?",
            (run_id,),
        ).fetchone()
        fence = connection.execute(
            "SELECT owner_id,runtime_lease_epoch,state FROM run_fences WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            workflow_lease.run_id != run_id
            or workflow_lease.owner_id != execution_lease.owner_id
            or workflow_lease.runtime_lease_epoch != execution_lease.epoch
            or workflow_lease.expires_at != execution_lease.expires_at
            or admission is None
            or str(admission["namespace"]) != workflow_lease.namespace
            or runtime is None
            or tuple(runtime[:2]) != (execution_lease.owner_id, execution_lease.epoch)
            or float(runtime["expires_at"]) <= now
            or float(runtime["expires_at"]) < execution_lease.expires_at
            or projection is None
            or tuple(projection[:2]) != (workflow_lease.owner_id, workflow_lease.epoch)
            or float(projection["expires_at"]) != float(runtime["expires_at"])
            or float(projection["expires_at"]) < workflow_lease.expires_at
            or fence is None
            or tuple(fence) != (execution_lease.owner_id, execution_lease.epoch, "active")
        ):
            raise UnitOfWorkConflict("workflow handoff authority is stale")

    async def ensure_and_bind_precreated_start(
        self,
        transaction: WorkflowTransaction,
        request: StartAdmissionRequest,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        dispatch_claim: RuntimeStartDispatchClaim,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> PrecreatedStartDispatch:

        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        tx = self._assert_open_workflow_transaction(transaction)
        if request.mode is not StartMode.PRECREATED:
            raise UnitOfWorkConflict("precreated admission requires precreated mode")
        payload_json, fingerprint, run_id, trace_id, thread_id = self._start_identity(request)
        receipt: StartAdmissionReceipt | None = None
        existing = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE request_key=?",
            (request.request_key,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["request_fingerprint"]) != fingerprint
                or str(existing["request_json"]) != payload_json
                or str(existing["run_id"]) != run_id
            ):
                raise UnitOfWorkConflict("start request key reused with different payload")
            receipt = self._start_receipt(existing, connection=tx.connection)
            if receipt.phase is StartPhase.SETTLED:
                return PrecreatedStartDispatch(
                    PrecreatedStartAction.SETTLED,
                    receipt,
                    serialized_outcome=receipt.serialized_outcome,
                )
        if dispatch_claim.run_id != run_id:
            raise UnitOfWorkConflict("dispatch claim belongs to another Run")
        dispatch = tx.connection.execute(
            "SELECT * FROM runtime_start_dispatch_claims WHERE claim_id=?",
            (dispatch_claim.claim_id,),
        ).fetchone()
        if dispatch is None:
            raise UnitOfWorkConflict("runtime start dispatch claim is missing")
        stable_claim = (
            str(dispatch["run_id"]),
            str(dispatch["owner_id"]),
            int(dispatch["runtime_lease_epoch"]),
            int(dispatch["claim_epoch"]),
        )
        if stable_claim != (
            dispatch_claim.run_id,
            dispatch_claim.owner_id,
            dispatch_claim.runtime_lease_epoch,
            dispatch_claim.claim_epoch,
        ):
            raise UnitOfWorkConflict("runtime start dispatch capability is stale")
        if existing is not None and str(dispatch["state"]) != "consumed":
            raise UnitOfWorkConflict("start request key reused with different payload")
        run = tx.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        snapshot = tx.connection.execute(
            "SELECT snapshot_json FROM run_start_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
        if (
            run is None
            or str(run["driver_kind"]) != "workflow"
            or str(run["state"]) != "running"
            or snapshot is None
        ):
            raise UnitOfWorkConflict("precreated generic Run identity changed")
        parsed_snapshot = StartSnapshot.from_json(
            cast(dict[str, JsonValue], json.loads(str(snapshot["snapshot_json"])))
        )
        if parsed_snapshot.workflow_admission != request:
            raise UnitOfWorkConflict("precreated start snapshot changed")
        if existing is not None:
            assert receipt is not None
            self._require_current_start_claim(
                tx.connection,
                receipt=receipt,
                execution_lease=execution_lease,
                run_fence=run_fence,
                now=now,
            )
            if str(existing["claim_owner"]) != execution_lease.owner_id:
                raise UnitOfWorkConflict("workflow start receipt belongs to another activation")
            if receipt.claim_action is not StartClaimAction.NEW:
                raise UnitOfWorkConflict("first start replay no longer has NEW claim authority")
            return PrecreatedStartDispatch(
                PrecreatedStartAction.NEW_CLAIMED,
                receipt,
                activation=receipt.activation,
            )
        if str(dispatch["state"]) != "claimed" or float(dispatch["expires_at"]) <= now:
            raise UnitOfWorkConflict("runtime start dispatch claim is inactive")
        self._require_runtime_and_fence(
            tx.connection,
            run_id=run_id,
            execution_lease=execution_lease,
            run_fence=run_fence,
            now=now,
            require_exact_expiry=False,
        )
        current_runtime = tx.connection.execute(
            "SELECT expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        assert current_runtime is not None
        current_expiry = float(current_runtime["expires_at"])
        namespace = request.checkpoint_namespace
        _fault(fault, "workflow:ensure_precreated_start:before_workflow_leases_write")
        tx.connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?)",
            (
                run_id,
                namespace,
                execution_lease.owner_id,
                execution_lease.epoch,
                current_expiry,
            ),
        )
        _fault(fault, "workflow:ensure_precreated_start:after_workflow_leases_write")
        _fault(
            fault,
            "workflow:ensure_precreated_start:before_workflow_start_admissions_write",
        )
        tx.connection.execute(
            "INSERT INTO"
            " workflow_start_admissions(request_key,request_id,request_fingerprint,request_json,mode,run_id,trace_id,thread_id,phase,version,claim_action,claim_owner,claim_epoch,claim_expires_at,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?, 'claimed',0,'new',?,?,?,?,?)",
            (
                request.request_key,
                request.request_id,
                fingerprint,
                payload_json,
                request.mode.value,
                run_id,
                trace_id,
                thread_id,
                execution_lease.owner_id,
                execution_lease.epoch,
                current_expiry,
                now,
                now,
            ),
        )
        _fault(
            fault,
            "workflow:ensure_precreated_start:after_workflow_start_admissions_write",
        )
        _fault(
            fault,
            "workflow:ensure_precreated_start:before_runtime_start_dispatch_claims_write",
        )
        changed = tx.connection.execute(
            "UPDATE runtime_start_dispatch_claims SET"
            " state='consumed',version=version+1,updated_at=? WHERE claim_id=? AND state='claimed'"
            " AND owner_id=? AND runtime_lease_epoch=? AND claim_epoch=?",
            (
                now,
                dispatch_claim.claim_id,
                dispatch_claim.owner_id,
                dispatch_claim.runtime_lease_epoch,
                dispatch_claim.claim_epoch,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("runtime start dispatch consume CAS failed")
        _fault(
            fault,
            "workflow:ensure_precreated_start:after_runtime_start_dispatch_claims_write",
        )
        tx.register_after_commit_fault("workflow:ensure_precreated_start:after_commit")
        stored = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE request_key=?",
            (request.request_key,),
        ).fetchone()
        assert stored is not None
        receipt = self._start_receipt(stored, connection=tx.connection)
        return PrecreatedStartDispatch(
            PrecreatedStartAction.NEW_CLAIMED,
            receipt,
            activation=receipt.activation,
        )

    async def recover_precreated_start(
        self,
        transaction: WorkflowTransaction,
        recovery_work: WorkflowRecoveryWork,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> PrecreatedStartDispatch:

        del ttl_seconds
        tx = self._assert_open_workflow_transaction(transaction)
        if recovery_work.receipt_kind is not WorkflowRecoveryReceiptKind.START:
            raise UnitOfWorkConflict("precreated start recovery work has wrong kind")
        row = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE request_key=?",
            (recovery_work.receipt_id,),
        ).fetchone()
        if row is None:
            raise UnitOfWorkConflict("precreated start receipt is missing")
        current = self._start_receipt(
            row,
            connection=tx.connection,
            activation_override=(
                recovery_work.receipt_snapshot.activation
                if int(row["version"]) == recovery_work.receipt_version
                else None
            ),
        )
        if (
            current.request != recovery_work.receipt_snapshot.request
            or current.request_fingerprint != recovery_work.request_fingerprint
        ):
            raise UnitOfWorkConflict("precreated start recovery identity changed")
        if current.phase is StartPhase.SETTLED:
            return PrecreatedStartDispatch(
                PrecreatedStartAction.SETTLED,
                current,
                serialized_outcome=current.serialized_outcome,
            )
        run = tx.connection.execute(
            "SELECT state FROM runs WHERE run_id=?", (current.run_id,)
        ).fetchone()
        if run is None or str(run["state"]) != RunState.RUNNING.value:
            raise UnitOfWorkConflict("precreated recovery requires RUNNING Run")
        if (
            current.version == recovery_work.receipt_version + 1
            and current.request == recovery_work.receipt_snapshot.request
            and current.request_fingerprint == recovery_work.request_fingerprint
            and current.phase == recovery_work.receipt_snapshot.phase
            and current.claim_owner == execution_lease.owner_id
        ):
            self._require_current_start_claim(
                tx.connection,
                receipt=current,
                execution_lease=execution_lease,
                run_fence=run_fence,
                now=now,
            )
            if current.claim_action is not StartClaimAction.RESUME:
                raise UnitOfWorkConflict("recovered start replay lacks RESUME claim authority")
            action = (
                PrecreatedStartAction.RESUME_RUNNING
                if current.phase is StartPhase.RUNNING
                else PrecreatedStartAction.RESUME_CLAIMED
            )
            return PrecreatedStartDispatch(
                action,
                current,
                activation=current.activation,
            )
        if current != recovery_work.receipt_snapshot:
            raise UnitOfWorkConflict("precreated start recovery snapshot changed")
        if (
            current.request_fingerprint != recovery_work.request_fingerprint
            or current.version != recovery_work.receipt_version
            or current.request.mode is not StartMode.PRECREATED
        ):
            raise UnitOfWorkConflict("precreated start recovery identity changed")
        if current.phase not in {StartPhase.CLAIMED, StartPhase.RUNNING}:
            raise UnitOfWorkConflict("precreated start recovery phase is invalid")
        self._require_runtime_and_fence(
            tx.connection,
            run_id=current.run_id,
            execution_lease=execution_lease,
            run_fence=run_fence,
            now=now,
            require_exact_expiry=False,
        )
        runtime = tx.connection.execute(
            "SELECT expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (current.run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        assert runtime is not None
        current_expiry = float(runtime["expires_at"])
        namespace = current.request.checkpoint_namespace
        projection = tx.connection.execute(
            "SELECT owner_id,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (current.run_id, namespace),
        ).fetchone()
        if (
            projection is not None
            and float(projection["expires_at"]) > now
            and str(projection["owner_id"]) != execution_lease.owner_id
        ):
            raise UnitOfWorkConflict("precreated workflow owner remains active")
        if current.phase is StartPhase.RUNNING:
            head = tx.connection.execute(
                "SELECT 1 FROM workflow_checkpoints WHERE run_id=? AND namespace=? LIMIT 1",
                (current.run_id, namespace),
            ).fetchone()
            if head is None:
                raise UnitOfWorkConflict("RUNNING workflow start has no genesis head")
        _fault(fault, "workflow:recover_precreated_start:before_workflow_leases_write")
        tx.connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(run_id,namespace) DO UPDATE SET"
            " owner_id=excluded.owner_id,epoch=excluded.epoch,expires_at=excluded.expires_at",
            (
                current.run_id,
                namespace,
                execution_lease.owner_id,
                execution_lease.epoch,
                current_expiry,
            ),
        )
        _fault(fault, "workflow:recover_precreated_start:after_workflow_leases_write")
        _fault(
            fault,
            "workflow:recover_precreated_start:before_workflow_start_admissions_write",
        )
        changed = tx.connection.execute(
            "UPDATE workflow_start_admissions SET version=version+1,"
            "claim_action='resume',claim_owner=?,claim_epoch=?,claim_expires_at=?,"
            "updated_at=? WHERE request_key=? AND version=? AND phase=?",
            (
                execution_lease.owner_id,
                execution_lease.epoch,
                current_expiry,
                now,
                current.request_key,
                current.version,
                current.phase.value,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("precreated start recovery CAS failed")
        _fault(
            fault,
            "workflow:recover_precreated_start:after_workflow_start_admissions_write",
        )
        tx.register_after_commit_fault("workflow:recover_precreated_start:after_commit")
        updated = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE request_key=?",
            (current.request_key,),
        ).fetchone()
        assert updated is not None
        receipt = self._start_receipt(updated, connection=tx.connection)
        action = (
            PrecreatedStartAction.RESUME_RUNNING
            if current.phase is StartPhase.RUNNING
            else PrecreatedStartAction.RESUME_CLAIMED
        )
        return PrecreatedStartDispatch(
            action,
            receipt,
            activation=receipt.activation,
        )

    def list_unsettled_start_admissions(
        self,
        snapshot_cursor: str | None,
        *,
        limit: int,
    ) -> tuple[tuple[StartAdmissionReceipt, ...], str | None]:
        _validate_workflow_page_limit(limit)
        rows = self.database.connection.execute(
            "SELECT * FROM workflow_start_admissions "
            "WHERE phase IN ('admitted','claimed','running') "
            "AND (? IS NULL OR request_key>?) ORDER BY request_key LIMIT ?",
            (snapshot_cursor, snapshot_cursor, limit + 1),
        ).fetchall()
        page = rows[:limit]
        return tuple(self._start_receipt(row) for row in page), (
            None if len(rows) <= limit else str(page[-1]["request_key"])
        )

    def list_unsettled_resume_admissions(
        self,
        snapshot_cursor: str | None,
        *,
        limit: int,
    ) -> tuple[tuple[ResumeAdmissionReceipt, ...], str | None]:
        _validate_workflow_page_limit(limit)
        rows = self.database.connection.execute(
            "SELECT * FROM workflow_resume_admissions WHERE phase IN"
            " ('admitted','claimed','retry_wait') AND (? IS NULL OR receipt_id>?) ORDER BY"
            " receipt_id LIMIT ?",
            (snapshot_cursor, snapshot_cursor, limit + 1),
        ).fetchall()
        page = rows[:limit]
        return tuple(self._resume_receipt(row) for row in page), (
            None if len(rows) <= limit else str(page[-1]["receipt_id"])
        )

    def _acquire_activation_rows(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        namespace: str,
        owner_id: str,
        now: float,
        ttl_seconds: float,
    ) -> WorkflowActivation:
        current = connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        if (
            current is not None
            and float(current["expires_at"]) > now
            and str(current["owner_id"]) != owner_id
        ):
            raise UnitOfWorkConflict("runtime activation is owned by another worker")
        epoch = (0 if current is None else int(current["epoch"])) + 1
        if (
            current is not None
            and float(current["expires_at"]) > now
            and str(current["owner_id"]) == owner_id
        ):
            epoch = int(current["epoch"])
        expires_at = now + ttl_seconds
        connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(run_id,namespace) DO UPDATE SET"
            " owner_id=excluded.owner_id,epoch=excluded.epoch,expires_at=excluded.expires_at",
            (run_id, RUNTIME_LEASE_NAMESPACE, owner_id, epoch, expires_at),
        )
        connection.execute(
            "INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(run_id,namespace) DO UPDATE SET"
            " owner_id=excluded.owner_id,epoch=excluded.epoch,expires_at=excluded.expires_at",
            (run_id, namespace, owner_id, epoch, expires_at),
        )
        fence = connection.execute(
            "SELECT epoch FROM run_fences WHERE run_id=?", (run_id,)
        ).fetchone()
        fence_epoch = (0 if fence is None else int(fence["epoch"])) + 1
        connection.execute(
            "INSERT INTO"
            " run_fences(run_id,epoch,owner_id,runtime_lease_epoch,state,acquired_at,released_at)"
            " VALUES(?,?,?,?, 'active',?,NULL) ON CONFLICT(run_id) DO UPDATE SET"
            " epoch=excluded.epoch,owner_id=excluded.owner_id,runtime_lease_epoch=excluded.runtime_lease_epoch,state='active',acquired_at=excluded.acquired_at,released_at=NULL",  # noqa: E501
            (run_id, fence_epoch, owner_id, epoch, now),
        )
        return WorkflowActivation(
            ExecutionLease(run_id, RUNTIME_LEASE_NAMESPACE, owner_id, epoch, expires_at),
            RunFenceLease(RunId(run_id), fence_epoch, owner_id, epoch),
            WorkflowLease(run_id, owner_id, epoch, expires_at, epoch, namespace),
        )

    async def claim_activation(
        self,
        transaction: WorkflowTransaction,
        run_id: str,
        expected_run_version: int,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowActivation:
        tx = self._assert_open_workflow_transaction(transaction)
        receipt = tx.connection.execute(
            "SELECT * FROM workflow_start_admissions WHERE run_id=?", (run_id,)
        ).fetchone()
        run = tx.connection.execute(
            "SELECT version,state FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if receipt is None or str(receipt["mode"]) != StartMode.STANDALONE.value:
            raise UnitOfWorkConflict("start receipt is not claimable")
        phase = str(receipt["phase"])
        if phase not in {
            StartPhase.ADMITTED.value,
            StartPhase.CLAIMED.value,
            StartPhase.RUNNING.value,
        }:
            raise UnitOfWorkConflict("start receipt is not claimable")
        namespace = str(json.loads(str(receipt["request_json"]))["checkpoint_namespace"])
        if phase in {StartPhase.CLAIMED.value, StartPhase.RUNNING.value}:
            runtime = tx.connection.execute(
                "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                "WHERE run_id=? AND namespace=?",
                (run_id, RUNTIME_LEASE_NAMESPACE),
            ).fetchone()
            if runtime is not None and float(runtime["expires_at"]) > now:
                if phase == StartPhase.RUNNING.value:
                    raise UnitOfWorkConflict("running workflow activation has not expired")
                if str(runtime["owner_id"]) != owner_id:
                    raise UnitOfWorkConflict("workflow activation has an active owner")
                activation = self._read_workflow_activation(
                    run_id,
                    namespace=namespace,
                    owner_id=owner_id,
                    connection=tx.connection,
                )
                if (
                    str(receipt["claim_owner"]) != owner_id
                    or int(receipt["claim_epoch"]) != activation.workflow_lease.epoch
                    or float(receipt["claim_expires_at"]) > activation.execution_lease.expires_at
                ):
                    raise UnitOfWorkConflict("workflow activation receipt is stale")
                return activation
        if (
            run is None
            or int(run["version"]) != expected_run_version
            or str(run["state"])
            not in ({"running"} if phase == StartPhase.RUNNING.value else {"created", "queued"})
        ):
            raise UnitOfWorkConflict("workflow Run is not claimable")
        _fault(fault, "workflow:claim_activation:before_workflow_leases_write")
        activation = self._acquire_activation_rows(
            tx.connection,
            run_id=run_id,
            namespace=namespace,
            owner_id=owner_id,
            now=now,
            ttl_seconds=ttl_seconds,
        )
        _fault(fault, "workflow:claim_activation:after_workflow_leases_write")
        _fault(fault, "workflow:claim_activation:before_workflow_start_admissions_write")
        claim_action = (
            StartClaimAction.NEW if phase == StartPhase.ADMITTED.value else StartClaimAction.RESUME
        )
        changed = tx.connection.execute(
            "UPDATE workflow_start_admissions SET phase='claimed',version=version+1,"
            "claim_action=?,claim_owner=?,claim_epoch=?,claim_expires_at=?,"
            "updated_at=? WHERE run_id=? AND phase=? AND version=?",
            (
                claim_action.value,
                owner_id,
                activation.execution_lease.epoch,
                activation.execution_lease.expires_at,
                now,
                run_id,
                phase,
                int(receipt["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("start claim CAS failed")
        _fault(fault, "workflow:claim_activation:after_workflow_start_admissions_write")
        return activation

    async def bind_activation(
        self,
        transaction: WorkflowTransaction,
        run_id: str,
        expected_run_version: int,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowActivation:
        del ttl_seconds
        tx = self._assert_open_workflow_transaction(transaction)
        run = tx.connection.execute("SELECT version FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or int(run["version"]) != expected_run_version:
            raise UnitOfWorkConflict("precreated Run version changed")
        self._require_runtime_and_fence(
            tx.connection,
            run_id=run_id,
            execution_lease=execution_lease,
            run_fence=run_fence,
            now=now,
        )
        receipt = tx.connection.execute(
            "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if receipt is None:
            raise UnitOfWorkConflict("workflow start admission is missing")
        namespace = str(json.loads(str(receipt["request_json"]))["checkpoint_namespace"])
        workflow = tx.connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (run_id, namespace),
        ).fetchone()
        if workflow is None or tuple(workflow) != (
            execution_lease.owner_id,
            execution_lease.epoch,
            execution_lease.expires_at,
        ):
            raise UnitOfWorkConflict("workflow lease projection changed")
        return WorkflowActivation(
            execution_lease,
            run_fence,
            WorkflowLease(
                run_id,
                execution_lease.owner_id,
                execution_lease.epoch,
                execution_lease.expires_at,
                execution_lease.epoch,
                namespace,
            ),
        )

    async def renew_activation(
        self,
        transaction: WorkflowTransaction,
        activation: WorkflowActivation,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowActivation:
        tx = self._assert_open_workflow_transaction(transaction)
        self._require_runtime_and_fence(
            tx.connection,
            run_id=activation.execution_lease.run_id,
            execution_lease=activation.execution_lease,
            run_fence=activation.run_fence,
            now=now,
        )
        expires_at = now + ttl_seconds
        _fault(fault, "workflow:renew_activation:before_runtime_lease_write")
        changed = tx.connection.execute(
            "UPDATE workflow_leases SET expires_at=? WHERE run_id=? AND namespace=? AND owner_id=?"
            " AND epoch=? AND expires_at=?",
            (
                expires_at,
                activation.execution_lease.run_id,
                RUNTIME_LEASE_NAMESPACE,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
                activation.execution_lease.expires_at,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("runtime lease renew CAS failed")
        _fault(fault, "workflow:renew_activation:after_runtime_lease_write")
        _fault(fault, "workflow:renew_activation:before_workflow_lease_write")
        changed = tx.connection.execute(
            "UPDATE workflow_leases SET expires_at=? WHERE run_id=? AND namespace=? AND owner_id=?"
            " AND epoch=? AND expires_at=?",
            (
                expires_at,
                activation.workflow_lease.run_id,
                activation.workflow_lease.namespace,
                activation.workflow_lease.owner_id,
                activation.workflow_lease.epoch,
                activation.workflow_lease.expires_at,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow lease renew CAS failed")
        _fault(fault, "workflow:renew_activation:after_workflow_lease_write")
        return WorkflowActivation(
            ExecutionLease(
                activation.execution_lease.run_id,
                RUNTIME_LEASE_NAMESPACE,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
                expires_at,
            ),
            activation.run_fence,
            WorkflowLease(
                activation.workflow_lease.run_id,
                activation.workflow_lease.owner_id,
                activation.workflow_lease.epoch,
                expires_at,
                activation.execution_lease.epoch,
                activation.workflow_lease.namespace,
            ),
        )

    async def release_activation(
        self,
        transaction: WorkflowTransaction,
        activation: WorkflowActivation,
        expected_run_version: int,
        outcome: Mapping[str, JsonValue],
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> None:
        tx = self._assert_open_workflow_transaction(transaction)
        fault = fault or tx._fault
        run = tx.connection.execute(
            "SELECT state,version,parent_run_id FROM runs WHERE run_id=?",
            (activation.execution_lease.run_id,),
        ).fetchone()
        if run is None or int(run["version"]) != expected_run_version:
            raise UnitOfWorkConflict("release Run version changed")
        terminal_status = outcome.get("terminal_status")
        if terminal_status is not None:
            if terminal_status != str(run["state"]):
                raise UnitOfWorkConflict("release terminal outcome differs from Run")
            if run["parent_run_id"] is not None:
                self._commit_native_child_terminal(
                    tx.connection,
                    activation=activation,
                    expected_run_version=expected_run_version,
                    terminal_state=RunState(str(terminal_status)),
                    output=outcome.get("output"),
                    now=now,
                    fault=fault,
                )
            self._record_workflow_terminal_outcome(
                tx.connection,
                activation=activation,
                expected_run_version=expected_run_version,
                terminal_state=RunState(str(terminal_status)),
                outcome=outcome,
                now=now,
                fault=fault,
            )
        _fault(fault, "workflow:release_activation:before_workflow_leases_write")
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
        _fault(fault, "workflow:release_activation:after_workflow_leases_write")
        _fault(fault, "workflow:release_activation:before_run_fences_write")
        changed = tx.connection.execute(
            "UPDATE run_fences SET state='released',released_at=? WHERE run_id=? AND owner_id=? AND"
            " epoch=? AND runtime_lease_epoch=? AND state='active'",
            (
                now,
                activation.execution_lease.run_id,
                activation.run_fence.owner_id,
                activation.run_fence.epoch,
                activation.execution_lease.epoch,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("activation release fence CAS failed")
        _fault(fault, "workflow:release_activation:after_run_fences_write")

    def _record_workflow_terminal_outcome(
        self,
        connection: sqlite3.Connection,
        *,
        activation: WorkflowActivation,
        expected_run_version: int,
        terminal_state: RunState,
        outcome: Mapping[str, JsonValue],
        now: float,
        fault: FaultHook | None,
    ) -> None:
        """Project the Native terminal transaction into its durable verifier chain."""

        run_id = activation.execution_lease.run_id
        checkpoint = connection.execute(
            "SELECT checkpoint_id,namespace,version,checkpoint_hash "
            "FROM workflow_checkpoints WHERE run_id=? AND namespace=? "
            "ORDER BY version DESC LIMIT 1",
            (run_id, activation.workflow_lease.namespace),
        ).fetchone()
        if checkpoint is None:
            raise UnitOfWorkConflict("terminal workflow checkpoint is missing")

        terminal_payload_value = json.loads(canonical_json(cast(JsonValue, dict(outcome))))
        if not isinstance(terminal_payload_value, dict):
            raise UnitOfWorkConflict("terminal workflow payload is invalid")
        terminal_payload = cast(dict[str, JsonValue], terminal_payload_value)
        event_payload_json = canonical_json(terminal_payload)
        checkpoint_id = str(checkpoint["checkpoint_id"])
        identity = f"{run_id}|{checkpoint_id}|{terminal_state.value}"
        event_id = hashlib.sha256(
            f"simple-harness.workflow.terminal-event|{identity}".encode()
        ).hexdigest()
        fence_receipt_id = hashlib.sha256(
            f"simple-harness.workflow.terminal-fence|{identity}".encode()
        ).hexdigest()
        receipt_id = hashlib.sha256(
            f"simple-harness.workflow.terminal-receipt|{identity}".encode()
        ).hexdigest()

        delivery_rows = connection.execute(
            "SELECT delivery_id,sink_kind,idempotency_key,payload_json,created_at "
            "FROM delivery_outbox WHERE run_id=? ORDER BY delivery_id",
            (run_id,),
        ).fetchall()
        delivery_ids: list[JsonValue] = [str(row["delivery_id"]) for row in delivery_rows]
        delivery_facts: list[JsonValue] = [
            cast(
                JsonValue,
                {
                    "delivery_id": str(row["delivery_id"]),
                    "sink_kind": str(row["sink_kind"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "payload": cast(JsonValue, json.loads(str(row["payload_json"]))),
                    "created_at": float(row["created_at"]),
                },
            )
            for row in delivery_rows
        ]
        canonical_outcome: dict[str, JsonValue] = {
            "receipt_id": receipt_id,
            "run_id": run_id,
            "checkpoint_id": checkpoint_id,
            "state": terminal_state.value,
            "event_id": event_id,
            "delivery_ids": delivery_ids,
            "terminal_payload": terminal_payload,
            "delivery_facts": delivery_facts,
        }
        outcome_hash = hashlib.sha256(canonical_json(canonical_outcome).encode()).hexdigest()

        _fault(fault, "workflow:release_activation:before_terminal_event_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=run_id,
            kind=f"run.{terminal_state.value}",
            payload=terminal_payload,
            now=now,
        )
        _fault(fault, "workflow:release_activation:after_terminal_event_write")
        _fault(fault, "workflow:release_activation:before_terminal_fence_write")
        connection.execute(
            "INSERT INTO workflow_terminal_fence_receipts("
            "receipt_id,run_id,owner_id,runtime_lease_epoch,run_fence_epoch,created_at"
            ") VALUES(?,?,?,?,?,?)",
            (
                fence_receipt_id,
                run_id,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
                activation.run_fence.epoch,
                now,
            ),
        )
        _fault(fault, "workflow:release_activation:after_terminal_fence_write")
        _fault(fault, "workflow:release_activation:before_terminal_receipt_write")
        connection.execute(
            "INSERT INTO workflow_terminal_receipts("
            "receipt_id,run_id,checkpoint_id,checkpoint_namespace,checkpoint_version,"
            "checkpoint_hash,state,run_version,event_id,event_payload_hash,"
            "delivery_ids_json,delivery_facts_json,terminal_payload_json,"
            "terminal_fence_receipt_id,outcome_hash,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                run_id,
                checkpoint_id,
                str(checkpoint["namespace"]),
                int(checkpoint["version"]),
                str(checkpoint["checkpoint_hash"]),
                terminal_state.value,
                expected_run_version,
                event_id,
                hashlib.sha256(event_payload_json.encode()).hexdigest(),
                canonical_json(delivery_ids),
                canonical_json(cast(JsonValue, delivery_facts)),
                event_payload_json,
                fence_receipt_id,
                outcome_hash,
                now,
            ),
        )
        _fault(fault, "workflow:release_activation:after_terminal_receipt_write")

    def _commit_native_child_terminal(
        self,
        connection: sqlite3.Connection,
        *,
        activation: WorkflowActivation,
        expected_run_version: int,
        terminal_state: RunState,
        output: JsonValue | None,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        """Close a Native child inside its terminal checkpoint transaction."""

        child_run_id = activation.execution_lease.run_id
        command = connection.execute(
            "SELECT * FROM child_commands WHERE child_run_id=?", (child_run_id,)
        ).fetchone()
        if command is None:
            raise UnitOfWorkConflict("workflow child has no durable launch command")
        command_id = str(command["command_id"])
        parent_run_id = str(command["parent_run_id"])
        link = connection.execute(
            "SELECT attachment_policy FROM run_links WHERE parent_run_id=? AND child_run_id=?",
            (parent_run_id, child_run_id),
        ).fetchone()
        if link is None:
            raise UnitOfWorkConflict("workflow child attachment policy is missing")
        policy = AttachmentPolicy(str(link["attachment_policy"]))
        previous_version = expected_run_version - 1
        if previous_version < 0:
            raise UnitOfWorkConflict("workflow child terminal version is invalid")
        identity = f"{child_run_id}:{previous_version}:{terminal_state.value}"
        signal_id = None if policy is AttachmentPolicy.DETACHED else f"{identity}:signal"
        event_id = f"{identity}:event"
        receipt_id = f"{identity}:receipt"

        terminal_state_value: Mapping[str, object] = (
            output if isinstance(output, Mapping) else {"values": {}}
        )
        terminal_intents = NativeWorkflowExecutable.terminal_intents(
            terminal_state_value,
            run_id=child_run_id,
            status=terminal_state.value,
            error=None,
            recovery_action=None,
        )
        if not terminal_intents or terminal_intents[-1].event_type != "workflow.final":
            raise UnitOfWorkConflict("workflow child terminal public projection is missing")
        result = cast(JsonValue, dict(terminal_intents[-1].payload))
        terminal_payload: dict[str, JsonValue] = {
            "status": terminal_state.value,
            "result": result,
        }
        payload_json = _object_json(terminal_payload, "terminal_payload")
        outcome_hash = hashlib.sha256(
            canonical_json(
                {
                    "command_id": command_id,
                    "expected_child_version": previous_version,
                    "terminal_state": terminal_state.value,
                    "signal_id": signal_id,
                    "event_id": event_id,
                    "payload": json.loads(payload_json),
                }
            ).encode()
        ).hexdigest()
        existing = connection.execute(
            "SELECT * FROM child_terminal_receipts WHERE command_id=?", (command_id,)
        ).fetchone()
        if existing is not None:
            receipt = _child_terminal_receipt(existing)
            expected = (
                receipt_id,
                child_run_id,
                terminal_state.value,
                outcome_hash,
                signal_id,
                event_id,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
                activation.run_fence.epoch,
            )
            actual = (
                receipt.receipt_id,
                receipt.child_run_id,
                receipt.terminal_state,
                receipt.outcome_hash,
                receipt.signal_id,
                receipt.event_id,
                receipt.owner_id,
                receipt.runtime_lease_epoch,
                receipt.fence_epoch,
            )
            if actual != expected:
                raise UnitOfWorkConflict("workflow child terminal receipt differs")
            return
        _fault(fault, "workflow:release_activation:before_child_command_write")
        changed = connection.execute(
            "UPDATE child_commands SET state='acked',updated_at=? "
            "WHERE command_id=? AND state IN ('pending','scheduled')",
            (now, command_id),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow child command terminal CAS failed")
        _fault(fault, "workflow:release_activation:after_child_command_write")
        if signal_id is not None:
            _fault(fault, "workflow:release_activation:before_child_signal_write")
            connection.execute(
                "INSERT INTO child_signals("
                "signal_id,parent_run_id,child_run_id,payload_json,state,version,"
                "created_at,updated_at) VALUES(?,?,?,?,'pending',0,?,?)",
                (signal_id, parent_run_id, child_run_id, payload_json, now, now),
            )
            _fault(fault, "workflow:release_activation:after_child_signal_write")
        _fault(fault, "workflow:release_activation:before_child_event_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=child_run_id,
            kind=f"child.{terminal_state.value}",
            payload={
                "command_id": command_id,
                "signal_id": signal_id,
                "terminal": terminal_payload,
            },
            now=now,
        )
        _fault(fault, "workflow:release_activation:after_child_event_write")
        _fault(fault, "workflow:release_activation:before_child_receipt_write")
        connection.execute(
            "INSERT INTO child_terminal_receipts("
            "receipt_id,command_id,child_run_id,terminal_state,outcome_hash,"
            "signal_id,event_id,owner_id,runtime_lease_epoch,fence_epoch,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                command_id,
                child_run_id,
                terminal_state.value,
                outcome_hash,
                signal_id,
                event_id,
                activation.execution_lease.owner_id,
                activation.execution_lease.epoch,
                activation.run_fence.epoch,
                now,
            ),
        )
        _fault(fault, "workflow:release_activation:after_child_receipt_write")
