"""Checkpoint, head and execution reads."""

from __future__ import annotations

import copy
import json
from typing import Any

from .core import NativeCheckpointCore
from .support import NATIVE_ENGINE_KIND, NativeCheckpointError


class CheckpointReadMixin(NativeCheckpointCore):
    """Checkpoint, head and execution reads."""

    __slots__ = ()

    async def get_checkpoint(
        self,
        run_id: str,
        checkpoint_id: str,
        *,
        checkpoint_ns: str | None = None,
    ) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            params: list[Any] = [run_id, checkpoint_id, NATIVE_ENGINE_KIND]
            clause = ""
            if checkpoint_ns is not None:
                clause = " AND o.checkpoint_ns=?"
                params.append(checkpoint_ns)
            row = await (
                await db.execute(
                    """SELECT c.* FROM workflow_checkpoint_owners o
                    JOIN workflow_checkpoints c USING(thread_id,checkpoint_ns,checkpoint_id)
                    WHERE o.run_id=? AND o.checkpoint_id=? AND c.engine_kind=?"""
                    + clause,
                    params,
                )
            ).fetchone()
            if row is None:
                return None
            return json.loads(bytes(row["checkpoint_blob"]).decode("utf-8"))
        finally:
            await db.close()

    async def load_head(self, run_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            run = await self._run_row(db, run_id)
            checkpoint_id = run["head_checkpoint_id"]
            if checkpoint_id is None:
                return None
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=?
                    AND checkpoint_id=?""",
                    (run["thread_id"], run["head_checkpoint_ns"], checkpoint_id),
                )
            ).fetchone()
            if row is None:
                raise NativeCheckpointError("checkpoint_missing", "run head checkpoint is missing")
            if str(row["engine_kind"]) != NATIVE_ENGINE_KIND:
                raise NativeCheckpointError(
                    "legacy_checkpoint_read_only",
                    "legacy checkpoints cannot be resumed by the native engine",
                )
            return json.loads(bytes(row["checkpoint_blob"]).decode("utf-8"))
        finally:
            await db.close()

    async def list(self, run_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("checkpoint limit cannot be negative")
        db = await self._connect()
        try:
            query = """SELECT c.checkpoint_blob FROM workflow_checkpoint_owners o
                JOIN workflow_checkpoints c USING(thread_id,checkpoint_ns,checkpoint_id)
                WHERE o.run_id=? AND c.engine_kind=? ORDER BY o.created_at DESC,o.checkpoint_id DESC"""
            params: list[Any] = [run_id, NATIVE_ENGINE_KIND]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = await (await db.execute(query, params)).fetchall()
            return [json.loads(bytes(row["checkpoint_blob"]).decode("utf-8")) for row in rows]
        finally:
            await db.close()

    async def load_execution(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
        checkpoint_ns: str | None = None,
    ) -> dict[str, Any] | object:
        protocol_call = thread_id is not None or checkpoint_ns is not None
        snapshot = await self.load_head(run_id)
        if snapshot is None:
            raise NativeCheckpointError("checkpoint_missing", "workflow run has no native head")
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT task_id,write_index,write_kind,payload_json,node_execution_id
                    FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=?
                    AND base_checkpoint_id=? AND write_kind IS NOT NULL
                    ORDER BY task_id,write_index""",
                    (snapshot["thread_id"], snapshot["checkpoint_ns"], snapshot["checkpoint_id"]),
                )
            ).fetchall()
        finally:
            await db.close()
        pending = [
            {
                "task_id": str(row["task_id"]),
                "write_index": int(row["write_index"]),
                "write_kind": str(row["write_kind"]),
                "payload": json.loads(str(row["payload_json"])),
                "node_execution_id": row["node_execution_id"],
            }
            for row in rows
        ]
        frontier_ids = {str(item.get("task_id")) for item in snapshot["frontier"]}
        for item in pending:
            if item["write_kind"] == "state_patch" and item["task_id"] not in frontier_ids:
                raise NativeCheckpointError(
                    "pending_write_frontier_mismatch",
                    "pending task result does not belong to the current frontier",
                )
        if not protocol_call:
            return {"snapshot": snapshot, "pending_writes": pending}
        if thread_id is not None and str(snapshot["thread_id"]) != thread_id:
            raise NativeCheckpointError("run_identity_mismatch", "native thread id changed")
        if checkpoint_ns is not None and str(snapshot["checkpoint_ns"]) != checkpoint_ns:
            raise NativeCheckpointError(
                "run_identity_mismatch", "native checkpoint namespace changed"
            )
        # 按需导入：测试替身（deskpet.workflows.contracts）
        from ...contracts import StatePatch

        # 按需导入：循环依赖（checkpointer ↔ native）
        from ...native import NativeExecution

        pending_results = {
            str(item["task_id"]): StatePatch(dict(item["payload"]["patch"]))
            for item in pending
            if item["write_kind"] == "state_patch"
        }
        pending_consumed_interrupt_ids = tuple(
            sorted(
                {
                    str(interrupt_id)
                    for item in pending
                    if item["write_kind"] == "state_patch"
                    for interrupt_id in item["payload"].get("consumed_interrupt_ids", ())
                }
            )
        )
        route_selections = {
            str(item["task_id"]): copy.deepcopy(dict(item["payload"]))
            for item in pending
            if item["write_kind"] == "route_selection"
        }
        first_attempt_times: dict[str, float] = {}
        projected_snapshot = copy.deepcopy(snapshot)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT node.task_id,MIN(attempt.started_at) AS first_started
                    FROM workflow_nodes node JOIN workflow_node_attempts attempt
                      ON attempt.node_execution_id=node.node_execution_id
                    WHERE node.run_id=? AND node.base_checkpoint_id=? GROUP BY node.task_id""",
                    (run_id, snapshot["checkpoint_id"]),
                )
            ).fetchall()
            first_attempt_times = {
                str(row["task_id"]): float(row["first_started"])
                for row in rows
                if row["task_id"] is not None and row["first_started"] is not None
            }
            retry_rows = await (
                await db.execute(
                    """SELECT node.task_id,node.latest_attempt,attempt.next_attempt_at
                    FROM workflow_nodes node JOIN workflow_node_attempts attempt
                      ON attempt.node_execution_id=node.node_execution_id
                     AND attempt.retry_attempt=node.latest_attempt
                    WHERE node.run_id=? AND node.base_checkpoint_id=?
                      AND node.latest_status='retryable'""",
                    (run_id, snapshot["checkpoint_id"]),
                )
            ).fetchall()
            retries = {str(row["task_id"]): row for row in retry_rows}
            for task in projected_snapshot["frontier"]:
                retry = retries.get(str(task.get("task_id") or ""))
                if retry is not None:
                    task["retry_attempt"] = int(retry["latest_attempt"]) + 1
                    task["next_attempt_at"] = retry["next_attempt_at"]
        finally:
            await db.close()
        return NativeExecution(
            snapshot=self._snapshot_object(projected_snapshot),
            pending_results=pending_results,
            pending_consumed_interrupt_ids=pending_consumed_interrupt_ids,
            first_attempt_times=first_attempt_times,
            route_selections=route_selections,
        )
