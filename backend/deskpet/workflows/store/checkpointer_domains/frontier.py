"""Frontier commits and retries."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts import JsonValue, canonical_json
from ..run_store import RunFence, StaleRunFence
from .core import NativeCheckpointCore
from .support import NATIVE_ENGINE_KIND, NativeCheckpointError, _native_mapping


class FrontierRetryMixin(NativeCheckpointCore):
    """Frontier commits and retries."""

    __slots__ = ()

    async def commit_frontier(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        *,
        state: Mapping[str, JsonValue],
        frontier: Sequence[Mapping[str, JsonValue] | object],
        step: int | None = None,
        completed_activations: Sequence[str] | Mapping[str, Sequence[str]] = (),
        join_firings: Sequence[str] | Mapping[str, JsonValue] = (),
        operation_id: str | None = None,
        decision_ids: Sequence[str | Mapping[str, Any]] = (),
        consumed_interrupt_ids: Sequence[str] = (),
        intents: Sequence[Mapping[str, Any]] = (),
        effect_links: Sequence[Mapping[str, Any]] = (),
        blob_refs: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
        terminal_status: str | None = None,
        terminal_error: Mapping[str, JsonValue] | None = None,
        recovery_action: str | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | object:
        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
        if fence is None or expected_head is None:
            raise TypeError("commit_frontier requires a fence and expected head")
        frontier_data = [_native_mapping(item, name="frontier task") for item in frontier]
        if step is None:
            current = await self.load_head(fence.run_id)
            if current is None or str(current["checkpoint_id"]) != expected_head:
                raise NativeCheckpointError(
                    "stale_checkpoint_head", "workflow checkpoint head changed"
                )
            step = int(current["step"]) + 1
        resolved_decisions: Sequence[str | Mapping[str, Any]] = tuple(decision_ids) + tuple(
            consumed_interrupt_ids
        )
        op_id = operation_id or self._stable_id(
            fence.run_id,
            expected_head,
            "frontier",
            step,
            hashlib.sha256(canonical_json(dict(state)).encode("utf-8")).hexdigest(),
        )
        checkpoint_id = self._stable_id(fence.run_id, expected_head, "frontier", op_id)
        request_hash = self._request_hash(
            {
                "state": dict(state),
                "frontier": frontier_data,
                "step": step,
                "completed_activations": (
                    {key: list(value) for key, value in completed_activations.items()}
                    if isinstance(completed_activations, Mapping)
                    else list(completed_activations)
                ),
                "join_firings": (
                    dict(join_firings) if isinstance(join_firings, Mapping) else list(join_firings)
                ),
                "decision_ids": [
                    item if isinstance(item, str) else dict(item) for item in resolved_decisions
                ],
                "intents": [dict(item) for item in intents],
                "effect_links": [dict(item) for item in effect_links],
                "blob_refs": list(blob_refs),
                "metadata": dict(metadata or {}),
                "terminal_status": terminal_status,
                "terminal_error": dict(terminal_error) if terminal_error is not None else None,
                "recovery_action": recovery_action,
            }
        )
        if terminal_status is not None and (
            frontier_data or terminal_status not in {"completed", "failed", "cancelled"}
        ):
            raise NativeCheckpointError(
                "invalid_terminal_frontier",
                "terminal status requires an empty frontier and an allowed status",
            )
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="frontier",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                if protocol_call:
                    # 按需导入：循环依赖（checkpointer ↔ native）
                    from ...native import NativeCommitResult

                    return NativeCommitResult(
                        self._snapshot_object(existing["snapshot"]),
                        tuple(existing.get("event_ids") or ()),
                    )
                return existing
            run = await self._run_row(db, fence.run_id)
            execution_adapter = await self._execution_adapter_for(db, fence.run_id)
            self._assert_fence_row(run, fence)
            self._assert_head(run, expected_head)
            pending_rows = await (
                await db.execute(
                    """SELECT task_id,write_index,write_kind,payload_json,node_execution_id
                    FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=?
                    AND base_checkpoint_id=? AND write_kind IS NOT NULL
                    ORDER BY task_id,write_index""",
                    (run["thread_id"], run["checkpoint_ns"], expected_head),
                )
            ).fetchall()
            head_row = await (
                await db.execute(
                    """SELECT checkpoint_blob,engine_kind FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                    (run["thread_id"], run["checkpoint_ns"], expected_head),
                )
            ).fetchone()
            if head_row is None or str(head_row["engine_kind"]) != NATIVE_ENGINE_KIND:
                raise NativeCheckpointError(
                    "legacy_checkpoint_read_only", "native frontier requires a native JSON head"
                )
            head_snapshot = json.loads(bytes(head_row["checkpoint_blob"]).decode("utf-8"))
            expected_task_ids = {
                str(item.get("task_id") or "") for item in list(head_snapshot.get("frontier") or [])
            }
            expected_task_ids.discard("")
            completed_task_ids = {
                str(row["task_id"])
                for row in pending_rows
                if str(row["write_kind"]) == "state_patch"
            }
            if completed_task_ids != expected_task_ids:
                raise NativeCheckpointError(
                    "frontier_results_incomplete",
                    "frontier checkpoint requires exactly one result for every task",
                    details={
                        "expected_task_ids": sorted(expected_task_ids),
                        "completed_task_ids": sorted(completed_task_ids),
                    },
                )
            pending_write_items: list[dict[str, JsonValue]] = [
                {
                    "task_id": str(row["task_id"]),
                    "write_index": int(row["write_index"]),
                    "write_kind": str(row["write_kind"]),
                    "payload": json.loads(str(row["payload_json"])),
                    "node_execution_id": row["node_execution_id"],
                }
                for row in pending_rows
            ]
            node_writes: Any = pending_write_items
            if protocol_call:
                node_writes = {
                    str(item["task_id"]): copy.deepcopy(dict(item["payload"]))
                    for item in pending_write_items
                    if item["write_kind"] == "state_patch"
                }
            snapshot = self._snapshot_payload(
                run=run,
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=expected_head,
                step=step,
                state=state,
                frontier=frontier_data,
                completed_activations=completed_activations,
                join_firings=join_firings,
                node_writes=node_writes,
                metadata={
                    **dict(metadata or {}),
                    "operation_id": op_id,
                    "operation_kind": "frontier",
                },
            )
            await self._insert_checkpoint(db, run=run, snapshot=snapshot, now=now)
            consumed = await (
                execution_adapter.consume_decisions(
                    db,
                    run_id=fence.run_id,
                    decisions=resolved_decisions,
                    checkpoint_id=checkpoint_id,
                    now=now,
                )
                if execution_adapter is not None
                else self._consume_decisions(
                    db,
                    run_id=fence.run_id,
                    decisions=resolved_decisions,
                    checkpoint_id=checkpoint_id,
                    now=now,
                )
            )
            execution_ids = [
                str(row["node_execution_id"])
                for row in pending_rows
                if row["write_kind"] == "state_patch" and row["node_execution_id"] is not None
            ]
            for execution_id in execution_ids:
                await db.execute(
                    "UPDATE workflow_nodes SET latest_status='succeeded',updated_at=? "
                    "WHERE node_execution_id=? AND latest_status='succeeded_pending'",
                    (now, execution_id),
                )
                await db.execute(
                    "UPDATE workflow_node_attempts SET status='succeeded' "
                    "WHERE node_execution_id=? AND status='succeeded_pending'",
                    (execution_id,),
                )
                if execution_adapter is None:
                    await db.execute(
                        """INSERT OR IGNORE INTO workflow_checkpoint_effects(
                            thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
                        ) SELECT ?,?,?,effect_id,node_execution_id FROM workflow_node_effects
                        WHERE node_execution_id=?""",
                        (run["thread_id"], run["checkpoint_ns"], checkpoint_id, execution_id),
                    )
            if execution_adapter is not None:
                await execution_adapter.link_effects(
                    db,
                    run_id=fence.run_id,
                    checkpoint_ns=str(run["checkpoint_ns"]),
                    checkpoint_id=checkpoint_id,
                    links=effect_links,
                    now=now,
                )
            for sha256 in blob_refs:
                exists = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (str(sha256),))
                ).fetchone()
                if exists is None:
                    raise NativeCheckpointError(
                        "blob_not_found", f"unknown blob reference: {sha256}"
                    )
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                        sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'checkpoint',?,?)""",
                    (str(sha256), checkpoint_id, now),
                )
            pending_owner_ids = tuple(
                f"{fence.run_id}:{expected_head}:{task_id}" for task_id in sorted(expected_task_ids)
            )
            if pending_owner_ids:
                await db.execute(
                    f"""DELETE FROM workflow_blob_refs WHERE owner_kind='pending_task'
                    AND owner_id IN ({",".join("?" for _ in pending_owner_ids)})""",
                    pending_owner_ids,
                )
            event_ids = [
                await (
                    execution_adapter.materialize_intent(db, run=run, intent=intent, now=now)
                    if execution_adapter is not None
                    else self._materialize_intent(db, run=run, intent=intent, now=now)
                )
                for intent in intents
            ]
            if terminal_status is None:
                cursor = await db.execute(
                    """UPDATE workflow_runs SET head_checkpoint_ns=checkpoint_ns,
                    head_checkpoint_id=?,active_nodes_json=?,updated_at=?
                    WHERE run_id=? AND head_checkpoint_id=? AND status='running'
                    AND lease_owner=? AND lease_epoch=? AND run_version=?""",
                    (
                        checkpoint_id,
                        canonical_json([str(item.get("node_id", "")) for item in frontier_data]),
                        now,
                        fence.run_id,
                        expected_head,
                        fence.owner,
                        fence.lease_epoch,
                        fence.run_version,
                    ),
                )
            else:
                error_json = (
                    canonical_json(dict(terminal_error)) if terminal_error is not None else None
                )
                cursor = await db.execute(
                    """UPDATE workflow_runs SET head_checkpoint_ns=checkpoint_ns,
                    head_checkpoint_id=?,active_nodes_json='[]',status=?,error_json=?,
                    recovery_action=?,ended_at=COALESCE(ended_at,?),lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,updated_at=?
                    WHERE run_id=? AND head_checkpoint_id=? AND status='running'
                    AND lease_owner=? AND lease_epoch=? AND run_version=?""",
                    (
                        checkpoint_id,
                        terminal_status,
                        error_json,
                        recovery_action,
                        now,
                        now,
                        fence.run_id,
                        expected_head,
                        fence.owner,
                        fence.lease_epoch,
                        fence.run_version,
                    ),
                )
            if cursor.rowcount != 1:
                raise StaleRunFence(f"native frontier head changed: {fence.run_id}")
            if execution_adapter is not None and terminal_status is not None:
                terminal_event_id = await execution_adapter.finalize_run(
                    db,
                    run=run,
                    terminal_status=terminal_status,
                    terminal_error=terminal_error,
                    recovery_action=recovery_action,
                    event_ids=event_ids,
                    now=now,
                )
                if terminal_event_id not in event_ids:
                    event_ids.append(terminal_event_id)
            await db.execute(
                """DELETE FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=?
                AND base_checkpoint_id=? AND write_kind IS NOT NULL""",
                (run["thread_id"], run["checkpoint_ns"], expected_head),
            )
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": checkpoint_id,
                "operation_id": op_id,
                "consumed_decision_ids": consumed,
                "event_ids": event_ids,
                "snapshot": snapshot,
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="frontier",
                request_hash=request_hash,
                result=result,
                now=now,
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
        await self._fault("frontier.after_db_commit_before_return")
        if protocol_call:
            # 按需导入：循环依赖（checkpointer ↔ native）
            from ...native import NativeCommitResult

            return NativeCommitResult(
                self._snapshot_object(result["snapshot"]), tuple(result["event_ids"])
            )
        return result

    async def commit_retry(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        task: Mapping[str, Any] | object | None = None,
        attempt: int | None = None,
        *,
        next_attempt_at: float,
        error_ref: str | None = None,
        error: object | None = None,
        operation_id: str | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | None:
        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
        if task is None:
            raise TypeError("commit_retry requires task")
        task_data = _native_mapping(task, name="task")
        if attempt is None:
            attempt = int(task_data.get("retry_attempt") or 1)
        if error_ref is None:
            if error is None:
                raise TypeError("commit_retry requires error or error_ref")
            error_ref = canonical_json(self._error_payload(error))
        if fence is None or expected_head is None:
            raise TypeError("commit_retry requires a fence and expected head")
        task_id = str(task_data.get("task_id") or "").strip()
        node_id = str(task_data.get("node_id") or "").strip()
        activation_id = str(task_data.get("activation_id") or task_id).strip()
        if not task_id or not node_id or attempt < 1:
            raise NativeCheckpointError(
                "invalid_retry", "task_id, node_id and attempt>=1 are required"
            )
        execution_id = str(
            task_data.get("node_execution_id")
            or self._stable_id(fence.run_id, expected_head, task_id, node_id)
        )
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "retry", task_id, attempt
        )
        request_hash = self._request_hash(
            {
                "task": task_data,
                "attempt": attempt,
                "next_attempt_at": float(next_attempt_at),
                "error_ref": error_ref,
            }
        )
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="retry",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return None if protocol_call else existing
            run = await self._run_row(db, fence.run_id)
            self._assert_fence_row(run, fence)
            self._assert_head(run, expected_head)
            await db.execute(
                """INSERT INTO workflow_nodes(
                    node_execution_id,run_id,node_id,base_checkpoint_id,invocation_key,
                    task_id,task_path,latest_attempt,latest_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,'retryable',?)
                ON CONFLICT(node_execution_id) DO UPDATE SET latest_attempt=excluded.latest_attempt,
                    latest_status='retryable',updated_at=excluded.updated_at""",
                (
                    execution_id,
                    fence.run_id,
                    node_id,
                    expected_head,
                    activation_id,
                    task_id,
                    str(task_data.get("task_path") or ""),
                    attempt,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_node_attempts(
                    node_execution_id,retry_attempt,task_id,task_path,status,
                    started_at,ended_at,error_ref,next_attempt_at
                ) VALUES(?,?,?,?, 'retryable',?,?,?,?)
                ON CONFLICT(node_execution_id,retry_attempt) DO UPDATE SET
                    status='retryable',ended_at=excluded.ended_at,error_ref=excluded.error_ref,
                    next_attempt_at=excluded.next_attempt_at""",
                (
                    execution_id,
                    attempt,
                    task_id,
                    str(task_data.get("task_path") or ""),
                    float(task_data.get("started_at") or now),
                    now,
                    error_ref,
                    float(next_attempt_at),
                ),
            )
            updated = await (
                await db.execute(
                    """UPDATE workflow_runs SET status='retryable',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,
                    recovery_action='retry',updated_at=? WHERE run_id=? AND status='running'
                    AND head_checkpoint_id=? AND lease_owner=? AND lease_epoch=? AND run_version=?
                    RETURNING run_version""",
                    (
                        now,
                        fence.run_id,
                        expected_head,
                        fence.owner,
                        fence.lease_epoch,
                        fence.run_version,
                    ),
                )
            ).fetchone()
            if updated is None:
                raise StaleRunFence(f"stale retry writer: {fence.run_id}")
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": expected_head,
                "task_id": task_id,
                "node_execution_id": execution_id,
                "attempt": attempt,
                "next_attempt_at": float(next_attempt_at),
                "run_version": int(updated["run_version"]),
                "operation_id": op_id,
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="retry",
                request_hash=request_hash,
                result=result,
                now=now,
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
        await self._fault("retry.after_db_commit_before_return")
        return None if protocol_call else result
