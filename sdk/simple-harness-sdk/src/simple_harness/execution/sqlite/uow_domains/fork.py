# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow fork preparation, checkpoints, commit and rollback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, canonical_json, freeze_json
from simple_harness.execution.uow import FaultHook, UnitOfWorkConflict
from simple_harness.workflow.execution_ports import (
    DangerousEffectConfirmation,
    DangerousEffectObservation,
    ForkPhase,
    ForkReceipt,
    ForkRequest,
    ForkWriteLease,
    RecoverySnapshot,
    StartMode,
    WorkflowTransaction,
)

from .core import ExecutionUnitOfWorkCore
from .support import _fault, _thaw


class WorkflowForkMixin(ExecutionUnitOfWorkCore):
    """Workflow fork preparation, checkpoints, commit and rollback."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _recovery_snapshot_from_connection(
            self, connection: sqlite3.Connection, run_id: str
        ) -> RecoverySnapshot: ...

    def _fork_receipt(self, row: sqlite3.Row) -> ForkReceipt:
        raw = json.loads(str(row["request_json"]))
        confirmation_raw = raw.get("dangerous_confirmation")
        confirmation: DangerousEffectConfirmation | None = None
        if isinstance(confirmation_raw, dict):
            observations = tuple(
                DangerousEffectObservation(**item) for item in confirmation_raw["observations"]
            )
            confirmation = DangerousEffectConfirmation(
                str(confirmation_raw["scope"]),
                observations,
                str(confirmation_raw["digest"]),
            )
        request = ForkRequest(
            fork_id=str(raw["fork_id"]),
            fingerprint=str(raw["fingerprint"]),
            source_run_id=str(raw["source_run_id"]),
            source_namespace=str(raw["source_namespace"]),
            source_checkpoint_id=str(raw["source_checkpoint_id"]),
            source_run_version=int(raw["source_run_version"]),
            source_head=str(raw["source_head"]),
            engine_hash=str(raw["engine_hash"]),
            manifest_hash=str(raw["manifest_hash"]),
            implementation_hash=str(raw["implementation_hash"]),
            schema_hash=str(raw["schema_hash"]),
            patch=raw["patch"],
            dangerous_confirmation=confirmation,
        )
        outcome = (
            None
            if row["outcome_json"] is None
            else freeze_json(json.loads(str(row["outcome_json"])))
        )
        return ForkReceipt(
            request,
            str(row["target_run_id"]),
            str(row["target_trace_id"]),
            str(row["target_thread_id"]),
            str(row["target_checkpoint_id"]),
            ForkPhase(str(row["phase"])),
            int(row["version"]),
            None if row["claim_owner"] is None else str(row["claim_owner"]),
            None if int(row["claim_epoch"]) == 0 else int(row["claim_epoch"]),
            None if row["claim_expires_at"] is None else float(row["claim_expires_at"]),
            outcome,
        )

    def read_fork(self, fork_id: str) -> ForkReceipt | None:
        row = self.database.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?", (fork_id,)
        ).fetchone()
        return None if row is None else self._fork_receipt(row)

    @staticmethod
    def _fork_payload(request: ForkRequest) -> dict[str, JsonValue]:
        confirmation: JsonValue = None
        if request.dangerous_confirmation is not None:
            confirmation = {
                "scope": request.dangerous_confirmation.scope,
                "digest": request.dangerous_confirmation.digest,
                "observations": [
                    {
                        "effect_id": item.effect_id,
                        "kind": item.kind,
                        "state": item.state,
                        "ledger_version": item.ledger_version,
                        "request_hash": item.request_hash,
                        "handoff_attempt": item.handoff_attempt,
                    }
                    for item in request.dangerous_confirmation.observations
                ],
            }
        return {
            "fork_id": request.fork_id,
            "fingerprint": request.fingerprint,
            "source_run_id": request.source_run_id,
            "source_namespace": request.source_namespace,
            "source_checkpoint_id": request.source_checkpoint_id,
            "source_run_version": request.source_run_version,
            "source_head": request.source_head,
            "engine_hash": request.engine_hash,
            "manifest_hash": request.manifest_hash,
            "implementation_hash": request.implementation_hash,
            "schema_hash": request.schema_hash,
            "patch": _thaw(request.patch),
            "dangerous_confirmation": confirmation,
        }

    @staticmethod
    def _dangerous_confirmation_is_current(
        connection: sqlite3.Connection, request: ForkRequest
    ) -> bool:
        rows = connection.execute(
            "SELECT effect_id,'tool' AS kind,state,version,request_hash,handoff_attempt FROM"
            " execution_effects WHERE run_id=? AND state IN ('handed_off','unknown','succeeded')"
            " ORDER BY effect_id",
            (request.source_run_id,),
        ).fetchall()
        actual = tuple(
            DangerousEffectObservation(
                str(row["effect_id"]),
                str(row["kind"]),
                str(row["state"]),
                int(row["version"]),
                str(row["request_hash"]),
                int(row["handoff_attempt"]),
            )
            for row in rows
        )
        if not actual:
            return request.dangerous_confirmation is None
        confirmation = request.dangerous_confirmation
        if confirmation is None or confirmation.observations != actual:
            return False
        payload = [
            {
                "effect_id": item.effect_id,
                "kind": item.kind,
                "state": item.state,
                "ledger_version": item.ledger_version,
                "request_hash": item.request_hash,
                "handoff_attempt": item.handoff_attempt,
            }
            for item in actual
        ]
        expected = hashlib.sha256(
            canonical_json(
                cast(JsonValue, {"scope": confirmation.scope, "observations": payload})
            ).encode()
        ).hexdigest()
        return confirmation.digest == expected

    @classmethod
    def _validate_dangerous_confirmation(
        cls, connection: sqlite3.Connection, request: ForkRequest
    ) -> None:
        if not cls._dangerous_confirmation_is_current(connection, request):
            raise UnitOfWorkConflict("dangerous effect confirmation digest changed")

    @staticmethod
    def _fork_source_head_is_current(connection: sqlite3.Connection, request: ForkRequest) -> bool:
        run = connection.execute(
            "SELECT version FROM runs WHERE run_id=?", (request.source_run_id,)
        ).fetchone()
        head = connection.execute(
            "SELECT checkpoint_id FROM workflow_checkpoints "
            "WHERE run_id=? AND namespace=? ORDER BY version DESC LIMIT 1",
            (request.source_run_id, request.source_namespace),
        ).fetchone()
        source_checkpoint = connection.execute(
            "SELECT 1 FROM workflow_checkpoints WHERE checkpoint_id=? AND run_id=? AND namespace=?",
            (
                request.source_checkpoint_id,
                request.source_run_id,
                request.source_namespace,
            ),
        ).fetchone()
        return (
            run is not None
            and int(run["version"]) == request.source_run_version
            and head is not None
            and str(head["checkpoint_id"]) == request.source_head
            and source_checkpoint is not None
        )

    def _rollback_fork_for_changed_source(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: float,
        fault: FaultHook | None,
    ) -> ForkReceipt:
        _fault(fault, "workflow:fork_source_changed:before_workflow_fork_receipts_write")
        changed = connection.execute(
            "UPDATE workflow_fork_receipts SET phase='rolled_back',version=version+1,"
            "outcome_json=?,claim_expires_at=NULL,updated_at=? "
            "WHERE fork_id=? AND version=? AND phase IN ('prepared','claimed','checkpointed')",
            (
                canonical_json({"reason": "source_or_effect_snapshot_changed"}),
                now,
                str(row["fork_id"]),
                int(row["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("fork source-change rollback CAS failed")
        _fault(fault, "workflow:fork_source_changed:after_workflow_fork_receipts_write")
        updated = connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
            (str(row["fork_id"]),),
        ).fetchone()
        assert updated is not None
        return self._fork_receipt(updated)

    async def prepare_fork(
        self,
        transaction: WorkflowTransaction,
        request: ForkRequest,
        expected_source_snapshot: RecoverySnapshot,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ForkReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        payload = self._fork_payload(request)
        payload_json = canonical_json(payload)
        actual_fingerprint = hashlib.sha256(
            canonical_json(
                {key: value for key, value in payload.items() if key != "fingerprint"}
            ).encode()
        ).hexdigest()
        if request.fingerprint != actual_fingerprint:
            raise UnitOfWorkConflict("fork fingerprint changed")
        existing = tx.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?", (request.fork_id,)
        ).fetchone()
        if existing is not None:
            if str(existing["fingerprint"]) != request.fingerprint:
                raise UnitOfWorkConflict("fork id reused with different request")
            return self._fork_receipt(existing)
        if (
            self._recovery_snapshot_from_connection(tx.connection, request.source_run_id)
            != expected_source_snapshot
        ):
            raise UnitOfWorkConflict("fork source snapshot changed")
        source = tx.connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (request.source_run_id,)
        ).fetchone()
        if source is None or not self._fork_source_head_is_current(tx.connection, request):
            raise UnitOfWorkConflict("fork source head changed")
        self._validate_dangerous_confirmation(tx.connection, request)
        target_run_id = self._derived_id("fork-run", request.fingerprint)
        target_trace_id = self._derived_id("fork-trace", request.fingerprint)
        target_thread_id = self._derived_id("fork-thread", request.fingerprint)
        target_checkpoint_id = self._derived_id("fork-checkpoint", request.fingerprint)
        target_request_id = self._derived_id("fork-request", request.fingerprint)
        source_admission = tx.connection.execute(
            "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
            (request.source_run_id,),
        ).fetchone()
        source_checkpoint = tx.connection.execute(
            "SELECT checkpoint_json FROM workflow_checkpoints "
            "WHERE checkpoint_id=? AND run_id=? AND namespace=?",
            (
                request.source_checkpoint_id,
                request.source_run_id,
                request.source_namespace,
            ),
        ).fetchone()
        if source_admission is None or source_checkpoint is None:
            raise UnitOfWorkConflict("fork source durable identity is incomplete")
        target_request = json.loads(str(source_admission["request_json"]))
        checkpoint_payload = json.loads(str(source_checkpoint["checkpoint_json"]))
        state = checkpoint_payload.get("state")
        if not isinstance(state, dict):
            raise UnitOfWorkConflict("fork source checkpoint state is invalid")
        target_state = {**state, **_thaw(request.patch)}
        target_request.update(
            {
                "request_key": f"workflow-fork:{request.fork_id}",
                "mode": StartMode.STANDALONE.value,
                "request_id": target_request_id,
                "requested_run_id": target_run_id,
                "requested_trace_id": target_trace_id,
                "requested_thread_id": target_thread_id,
                "start_input": target_state,
            }
        )
        target_request_json = canonical_json(target_request)
        target_request_fingerprint = hashlib.sha256(target_request_json.encode()).hexdigest()
        target_snapshot = {
            **target_request,
            "resolved_run_id": target_run_id,
            "trace_id": target_trace_id,
            "thread_id": target_thread_id,
            "fork_id": request.fork_id,
            "fork_source_checkpoint_id": request.source_checkpoint_id,
        }
        target_snapshot_json = canonical_json(target_snapshot)
        _fault(fault, "workflow:prepare_fork:before_runs_write")
        tx.connection.execute(
            "INSERT INTO"
            " runs(run_id,execution_session_id,request_id,root_run_id,parent_run_id,profile_key,driver_kind,state,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,'reserved_fork',0,?,?)",
            (
                target_run_id,
                str(source["execution_session_id"]),
                target_request_id,
                target_run_id,
                request.source_run_id,
                str(source["profile_key"]),
                "workflow",
                now,
                now,
            ),
        )
        _fault(fault, "workflow:prepare_fork:after_runs_write")
        _fault(fault, "workflow:prepare_fork:before_run_start_snapshots_write")
        tx.connection.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json,snapshot_hash,created_at) "
            "VALUES(?,?,?,?)",
            (
                target_run_id,
                target_snapshot_json,
                hashlib.sha256(target_snapshot_json.encode()).hexdigest(),
                now,
            ),
        )
        _fault(fault, "workflow:prepare_fork:after_run_start_snapshots_write")
        _fault(fault, "workflow:prepare_fork:before_workflow_start_admissions_write")
        tx.connection.execute(
            "INSERT INTO workflow_start_admissions(request_key,request_id,"
            "request_fingerprint,request_json,mode,run_id,trace_id,thread_id,phase,"
            "version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?, 'admitted',0,?,?)",
            (
                str(target_request["request_key"]),
                target_request_id,
                target_request_fingerprint,
                target_request_json,
                StartMode.STANDALONE.value,
                target_run_id,
                target_trace_id,
                target_thread_id,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:prepare_fork:after_workflow_start_admissions_write")
        _fault(fault, "workflow:prepare_fork:before_workflow_fork_receipts_write")
        tx.connection.execute(
            "INSERT INTO"
            " workflow_fork_receipts(fork_id,fingerprint,request_json,source_run_id,source_namespace,source_checkpoint_id,source_run_version,source_head,target_run_id,target_trace_id,target_thread_id,target_checkpoint_id,phase,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'prepared',0,?,?)",
            (
                request.fork_id,
                request.fingerprint,
                payload_json,
                request.source_run_id,
                request.source_namespace,
                request.source_checkpoint_id,
                request.source_run_version,
                request.source_head,
                target_run_id,
                target_trace_id,
                target_thread_id,
                target_checkpoint_id,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:prepare_fork:after_workflow_fork_receipts_write")
        return self._fork_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
                (request.fork_id,),
            ).fetchone()
        )

    async def claim_fork(
        self,
        transaction: WorkflowTransaction,
        fork_id: str,
        expected_receipt_version: int,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> ForkWriteLease:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?", (fork_id,)
        ).fetchone()
        if (
            row is not None
            and str(row["phase"]) == "claimed"
            and str(row["claim_owner"]) == owner_id
            and int(row["version"]) == expected_receipt_version + 1
            and float(row["claim_expires_at"]) > now
        ):
            return ForkWriteLease(
                fork_id,
                str(row["target_run_id"]),
                owner_id,
                int(row["claim_epoch"]),
                float(row["claim_expires_at"]),
                int(row["version"]),
                "write",
            )
        if (
            row is not None
            and row["claim_expires_at"] is not None
            and float(row["claim_expires_at"]) > now
        ):
            raise UnitOfWorkConflict("fork receipt has an active claimant")
        if (
            row is None
            or int(row["version"]) != expected_receipt_version
            or str(row["phase"]) not in {"prepared", "claimed", "checkpointed"}
        ):
            raise UnitOfWorkConflict("fork receipt is not claimable")
        epoch = int(row["claim_epoch"]) + 1
        mode = "commit_only" if str(row["phase"]) == "checkpointed" else "write"
        expires = now + ttl_seconds
        _fault(fault, "workflow:claim_fork:before_workflow_fork_receipts_write")
        tx.connection.execute(
            "UPDATE workflow_fork_receipts SET"
            " phase=?,version=version+1,claim_owner=?,claim_epoch=?,claim_expires_at=?,updated_at=?"
            " WHERE fork_id=? AND version=?",
            (
                str(row["phase"]) if mode == "commit_only" else "claimed",
                owner_id,
                epoch,
                expires,
                now,
                fork_id,
                expected_receipt_version,
            ),
        )
        _fault(fault, "workflow:claim_fork:after_workflow_fork_receipts_write")
        return ForkWriteLease(
            fork_id,
            str(row["target_run_id"]),
            owner_id,
            epoch,
            expires,
            expected_receipt_version + 1,
            mode,
        )

    async def checkpoint_fork(
        self,
        transaction: WorkflowTransaction,
        fork_lease: ForkWriteLease,
        expected_target_head: str | None,
        checkpoint_operation_id: str,
        checkpoint: Mapping[str, JsonValue],
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ForkReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
            (fork_lease.fork_id,),
        ).fetchone()
        raw = dict(checkpoint)
        raw["checkpoint_operation_id"] = checkpoint_operation_id
        checkpoint_json = canonical_json(raw)
        checkpoint_hash = hashlib.sha256(checkpoint_json.encode()).hexdigest()
        if fork_lease.mode == "commit_only":
            raise UnitOfWorkConflict("commit-only fork lease cannot rewrite checkpoint")
        if row is not None and str(row["phase"]) in {"checkpointed", "committed"}:
            target = tx.connection.execute(
                "SELECT checkpoint_hash,checkpoint_json FROM workflow_checkpoints "
                "WHERE checkpoint_id=? AND run_id=?",
                (str(row["target_checkpoint_id"]), fork_lease.target_run_id),
            ).fetchone()
            if (
                str(row["claim_owner"]) != fork_lease.owner_id
                or int(row["claim_epoch"]) != fork_lease.claim_epoch
                or target is None
                or str(target["checkpoint_hash"]) != checkpoint_hash
                or str(target["checkpoint_json"]) != checkpoint_json
            ):
                raise UnitOfWorkConflict("fork checkpoint replay changed")
            if str(row["phase"]) == "committed":
                return self._fork_receipt(row)
            request = self._fork_receipt(row).request
            if not self._fork_source_head_is_current(
                tx.connection, request
            ) or not self._dangerous_confirmation_is_current(tx.connection, request):
                return self._rollback_fork_for_changed_source(
                    tx.connection, row, now=now, fault=fault
                )
            return self._fork_receipt(row)
        if (
            row is None
            or str(row["phase"]) != "claimed"
            or (
                row["claim_owner"],
                int(row["claim_epoch"]),
                float(row["claim_expires_at"]),
            )
            != (fork_lease.owner_id, fork_lease.claim_epoch, fork_lease.expires_at)
            or fork_lease.expires_at <= now
        ):
            raise UnitOfWorkConflict("fork write lease is stale")
        request = self._fork_receipt(row).request
        if not self._fork_source_head_is_current(
            tx.connection, request
        ) or not self._dangerous_confirmation_is_current(tx.connection, request):
            return self._rollback_fork_for_changed_source(tx.connection, row, now=now, fault=fault)
        head = tx.connection.execute(
            "SELECT checkpoint_id FROM workflow_checkpoints WHERE run_id=? ORDER BY version DESC"
            " LIMIT 1",
            (fork_lease.target_run_id,),
        ).fetchone()
        actual = None if head is None else str(head["checkpoint_id"])
        if actual != expected_target_head:
            raise UnitOfWorkConflict("fork target head changed")
        _fault(fault, "workflow:checkpoint_fork:before_workflow_checkpoints_write")
        tx.connection.execute(
            "INSERT INTO"
            " workflow_checkpoints(checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,lease_epoch,version,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,1,0,?)",
            (
                str(row["target_checkpoint_id"]),
                fork_lease.target_run_id,
                request.source_namespace,
                checkpoint_json,
                checkpoint_hash,
                now,
            ),
        )
        _fault(fault, "workflow:checkpoint_fork:after_workflow_checkpoints_write")
        _fault(
            fault,
            "workflow:checkpoint_fork:before_workflow_fork_receipts_write",
        )
        tx.connection.execute(
            "UPDATE workflow_fork_receipts SET"
            " phase='checkpointed',version=version+1,target_checkpoint_hash=?,updated_at=? WHERE"
            " fork_id=? AND version=?",
            (
                checkpoint_hash,
                now,
                fork_lease.fork_id,
                fork_lease.expected_receipt_version,
            ),
        )
        _fault(
            fault,
            "workflow:checkpoint_fork:after_workflow_fork_receipts_write",
        )
        return self._fork_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
                (fork_lease.fork_id,),
            ).fetchone()
        )

    async def commit_fork(
        self,
        transaction: WorkflowTransaction,
        fork_lease: ForkWriteLease,
        expected_receipt_version: int,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ForkReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
            (fork_lease.fork_id,),
        ).fetchone()
        if row is not None and str(row["phase"]) == "committed":
            if (
                str(row["target_run_id"]) != fork_lease.target_run_id
                or str(row["claim_owner"]) != fork_lease.owner_id
                or int(row["claim_epoch"]) != fork_lease.claim_epoch
            ):
                raise UnitOfWorkConflict("fork commit replay changed")
            return self._fork_receipt(row)
        if (
            row is None
            or str(row["phase"]) != "checkpointed"
            or int(row["version"]) != expected_receipt_version
            or str(row["claim_owner"]) != fork_lease.owner_id
            or int(row["claim_epoch"]) != fork_lease.claim_epoch
        ):
            raise UnitOfWorkConflict("fork commit binding changed")
        request = self._fork_receipt(row).request
        if not self._fork_source_head_is_current(
            tx.connection, request
        ) or not self._dangerous_confirmation_is_current(tx.connection, request):
            return self._rollback_fork_for_changed_source(tx.connection, row, now=now, fault=fault)
        _fault(fault, "workflow:commit_fork:before_runs_write")
        tx.connection.execute(
            "UPDATE runs SET state='created',version=version+1,updated_at=? WHERE run_id=? AND"
            " state='reserved_fork'",
            (now, fork_lease.target_run_id),
        )
        _fault(fault, "workflow:commit_fork:after_runs_write")
        _fault(fault, "workflow:commit_fork:before_workflow_fork_receipts_write")
        tx.connection.execute(
            "UPDATE workflow_fork_receipts SET"
            " phase='committed',version=version+1,outcome_json=?,updated_at=? WHERE fork_id=? AND"
            " version=?",
            (
                canonical_json({"run_id": fork_lease.target_run_id}),
                now,
                fork_lease.fork_id,
                expected_receipt_version,
            ),
        )
        _fault(fault, "workflow:commit_fork:after_workflow_fork_receipts_write")
        return self._fork_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
                (fork_lease.fork_id,),
            ).fetchone()
        )

    async def rollback_fork(
        self,
        transaction: WorkflowTransaction,
        fork_lease: ForkWriteLease,
        expected_receipt_version: int,
        reason: str,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ForkReceipt:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
            (fork_lease.fork_id,),
        ).fetchone()
        if (
            row is None
            or int(row["version"]) != expected_receipt_version
            or str(row["phase"]) not in {"prepared", "claimed", "checkpointed"}
        ):
            raise UnitOfWorkConflict("fork rollback binding changed")
        _fault(fault, "workflow:rollback_fork:before_workflow_fork_receipts_write")
        tx.connection.execute(
            "UPDATE workflow_fork_receipts SET"
            " phase='rolled_back',version=version+1,outcome_json=?,updated_at=? WHERE fork_id=? AND"
            " version=?",
            (
                canonical_json({"reason": reason}),
                now,
                fork_lease.fork_id,
                expected_receipt_version,
            ),
        )
        _fault(fault, "workflow:rollback_fork:after_workflow_fork_receipts_write")
        return self._fork_receipt(
            tx.connection.execute(
                "SELECT * FROM workflow_fork_receipts WHERE fork_id=?",
                (fork_lease.fork_id,),
            ).fetchone()
        )

    def list_orphaned_forks(
        self, snapshot_cursor: str | None, *, now: float
    ) -> tuple[tuple[ForkReceipt, ...], str | None]:
        rows = self.database.connection.execute(
            "SELECT * FROM workflow_fork_receipts WHERE phase IN"
            " ('prepared','claimed','checkpointed') AND (? IS NULL OR fork_id>?) AND"
            " (claim_expires_at IS NULL OR claim_expires_at<=?) ORDER BY fork_id LIMIT 101",
            (snapshot_cursor, snapshot_cursor, now),
        ).fetchall()
        return tuple(self._fork_receipt(row) for row in rows[:100]), (
            None if len(rows) <= 100 else str(rows[99]["fork_id"])
        )
