"""Typed failure mapping and crash-recovery helpers for workflow runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import WorkflowRunStatus
from .errors import (
    ERROR_DISPOSITIONS,
    InvalidStatePatch,
    LeaseLostError,
    StateMergeConflict,
    WorkflowDependencyUnavailable,
    WorkflowErrorCode,
    WorkflowNodeError,
)
from .store import StaleRunFence, WorkflowRunStore


@dataclass(frozen=True, slots=True)
class FailureMapping:
    code: WorkflowErrorCode
    target_status: WorkflowRunStatus
    message_ref: str
    retryable: bool
    recovery_action: str
    reason: str

    def envelope(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "code": self.code.value,
            "message_ref": self.message_ref,
            "retryable": self.retryable,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    run_id: str
    previous_status: str
    status: str
    action: str
    reason: str


def map_workflow_failure(exc: BaseException) -> FailureMapping:
    if isinstance(exc, WorkflowNodeError):
        code = exc.code
        message_ref = exc.message_ref
        retryable = exc.retryable
    elif isinstance(exc, (LeaseLostError, StaleRunFence)):
        code = WorkflowErrorCode.LEASE_LOST
        message_ref = "workflow_runner:lease_lost"
        retryable = True
    elif isinstance(exc, asyncio.CancelledError):
        code = WorkflowErrorCode.CANCELLED
        message_ref = "workflow_runner:cancelled"
        retryable = False
    elif exc.__class__.__name__ == "GraphRecursionError":
        return FailureMapping(
            WorkflowErrorCode.INVALID_STATE,
            WorkflowRunStatus.BLOCKED,
            "workflow_runner:recursion_limit_before_budget_exit",
            False,
            "inspect_budget_or_fork",
            "recursion_limit_before_budget_exit",
        )
    elif isinstance(exc, (InvalidStatePatch, StateMergeConflict)):
        code = WorkflowErrorCode.INVALID_STATE
        message_ref = f"workflow_runner:{getattr(exc, 'code', 'invalid_state')}"
        retryable = False
    elif isinstance(exc, WorkflowDependencyUnavailable):
        return FailureMapping(
            WorkflowErrorCode.PERMANENT,
            WorkflowRunStatus.BLOCKED,
            "workflow_runner:graph_version_unavailable",
            False,
            "restore_graph_version_or_fork",
            "graph_version_unavailable",
        )
    elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) or (
        isinstance(exc, ValueError)
        and any(token in str(exc).lower() for token in ("checkpoint", "deserialize", "integrity", "msgpack"))
    ):
        code = WorkflowErrorCode.CHECKPOINT_CORRUPT
        message_ref = "workflow_runner:checkpoint_corrupt"
        retryable = False
    else:
        code = WorkflowErrorCode.PERMANENT
        message_ref = f"workflow_runner:{exc.__class__.__name__}"
        retryable = False

    disposition = ERROR_DISPOSITIONS[code]
    if code is WorkflowErrorCode.CANCELLED:
        target = WorkflowRunStatus.CANCELLED
    elif code in {WorkflowErrorCode.CHECKPOINT_CORRUPT, WorkflowErrorCode.EFFECT_UNCERTAIN}:
        target = WorkflowRunStatus.BLOCKED
    elif retryable:
        target = WorkflowRunStatus.RETRYABLE
    elif code is WorkflowErrorCode.PERMISSION_DENIED:
        target = WorkflowRunStatus.BLOCKED
    else:
        target = WorkflowRunStatus.FAILED
    return FailureMapping(
        code,
        target,
        message_ref,
        retryable,
        disposition.recovery_action,
        code.value,
    )


async def expire_stale_lease(
    store: WorkflowRunStore,
    row: dict[str, Any],
    *,
    clock=time.time,
) -> RecoveryRecord | None:
    """Fence an expired owner and preserve pending writes for graph recovery."""

    if row["status"] != WorkflowRunStatus.RUNNING.value:
        return None
    now = clock()
    expires_at = row.get("lease_expires_at")
    if expires_at is None or float(expires_at) > now:
        return None
    error = map_workflow_failure(LeaseLostError("expired workflow lease"))
    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        current = await (
            await db.execute(
                """UPDATE workflow_runs SET status='retryable',lease_owner=NULL,lease_expires_at=NULL,
                heartbeat_at=NULL,lease_epoch=lease_epoch+1,run_version=run_version+1,error_json=?,
                recovery_action=?,updated_at=? WHERE run_id=? AND status='running' AND run_version=?
                AND lease_expires_at<=? RETURNING *""",
                (
                    json.dumps(error.envelope(), sort_keys=True, separators=(",", ":")),
                    error.recovery_action,
                    now,
                    row["run_id"],
                    row["run_version"],
                    now,
                ),
            )
        ).fetchone()
        if current is None:
            await db.rollback()
            return None
        await db.execute(
            """UPDATE workflow_node_attempts SET status='abandoned',ended_at=?,error_ref=?
            WHERE status='running' AND node_execution_id IN
            (SELECT node_execution_id FROM workflow_nodes WHERE run_id=?)""",
            (now, error.message_ref, row["run_id"]),
        )
        await db.execute(
            """UPDATE workflow_nodes SET latest_status='retryable',updated_at=?
            WHERE run_id=? AND latest_status='running'""",
            (now, row["run_id"]),
        )
        await db.commit()
        return RecoveryRecord(
            str(row["run_id"]),
            WorkflowRunStatus.RUNNING.value,
            WorkflowRunStatus.RETRYABLE.value,
            "reclaim",
            "lease_expired",
        )
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise
    finally:
        await db.close()


async def repair_head_projection(
    store: WorkflowRunStore,
    row: dict[str, Any],
    *,
    clock=time.time,
) -> RecoveryRecord | None:
    """Rebuild a stale head cache only from checkpoints owned by this run."""

    if row["status"] == WorkflowRunStatus.RUNNING.value:
        return None
    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        current = None
        if row.get("head_checkpoint_id"):
            current = await (
                await db.execute(
                    """SELECT 1 FROM workflow_checkpoint_owners o JOIN workflow_checkpoints c
                    ON c.thread_id=o.thread_id AND c.checkpoint_ns=o.checkpoint_ns
                    AND c.checkpoint_id=o.checkpoint_id WHERE o.run_id=? AND o.checkpoint_ns=?
                    AND o.checkpoint_id=?""",
                    (row["run_id"], row["head_checkpoint_ns"], row["head_checkpoint_id"]),
                )
            ).fetchone()
        if current is not None:
            await db.commit()
            return None
        candidate = await (
            await db.execute(
                """SELECT o.checkpoint_ns,o.checkpoint_id FROM workflow_checkpoint_owners o
                JOIN workflow_checkpoints c ON c.thread_id=o.thread_id AND c.checkpoint_ns=o.checkpoint_ns
                AND c.checkpoint_id=o.checkpoint_id WHERE o.run_id=?
                ORDER BY o.created_at DESC,o.checkpoint_id DESC LIMIT 1""",
                (row["run_id"],),
            )
        ).fetchone()
        if candidate is None:
            await db.commit()
            return None
        now = clock()
        updated = await db.execute(
            """UPDATE workflow_runs SET head_checkpoint_ns=?,head_checkpoint_id=?,
            run_version=run_version+1,recovery_action='resume',updated_at=?
            WHERE run_id=? AND run_version=? AND status=?""",
            (
                candidate["checkpoint_ns"],
                candidate["checkpoint_id"],
                now,
                row["run_id"],
                row["run_version"],
                row["status"],
            ),
        )
        if updated.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()
        return RecoveryRecord(
            str(row["run_id"]),
            str(row["status"]),
            str(row["status"]),
            "rebuild_head_projection",
            "head_projection_stale",
        )
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise
    finally:
        await db.close()


def quarantine_checkpoint(
    db_path: str | Path,
    *,
    run_id: str,
    checkpoint_id: str,
    checkpoint_blob: bytes,
    metadata_blob: bytes,
) -> Path:
    """Copy corrupt bytes aside without changing the authoritative lineage."""

    digest = hashlib.sha256(checkpoint_blob).hexdigest()[:16]
    run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    checkpoint_key = hashlib.sha256(checkpoint_id.encode("utf-8")).hexdigest()[:12]
    directory = Path(db_path).with_suffix(".wfq") / run_key
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{checkpoint_key}.{digest}.chk"
    if not target.exists():
        target.write_bytes(checkpoint_blob)
        target.with_suffix(".meta").write_bytes(metadata_blob)
    return target


__all__ = [
    "FailureMapping",
    "RecoveryRecord",
    "expire_stale_lease",
    "map_workflow_failure",
    "quarantine_checkpoint",
    "repair_head_projection",
]
