# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Child signals to parents and spawn-child continuation acknowledgements."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, RunId, canonical_json, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.contracts.children import (
    AttachmentPolicy,
    ChildSignalAckReceipt,
    ChildSignalAckResult,
    ChildSignalRecord,
    ChildSignalState,
)
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    ContinuationRecord,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
    WorkflowCheckpoint,
)

from ..audit_witness import record_claim_witness
from .core import ExecutionUnitOfWorkCore
from .support import (
    _child_signal_ack_receipt,
    _child_signal_record,
    _fault,
    _object_json,
    _required,
    _time,
    _workflow_checkpoint,
)


class ChildSignalMixin(ExecutionUnitOfWorkCore):
    """Child signals to parents and spawn-child continuation acknowledgements."""

    __slots__ = ()

    if TYPE_CHECKING:

        def read_child_signal_ack_receipt(
            self, receipt_id: str
        ) -> ChildSignalAckReceipt | None: ...
        def read_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...

    def ack_spawn_child_continuation_and_continue_batch(
        self,
        *,
        run_id: str,
        continuation_claim: ContinuationRecord,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowCheckpoint:
        """ACK one child signal and restore its suspended ordered Tool batch."""

        run_id = _required(run_id, "run_id")
        now = _time(now)
        if continuation_claim.run_id != run_id or execution_lease.run_id != run_id:
            raise UnitOfWorkConflict("workflow spawn continuation belongs elsewhere")
        with self.database.transaction() as connection:
            wait = connection.execute(
                "SELECT * FROM workflow_spawn_child_wait_receipts WHERE continuation_id=?",
                (continuation_claim.continuation_id,),
            ).fetchone()
            if wait is None:
                raise UnitOfWorkNotFound(continuation_claim.continuation_id)
            latest = connection.execute(
                "SELECT * FROM workflow_checkpoints WHERE run_id=? "
                "AND namespace='react.termination.v1' "
                "ORDER BY version DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if latest is None:
                raise UnitOfWorkConflict("workflow spawn ReAct checkpoint is missing")
            if str(wait["state"]) in {
                "acked_completion_pending",
                "acked",
            }:
                receipt = connection.execute(
                    "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
                    (wait["progress_receipt_id"],),
                ).fetchone()
                continuation = connection.execute(
                    "SELECT state,ack_receipt_id FROM continuations WHERE continuation_id=?",
                    (continuation_claim.continuation_id,),
                ).fetchone()
                if (
                    receipt is None
                    or continuation is None
                    or str(continuation["state"]) != "acked"
                    or continuation["ack_receipt_id"] != wait["progress_receipt_id"]
                    or str(receipt["owner_id"]) != continuation_claim.claimed_by
                    or int(receipt["runtime_lease_epoch"]) != continuation_claim.runtime_lease_epoch
                    or int(receipt["claim_epoch"]) != continuation_claim.claim_epoch
                ):
                    raise UnitOfWorkConflict("workflow spawn continuation replay differs")
                return _workflow_checkpoint(latest)
            if str(wait["state"]) != "claimed":
                raise UnitOfWorkConflict("workflow spawn child wait is not claimed")
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            self._require_continuation_claim(connection, continuation_claim, execution_lease)
            run = connection.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None or str(run["state"]) != RunState.RUNNING.value:
                raise UnitOfWorkConflict("workflow spawn continuation requires a RUNNING parent")
            checkpoint_payload = json.loads(str(latest["checkpoint_json"]))
            if (
                not isinstance(checkpoint_payload, dict)
                or checkpoint_payload.get("phase") != "child_wait"
                or int(latest["version"]) != int(wait["react_checkpoint_revision"])
                or str(latest["checkpoint_hash"]) != str(wait["react_checkpoint_hash"])
                or checkpoint_payload.get("workflow_spawn_operation_id")
                != str(wait["spawn_operation_id"])
                or checkpoint_payload.get("workflow_spawn_child_run_id")
                != str(wait["child_run_id"])
            ):
                raise UnitOfWorkConflict("workflow spawn suspended ReAct checkpoint differs")
            continuation_payload = thaw_json(continuation_claim.payload)
            if (
                not isinstance(continuation_payload, dict)
                or continuation_payload.get("kind") != "child_terminal"
                or continuation_payload.get("signal_id") != wait["child_signal_id"]
                or continuation_payload.get("child_run_id") != wait["child_run_id"]
                or not isinstance(continuation_payload.get("payload"), dict)
            ):
                raise UnitOfWorkConflict("workflow spawn child completion payload differs")
            pending_completion: dict[str, JsonValue] = {
                "schema_version": "workflow_child_completion.v1",
                "child_run_id": str(wait["child_run_id"]),
                "terminal": cast(JsonValue, continuation_payload["payload"]),
            }
            pending_json = canonical_json(pending_completion)
            pending_hash = hashlib.sha256(pending_json.encode()).hexdigest()
            append_id = self._derived_id(
                "workflow-spawn/child-completion-append/v1",
                str(wait["spawn_operation_id"]),
            )
            receipt_id = self._derived_id(
                "workflow-spawn/continuation-progress/v1",
                f"{continuation_claim.continuation_id}:{continuation_claim.claim_epoch}",
            )
            outcome_hash = hashlib.sha256(
                canonical_json(
                    {
                        "run_id": run_id,
                        "continuation_id": continuation_claim.continuation_id,
                        "claim_epoch": continuation_claim.claim_epoch,
                        "parent_wait_receipt_id": wait["parent_wait_receipt_id"],
                        "pending_child_completion_hash": pending_hash,
                    }
                ).encode()
            ).hexdigest()
            _fault(fault, "workflow:spawn_child_continue:before_progress_write")
            connection.execute(
                "INSERT INTO continuation_progress_receipts("
                "receipt_id,continuation_id,run_id,owner_id,runtime_lease_epoch,"
                "claim_epoch,outcome_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    continuation_claim.continuation_id,
                    run_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    outcome_hash,
                    now,
                ),
            )
            _fault(fault, "workflow:spawn_child_continue:after_progress_write")
            _fault(fault, "workflow:spawn_child_continue:before_continuation_write")
            changed = connection.execute(
                "UPDATE continuations SET state='acked',acked_at=?,ack_receipt_id=?,"
                "version=version+1 WHERE continuation_id=? AND state='claimed' "
                "AND claimed_by=? AND runtime_lease_epoch=? AND claim_epoch=? "
                "AND version=?",
                (
                    now,
                    receipt_id,
                    continuation_claim.continuation_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    continuation_claim.version,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn continuation ACK CAS failed")
            _fault(fault, "workflow:spawn_child_continue:after_continuation_write")
            next_wait_version = int(wait["version"]) + 1
            lifecycle_hash = hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(wait["identity_hash"]),
                        "state": "acked_completion_pending",
                        "version": next_wait_version,
                        "child_signal_id": wait["child_signal_id"],
                        "continuation_id": continuation_claim.continuation_id,
                        "progress_receipt_id": receipt_id,
                        "pending_child_completion_hash": pending_hash,
                    }
                ).encode()
            ).hexdigest()
            _fault(fault, "workflow:spawn_child_continue:before_wait_write")
            changed = connection.execute(
                "UPDATE workflow_spawn_child_wait_receipts SET "
                "state='acked_completion_pending',progress_receipt_id=?,"
                "pending_child_completion_json=?,pending_child_completion_hash=?,"
                "child_completion_append_id=?,version=?,lifecycle_hash=? "
                "WHERE parent_wait_receipt_id=? AND state='claimed' AND version=?",
                (
                    receipt_id,
                    pending_json,
                    pending_hash,
                    append_id,
                    next_wait_version,
                    lifecycle_hash,
                    str(wait["parent_wait_receipt_id"]),
                    int(wait["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn child-wait ACK CAS failed")
            _fault(fault, "workflow:spawn_child_continue:after_wait_write")
            next_checkpoint = dict(checkpoint_payload)
            next_checkpoint.update(
                {
                    "phase": "tool_batch_reserved",
                    "tool_result_progress": int(wait["next_tool_ordinal"]),
                    "workflow_spawn_wait_receipt_id": str(wait["parent_wait_receipt_id"]),
                    "pending_child_completion": pending_completion,
                    "pending_child_completion_hash": pending_hash,
                    "pending_child_completion_append_id": append_id,
                }
            )
            next_checkpoint.pop("workflow_spawn_operation_id", None)
            next_checkpoint.pop("workflow_spawn_child_run_id", None)
            checkpoint_json = canonical_json(cast(JsonValue, next_checkpoint))
            checkpoint_hash = hashlib.sha256(checkpoint_json.encode()).hexdigest()
            checkpoint_version = int(latest["version"]) + 1
            _fault(fault, "workflow:spawn_child_continue:before_checkpoint_write")
            connection.execute(
                "INSERT INTO workflow_checkpoints("
                "checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,"
                "lease_epoch,version,created_at) VALUES(?,?,"
                "'react.termination.v1',?,?,?,?,?)",
                (
                    f"{run_id}:react.termination.v1:{checkpoint_version}",
                    run_id,
                    checkpoint_json,
                    checkpoint_hash,
                    execution_lease.epoch,
                    checkpoint_version,
                    now,
                ),
            )
            _fault(fault, "workflow:spawn_child_continue:after_checkpoint_write")
        _fault(fault, "workflow:spawn_child_continue:after_commit")
        result = self.read_react_checkpoint(run_id)
        assert result is not None
        return result

    def commit_pending_spawn_child_completion_and_react_ready(
        self,
        *,
        run_id: str,
        expected_checkpoint_version: int,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowCheckpoint:
        """Append the public child completion once the Provider Tool batch is closed."""

        # 按需导入：循环依赖（uow ↔ context）
        from simple_harness.runtime.context import _append_context_in_transaction

        run_id = _required(run_id, "run_id")
        now = _time(now)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            latest = connection.execute(
                "SELECT * FROM workflow_checkpoints WHERE run_id=? "
                "AND namespace='react.termination.v1' "
                "ORDER BY version DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if latest is None:
                raise UnitOfWorkConflict("workflow spawn ReAct checkpoint is missing")
            checkpoint_payload = json.loads(str(latest["checkpoint_json"]))
            if not isinstance(checkpoint_payload, dict):
                raise UnitOfWorkConflict("workflow spawn ReAct checkpoint is malformed")
            wait_receipt_id = checkpoint_payload.get("workflow_spawn_wait_receipt_id")
            completed_wait_receipt_id = checkpoint_payload.get(
                "last_workflow_spawn_wait_receipt_id"
            )
            wait = connection.execute(
                "SELECT * FROM workflow_spawn_child_wait_receipts WHERE parent_wait_receipt_id=?",
                (wait_receipt_id if wait_receipt_id is not None else completed_wait_receipt_id,),
            ).fetchone()
            if (
                wait is not None
                and str(wait["state"]) == "acked"
                and checkpoint_payload.get("phase") == "ready"
                and int(latest["version"]) == expected_checkpoint_version + 1
            ):
                return _workflow_checkpoint(latest)
            if (
                int(latest["version"]) != expected_checkpoint_version
                or checkpoint_payload.get("phase") != "tool_batch_reserved"
                or wait is None
                or str(wait["state"]) != "acked_completion_pending"
                or wait["pending_child_completion_json"] is None
                or wait["pending_child_completion_hash"] is None
                or wait["child_completion_append_id"] is None
            ):
                raise UnitOfWorkConflict("pending workflow child completion authority differs")
            response_payload = checkpoint_payload.get("provider_response_snapshot")
            if not isinstance(response_payload, dict):
                raise UnitOfWorkConflict("workflow spawn Provider response is missing")
            tool_calls = response_payload.get("tool_calls")
            if not isinstance(tool_calls, list) or checkpoint_payload.get(
                "tool_result_progress"
            ) != len(tool_calls):
                raise UnitOfWorkConflict("workflow spawn Tool batch is not fully appended")
            pending_json = str(wait["pending_child_completion_json"])
            if hashlib.sha256(pending_json.encode()).hexdigest() != str(
                wait["pending_child_completion_hash"]
            ) or checkpoint_payload.get("pending_child_completion_hash") != str(
                wait["pending_child_completion_hash"]
            ):
                raise UnitOfWorkConflict("pending workflow child completion payload differs")
            context_revision = checkpoint_payload.get("context_revision")
            if isinstance(context_revision, bool) or not isinstance(context_revision, int):
                raise UnitOfWorkConflict("workflow spawn Context revision is missing")
            completion = json.loads(pending_json)
            context = _append_context_in_transaction(
                connection,
                RunId(run_id),
                execution_lease,
                context_revision,
                str(wait["child_completion_append_id"]),
                (
                    Message(
                        MessageRole.USER,
                        canonical_json(cast(JsonValue, completion)),
                        name="workflow_child_completion",
                    ),
                ),
                now=now,
            )
            _fault(fault, "workflow:spawn_child_complete:after_context_write")
            next_checkpoint = dict(checkpoint_payload)
            next_checkpoint.update(
                {
                    "phase": "ready",
                    "provider_request_id": None,
                    "tool_batch_id": None,
                    "context_revision": None,
                    "provider_request_snapshot": None,
                    "provider_request_fingerprint": None,
                    "provider_response_snapshot": None,
                    "provider_response_digest": None,
                    "tool_result_progress": 0,
                    "workflow_spawn_wait_receipt_id": None,
                    "pending_child_completion": None,
                    "pending_child_completion_hash": None,
                    "pending_child_completion_append_id": None,
                    "last_workflow_spawn_wait_receipt_id": str(wait["parent_wait_receipt_id"]),
                    "last_observed_at": now,
                }
            )
            next_json = canonical_json(cast(JsonValue, next_checkpoint))
            next_hash = hashlib.sha256(next_json.encode()).hexdigest()
            next_version = int(latest["version"]) + 1
            _fault(fault, "workflow:spawn_child_complete:before_checkpoint_write")
            connection.execute(
                "INSERT INTO workflow_checkpoints("
                "checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,"
                "lease_epoch,version,created_at) VALUES(?,?,"
                "'react.termination.v1',?,?,?,?,?)",
                (
                    f"{run_id}:react.termination.v1:{next_version}",
                    run_id,
                    next_json,
                    next_hash,
                    execution_lease.epoch,
                    next_version,
                    now,
                ),
            )
            _fault(fault, "workflow:spawn_child_complete:after_checkpoint_write")
            wait_version = int(wait["version"]) + 1
            lifecycle_hash = hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(wait["identity_hash"]),
                        "state": "acked",
                        "version": wait_version,
                        "progress_receipt_id": wait["progress_receipt_id"],
                        "child_completion_append_id": wait["child_completion_append_id"],
                        "child_completion_context_revision": context.revision,
                    }
                ).encode()
            ).hexdigest()
            _fault(fault, "workflow:spawn_child_complete:before_wait_write")
            changed = connection.execute(
                "UPDATE workflow_spawn_child_wait_receipts SET state='acked',"
                "child_completion_append_receipt_id=?,"
                "child_completion_context_revision=?,version=?,lifecycle_hash=? "
                "WHERE parent_wait_receipt_id=? "
                "AND state='acked_completion_pending' AND version=?",
                (
                    str(wait["child_completion_append_id"]),
                    context.revision,
                    wait_version,
                    lifecycle_hash,
                    str(wait["parent_wait_receipt_id"]),
                    int(wait["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow spawn child completion ACK CAS failed")
            _fault(fault, "workflow:spawn_child_complete:after_wait_write")
        _fault(fault, "workflow:spawn_child_complete:after_commit")
        result = self.read_react_checkpoint(run_id)
        assert result is not None
        return result

    def claim_next_child_signal(
        self,
        *,
        parent_run_id: str,
        owner_id: str,
        now: float,
        lease_seconds: float,
        fault: FaultHook | None = None,
    ) -> ChildSignalRecord | None:
        """Claim only the durable oldest non-acked signal for one parent."""

        parent_run_id = _required(parent_run_id, "parent_run_id")
        owner_id = _required(owner_id, "owner_id")
        now = _time(now)
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or not math.isfinite(float(lease_seconds))
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be finite and positive")
        expires_at = now + float(lease_seconds)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM child_signals
                WHERE parent_run_id = ? AND state <> 'acked'
                ORDER BY created_at, signal_id
                LIMIT 1
                """,
                (parent_run_id,),
            ).fetchone()
            if row is None:
                return None
            parent = connection.execute(
                "SELECT state FROM runs WHERE run_id=?",
                (parent_run_id,),
            ).fetchone()
            spawn_wait = connection.execute(
                "SELECT * FROM workflow_spawn_child_wait_receipts "
                "WHERE parent_run_id=? AND child_run_id=? "
                "AND state='acked_parent_terminal' "
                "AND parent_terminal_phase_kind='child_active'",
                (parent_run_id, str(row["child_run_id"])),
            ).fetchone()
            if parent is not None and str(parent["state"]) in {
                RunState.COMPLETED.value,
                RunState.FAILED.value,
                RunState.CANCELLED.value,
            }:
                self._quarantine_late_spawn_child_signal(
                    connection,
                    signal=row,
                    spawn_wait=spawn_wait,
                    owner_id=owner_id,
                    now=now,
                    expires_at=expires_at,
                    fault=fault,
                )
                return None
            state = ChildSignalState(str(row["state"]))
            if state is ChildSignalState.CLAIMED:
                claim_expires_at = float(row["claim_expires_at"])
                if claim_expires_at > now:
                    return None
            version = int(row["version"])
            claim_epoch = int(row["claim_epoch"])
            _fault(fault, "child_signal_claim.signal.before_write")
            changed = connection.execute(
                """
                UPDATE child_signals
                SET state = 'claimed', version = version + 1,
                    claimed_by = ?, claimed_at = ?, claim_expires_at = ?,
                    claim_epoch = claim_epoch + 1, updated_at = ?
                WHERE signal_id = ? AND version = ?
                  AND (
                    state = 'pending'
                    OR (state = 'claimed' AND claim_expires_at <= ?)
                  )
                """,
                (
                    owner_id,
                    now,
                    expires_at,
                    now,
                    str(row["signal_id"]),
                    version,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("child signal claim CAS failed")

            record_claim_witness(connection, "child_signals", str(row["signal_id"]), now=now)
            _fault(fault, "child_signal_claim.signal.after_write")
            expected_epoch = claim_epoch + 1
        _fault(fault, "child_signal_claim.after_commit")
        result = self.read_child_signal(str(row["signal_id"]))
        assert result is not None and result.claim_epoch == expected_epoch
        return result

    def _quarantine_late_spawn_child_signal(
        self,
        connection: sqlite3.Connection,
        *,
        signal: sqlite3.Row,
        spawn_wait: sqlite3.Row | None,
        owner_id: str,
        now: float,
        expires_at: float,
        fault: FaultHook | None,
    ) -> None:
        signal_id = str(signal["signal_id"])
        parent_run_id = str(signal["parent_run_id"])
        continuation_id = f"child-signal:{signal_id}:terminal-quarantine"
        receipt_id = f"child-signal:{signal_id}:terminal-quarantine:receipt"
        event_id = f"child-signal:{signal_id}:terminal-quarantined"
        continuation_payload: dict[str, JsonValue] = {
            "kind": "child_terminal_quarantined",
            "signal_id": signal_id,
            "child_run_id": str(signal["child_run_id"]),
        }
        event_payload: dict[str, JsonValue] = {
            "signal_id": signal_id,
            "continuation_id": continuation_id,
            "receipt_id": receipt_id,
            "reason": "parent_terminal",
        }
        continuation_json = canonical_json(continuation_payload)
        event_json = canonical_json(event_payload)
        fifo_seq = int(
            connection.execute(
                "SELECT COALESCE(MAX(fifo_seq),0)+1 FROM continuations WHERE run_id=?",
                (parent_run_id,),
            ).fetchone()[0]
        )
        _fault(fault, "child_signal_quarantine.continuation.before_write")
        connection.execute(
            "INSERT INTO continuations(continuation_id,run_id,fifo_seq,"
            "payload_json,state,version,claimed_by,runtime_lease_epoch,"
            "claim_epoch,ack_receipt_id,created_at,claimed_at,acked_at) "
            "VALUES(?,?,?,?,'quarantined',0,NULL,NULL,0,NULL,?,NULL,?)",
            (
                continuation_id,
                parent_run_id,
                fifo_seq,
                continuation_json,
                now,
                now,
            ),
        )
        _fault(fault, "child_signal_quarantine.continuation.after_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=parent_run_id,
            kind="child.signal_quarantined",
            payload=event_payload,
            now=now,
        )
        signal_state = ChildSignalState(str(signal["state"]))
        if signal_state is ChildSignalState.PENDING:
            ack_owner = owner_id
            ack_epoch = 1
            claimed_at = now
            claim_expires_at = expires_at
        else:
            ack_owner = str(signal["claimed_by"])
            ack_epoch = int(signal["claim_epoch"])
            claimed_at = float(signal["claimed_at"])
            claim_expires_at = float(signal["claim_expires_at"])
        _fault(fault, "child_signal_quarantine.receipt.before_write")
        connection.execute(
            "INSERT INTO child_signal_ack_receipts("
            "receipt_id,signal_id,parent_run_id,owner_id,claim_epoch,"
            "continuation_id,event_id,continuation_payload_hash,"
            "event_payload_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                signal_id,
                parent_run_id,
                ack_owner,
                ack_epoch,
                continuation_id,
                event_id,
                hashlib.sha256(continuation_json.encode()).hexdigest(),
                hashlib.sha256(event_json.encode()).hexdigest(),
                now,
            ),
        )
        _fault(fault, "child_signal_quarantine.receipt.after_write")
        _fault(fault, "child_signal_quarantine.signal.before_write")
        changed = connection.execute(
            "UPDATE child_signals SET state='acked',version=version+1,"
            "claimed_by=?,claimed_at=?,claim_expires_at=?,claim_epoch=?,"
            "acked_at=?,ack_receipt_id=?,updated_at=? "
            "WHERE signal_id=? AND version=? AND state IN ('pending','claimed')",
            (
                ack_owner,
                claimed_at,
                claim_expires_at,
                ack_epoch,
                now,
                receipt_id,
                now,
                signal_id,
                int(signal["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("late child signal quarantine CAS failed")
        _fault(fault, "child_signal_quarantine.signal.after_write")
        if spawn_wait is None:
            return
        next_version = int(spawn_wait["version"]) + 1
        lifecycle_hash = hashlib.sha256(
            canonical_json(
                {
                    "identity_hash": str(spawn_wait["identity_hash"]),
                    "state": "acked_parent_terminal",
                    "version": next_version,
                    "phase_kind": "child_active",
                    "terminal_receipt_id": spawn_wait["pending_completion_terminal_receipt_id"],
                    "terminal_state": spawn_wait["pending_completion_terminal_state"],
                    "terminal_hash": spawn_wait["pending_completion_terminal_hash"],
                    "late_signal_quarantine_receipt_id": receipt_id,
                }
            ).encode()
        ).hexdigest()
        _fault(fault, "child_signal_quarantine.spawn_wait.before_write")
        changed = connection.execute(
            "UPDATE workflow_spawn_child_wait_receipts SET "
            "late_signal_quarantine_receipt_id=?,child_signal_id=?,"
            "continuation_id=?,version=?,lifecycle_hash=? "
            "WHERE parent_wait_receipt_id=? AND state='acked_parent_terminal' "
            "AND parent_terminal_phase_kind='child_active' AND version=? "
            "AND late_signal_quarantine_receipt_id IS NULL",
            (
                receipt_id,
                signal_id,
                continuation_id,
                next_version,
                lifecycle_hash,
                str(spawn_wait["parent_wait_receipt_id"]),
                int(spawn_wait["version"]),
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("late child signal wait closure CAS failed")
        _fault(fault, "child_signal_quarantine.spawn_wait.after_write")

    def ack_child_signal_and_commit_parent_progress(
        self,
        *,
        signal_id: str,
        owner_id: str,
        claim_epoch: int,
        receipt_id: str,
        continuation_id: str,
        continuation_payload: Mapping[str, JsonValue],
        event_id: str,
        event_payload: Mapping[str, JsonValue],
        now: float,
        fault: FaultHook | None = None,
    ) -> ChildSignalAckResult:
        signal_id = _required(signal_id, "signal_id")
        owner_id = _required(owner_id, "owner_id")
        receipt_id = _required(receipt_id, "receipt_id")
        continuation_id = _required(continuation_id, "continuation_id")
        event_id = _required(event_id, "event_id")
        if isinstance(claim_epoch, bool) or claim_epoch < 1:
            raise ValueError("claim_epoch must be positive")
        continuation_json = _object_json(continuation_payload, "continuation_payload")
        event_json = _object_json(event_payload, "event_payload")
        continuation_hash = hashlib.sha256(continuation_json.encode("utf-8")).hexdigest()
        event_hash = hashlib.sha256(event_json.encode("utf-8")).hexdigest()
        now = _time(now)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM child_signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            if row is None:
                raise UnitOfWorkNotFound(signal_id)
            signal = _child_signal_record(row)
            if signal.state is ChildSignalState.ACKED:
                receipt_row = connection.execute(
                    "SELECT * FROM child_signal_ack_receipts WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
                if receipt_row is None:
                    raise UnitOfWorkConflict("child signal was already acknowledged differently")
                receipt = _child_signal_ack_receipt(receipt_row)
                if (
                    signal.ack_receipt_id == receipt_id
                    and receipt.signal_id == signal_id
                    and receipt.parent_run_id == signal.parent_run_id
                    and receipt.owner_id == owner_id
                    and receipt.claim_epoch == claim_epoch
                    and receipt.continuation_id == continuation_id
                    and receipt.event_id == event_id
                    and receipt.continuation_payload_hash == continuation_hash
                    and receipt.event_payload_hash == event_hash
                ):
                    return ChildSignalAckResult(signal, receipt)
                raise UnitOfWorkConflict("child signal was already acknowledged differently")
            if (
                signal.state is not ChildSignalState.CLAIMED
                or signal.claimed_by != owner_id
                or signal.claim_epoch != claim_epoch
                or signal.claim_expires_at is None
                or signal.claim_expires_at <= now
            ):
                raise UnitOfWorkConflict("child signal ack claim is stale or expired")
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(fifo_seq), 0) + 1 FROM continuations WHERE run_id = ?",
                    (signal.parent_run_id,),
                ).fetchone()[0]
            )
            _fault(fault, "child_signal_ack.continuation.before_write")
            connection.execute(
                """
                INSERT INTO continuations(
                    continuation_id, run_id, fifo_seq, payload_json, state,
                    version, claimed_by, created_at, claimed_at, acked_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, NULL, ?, NULL, NULL)
                """,
                (
                    continuation_id,
                    signal.parent_run_id,
                    sequence,
                    continuation_json,
                    now,
                ),
            )
            _fault(fault, "child_signal_ack.continuation.after_write")
            _fault(fault, "child_signal_ack.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=signal.parent_run_id,
                kind="child.signal_acked",
                payload=json.loads(event_json),
                now=now,
            )
            _fault(fault, "child_signal_ack.event.after_write")
            _fault(fault, "child_signal_ack.receipt.before_write")
            connection.execute(
                """
                INSERT INTO child_signal_ack_receipts(
                    receipt_id, signal_id, parent_run_id, owner_id, claim_epoch,
                    continuation_id, event_id, continuation_payload_hash,
                    event_payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    signal_id,
                    signal.parent_run_id,
                    owner_id,
                    claim_epoch,
                    continuation_id,
                    event_id,
                    continuation_hash,
                    event_hash,
                    now,
                ),
            )
            _fault(fault, "child_signal_ack.receipt.after_write")
            _fault(fault, "child_signal_ack.signal.before_write")
            changed = connection.execute(
                """
                UPDATE child_signals
                SET state = 'acked', version = version + 1,
                    acked_at = ?, ack_receipt_id = ?, updated_at = ?
                WHERE signal_id = ? AND state = 'claimed'
                  AND claimed_by = ? AND claim_epoch = ?
                  AND claim_expires_at > ?
                """,
                (
                    now,
                    receipt_id,
                    now,
                    signal_id,
                    owner_id,
                    claim_epoch,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("child signal ack CAS failed")
            _fault(fault, "child_signal_ack.signal.after_write")
            spawn_wait = connection.execute(
                """
                SELECT * FROM workflow_spawn_child_wait_receipts
                WHERE parent_run_id=? AND child_run_id=? AND state='unconsumed'
                """,
                (signal.parent_run_id, signal.child_run_id),
            ).fetchone()
            if spawn_wait is not None:
                next_wait_version = int(spawn_wait["version"]) + 1
                lifecycle_hash = hashlib.sha256(
                    canonical_json(
                        {
                            "identity_hash": str(spawn_wait["identity_hash"]),
                            "state": "woken",
                            "version": next_wait_version,
                            "child_signal_id": signal_id,
                            "continuation_id": continuation_id,
                        }
                    ).encode()
                ).hexdigest()
                _fault(fault, "child_signal_ack.spawn_wait.before_write")
                changed = connection.execute(
                    """
                    UPDATE workflow_spawn_child_wait_receipts
                    SET state='woken',child_signal_id=?,continuation_id=?,
                        version=?,lifecycle_hash=?
                    WHERE parent_wait_receipt_id=? AND state='unconsumed'
                      AND version=?
                    """,
                    (
                        signal_id,
                        continuation_id,
                        next_wait_version,
                        lifecycle_hash,
                        str(spawn_wait["parent_wait_receipt_id"]),
                        int(spawn_wait["version"]),
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("workflow spawn child-wait wake CAS failed")
                _fault(fault, "child_signal_ack.spawn_wait.after_write")
            link = connection.execute(
                "SELECT attachment_policy FROM run_links WHERE parent_run_id = ? AND"
                " child_run_id = ?",
                (signal.parent_run_id, signal.child_run_id),
            ).fetchone()
            if link is None:
                raise UnitOfWorkConflict("child signal has no durable run link")
            if str(link[0]) != AttachmentPolicy.DETACHED.value:
                _fault(fault, "child_signal_ack.parent.before_write")
                changed = connection.execute(
                    """
                    UPDATE runs SET state = 'queued', version = version + 1, updated_at = ?
                    WHERE run_id = ? AND state = 'waiting'
                    """,
                    (now, signal.parent_run_id),
                ).rowcount
                if changed != 1:
                    parent = connection.execute(
                        "SELECT state FROM runs WHERE run_id = ?",
                        (signal.parent_run_id,),
                    ).fetchone()
                    if parent is None or str(parent["state"]) not in {
                        RunState.QUEUED.value,
                        RunState.RUNNING.value,
                    }:
                        raise UnitOfWorkConflict("attached parent wake CAS failed")
                _fault(fault, "child_signal_ack.parent.after_write")
        _fault(fault, "child_signal_ack.after_commit")
        result_signal = self.read_child_signal(signal_id)
        result_receipt = self.read_child_signal_ack_receipt(receipt_id)
        assert result_signal is not None and result_receipt is not None
        return ChildSignalAckResult(result_signal, result_receipt)
