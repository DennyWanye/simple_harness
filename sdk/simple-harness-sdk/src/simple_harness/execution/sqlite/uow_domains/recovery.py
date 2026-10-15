# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow recovery candidates, snapshots and outcomes."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    FaultHook,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.workflow.execution_ports import (
    RecoveryCandidate,
    RecoveryClaim,
    RecoveryOutcome,
    RecoverySnapshot,
    WorkflowTransaction,
)

from .core import ExecutionUnitOfWorkCore
from .support import _fault


def _recovery_candidate_json(value: RecoveryCandidate) -> dict[str, JsonValue]:
    return {
        "run_id": value.run_id,
        "run_version": value.run_version,
        "status": value.status,
        "runtime_lease_owner": value.runtime_lease_owner,
        "runtime_lease_epoch": value.runtime_lease_epoch,
        "runtime_lease_expires_at": value.runtime_lease_expires_at,
        "workflow_lease_namespace": value.workflow_lease_namespace,
        "workflow_lease_owner": value.workflow_lease_owner,
        "workflow_lease_epoch": value.workflow_lease_epoch,
        "workflow_lease_expires_at": value.workflow_lease_expires_at,
        "run_fence_owner": value.run_fence_owner,
        "run_fence_runtime_lease_epoch": value.run_fence_runtime_lease_epoch,
        "run_fence_epoch": value.run_fence_epoch,
        "run_fence_state": value.run_fence_state,
        "checkpoint_head": value.checkpoint_head,
    }


def _recovery_snapshot_json(value: RecoverySnapshot) -> dict[str, JsonValue]:
    return {
        "candidate": _recovery_candidate_json(value.candidate),
        "manifest_hash": value.manifest_hash,
        "implementation_hash": value.implementation_hash,
        "checkpoint_hash": value.checkpoint_hash,
        "unresolved_blocker_ids": list(value.unresolved_blocker_ids),
    }


class WorkflowRecoveryMixin(ExecutionUnitOfWorkCore):
    """Workflow recovery candidates, snapshots and outcomes."""

    __slots__ = ()

    def list_candidates(
        self, snapshot_cursor: str | None
    ) -> tuple[tuple[RecoveryCandidate, ...], str | None]:
        rows = self._recovery_candidate_rows(
            self.database.connection, snapshot_cursor=snapshot_cursor, limit=101
        )
        candidates = [self._recovery_candidate(row) for row in rows[:100]]
        return tuple(candidates), (None if len(rows) <= 100 else str(rows[99]["run_id"]))

    @staticmethod
    def _recovery_candidate_rows(
        connection: sqlite3.Connection,
        *,
        snapshot_cursor: str | None,
        run_id: str | None = None,
        limit: int | None = None,
        include_terminal: bool = False,
    ) -> list[sqlite3.Row]:
        limit_clause = "" if limit is None else " LIMIT ?"
        parameters: list[object] = [
            1 if include_terminal else 0,
            snapshot_cursor,
            snapshot_cursor,
            run_id,
            run_id,
            RUNTIME_LEASE_NAMESPACE,
        ]
        if limit is not None:
            parameters.append(limit)
        return connection.execute(
            """
            WITH candidate_runs AS (
                SELECT runs.run_id, runs.state, runs.version,
                       json_extract(admissions.request_json, '$.checkpoint_namespace')
                           AS workflow_namespace
                FROM runs
                LEFT JOIN workflow_start_admissions AS admissions
                  ON admissions.run_id = runs.run_id
                WHERE runs.driver_kind = 'workflow'
                  AND (? = 1 OR runs.state NOT IN
                      ('reserved_fork','completed','failed','cancelled'))
                  AND (? IS NULL OR runs.run_id > ?)
                  AND (? IS NULL OR runs.run_id = ?)
            )
            SELECT candidate_runs.run_id,
                   candidate_runs.state,
                   candidate_runs.version,
                   candidate_runs.workflow_namespace,
                   runtime.owner_id AS runtime_owner,
                   runtime.epoch AS runtime_epoch,
                   runtime.expires_at AS runtime_expires_at,
                   workflow.owner_id AS workflow_owner,
                   workflow.epoch AS workflow_epoch,
                   workflow.expires_at AS workflow_expires_at,
                   fence.owner_id AS fence_owner,
                   fence.runtime_lease_epoch AS fence_runtime_epoch,
                   fence.epoch AS fence_epoch,
                   fence.state AS fence_state,
                   head.checkpoint_id AS checkpoint_head
            FROM candidate_runs
            LEFT JOIN workflow_leases AS runtime
              ON runtime.run_id = candidate_runs.run_id
             AND runtime.namespace = ?
            LEFT JOIN workflow_leases AS workflow
              ON workflow.run_id = candidate_runs.run_id
             AND workflow.namespace = candidate_runs.workflow_namespace
            LEFT JOIN run_fences AS fence
              ON fence.run_id = candidate_runs.run_id
            LEFT JOIN workflow_checkpoints AS head
              ON head.run_id = candidate_runs.run_id
             AND head.namespace = candidate_runs.workflow_namespace
             AND head.version = (
                 SELECT MAX(candidate_head.version)
                 FROM workflow_checkpoints AS candidate_head
                 WHERE candidate_head.run_id = candidate_runs.run_id
                   AND candidate_head.namespace = candidate_runs.workflow_namespace
             )
            ORDER BY candidate_runs.run_id
            """
            + limit_clause,
            parameters,
        ).fetchall()

    @staticmethod
    def _recovery_candidate(row: sqlite3.Row) -> RecoveryCandidate:
        def text(name: str) -> str | None:
            return None if row[name] is None else str(row[name])

        def integer(name: str) -> int | None:
            return None if row[name] is None else int(row[name])

        def number(name: str) -> float | None:
            return None if row[name] is None else float(row[name])

        return RecoveryCandidate(
            run_id=str(row["run_id"]),
            run_version=int(row["version"]),
            status=str(row["state"]),
            runtime_lease_owner=text("runtime_owner"),
            runtime_lease_epoch=integer("runtime_epoch"),
            runtime_lease_expires_at=number("runtime_expires_at"),
            workflow_lease_namespace=text("workflow_namespace"),
            workflow_lease_owner=text("workflow_owner"),
            workflow_lease_epoch=integer("workflow_epoch"),
            workflow_lease_expires_at=number("workflow_expires_at"),
            run_fence_owner=text("fence_owner"),
            run_fence_runtime_lease_epoch=integer("fence_runtime_epoch"),
            run_fence_epoch=integer("fence_epoch"),
            run_fence_state=text("fence_state"),
            checkpoint_head=text("checkpoint_head"),
        )

    def _recovery_snapshot_from_connection(
        self, connection: sqlite3.Connection, run_id: str
    ) -> RecoverySnapshot:
        rows = self._recovery_candidate_rows(
            connection,
            snapshot_cursor=None,
            run_id=run_id,
            include_terminal=True,
        )
        if not rows:
            raise UnitOfWorkNotFound(run_id)
        candidate = self._recovery_candidate(rows[0])
        head = connection.execute(
            "SELECT checkpoint_hash FROM workflow_checkpoints "
            "WHERE run_id=? AND namespace=? AND checkpoint_id=?",
            (run_id, candidate.workflow_lease_namespace, candidate.checkpoint_head),
        ).fetchone()
        start_row = connection.execute(
            "SELECT snapshot_json FROM run_start_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
        start = {} if start_row is None else json.loads(str(start_row["snapshot_json"]))
        manifest = start.get("manifest_hash")
        implementation = start.get("implementation_hash")
        blockers = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT blocker_id FROM run_wait_blockers WHERE run_id=? AND wake_consumed=0 ORDER"
                " BY blocker_id",
                (run_id,),
            ).fetchall()
        )
        return RecoverySnapshot(
            candidate,
            None if not isinstance(manifest, str) else manifest,
            None if not isinstance(implementation, str) else implementation,
            None if head is None else str(head["checkpoint_hash"]),
            blockers,
        )

    def read_recovery_snapshot(self, run_id: str) -> RecoverySnapshot:
        return self._recovery_snapshot_from_connection(self.database.connection, run_id)

    async def commit_recovery_outcome(
        self,
        transaction: WorkflowTransaction,
        candidate: RecoveryCandidate,
        expected_snapshot: RecoverySnapshot,
        outcome: RecoveryOutcome,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> RecoveryOutcome:
        tx = self._assert_open_workflow_transaction(transaction)
        candidate_hash = hashlib.sha256(
            canonical_json(_recovery_candidate_json(candidate)).encode()
        ).hexdigest()
        snapshot_hash = hashlib.sha256(
            canonical_json(_recovery_snapshot_json(expected_snapshot)).encode()
        ).hexdigest()
        existing = tx.connection.execute(
            "SELECT * FROM workflow_recovery_receipts WHERE receipt_id=?",
            (outcome.receipt_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["candidate_hash"]) != candidate_hash
                or str(existing["snapshot_hash"]) != snapshot_hash
                or str(existing["previous_status"]) != outcome.previous_status
                or str(existing["status"]) != outcome.status
                or str(existing["action"]) != outcome.action
                or str(existing["reason"]) != outcome.reason
            ):
                raise UnitOfWorkConflict("recovery receipt changed")
            return RecoveryOutcome(
                str(existing["previous_status"]),
                str(existing["status"]),
                str(existing["action"]),
                str(existing["reason"]),
                str(existing["receipt_id"]),
            )
        current = self._recovery_snapshot_from_connection(tx.connection, candidate.run_id)
        if current != expected_snapshot or current.candidate != candidate:
            raise UnitOfWorkConflict("recovery snapshot changed")
        _fault(fault, "workflow:commit_recovery_outcome:before_runs_write")
        changed = tx.connection.execute(
            "UPDATE runs SET state=?,version=version+1,updated_at=? WHERE run_id=? AND state=? AND"
            " version=?",
            (
                outcome.status,
                now,
                candidate.run_id,
                candidate.status,
                candidate.run_version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("recovery Run CAS failed")
        _fault(fault, "workflow:commit_recovery_outcome:after_runs_write")
        _fault(
            fault,
            "workflow:commit_recovery_outcome:before_workflow_recovery_receipts_write",
        )
        tx.connection.execute(
            "INSERT INTO"
            " workflow_recovery_receipts(receipt_id,run_id,candidate_hash,snapshot_hash,previous_status,status,action,reason,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                outcome.receipt_id,
                candidate.run_id,
                candidate_hash,
                snapshot_hash,
                outcome.previous_status,
                outcome.status,
                outcome.action,
                outcome.reason,
                now,
            ),
        )
        _fault(
            fault,
            "workflow:commit_recovery_outcome:after_workflow_recovery_receipts_write",
        )
        return outcome

    async def claim_resolved_recovery(
        self,
        transaction: WorkflowTransaction,
        blocker_id: str,
        expected_resolution_version: int,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> RecoveryClaim:
        tx = self._assert_open_workflow_transaction(transaction)
        blocker = tx.connection.execute(
            "SELECT version,resolution_id FROM run_wait_blockers WHERE blocker_id=?",
            (blocker_id,),
        ).fetchone()
        if (
            blocker is None
            or blocker["resolution_id"] is None
            or int(blocker["version"]) != expected_resolution_version
        ):
            raise UnitOfWorkConflict("resolved recovery blocker changed")
        claim = tx.connection.execute(
            "SELECT * FROM workflow_recovery_claims WHERE blocker_id=?",
            (blocker_id,),
        ).fetchone()
        if claim is not None and float(claim["expires_at"]) > now:
            raise UnitOfWorkConflict("resolved recovery blocker has an active claimant")
        epoch = 1 if claim is None else int(claim["epoch"]) + 1
        expires = now + ttl_seconds
        run_id = str(
            tx.connection.execute(
                "SELECT run_id FROM run_wait_blockers WHERE blocker_id=?",
                (blocker_id,),
            ).fetchone()[0]
        )
        _fault(
            fault,
            "workflow:claim_resolved_recovery:before_workflow_recovery_claims_write",
        )
        if claim is None:
            tx.connection.execute(
                "INSERT INTO"
                " workflow_recovery_claims(blocker_id,run_id,resolution_version,owner_id,epoch,expires_at,created_at,updated_at)"  # noqa: E501
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    blocker_id,
                    run_id,
                    expected_resolution_version,
                    owner_id,
                    epoch,
                    expires,
                    now,
                    now,
                ),
            )
        else:
            tx.connection.execute(
                "UPDATE workflow_recovery_claims SET"
                " resolution_version=?,owner_id=?,epoch=?,expires_at=?,updated_at=? WHERE"
                " blocker_id=? AND epoch=?",
                (
                    expected_resolution_version,
                    owner_id,
                    epoch,
                    expires,
                    now,
                    blocker_id,
                    int(claim["epoch"]),
                ),
            )
        _fault(
            fault,
            "workflow:claim_resolved_recovery:after_workflow_recovery_claims_write",
        )
        return RecoveryClaim(blocker_id, owner_id, epoch, expires)
