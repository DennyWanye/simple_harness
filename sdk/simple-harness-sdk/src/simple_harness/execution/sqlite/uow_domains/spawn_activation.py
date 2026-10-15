# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Activation of ready workflow spawn continuations."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from simple_harness.execution.recovery import RecoveryKind, WaitBlockerRecord
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.orchestration import (
    WorkflowSpawnContinuationReady,
    WorkflowSpawnReadyActivation,
    WorkflowSpawnReadyActivationState,
    _create_workflow_spawn_ready_activation,
)
from simple_harness.workflow.execution_ports import WorkflowTransaction

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import (
        WorkflowSpawnContinuationReady,
        WorkflowSpawnReadyActivation,
    )
from .core import ExecutionUnitOfWorkCore
from .support import (
    _fault,
    _positive_ttl,
    _required,
    _time,
    _wait_blocker_record,
    _workflow_spawn_activation_hash,
    _workflow_spawn_continuation_ready,
    _workflow_spawn_ready_activation,
)


class WorkflowSpawnActivationMixin(ExecutionUnitOfWorkCore):
    """Activation of ready workflow spawn continuations."""

    __slots__ = ()

    async def consume_spawn_ready_and_claim_activation(
        self,
        transaction: WorkflowTransaction,
        ready: WorkflowSpawnContinuationReady,
        blocker_snapshot: WaitBlockerRecord,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnReadyActivation:

        tx = self._assert_open_workflow_transaction(transaction)
        owner_id = _required(owner_id, "owner_id")
        now = _time(now)
        expires_at = now + _positive_ttl(ttl_seconds)
        continuation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (ready.spawn_operation_id,),
        ).fetchone()
        if continuation is None or str(continuation["state"]) == "completed":
            raise UnitOfWorkConflict("workflow spawn continuation is unavailable")
        self._require_matching_spawn_ready(tx.connection, continuation, ready)
        blocker = tx.connection.execute(
            "SELECT * FROM run_wait_blockers WHERE blocker_id=?",
            (blocker_snapshot.blocker_id,),
        ).fetchone()
        if (
            blocker is None
            or _wait_blocker_record(blocker) != blocker_snapshot
            or blocker_snapshot.run_id != str(continuation["parent_run_id"])
            or blocker_snapshot.kind is not RecoveryKind.TOOL
            or blocker_snapshot.ledger_identity != ready.effect_id
            or blocker_snapshot.handoff_attempt != ready.handoff_attempt
            or blocker_snapshot.wake_consumed
        ):
            raise UnitOfWorkConflict("workflow spawn blocker authority differs")
        run = tx.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (blocker_snapshot.run_id,),
        ).fetchone()
        if run is None or str(run["state"]) != RunState.WAITING.value:
            raise UnitOfWorkConflict("workflow spawn parent Run is not waiting")
        existing_active = tx.connection.execute(
            """
            SELECT * FROM workflow_spawn_ready_activations
            WHERE ready_receipt_id=? AND state='active'
            """,
            (ready.ready_receipt_id,),
        ).fetchone()
        if existing_active is not None:
            activation = _workflow_spawn_ready_activation(tx.connection, existing_active)
            if activation.execution_lease.owner_id != owner_id:
                raise UnitOfWorkConflict("workflow spawn ready has a live activation owner")
            return activation
        runtime_row = tx.connection.execute(
            "SELECT owner_id,epoch,expires_at FROM workflow_leases WHERE run_id=? AND namespace=?",
            (blocker_snapshot.run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence_row = tx.connection.execute(
            "SELECT owner_id,epoch,runtime_lease_epoch,state FROM run_fences WHERE run_id=?",
            (blocker_snapshot.run_id,),
        ).fetchone()
        if runtime_row is not None and float(runtime_row["expires_at"]) > now:
            if str(runtime_row["owner_id"]) != owner_id:
                raise UnitOfWorkConflict("workflow spawn parent has a live foreign Runtime owner")
            runtime_epoch = int(runtime_row["epoch"])
            expires_at = max(expires_at, float(runtime_row["expires_at"]))
        else:
            runtime_epoch = (
                max(
                    0 if runtime_row is None else int(runtime_row["epoch"]),
                    0 if fence_row is None else int(fence_row["runtime_lease_epoch"]),
                )
                + 1
            )
        _fault(fault, "workflow:spawn_activation:before_runtime_lease_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(run_id,namespace) DO UPDATE SET
                owner_id=excluded.owner_id,epoch=excluded.epoch,
                expires_at=excluded.expires_at
            """,
            (
                blocker_snapshot.run_id,
                RUNTIME_LEASE_NAMESPACE,
                owner_id,
                runtime_epoch,
                expires_at,
            ),
        )
        _fault(fault, "workflow:spawn_activation:after_runtime_lease_write")
        fence_epoch = 1 if fence_row is None else int(fence_row["epoch"])
        if fence_row is not None and (
            str(fence_row["state"]) != "active"
            or str(fence_row["owner_id"]) != owner_id
            or int(fence_row["runtime_lease_epoch"]) != runtime_epoch
        ):
            fence_epoch += 1
        _fault(fault, "workflow:spawn_activation:before_run_fence_write")
        tx.connection.execute(
            """
            INSERT INTO run_fences(
                run_id,owner_id,runtime_lease_epoch,epoch,state,acquired_at,released_at
            ) VALUES(?,?,?,?,'active',?,NULL)
            ON CONFLICT(run_id) DO UPDATE SET
                owner_id=excluded.owner_id,
                runtime_lease_epoch=excluded.runtime_lease_epoch,
                epoch=excluded.epoch,state='active',acquired_at=excluded.acquired_at,
                released_at=NULL
            """,
            (blocker_snapshot.run_id, owner_id, runtime_epoch, fence_epoch, now),
        )
        _fault(fault, "workflow:spawn_activation:after_run_fence_write")
        workflow_namespace: str | None = None
        admission = tx.connection.execute(
            "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
            (blocker_snapshot.run_id,),
        ).fetchone()
        if admission is not None:
            admission_payload = json.loads(str(admission["request_json"]))
            if not isinstance(admission_payload, dict):
                raise UnitOfWorkConflict("workflow spawn parent admission is malformed")
            workflow_namespace = _required(
                admission_payload.get("checkpoint_namespace"),
                "checkpoint_namespace",
            )
            _fault(fault, "workflow:spawn_activation:before_workflow_lease_write")
            tx.connection.execute(
                """
                INSERT INTO workflow_leases(run_id,namespace,owner_id,epoch,expires_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(run_id,namespace) DO UPDATE SET
                    owner_id=excluded.owner_id,epoch=excluded.epoch,
                    expires_at=excluded.expires_at
                """,
                (
                    blocker_snapshot.run_id,
                    workflow_namespace,
                    owner_id,
                    runtime_epoch,
                    expires_at,
                ),
            )
            _fault(fault, "workflow:spawn_activation:after_workflow_lease_write")
        next_claim_epoch = int(continuation["claim_epoch"]) + 1
        _fault(fault, "workflow:spawn_activation:before_continuation_write")
        changed = tx.connection.execute(
            """
            UPDATE workflow_spawn_continuations
            SET state='claimed',owner_id=?,runtime_lease_epoch=?,run_fence_epoch=?,
                workflow_lease_epoch=?,claim_epoch=?,expires_at=?,version=version+1,
                updated_at=?
            WHERE operation_id=? AND version=? AND state<>'completed'
            """,
            (
                owner_id,
                runtime_epoch,
                fence_epoch,
                None if workflow_namespace is None else runtime_epoch,
                next_claim_epoch,
                expires_at,
                now,
                ready.spawn_operation_id,
                int(continuation["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn activation continuation CAS failed")
        _fault(fault, "workflow:spawn_activation:after_continuation_write")
        changed = tx.connection.execute(
            """
            UPDATE runs SET state='running',version=version+1,updated_at=?
            WHERE run_id=? AND state='waiting' AND version=?
            """,
            (now, blocker_snapshot.run_id, int(run["version"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn activation Run CAS failed")
        tx.connection.execute(
            "UPDATE run_wait_blockers SET wake_consumed=1,consumed_at=?,version=version+1 WHERE"
            " blocker_id=? AND wake_consumed=0 AND version=?",
            (now, blocker_snapshot.blocker_id, blocker_snapshot.version),
        )
        tx.connection.execute(
            "UPDATE workflow_spawn_continuation_ready SET consumed_at=? WHERE ready_receipt_id=?"
            " AND consumed_at IS NULL",
            (now, ready.ready_receipt_id),
        )
        activation_receipt_id = self._derived_id(
            "workflow-spawn/activation/v1",
            f"{ready.ready_receipt_id}:{runtime_epoch}:1",
        )
        canonical_hash = _workflow_spawn_activation_hash(
            activation_receipt_id=activation_receipt_id,
            ready_receipt_id=ready.ready_receipt_id,
            spawn_operation_id=ready.spawn_operation_id,
            parent_run_id=blocker_snapshot.run_id,
            effect_id=ready.effect_id,
            owner_id=owner_id,
            runtime_lease_epoch=runtime_epoch,
            run_fence_epoch=fence_epoch,
            workflow_lease_epoch=(None if workflow_namespace is None else runtime_epoch),
            continuation_claim_epoch=next_claim_epoch,
            predecessor_activation_receipt_id=None,
            version=1,
        )
        _fault(fault, "workflow:spawn_activation:before_receipt_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_ready_activations(
                activation_receipt_id,ready_receipt_id,spawn_operation_id,
                parent_run_id,effect_id,owner_id,runtime_lease_epoch,
                run_fence_epoch,workflow_lease_epoch,continuation_claim_epoch,
                predecessor_activation_receipt_id,state,version,canonical_hash,
                created_at,superseded_at,consumed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'active',1,?,?,NULL,NULL)
            """,
            (
                activation_receipt_id,
                ready.ready_receipt_id,
                ready.spawn_operation_id,
                blocker_snapshot.run_id,
                ready.effect_id,
                owner_id,
                runtime_epoch,
                fence_epoch,
                None if workflow_namespace is None else runtime_epoch,
                next_claim_epoch,
                canonical_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_activation:after_receipt_write")
        self._insert_event(
            tx.connection,
            event_id=f"{blocker_snapshot.run_id}:spawn-ready:{activation_receipt_id}",
            run_id=blocker_snapshot.run_id,
            kind="run.recovered",
            payload={
                "owner_id": owner_id,
                "lease_epoch": runtime_epoch,
                "spawn_operation_id": ready.spawn_operation_id,
            },
            now=now,
        )
        tx.register_after_commit_fault("workflow:spawn_activation:after_commit")
        stored = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations WHERE activation_receipt_id=?",
            (activation_receipt_id,),
        ).fetchone()
        assert stored is not None
        activation = _workflow_spawn_ready_activation(tx.connection, stored)
        if activation.state is not WorkflowSpawnReadyActivationState.ACTIVE:
            raise AssertionError("new workflow spawn activation must be active")
        return _create_workflow_spawn_ready_activation(
            ready_receipt=activation.ready_receipt,
            continuation_claim=activation.continuation_claim,
            execution_lease=activation.execution_lease,
            run_fence=activation.run_fence,
            workflow_lease=activation.workflow_lease,
            blocker_id=activation.blocker_id,
            activation_receipt_id=activation.activation_receipt_id,
            activation_version=activation.activation_version,
            predecessor_activation_receipt_id=(activation.predecessor_activation_receipt_id),
            state=activation.state,
        )

    async def read_spawn_ready_activation(
        self,
        transaction: WorkflowTransaction,
        parent_run_id: str,
        activation_receipt_id: str | None = None,
    ) -> WorkflowSpawnReadyActivation | None:
        tx = self._assert_open_workflow_transaction(transaction)
        parent_run_id = _required(parent_run_id, "parent_run_id")
        if activation_receipt_id is None:
            row = tx.connection.execute(
                """
                SELECT * FROM workflow_spawn_ready_activations
                WHERE parent_run_id=? AND state='active'
                ORDER BY created_at DESC,activation_receipt_id DESC LIMIT 1
                """,
                (parent_run_id,),
            ).fetchone()
        else:
            activation_receipt_id = _required(activation_receipt_id, "activation_receipt_id")
            row = tx.connection.execute(
                """
                SELECT * FROM workflow_spawn_ready_activations
                WHERE parent_run_id=? AND activation_receipt_id=?
                """,
                (parent_run_id, activation_receipt_id),
            ).fetchone()
        if row is None:
            return None
        return _workflow_spawn_ready_activation(tx.connection, row)

    async def reclaim_spawn_ready_activation(
        self,
        transaction: WorkflowTransaction,
        prior: WorkflowSpawnReadyActivation,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnReadyActivation:

        tx = self._assert_open_workflow_transaction(transaction)
        if not isinstance(prior, WorkflowSpawnReadyActivation):
            raise TypeError("prior must be a WorkflowSpawnReadyActivation")
        owner_id = _required(owner_id, "owner_id")
        now = _time(now)
        expires_at = now + _positive_ttl(ttl_seconds)
        row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations WHERE activation_receipt_id=?",
            (prior.activation_receipt_id,),
        ).fetchone()
        if row is None:
            raise UnitOfWorkConflict("workflow spawn activation receipt is missing")
        if str(row["state"]) != WorkflowSpawnReadyActivationState.ACTIVE.value:
            raise UnitOfWorkConflict("workflow spawn activation is stale")
        stored = _workflow_spawn_ready_activation(tx.connection, row)
        if stored != prior or stored.state is not WorkflowSpawnReadyActivationState.ACTIVE:
            raise UnitOfWorkConflict("workflow spawn activation is stale")
        runtime = tx.connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (prior.execution_lease.run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence = tx.connection.execute(
            "SELECT * FROM run_fences WHERE run_id=?",
            (prior.execution_lease.run_id,),
        ).fetchone()
        run = tx.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (prior.execution_lease.run_id,),
        ).fetchone()
        if runtime is None or fence is None or run is None:
            raise UnitOfWorkConflict("workflow spawn activation authority disappeared")
        if str(run["state"]) != RunState.RUNNING.value:
            raise UnitOfWorkConflict("workflow spawn parent Run is not recoverable")
        if float(runtime["expires_at"]) > now:
            if str(runtime["owner_id"]) != owner_id:
                raise UnitOfWorkConflict(
                    "workflow spawn activation has a live foreign Runtime owner"
                )
            return stored
        if (
            str(runtime["owner_id"]) != prior.execution_lease.owner_id
            or int(runtime["epoch"]) != prior.execution_lease.epoch
            or str(fence["owner_id"]) != prior.run_fence.owner_id
            or int(fence["runtime_lease_epoch"]) != prior.run_fence.runtime_lease_epoch
            or int(fence["epoch"]) != prior.run_fence.epoch
            or str(fence["state"]) != "active"
        ):
            raise UnitOfWorkConflict("workflow spawn activation authority drifted")

        workflow_namespace: str | None = None
        workflow_epoch: int | None = None
        prior_workflow_lease = prior.workflow_lease
        if prior_workflow_lease is not None:
            workflow_namespace = prior_workflow_lease.namespace
            projection = tx.connection.execute(
                "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
                (prior.execution_lease.run_id, workflow_namespace),
            ).fetchone()
            if (
                projection is None
                or str(projection["owner_id"]) != prior_workflow_lease.owner_id
                or int(projection["epoch"]) != prior_workflow_lease.epoch
                or float(projection["expires_at"]) > now
            ):
                raise UnitOfWorkConflict(
                    "workflow spawn Workflow activation is still live or drifted"
                )
            workflow_epoch = int(projection["epoch"]) + 1

        runtime_epoch = max(int(runtime["epoch"]), int(fence["runtime_lease_epoch"])) + 1
        fence_epoch = int(fence["epoch"]) + 1
        continuation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (prior.ready_receipt.spawn_operation_id,),
        ).fetchone()
        if (
            continuation is None
            or str(continuation["state"]) != "claimed"
            or str(continuation["owner_id"]) != prior.continuation_claim.owner_id
            or int(continuation["runtime_lease_epoch"])
            != prior.continuation_claim.runtime_lease_epoch
            or int(continuation["run_fence_epoch"]) != prior.continuation_claim.run_fence_epoch
            or int(continuation["claim_epoch"]) != prior.continuation_claim.claim_epoch
            or int(continuation["version"]) != prior.continuation_claim.version
        ):
            raise UnitOfWorkConflict("workflow spawn continuation claim drifted")
        next_claim_epoch = int(continuation["claim_epoch"]) + 1
        activation_version = prior.activation_version + 1
        activation_receipt_id = self._derived_id(
            "workflow-spawn/activation/v1",
            f"{prior.ready_receipt.ready_receipt_id}:{runtime_epoch}:{activation_version}",
        )

        _fault(fault, "workflow:spawn_activation_reclaim:before_runtime_lease_write")
        changed = tx.connection.execute(
            """
            UPDATE workflow_leases SET owner_id=?,epoch=?,expires_at=?
            WHERE run_id=? AND namespace=? AND owner_id=? AND epoch=? AND expires_at<=?
            """,
            (
                owner_id,
                runtime_epoch,
                expires_at,
                prior.execution_lease.run_id,
                RUNTIME_LEASE_NAMESPACE,
                prior.execution_lease.owner_id,
                prior.execution_lease.epoch,
                now,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn Runtime takeover CAS failed")
        _fault(fault, "workflow:spawn_activation_reclaim:after_runtime_lease_write")
        changed = tx.connection.execute(
            """
            UPDATE run_fences
            SET owner_id=?,runtime_lease_epoch=?,epoch=?,state='active',
                acquired_at=?,released_at=NULL
            WHERE run_id=? AND owner_id=? AND runtime_lease_epoch=? AND epoch=?
              AND state='active'
            """,
            (
                owner_id,
                runtime_epoch,
                fence_epoch,
                now,
                prior.execution_lease.run_id,
                prior.run_fence.owner_id,
                prior.run_fence.runtime_lease_epoch,
                prior.run_fence.epoch,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn RunFence takeover CAS failed")
        _fault(fault, "workflow:spawn_activation_reclaim:after_run_fence_write")
        if (
            workflow_namespace is not None
            and workflow_epoch is not None
            and prior_workflow_lease is not None
        ):
            changed = tx.connection.execute(
                """
                UPDATE workflow_leases SET owner_id=?,epoch=?,expires_at=?
                WHERE run_id=? AND namespace=? AND owner_id=? AND epoch=?
                  AND expires_at<=?
                """,
                (
                    owner_id,
                    workflow_epoch,
                    expires_at,
                    prior.execution_lease.run_id,
                    workflow_namespace,
                    prior_workflow_lease.owner_id,
                    prior_workflow_lease.epoch,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn Workflow takeover CAS failed")
            _fault(fault, "workflow:spawn_activation_reclaim:after_workflow_lease_write")

        changed = tx.connection.execute(
            """
            UPDATE workflow_spawn_continuations
            SET owner_id=?,runtime_lease_epoch=?,run_fence_epoch=?,
                workflow_lease_epoch=?,claim_epoch=?,expires_at=?,
                version=version+1,updated_at=?
            WHERE operation_id=? AND state='claimed' AND version=?
            """,
            (
                owner_id,
                runtime_epoch,
                fence_epoch,
                workflow_epoch,
                next_claim_epoch,
                expires_at,
                now,
                prior.ready_receipt.spawn_operation_id,
                prior.continuation_claim.version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn continuation takeover CAS failed")
        _fault(fault, "workflow:spawn_activation_reclaim:after_continuation_write")

        superseded_version = prior.activation_version + 1
        superseded_hash = _workflow_spawn_activation_hash(
            activation_receipt_id=prior.activation_receipt_id,
            ready_receipt_id=prior.ready_receipt.ready_receipt_id,
            spawn_operation_id=prior.ready_receipt.spawn_operation_id,
            parent_run_id=prior.execution_lease.run_id,
            effect_id=prior.ready_receipt.effect_id,
            owner_id=prior.execution_lease.owner_id,
            runtime_lease_epoch=prior.execution_lease.epoch,
            run_fence_epoch=prior.run_fence.epoch,
            workflow_lease_epoch=(
                None if prior.workflow_lease is None else prior.workflow_lease.epoch
            ),
            continuation_claim_epoch=prior.continuation_claim.claim_epoch,
            predecessor_activation_receipt_id=(prior.predecessor_activation_receipt_id),
            version=superseded_version,
        )
        changed = tx.connection.execute(
            """
            UPDATE workflow_spawn_ready_activations
            SET state='superseded',version=?,canonical_hash=?,superseded_at=?
            WHERE activation_receipt_id=? AND state='active' AND version=?
            """,
            (
                superseded_version,
                superseded_hash,
                now,
                prior.activation_receipt_id,
                prior.activation_version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn predecessor supersede CAS failed")
        _fault(fault, "workflow:spawn_activation_reclaim:after_predecessor_write")

        successor_hash = _workflow_spawn_activation_hash(
            activation_receipt_id=activation_receipt_id,
            ready_receipt_id=prior.ready_receipt.ready_receipt_id,
            spawn_operation_id=prior.ready_receipt.spawn_operation_id,
            parent_run_id=prior.execution_lease.run_id,
            effect_id=prior.ready_receipt.effect_id,
            owner_id=owner_id,
            runtime_lease_epoch=runtime_epoch,
            run_fence_epoch=fence_epoch,
            workflow_lease_epoch=workflow_epoch,
            continuation_claim_epoch=next_claim_epoch,
            predecessor_activation_receipt_id=prior.activation_receipt_id,
            version=activation_version,
        )
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_ready_activations(
                activation_receipt_id,ready_receipt_id,spawn_operation_id,
                parent_run_id,effect_id,owner_id,runtime_lease_epoch,
                run_fence_epoch,workflow_lease_epoch,continuation_claim_epoch,
                predecessor_activation_receipt_id,state,version,canonical_hash,
                created_at,superseded_at,consumed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,NULL,NULL)
            """,
            (
                activation_receipt_id,
                prior.ready_receipt.ready_receipt_id,
                prior.ready_receipt.spawn_operation_id,
                prior.execution_lease.run_id,
                prior.ready_receipt.effect_id,
                owner_id,
                runtime_epoch,
                fence_epoch,
                workflow_epoch,
                next_claim_epoch,
                prior.activation_receipt_id,
                activation_version,
                successor_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_activation_reclaim:after_successor_write")
        tx.connection.execute(
            "UPDATE runs SET version=version+1,updated_at=? WHERE run_id=? AND state='running'",
            (now, prior.execution_lease.run_id),
        )
        self._insert_event(
            tx.connection,
            event_id=f"{prior.execution_lease.run_id}:spawn-ready:{activation_receipt_id}",
            run_id=prior.execution_lease.run_id,
            kind="run.recovered",
            payload={
                "owner_id": owner_id,
                "lease_epoch": runtime_epoch,
                "spawn_operation_id": prior.ready_receipt.spawn_operation_id,
                "predecessor_activation_receipt_id": prior.activation_receipt_id,
            },
            now=now,
        )
        tx.register_after_commit_fault("workflow:spawn_activation_reclaim:after_commit")
        successor = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations WHERE activation_receipt_id=?",
            (activation_receipt_id,),
        ).fetchone()
        assert successor is not None
        return _workflow_spawn_ready_activation(tx.connection, successor)

    @staticmethod
    def _require_matching_spawn_ready(
        connection: sqlite3.Connection,
        continuation: sqlite3.Row,
        ready: WorkflowSpawnContinuationReady,
    ) -> None:
        durable = connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
            (ready.ready_receipt_id,),
        ).fetchone()
        if (
            durable is None
            or _workflow_spawn_continuation_ready(durable) != ready
            or ready.spawn_operation_id != str(continuation["operation_id"])
            or ready.ticket_receipt_id != str(continuation["ticket_receipt_id"])
            or ready.effect_id != str(continuation["effect_id"])
            or ready.handoff_attempt != int(continuation["handoff_attempt"])
            or durable["consumed_at"] is not None
        ):
            raise UnitOfWorkConflict("workflow spawn ready evidence differs")
