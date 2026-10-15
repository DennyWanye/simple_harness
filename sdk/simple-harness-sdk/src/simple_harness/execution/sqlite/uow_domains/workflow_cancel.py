# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow cancellation requests and convergence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import ExecutionLease, FaultHook, RunState, UnitOfWorkConflict
from simple_harness.workflow.execution_ports import (
    CancelConvergenceLease,
    CancelWorkflowOutcome,
    CancelWorkflowRequest,
    WorkflowActivation,
    WorkflowTransaction,
)

from ..delivery_audit import record_version as _delivery_audit_version
from .core import ExecutionUnitOfWorkCore
from .support import _fault, _required


class WorkflowCancelMixin(ExecutionUnitOfWorkCore):
    """Workflow cancellation requests and convergence."""

    __slots__ = ()

    if TYPE_CHECKING:

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

    @staticmethod
    def _cancel_outcome(row: sqlite3.Row) -> CancelWorkflowOutcome:
        return CancelWorkflowOutcome(
            str(row["cancel_id"]),
            int(row["generation"]),
            str(row["phase"]),
            tuple(json.loads(str(row["blocker_ids_json"]))),
            None if row["terminal"] is None else bool(row["terminal"]),
        )

    @staticmethod
    def _cancel_resolution_snapshot(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, JsonValue] | None:
        frozen_blockers = json.loads(str(row["blocker_snapshot_json"]))
        if not isinstance(frozen_blockers, dict):
            raise UnitOfWorkConflict("cancel blocker snapshot is corrupt")
        resolved_snapshot: dict[str, JsonValue] = {}
        for blocker_id, frozen_identity in sorted(frozen_blockers.items()):
            if not isinstance(blocker_id, str) or not isinstance(frozen_identity, dict):
                raise UnitOfWorkConflict("cancel blocker snapshot is corrupt")
            blocker = connection.execute(
                "SELECT * FROM run_wait_blockers WHERE blocker_id=? AND run_id=?",
                (blocker_id, str(row["run_id"])),
            ).fetchone()
            if (
                blocker is None
                or str(blocker["kind"]) != frozen_identity.get("kind")
                or str(blocker["ledger_identity"]) != frozen_identity.get("ledger_identity")
                or int(blocker["handoff_attempt"]) != frozen_identity.get("handoff_attempt")
                or int(blocker["version"])
                < int(frozen_identity.get("observed_blocker_version", -1))
            ):
                raise UnitOfWorkConflict("cancel blocker identity changed")
            if blocker["resolution_id"] is None:
                return None
            resolution = connection.execute(
                """
                SELECT resolution_id,kind,ledger_identity,handoff_attempt,outcome,
                       outcome_hash
                FROM reconciliation_resolutions WHERE resolution_id=?
                """,
                (str(blocker["resolution_id"]),),
            ).fetchone()
            if (
                resolution is None
                or str(resolution["kind"]) != str(blocker["kind"])
                or str(resolution["ledger_identity"]) != str(blocker["ledger_identity"])
                or int(resolution["handoff_attempt"]) != int(blocker["handoff_attempt"])
            ):
                raise UnitOfWorkConflict("cancel blocker resolution changed")
            resolved_snapshot[blocker_id] = {
                "blocker_version": int(blocker["version"]),
                "resolution_id": str(resolution["resolution_id"]),
                "outcome": str(resolution["outcome"]),
                "outcome_hash": str(resolution["outcome_hash"]),
            }
        return resolved_snapshot

    def read_cancel_resolution_snapshot(self, cancel_id: str) -> Mapping[str, JsonValue] | None:
        row = self.database.connection.execute(
            "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?", (cancel_id,)
        ).fetchone()
        if row is None:
            raise UnitOfWorkConflict("cancel receipt does not exist")
        return self._cancel_resolution_snapshot(self.database.connection, row)

    def read_cancel_outcome(self, *, run_id: str, generation: int) -> CancelWorkflowOutcome | None:
        row = self.database.connection.execute(
            "SELECT * FROM workflow_cancel_receipts WHERE run_id=? AND generation=?",
            (run_id, generation),
        ).fetchone()
        return None if row is None else self._cancel_outcome(row)

    def read_cancel_request(self, *, run_id: str, generation: int) -> CancelWorkflowRequest | None:
        row = self.database.connection.execute(
            "SELECT cancel_id,run_id,reason,generation "
            "FROM workflow_cancel_receipts WHERE run_id=? AND generation=?",
            (run_id, generation),
        ).fetchone()
        if row is None:
            return None
        return CancelWorkflowRequest(
            str(row["cancel_id"]),
            str(row["run_id"]),
            str(row["reason"]),
            int(row["generation"]),
        )

    def verify_workflow_cancel_terminal(
        self, *, run_id: str, cancel_id: str, generation: int
    ) -> bool:
        row = self.database.connection.execute(
            """
            SELECT receipts.phase, receipts.terminal, receipts.outcome_json,
                   runs.state
            FROM workflow_cancel_receipts AS receipts
            JOIN runs ON runs.run_id = receipts.run_id
            WHERE receipts.cancel_id=? AND receipts.run_id=?
              AND receipts.generation=?
            """,
            (cancel_id, run_id, generation),
        ).fetchone()
        if (
            row is None
            or str(row["phase"]) != "terminal"
            or int(row["terminal"]) != 1
            or str(row["state"]) != RunState.CANCELLED.value
            or row["outcome_json"] is None
        ):
            return False
        outcome = json.loads(str(row["outcome_json"]))
        if not isinstance(outcome, dict):
            return False
        event = outcome.get("terminal_event")
        if not isinstance(event, dict):
            return False
        event_id = event.get("event_id")
        if (
            not isinstance(event_id, str)
            or event.get("cancel_id") != cancel_id
            or event.get("generation") != generation
        ):
            return False
        durable_event = self.database.connection.execute(
            "SELECT kind,payload_json FROM run_events WHERE event_id=? AND run_id=?",
            (event_id, run_id),
        ).fetchone()
        return (
            durable_event is not None
            and str(durable_event["kind"]) == "workflow.cancelled"
            and json.loads(str(durable_event["payload_json"])) == event
        )

    async def request_cancel(
        self,
        transaction: WorkflowTransaction,
        request: CancelWorkflowRequest,
        expected_run_version: int,
        activation: WorkflowActivation | None,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> CancelWorkflowOutcome:
        tx = self._assert_open_workflow_transaction(transaction)
        existing = tx.connection.execute(
            "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?",
            (request.cancel_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["run_id"]) != request.run_id
                or str(existing["reason"]) != request.reason
                or int(existing["generation"]) != request.expected_generation
            ):
                raise UnitOfWorkConflict("cancel identity reused differently")
            if activation is not None and not bool(existing["terminal"]):
                self._require_runtime_and_fence(
                    tx.connection,
                    run_id=request.run_id,
                    execution_lease=activation.execution_lease,
                    run_fence=activation.run_fence,
                    now=now,
                    require_exact_expiry=False,
                )
                projection = tx.connection.execute(
                    "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                    "WHERE run_id=? AND namespace=?",
                    (request.run_id, activation.workflow_lease.namespace),
                ).fetchone()
                if projection is None or tuple(projection) != (
                    activation.workflow_lease.owner_id,
                    activation.workflow_lease.epoch,
                    activation.workflow_lease.expires_at,
                ):
                    raise UnitOfWorkConflict("cancel workflow activation changed")
                self._invalidate_cancel_activation(
                    tx.connection,
                    request.run_id,
                    activation,
                    now=now,
                    fault=fault,
                )
            return self._cancel_outcome(existing)
        run = tx.connection.execute(
            "SELECT state,version FROM runs WHERE run_id=?", (request.run_id,)
        ).fetchone()
        if (
            run is None
            or int(run["version"]) != expected_run_version
            or str(run["state"]) in {"completed", "failed", "cancelled"}
        ):
            raise UnitOfWorkConflict("cancel Run version changed")
        if activation is not None:
            self._require_runtime_and_fence(
                tx.connection,
                run_id=request.run_id,
                execution_lease=activation.execution_lease,
                run_fence=activation.run_fence,
                now=now,
                require_exact_expiry=False,
            )
            projection = tx.connection.execute(
                "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                "WHERE run_id=? AND namespace=?",
                (request.run_id, activation.workflow_lease.namespace),
            ).fetchone()
            if projection is None or tuple(projection) != (
                activation.workflow_lease.owner_id,
                activation.workflow_lease.epoch,
                activation.workflow_lease.expires_at,
            ):
                raise UnitOfWorkConflict("cancel workflow activation changed")
        current_generation = tx.connection.execute(
            "SELECT MAX(generation) FROM workflow_cancel_receipts WHERE run_id=?",
            (request.run_id,),
        ).fetchone()[0]
        expected_generation = 0 if current_generation is None else int(current_generation) + 1
        if request.expected_generation != expected_generation:
            raise UnitOfWorkConflict("cancel generation changed")
        blocker_rows = tx.connection.execute(
            """
            SELECT blocker_id, kind, ledger_identity, handoff_attempt, version
            FROM run_wait_blockers
            WHERE run_id=?
            ORDER BY blocker_id
            """,
            (request.run_id,),
        ).fetchall()
        blocker_ids = tuple(str(row["blocker_id"]) for row in blocker_rows)
        blocker_snapshot: dict[str, JsonValue] = {
            str(row["blocker_id"]): {
                "kind": str(row["kind"]),
                "ledger_identity": str(row["ledger_identity"]),
                "handoff_attempt": int(row["handoff_attempt"]),
                "observed_blocker_version": int(row["version"]),
            }
            for row in blocker_rows
        }
        phase = "cancelling" if blocker_ids else "requested"
        _fault(fault, "workflow:request_cancel:before_runs_write")
        tx.connection.execute(
            "UPDATE runs SET state='cancel_requested',version=version+1,updated_at=? WHERE run_id=?"
            " AND version=?",
            (now, request.run_id, expected_run_version),
        )
        _fault(fault, "workflow:request_cancel:after_runs_write")
        self._invalidate_cancel_activation(
            tx.connection,
            request.run_id,
            activation,
            now=now,
            fault=fault,
        )
        _fault(fault, "workflow:request_cancel:before_workflow_cancel_receipts_write")
        tx.connection.execute(
            "INSERT INTO"
            " workflow_cancel_receipts(cancel_id,run_id,generation,reason,phase,blocker_ids_json,blocker_snapshot_json,terminal,version,created_at,updated_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,NULL,0,?,?)",
            (
                request.cancel_id,
                request.run_id,
                request.expected_generation,
                request.reason,
                phase,
                canonical_json(list(blocker_ids)),
                canonical_json(blocker_snapshot),
                now,
                now,
            ),
        )
        _fault(fault, "workflow:request_cancel:after_workflow_cancel_receipts_write")
        return self._cancel_outcome(
            tx.connection.execute(
                "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?",
                (request.cancel_id,),
            ).fetchone()
        )

    @staticmethod
    def _invalidate_cancel_activation(
        connection: sqlite3.Connection,
        run_id: str,
        activation: WorkflowActivation | None,
        *,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        _fault(fault, "workflow:request_cancel:before_workflow_leases_write")
        if activation is None:
            connection.execute("DELETE FROM workflow_leases WHERE run_id=?", (run_id,))
        else:
            changed = connection.execute(
                "DELETE FROM workflow_leases WHERE run_id=? AND owner_id=? AND epoch=?",
                (
                    run_id,
                    activation.execution_lease.owner_id,
                    activation.execution_lease.epoch,
                ),
            ).rowcount
            if changed < 2:
                raise UnitOfWorkConflict("cancel activation projection changed")
        _fault(fault, "workflow:request_cancel:after_workflow_leases_write")
        _fault(fault, "workflow:request_cancel:before_run_fences_write")
        if activation is None:
            changed = connection.execute(
                "UPDATE run_fences SET epoch=epoch+1,state='cancelled',released_at=? "
                "WHERE run_id=? AND state='active'",
                (now, run_id),
            ).rowcount
        else:
            changed = connection.execute(
                "UPDATE run_fences SET epoch=epoch+1,state='cancelled',released_at=? "
                "WHERE run_id=? AND owner_id=? AND epoch=? "
                "AND runtime_lease_epoch=? AND state='active'",
                (
                    now,
                    run_id,
                    activation.run_fence.owner_id,
                    activation.run_fence.epoch,
                    activation.execution_lease.epoch,
                ),
            ).rowcount
        if activation is not None and changed != 1:
            raise UnitOfWorkConflict("cancel activation fence changed")
        _fault(fault, "workflow:request_cancel:after_run_fences_write")

    async def claim_cancel_convergence(
        self,
        transaction: WorkflowTransaction,
        cancel_id: str,
        expected_generation: int,
        owner_id: str,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> CancelConvergenceLease:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?", (cancel_id,)
        ).fetchone()
        if (
            row is None
            or int(row["generation"]) != expected_generation
            or str(row["phase"]) == "terminal"
        ):
            raise UnitOfWorkConflict("cancel convergence is not claimable")
        if (
            row["convergence_expires_at"] is not None
            and float(row["convergence_expires_at"]) > now
            and str(row["convergence_owner"]) != owner_id
        ):
            raise UnitOfWorkConflict("cancel convergence has an active owner")
        epoch = int(row["convergence_epoch"]) + 1
        expires_at = now + ttl_seconds
        _fault(
            fault,
            "workflow:claim_cancel_convergence:before_workflow_cancel_receipts_write",
        )
        changed = tx.connection.execute(
            "UPDATE workflow_cancel_receipts SET"
            " phase='cancelling',convergence_owner=?,convergence_epoch=?,convergence_expires_at=?,version=version+1,updated_at=?"  # noqa: E501
            " WHERE cancel_id=? AND generation=? AND version=?",
            (
                owner_id,
                epoch,
                expires_at,
                now,
                cancel_id,
                expected_generation,
                int(row["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("cancel convergence CAS failed")
        _fault(
            fault,
            "workflow:claim_cancel_convergence:after_workflow_cancel_receipts_write",
        )
        return CancelConvergenceLease(
            str(row["run_id"]), expected_generation, owner_id, epoch, expires_at
        )

    async def settle_cancel_convergence(
        self,
        transaction: WorkflowTransaction,
        cancel_lease: CancelConvergenceLease,
        resolution_snapshot: Mapping[str, JsonValue],
        terminal_checkpoint: Mapping[str, JsonValue],
        terminal_event: Mapping[str, JsonValue],
        deliveries: Sequence[Mapping[str, JsonValue]],
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> CancelWorkflowOutcome:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_cancel_receipts WHERE run_id=? AND generation=?",
            (cancel_lease.run_id, cancel_lease.generation),
        ).fetchone()
        if (
            row is None
            or (
                row["convergence_owner"],
                int(row["convergence_epoch"]),
                float(row["convergence_expires_at"]),
            )
            != (cancel_lease.owner_id, cancel_lease.epoch, cancel_lease.expires_at)
            or cancel_lease.expires_at <= now
        ):
            raise UnitOfWorkConflict("cancel convergence lease is stale")
        resolved_snapshot = self._cancel_resolution_snapshot(tx.connection, row)
        if resolved_snapshot is None:
            raise UnitOfWorkConflict("cancel blockers remain unresolved")
        if canonical_json(dict(resolution_snapshot)) != canonical_json(resolved_snapshot):
            raise UnitOfWorkConflict("cancel resolution snapshot changed")
        checkpoint_id = _required(terminal_checkpoint.get("checkpoint_id"), "checkpoint_id")
        namespace = _required(terminal_checkpoint.get("namespace", "native"), "namespace")
        checkpoint_payload = cast(dict[str, JsonValue], dict(terminal_checkpoint))
        checkpoint_json = canonical_json(checkpoint_payload)
        outcome = {
            "resolution_snapshot": dict(resolution_snapshot),
            "terminal_checkpoint": dict(terminal_checkpoint),
            "terminal_event": dict(terminal_event),
            "deliveries": [dict(item) for item in deliveries],
        }
        _fault(fault, "workflow:settle_cancel_convergence:before_runs_write")
        tx.connection.execute(
            "UPDATE runs SET state='cancelled',version=version+1,updated_at=? WHERE run_id=? AND"
            " state='cancel_requested'",
            (now, cancel_lease.run_id),
        )
        _fault(fault, "workflow:settle_cancel_convergence:after_runs_write")
        _fault(
            fault,
            "workflow:settle_cancel_convergence:before_workflow_checkpoints_write",
        )
        next_version = int(
            tx.connection.execute(
                "SELECT COALESCE(MAX(version),-1)+1 FROM workflow_checkpoints WHERE run_id=? AND"
                " namespace=?",
                (cancel_lease.run_id, namespace),
            ).fetchone()[0]
        )
        tx.connection.execute(
            "INSERT INTO"
            " workflow_checkpoints(checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,lease_epoch,version,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                checkpoint_id,
                cancel_lease.run_id,
                namespace,
                checkpoint_json,
                hashlib.sha256(checkpoint_json.encode()).hexdigest(),
                cancel_lease.epoch,
                next_version,
                now,
            ),
        )
        _fault(
            fault,
            "workflow:settle_cancel_convergence:after_workflow_checkpoints_write",
        )
        event_id = _required(terminal_event.get("event_id"), "event_id")
        _fault(fault, "workflow:settle_cancel_convergence:before_run_events_write")
        self._insert_event(
            tx.connection,
            event_id=event_id,
            run_id=cancel_lease.run_id,
            kind="workflow.cancelled",
            payload=cast(dict[str, JsonValue], dict(terminal_event)),
            now=now,
        )
        _fault(fault, "workflow:settle_cancel_convergence:after_run_events_write")
        _fault(
            fault,
            "workflow:settle_cancel_convergence:before_delivery_outbox_write",
        )
        for item in deliveries:
            delivery_id = _required(item.get("delivery_id"), "delivery_id")
            sink_kind = _required(item.get("sink_kind"), "sink_kind")
            idempotency_key = _required(item.get("idempotency_key"), "idempotency_key")
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                raise UnitOfWorkConflict("cancel delivery payload must be an object")
            tx.connection.execute(
                "INSERT INTO"
                " delivery_outbox(delivery_id,run_id,sink_kind,idempotency_key,payload_json,state,version,created_at)"  # noqa: E501
                " VALUES(?,?,?,?,?,'pending',0,?)",
                (
                    delivery_id,
                    cancel_lease.run_id,
                    sink_kind,
                    idempotency_key,
                    canonical_json(dict(payload)),
                    now,
                ),
            )
            _delivery_audit_version(tx.connection, delivery_id, now=now, operation="created")
        _fault(
            fault,
            "workflow:settle_cancel_convergence:after_delivery_outbox_write",
        )
        self._materialize_cancelled_attached_child_terminal(
            tx.connection,
            run_id=cancel_lease.run_id,
            cancel_id=str(row["cancel_id"]),
            owner_id=cancel_lease.owner_id,
            now=now,
            fault=fault,
        )
        _fault(
            fault,
            "workflow:settle_cancel_convergence:before_workflow_cancel_receipts_write",
        )
        tx.connection.execute(
            "UPDATE workflow_cancel_receipts SET"
            " phase='terminal',terminal=1,outcome_json=?,version=version+1,updated_at=? WHERE"
            " cancel_id=? AND convergence_owner=? AND convergence_epoch=?",
            (
                canonical_json(cast(JsonValue, outcome)),
                now,
                str(row["cancel_id"]),
                cancel_lease.owner_id,
                cancel_lease.epoch,
            ),
        )
        _fault(
            fault,
            "workflow:settle_cancel_convergence:after_workflow_cancel_receipts_write",
        )
        return self._cancel_outcome(
            tx.connection.execute(
                "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?",
                (str(row["cancel_id"]),),
            ).fetchone()
        )

    def _materialize_cancelled_attached_child_terminal(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        cancel_id: str,
        owner_id: str,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        command = connection.execute(
            "SELECT commands.* FROM child_commands AS commands "
            "JOIN run_links AS links ON links.child_run_id=commands.child_run_id "
            "WHERE commands.child_run_id=? AND links.attachment_policy='attached'",
            (run_id,),
        ).fetchone()
        if command is None:
            return
        existing = connection.execute(
            "SELECT * FROM child_terminal_receipts WHERE child_run_id=?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["command_id"]) == str(command["command_id"])
                and str(existing["terminal_state"]) == RunState.CANCELLED.value
            ):
                return
            raise UnitOfWorkConflict("attached child terminal receipt differs")
        fence = connection.execute(
            "SELECT * FROM run_fences WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if fence is None or str(fence["state"]) != "cancelled":
            raise UnitOfWorkConflict("cancelled attached child lacks its cancelled Run fence")
        signal_id = f"{cancel_id}:child-signal"
        event_id = f"{cancel_id}:child-terminal-event"
        receipt_id = f"{cancel_id}:child-terminal-receipt"
        public_terminal: dict[str, JsonValue] = {
            "status": RunState.CANCELLED.value,
            "result": {
                "kind": "workflow_terminal",
                "status": RunState.CANCELLED.value,
                "error": None,
                "recovery_action": None,
                "card": None,
            },
        }
        public_json = canonical_json(public_terminal)
        _fault(fault, "workflow:settle_cancel_child:before_child_command_write")
        changed = connection.execute(
            "UPDATE child_commands SET state='acked',updated_at=? "
            "WHERE command_id=? AND state IN ('pending','scheduled')",
            (now, str(command["command_id"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("cancelled attached child command CAS failed")
        _fault(fault, "workflow:settle_cancel_child:after_child_command_write")
        _fault(fault, "workflow:settle_cancel_child:before_child_signal_write")
        connection.execute(
            "INSERT INTO child_signals("
            "signal_id,parent_run_id,child_run_id,payload_json,state,version,"
            "claim_epoch,created_at,updated_at) "
            "VALUES(?,?,?,?,'pending',0,0,?,?)",
            (
                signal_id,
                str(command["parent_run_id"]),
                run_id,
                public_json,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:settle_cancel_child:after_child_signal_write")
        _fault(fault, "workflow:settle_cancel_child:before_child_event_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=run_id,
            kind="child.terminal.cancelled",
            payload=public_terminal,
            now=now,
        )
        _fault(fault, "workflow:settle_cancel_child:after_child_event_write")
        _fault(fault, "workflow:settle_cancel_child:before_child_receipt_write")
        connection.execute(
            "INSERT INTO child_terminal_receipts("
            "receipt_id,command_id,child_run_id,terminal_state,outcome_hash,"
            "signal_id,event_id,owner_id,runtime_lease_epoch,fence_epoch,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                str(command["command_id"]),
                run_id,
                RunState.CANCELLED.value,
                hashlib.sha256(public_json.encode()).hexdigest(),
                signal_id,
                event_id,
                owner_id,
                int(fence["runtime_lease_epoch"]),
                int(fence["epoch"]),
                now,
            ),
        )
        _fault(fault, "workflow:settle_cancel_child:after_child_receipt_write")
