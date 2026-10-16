"""Frontier commits and retries."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...contracts import JsonValue, canonical_json
from ..run_store import RunFence, StaleRunFence
from .core import NativeCheckpointCore
from .support import NATIVE_ENGINE_KIND, NativeCheckpointError, _native_mapping


@dataclass
class _FrontierCommit:
    """FrontierRetryMixin.commit_frontier 的运行状态：原函数的局部变量与参数（切段用）。"""

    flow: Any = None
    NativeCommitResult: Any = None
    blob_refs: Any = None
    checkpoint_id: Any = None
    completed_activations: Any = None
    completed_task_ids: Any = None
    configurable: Any = None
    consumed: Any = None
    consumed_interrupt_ids: Any = None
    current: Any = None
    cursor: Any = None
    db: Any = None
    decision_ids: Any = None
    effect_links: Any = None
    error_json: Any = None
    event_ids: Any = None
    execution_adapter: Any = None
    execution_id: Any = None
    execution_ids: Any = None
    existing: Any = None
    exists: Any = None
    expected_head: Any = None
    expected_task_ids: Any = None
    fence: Any = None
    frontier: Any = None
    frontier_data: Any = None
    head_row: Any = None
    head_snapshot: Any = None
    intents: Any = None
    join_firings: Any = None
    metadata: Any = None
    node_writes: Any = None
    now: Any = None
    op_id: Any = None
    operation_id: Any = None
    pending_owner_ids: Any = None
    pending_rows: Any = None
    pending_write_items: Any = None
    protocol_call: Any = None
    recovery_action: Any = None
    request_hash: Any = None
    resolved_decisions: Any = None
    result: Any = None
    run: Any = None
    sha256: Any = None
    snapshot: Any = None
    state: Any = None
    step: Any = None
    terminal_error: Any = None
    terminal_event_id: Any = None
    terminal_status: Any = None


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
        st = _FrontierCommit(
            fence=fence,
            expected_head=expected_head,
            state=state,
            frontier=frontier,
            step=step,
            completed_activations=completed_activations,
            join_firings=join_firings,
            operation_id=operation_id,
            decision_ids=decision_ids,
            consumed_interrupt_ids=consumed_interrupt_ids,
            intents=intents,
            effect_links=effect_links,
            blob_refs=blob_refs,
            metadata=metadata,
            terminal_status=terminal_status,
            terminal_error=terminal_error,
            recovery_action=recovery_action,
            configurable=configurable,
        )
        st.protocol_call = st.configurable is not None
        if st.protocol_call:
            st.fence = self._fence_from_config(st.configurable or {})
        if st.fence is None or st.expected_head is None:
            raise TypeError("commit_frontier requires a fence and expected head")
        st.frontier_data = [_native_mapping(item, name="frontier task") for item in st.frontier]
        if st.step is None:
            st.current = await self.load_head(st.fence.run_id)
            if st.current is None or str(st.current["checkpoint_id"]) != st.expected_head:
                raise NativeCheckpointError(
                    "stale_checkpoint_head", "workflow checkpoint head changed"
                )
            st.step = int(st.current["step"]) + 1
        st.resolved_decisions: Sequence[str | Mapping[str, Any]] = tuple(st.decision_ids) + tuple(
            st.consumed_interrupt_ids
        )
        st.op_id = st.operation_id or self._stable_id(
            st.fence.run_id,
            st.expected_head,
            "frontier",
            st.step,
            hashlib.sha256(canonical_json(dict(st.state)).encode("utf-8")).hexdigest(),
        )
        st.checkpoint_id = self._stable_id(st.fence.run_id, st.expected_head, "frontier", st.op_id)
        st.request_hash = self._request_hash(
            {
                "state": dict(st.state),
                "frontier": st.frontier_data,
                "step": st.step,
                "completed_activations": (
                    {key: list(value) for key, value in st.completed_activations.items()}
                    if isinstance(st.completed_activations, Mapping)
                    else list(st.completed_activations)
                ),
                "join_firings": (
                    dict(st.join_firings)
                    if isinstance(st.join_firings, Mapping)
                    else list(st.join_firings)
                ),
                "decision_ids": [
                    item if isinstance(item, str) else dict(item) for item in st.resolved_decisions
                ],
                "intents": [dict(item) for item in st.intents],
                "effect_links": [dict(item) for item in st.effect_links],
                "blob_refs": list(st.blob_refs),
                "metadata": dict(st.metadata or {}),
                "terminal_status": st.terminal_status,
                "terminal_error": dict(st.terminal_error)
                if st.terminal_error is not None
                else None,
                "recovery_action": st.recovery_action,
            }
        )
        if st.terminal_status is not None and (
            st.frontier_data or st.terminal_status not in {"completed", "failed", "cancelled"}
        ):
            raise NativeCheckpointError(
                "invalid_terminal_frontier",
                "terminal status requires an empty frontier and an allowed status",
            )
        st.now = self._clock()
        st.db = await self._connect()
        try:
            await st.db.execute("BEGIN IMMEDIATE")
            st.existing = await self._operation(
                st.db,
                operation_id=st.op_id,
                run_id=st.fence.run_id,
                operation_kind="frontier",
                request_hash=st.request_hash,
            )
            st.flow = None
            await self._replay_existing_frontier(st)
            if st.flow == "stop":
                return st.result
            await self._load_frontier_inputs(st)
            await self._commit_frontier_writes(st)
            await self._advance_frontier_head(st)
            if st.cursor.rowcount != 1:
                raise StaleRunFence(f"native frontier head changed: {st.fence.run_id}")
            await self._finish_frontier_operation(st)
            await st.db.commit()
        except BaseException:
            if st.db.in_transaction:
                await st.db.rollback()
            raise
        finally:
            await st.db.close()
        await self._fault("frontier.after_db_commit_before_return")
        if st.protocol_call:
            # 按需导入：循环依赖（checkpointer ↔ native）
            from ...native import NativeCommitResult

            st.NativeCommitResult = NativeCommitResult

            return st.NativeCommitResult(
                self._snapshot_object(st.result["snapshot"]), tuple(st.result["event_ids"])
            )
        return st.result

    async def _load_frontier_inputs(self, st: _FrontierCommit) -> None:
        """读运行、执行适配器、待写记录与检查点头，核对任务集合，生成快照并消费决定。"""

        st.run = await self._run_row(st.db, st.fence.run_id)
        st.execution_adapter = await self._execution_adapter_for(st.db, st.fence.run_id)
        self._assert_fence_row(st.run, st.fence)
        self._assert_head(st.run, st.expected_head)
        st.pending_rows = await (
            await st.db.execute(
                """SELECT task_id,write_index,write_kind,payload_json,node_execution_id
                    FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=?
                    AND base_checkpoint_id=? AND write_kind IS NOT NULL
                    ORDER BY task_id,write_index""",
                (st.run["thread_id"], st.run["checkpoint_ns"], st.expected_head),
            )
        ).fetchall()
        st.head_row = await (
            await st.db.execute(
                """SELECT checkpoint_blob,engine_kind FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                (st.run["thread_id"], st.run["checkpoint_ns"], st.expected_head),
            )
        ).fetchone()
        if st.head_row is None or str(st.head_row["engine_kind"]) != NATIVE_ENGINE_KIND:
            raise NativeCheckpointError(
                "legacy_checkpoint_read_only", "native frontier requires a native JSON head"
            )
        st.head_snapshot = json.loads(bytes(st.head_row["checkpoint_blob"]).decode("utf-8"))
        st.expected_task_ids = {
            str(item.get("task_id") or "") for item in list(st.head_snapshot.get("frontier") or [])
        }
        st.expected_task_ids.discard("")
        st.completed_task_ids = {
            str(row["task_id"])
            for row in st.pending_rows
            if str(row["write_kind"]) == "state_patch"
        }
        if st.completed_task_ids != st.expected_task_ids:
            raise NativeCheckpointError(
                "frontier_results_incomplete",
                "frontier checkpoint requires exactly one result for every task",
                details={
                    "expected_task_ids": sorted(st.expected_task_ids),
                    "completed_task_ids": sorted(st.completed_task_ids),
                },
            )
        st.pending_write_items: list[dict[str, JsonValue]] = [
            {
                "task_id": str(row["task_id"]),
                "write_index": int(row["write_index"]),
                "write_kind": str(row["write_kind"]),
                "payload": json.loads(str(row["payload_json"])),
                "node_execution_id": row["node_execution_id"],
            }
            for row in st.pending_rows
        ]
        st.node_writes: Any = st.pending_write_items
        if st.protocol_call:
            st.node_writes = {
                str(item["task_id"]): copy.deepcopy(dict(item["payload"]))
                for item in st.pending_write_items
                if item["write_kind"] == "state_patch"
            }
        st.snapshot = self._snapshot_payload(
            run=st.run,
            checkpoint_id=st.checkpoint_id,
            parent_checkpoint_id=st.expected_head,
            step=st.step,
            state=st.state,
            frontier=st.frontier_data,
            completed_activations=st.completed_activations,
            join_firings=st.join_firings,
            node_writes=st.node_writes,
            metadata={
                **dict(st.metadata or {}),
                "operation_id": st.op_id,
                "operation_kind": "frontier",
            },
        )
        await self._insert_checkpoint(st.db, run=st.run, snapshot=st.snapshot, now=st.now)
        st.consumed = await (
            st.execution_adapter.consume_decisions(
                st.db,
                run_id=st.fence.run_id,
                decisions=st.resolved_decisions,
                checkpoint_id=st.checkpoint_id,
                now=st.now,
            )
            if st.execution_adapter is not None
            else self._consume_decisions(
                st.db,
                run_id=st.fence.run_id,
                decisions=st.resolved_decisions,
                checkpoint_id=st.checkpoint_id,
                now=st.now,
            )
        )

    async def _replay_existing_frontier(self, st: _FrontierCommit) -> None:
        """同一操作已经提交过：原样返回上次的结果。"""

        if st.existing is not None:
            await st.db.commit()
            if st.protocol_call:
                # 按需导入：循环依赖（checkpointer ↔ native）
                from ...native import NativeCommitResult

                st.NativeCommitResult = NativeCommitResult

                st.result = st.NativeCommitResult(
                    self._snapshot_object(st.existing["snapshot"]),
                    tuple(st.existing.get("event_ids") or ()),
                )
                st.flow = "stop"
                return
            st.result = st.existing
            st.flow = "stop"
            return

    async def _commit_frontier_writes(self, st: _FrontierCommit) -> None:
        """写节点执行、登记数据块引用、清掉待办任务的引用、物化意图事件。"""

        st.execution_ids = [
            str(row["node_execution_id"])
            for row in st.pending_rows
            if row["write_kind"] == "state_patch" and row["node_execution_id"] is not None
        ]
        for st.execution_id in st.execution_ids:
            await st.db.execute(
                "UPDATE workflow_nodes SET latest_status='succeeded',updated_at=? "
                "WHERE node_execution_id=? AND latest_status='succeeded_pending'",
                (st.now, st.execution_id),
            )
            await st.db.execute(
                "UPDATE workflow_node_attempts SET status='succeeded' "
                "WHERE node_execution_id=? AND status='succeeded_pending'",
                (st.execution_id,),
            )
            if st.execution_adapter is None:
                await st.db.execute(
                    """INSERT OR IGNORE INTO workflow_checkpoint_effects(
                            thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
                        ) SELECT ?,?,?,effect_id,node_execution_id FROM workflow_node_effects
                        WHERE node_execution_id=?""",
                    (
                        st.run["thread_id"],
                        st.run["checkpoint_ns"],
                        st.checkpoint_id,
                        st.execution_id,
                    ),
                )
        if st.execution_adapter is not None:
            await st.execution_adapter.link_effects(
                st.db,
                run_id=st.fence.run_id,
                checkpoint_ns=str(st.run["checkpoint_ns"]),
                checkpoint_id=st.checkpoint_id,
                links=st.effect_links,
                now=st.now,
            )
        for st.sha256 in st.blob_refs:
            st.exists = await (
                await st.db.execute(
                    "SELECT 1 FROM workflow_blobs WHERE sha256=?", (str(st.sha256),)
                )
            ).fetchone()
            if st.exists is None:
                raise NativeCheckpointError(
                    "blob_not_found", f"unknown blob reference: {st.sha256}"
                )
            await st.db.execute(
                """INSERT OR IGNORE INTO workflow_blob_refs(
                        sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'checkpoint',?,?)""",
                (str(st.sha256), st.checkpoint_id, st.now),
            )
        st.pending_owner_ids = tuple(
            f"{st.fence.run_id}:{st.expected_head}:{task_id}"
            for task_id in sorted(st.expected_task_ids)
        )
        if st.pending_owner_ids:
            await st.db.execute(
                f"""DELETE FROM workflow_blob_refs WHERE owner_kind='pending_task'
                    AND owner_id IN ({",".join("?" for _ in st.pending_owner_ids)})""",
                st.pending_owner_ids,
            )
        st.event_ids = [
            await (
                st.execution_adapter.materialize_intent(
                    st.db, run=st.run, intent=intent, now=st.now
                )
                if st.execution_adapter is not None
                else self._materialize_intent(st.db, run=st.run, intent=intent, now=st.now)
            )
            for intent in st.intents
        ]

    async def _advance_frontier_head(self, st: _FrontierCommit) -> None:
        """推进运行的检查点头（终态时连带写终态）。"""

        if st.terminal_status is None:
            st.cursor = await st.db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=checkpoint_ns,
                    head_checkpoint_id=?,active_nodes_json=?,updated_at=?
                    WHERE run_id=? AND head_checkpoint_id=? AND status='running'
                    AND lease_owner=? AND lease_epoch=? AND run_version=?""",
                (
                    st.checkpoint_id,
                    canonical_json([str(item.get("node_id", "")) for item in st.frontier_data]),
                    st.now,
                    st.fence.run_id,
                    st.expected_head,
                    st.fence.owner,
                    st.fence.lease_epoch,
                    st.fence.run_version,
                ),
            )
        else:
            st.error_json = (
                canonical_json(dict(st.terminal_error)) if st.terminal_error is not None else None
            )
            st.cursor = await st.db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=checkpoint_ns,
                    head_checkpoint_id=?,active_nodes_json='[]',status=?,error_json=?,
                    recovery_action=?,ended_at=COALESCE(ended_at,?),lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,updated_at=?
                    WHERE run_id=? AND head_checkpoint_id=? AND status='running'
                    AND lease_owner=? AND lease_epoch=? AND run_version=?""",
                (
                    st.checkpoint_id,
                    st.terminal_status,
                    st.error_json,
                    st.recovery_action,
                    st.now,
                    st.now,
                    st.fence.run_id,
                    st.expected_head,
                    st.fence.owner,
                    st.fence.lease_epoch,
                    st.fence.run_version,
                ),
            )

    async def _finish_frontier_operation(self, st: _FrontierCommit) -> None:
        """终态收尾、删掉已消费的待写记录、记录操作结果。"""

        if st.execution_adapter is not None and st.terminal_status is not None:
            st.terminal_event_id = await st.execution_adapter.finalize_run(
                st.db,
                run=st.run,
                terminal_status=st.terminal_status,
                terminal_error=st.terminal_error,
                recovery_action=st.recovery_action,
                event_ids=st.event_ids,
                now=st.now,
            )
            if st.terminal_event_id not in st.event_ids:
                st.event_ids.append(st.terminal_event_id)
        await st.db.execute(
            """DELETE FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=?
                AND base_checkpoint_id=? AND write_kind IS NOT NULL""",
            (st.run["thread_id"], st.run["checkpoint_ns"], st.expected_head),
        )
        st.result: dict[str, JsonValue] = {
            "run_id": st.fence.run_id,
            "checkpoint_id": st.checkpoint_id,
            "operation_id": st.op_id,
            "consumed_decision_ids": st.consumed,
            "event_ids": st.event_ids,
            "snapshot": st.snapshot,
        }
        await self._record_operation(
            st.db,
            operation_id=st.op_id,
            run_id=st.fence.run_id,
            operation_kind="frontier",
            request_hash=st.request_hash,
            result=st.result,
            now=st.now,
        )

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
