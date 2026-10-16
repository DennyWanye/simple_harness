"""Interrupts, failures and native forks."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts import JsonValue, canonical_json, validate_json_value
from ...human import HumanDecisionStore
from ..run_store import RunFence, StaleRunFence
from .core import NativeCheckpointCore
from .support import NATIVE_ENGINE_KIND, NativeCheckpointError, _native_mapping


class InterruptFailureMixin(NativeCheckpointCore):
    """Interrupts, failures and native forks."""

    __slots__ = ()

    async def commit_interrupt(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        task: Mapping[str, Any] | object | None = None,
        *,
        state: Mapping[str, JsonValue] | None = None,
        frontier: Sequence[Mapping[str, JsonValue]] | None = None,
        step: int | None = None,
        interrupt_id: str | None = None,
        prompt: JsonValue = None,
        kind: str = "human_decision",
        expires_at: float | None = None,
        operation_id: str | None = None,
        interrupt: Mapping[str, JsonValue] | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | None:

        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
        if fence is None or expected_head is None or task is None:
            raise TypeError("commit_interrupt requires a fence, expected head and task")
        task_data = _native_mapping(task, name="task")
        task_id = str(task_data.get("task_id") or "").strip()
        if interrupt is not None:
            interrupt_id = str(interrupt["interrupt_id"])
            prompt = copy.deepcopy(interrupt.get("payload"))
            if isinstance(prompt, Mapping):
                kind = str(prompt.get("kind") or kind)
                expires_at = prompt.get("expires_at") if expires_at is None else expires_at
        if not interrupt_id:
            raise TypeError("commit_interrupt requires interrupt_id")
        if state is None or frontier is None or step is None:
            current = await self.load_head(fence.run_id)
            if current is None or str(current["checkpoint_id"]) != expected_head:
                raise NativeCheckpointError(
                    "stale_checkpoint_head", "workflow checkpoint head changed"
                )
            state = dict(current["state"])
            frontier = [dict(item) for item in current["frontier"]]
            step = int(current["step"])
        else:
            current = None
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "interrupt", task_id, interrupt_id
        )
        checkpoint_id = self._stable_id(fence.run_id, expected_head, "interrupt", op_id)
        interrupt_data: dict[str, JsonValue] = {
            "interrupt_id": interrupt_id,
            "task_id": task_id,
            "kind": kind,
            "prompt": copy.deepcopy(prompt),
            "expires_at": expires_at,
        }
        request_hash = self._request_hash(
            {
                "task": task_data,
                "state": dict(state),
                "frontier": [dict(item) for item in frontier],
                "step": step,
                "interrupt": interrupt_data,
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
                operation_kind="interrupt",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return None if protocol_call else existing
            run = await self._run_row(db, fence.run_id)
            execution_adapter = await self._execution_adapter_for(db, fence.run_id)
            self._assert_fence_row(run, fence)
            self._assert_head(run, expected_head)
            snapshot = self._snapshot_payload(
                run=run,
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=expected_head,
                step=step,
                state=state,
                frontier=frontier,
                completed_activations=(current or {}).get("completed_activations", ()),
                join_firings=(current or {}).get("join_firings", ()),
                node_writes=(current or {}).get("node_writes", ()),
                interrupt=interrupt_data,
                metadata={"operation_id": op_id, "operation_kind": "interrupt"},
            )
            await self._insert_checkpoint(db, run=run, snapshot=snapshot, now=now)
            await self._write_pending(
                db,
                run=run,
                base_checkpoint_id=checkpoint_id,
                task_id=task_id or f"interrupt:{interrupt_id}",
                write_index=-1,
                write_kind="interrupt",
                payload=interrupt_data,
                node_execution_id=None,
            )
            if execution_adapter is not None:
                decision_data = await execution_adapter.open_decision(
                    db,
                    run=run,
                    interrupt_id=interrupt_id,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id or None,
                    kind=kind,
                    prompt=prompt,
                    expires_at=expires_at,
                    now=now,
                )
            else:
                decision = await HumanDecisionStore.open_graph_interrupt_in_transaction(
                    db,
                    fence=fence,
                    interrupt_id=interrupt_id,
                    prompt=prompt,
                    checkpoint_id=checkpoint_id,
                    checkpoint_ns=str(run["checkpoint_ns"]),
                    task_id=task_id or None,
                    kind=kind,
                    expires_at=expires_at,
                    now=now,
                )
                decision_data = {
                    "decision_id": decision.decision_id,
                    "kind": decision.kind,
                    "nonce": decision.nonce,
                    "version": decision.version,
                }
            decision_event_intent = {
                "intent_id": (
                    f"decision:{decision_data['decision_id']}:open:"
                    f"v{decision_data['version']}:projection:v2"
                ),
                "event_key": (
                    f"decision:{decision_data['decision_id']}:open:"
                    f"v{decision_data['version']}:projection:v2"
                ),
                "event_type": "workflow.decision",
                "payload": {
                    "kind": "decision",
                    "status": "open",
                    "decision_id": decision_data["decision_id"],
                    "decision_kind": decision_data["kind"],
                    "nonce": decision_data["nonce"],
                    "version": decision_data["version"],
                    "prompt": copy.deepcopy(prompt),
                },
            }
            decision_event_id = await (
                execution_adapter.materialize_intent(
                    db, run=run, intent=decision_event_intent, now=now
                )
                if execution_adapter is not None
                else self._materialize_intent(db, run=run, intent=decision_event_intent, now=now)
            )
            if execution_adapter is not None:
                waiting = await db.execute(
                    """UPDATE workflow_runs SET status='waiting',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,
                    recovery_action='resume',updated_at=? WHERE run_id=? AND status='running'
                    AND head_checkpoint_id=? AND lease_owner=? AND lease_epoch=?
                    AND run_version=?""",
                    (
                        now,
                        fence.run_id,
                        expected_head,
                        fence.owner,
                        fence.lease_epoch,
                        fence.run_version,
                    ),
                )
                if waiting.rowcount != 1:
                    raise StaleRunFence(f"stale interrupt writer: {fence.run_id}")
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": checkpoint_id,
                "decision_id": str(decision_data["decision_id"]),
                "interrupt_id": interrupt_id,
                "operation_id": op_id,
                "snapshot": snapshot,
                "event_ids": [decision_event_id],
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="interrupt",
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
        await self._fault("interrupt.after_db_commit_before_return")
        return None if protocol_call else result

    async def _commit_failure_kind(
        self,
        fence: RunFence,
        expected_head: str,
        *,
        error: Mapping[str, JsonValue],
        operation_id: str,
        operation_kind: str,
        task: Mapping[str, Any] | object | None,
        frontier: Sequence[Mapping[str, Any]],
        intent: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        validate_json_value(dict(error), path="$.error")
        request_payload: dict[str, JsonValue] = {
            "error": dict(error),
            "task": _native_mapping(task, name="task") if task is not None else None,
            "frontier": [dict(item) for item in frontier],
            "intent": dict(intent) if intent is not None else None,
        }
        request_hash = self._request_hash(request_payload)
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=operation_id,
                run_id=fence.run_id,
                operation_kind=operation_kind,
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return existing
            run = await self._run_row(db, fence.run_id)
            execution_adapter = await self._execution_adapter_for(db, fence.run_id)
            self._assert_fence_row(run, fence)
            self._assert_head(run, expected_head)
            execution_ids: list[str] = []
            if task is not None:
                task_data = _native_mapping(task, name="task")
                task_id = str(task_data.get("task_id") or "")
                node_id = str(task_data.get("node_id") or "")
                activation = str(task_data.get("activation_id") or task_id)
                execution_ids.append(
                    str(
                        task_data.get("node_execution_id")
                        or self._stable_id(fence.run_id, expected_head, task_id, node_id)
                    )
                )
            for item in frontier:
                if item.get("node_execution_id"):
                    execution_ids.append(str(item["node_execution_id"]))
            attempt_status = "failed_engine" if operation_kind == "engine_failure" else "failed"
            for execution_id in set(execution_ids):
                await db.execute(
                    "UPDATE workflow_nodes SET latest_status=?,updated_at=? WHERE node_execution_id=?",
                    (attempt_status, now, execution_id),
                )
                await db.execute(
                    "UPDATE workflow_node_attempts SET status=?,ended_at=?,error_ref=? "
                    "WHERE node_execution_id=? AND status IN ('running','succeeded_pending')",
                    (attempt_status, now, canonical_json(dict(error)), execution_id),
                )
            event_ids: list[str] = []
            if intent is not None:
                event_ids.append(
                    await (
                        execution_adapter.materialize_intent(db, run=run, intent=intent, now=now)
                        if execution_adapter is not None
                        else self._materialize_intent(db, run=run, intent=intent, now=now)
                    )
                )
            updated = await (
                await db.execute(
                    """UPDATE workflow_runs SET status='failed',error_json=?,recovery_action=NULL,
                    lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,ended_at=?,
                    run_version=run_version+1,updated_at=? WHERE run_id=? AND status='running'
                    AND head_checkpoint_id=? AND lease_owner=? AND lease_epoch=? AND run_version=?
                    RETURNING run_version""",
                    (
                        canonical_json(dict(error)),
                        now,
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
                raise StaleRunFence(f"stale failure writer: {fence.run_id}")
            if execution_adapter is not None:
                terminal_event_id = await execution_adapter.finalize_run(
                    db,
                    run=run,
                    terminal_status="failed",
                    terminal_error=error,
                    recovery_action=None,
                    event_ids=event_ids,
                    now=now,
                )
                if terminal_event_id not in event_ids:
                    event_ids.append(terminal_event_id)
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": expected_head,
                "status": "failed",
                "run_version": int(updated["run_version"]),
                "event_ids": event_ids,
                "operation_id": operation_id,
            }
            await self._record_operation(
                db,
                operation_id=operation_id,
                run_id=fence.run_id,
                operation_kind=operation_kind,
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
        await self._fault(f"{operation_kind}.after_db_commit_before_return")
        return result

    async def commit_launch_failure(
        self,
        run_id: str,
        *,
        error: Mapping[str, JsonValue],
        recovery_action: str = "inspect_or_cancel",
    ) -> dict[str, Any]:
        """Atomically fail a run that crashed before the native executor claimed it."""

        error_data = copy.deepcopy(dict(error))
        validate_json_value(error_data, path="$.error")
        operation_id = self._stable_id(run_id, "launch_failure")
        request_hash = self._request_hash({"error": error_data, "recovery_action": recovery_action})
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=operation_id,
                run_id=run_id,
                operation_kind="launch_failure",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return existing
            run = await self._run_row(db, run_id)
            execution_adapter = await self._execution_adapter_for(db, run_id)
            if str(run["status"]) != "created":
                raise NativeCheckpointError(
                    "launch_failure_not_applicable",
                    "launch failure can only finalize an unclaimed created run",
                )
            final_intent = {
                "intent_id": f"{run_id}:run-final",
                "event_key": "run:terminal",
                "event_type": "workflow.final",
                "channel": "final",
                "payload": {
                    "kind": "final",
                    "status": "failed",
                    "error": error_data,
                    "recovery_action": recovery_action,
                    "card": {
                        "run_id": run_id,
                        "status": "failed",
                        "error": error_data,
                        "recovery_action": recovery_action,
                    },
                },
            }
            event_id = await (
                execution_adapter.materialize_intent(db, run=run, intent=final_intent, now=now)
                if execution_adapter is not None
                else self._materialize_intent(db, run=run, intent=final_intent, now=now)
            )
            updated = await (
                await db.execute(
                    """UPDATE workflow_runs SET status='failed',error_json=?,recovery_action=?,
                    lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,ended_at=?,
                    run_version=run_version+1,updated_at=? WHERE run_id=? AND status='created'
                    RETURNING run_version""",
                    (canonical_json(error_data), recovery_action, now, now, run_id),
                )
            ).fetchone()
            if updated is None:
                raise StaleRunFence(f"launch failure raced with a runner claim: {run_id}")
            if execution_adapter is not None:
                terminal_event_id = await execution_adapter.finalize_run(
                    db,
                    run=run,
                    terminal_status="failed",
                    terminal_error=error_data,
                    recovery_action=recovery_action,
                    event_ids=(event_id,),
                    now=now,
                )
                if terminal_event_id != event_id:
                    event_id = terminal_event_id
            result: dict[str, JsonValue] = {
                "run_id": run_id,
                "status": "failed",
                "run_version": int(updated["run_version"]),
                "event_ids": [event_id],
                "operation_id": operation_id,
            }
            await self._record_operation(
                db,
                operation_id=operation_id,
                run_id=run_id,
                operation_kind="launch_failure",
                request_hash=request_hash,
                result=result,
                now=now,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_failure(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        task: Mapping[str, Any] | object | None = None,
        *,
        error: Mapping[str, JsonValue] | object,
        operation_id: str | None = None,
        intent: Mapping[str, Any] | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | None:
        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
        if fence is None or expected_head is None or task is None:
            raise TypeError("commit_failure requires a fence, expected head and task")
        error_data = self._error_payload(error)
        task_data = _native_mapping(task, name="task")
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "failure", task_data.get("task_id", "")
        )
        final_intent = intent or {
            "intent_id": f"{fence.run_id}:run-final",
            "event_key": "run:terminal",
            "event_type": "workflow.final",
            "channel": "final",
            "payload": {
                "kind": "final",
                "status": "failed",
                "error": copy.deepcopy(error_data),
                "card": {
                    "run_id": fence.run_id,
                    "status": "failed",
                    "error": copy.deepcopy(error_data),
                },
            },
        }
        result = await self._commit_failure_kind(
            fence,
            expected_head,
            error=error_data,
            operation_id=op_id,
            operation_kind="failure",
            task=task_data,
            frontier=(),
            intent=final_intent,
        )
        return None if protocol_call else result

    async def commit_engine_failure(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        frontier: Sequence[Mapping[str, Any] | object] = (),
        *,
        error: Mapping[str, JsonValue] | object,
        operation_id: str | None = None,
        intent: Mapping[str, Any] | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | None:
        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
        if fence is None or expected_head is None:
            raise TypeError("commit_engine_failure requires a fence and expected head")
        error_data = self._error_payload(error)
        frontier_data = [_native_mapping(item, name="frontier task") for item in frontier]
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "engine_failure", canonical_json(error_data)
        )
        final_intent = intent or {
            "intent_id": f"{fence.run_id}:run-final",
            "event_key": "run:terminal",
            "event_type": "workflow.final",
            "channel": "final",
            "payload": {
                "kind": "final",
                "status": "failed",
                "error": copy.deepcopy(error_data),
                "card": {
                    "run_id": fence.run_id,
                    "status": "failed",
                    "error": copy.deepcopy(error_data),
                },
            },
        }
        result = await self._commit_failure_kind(
            fence,
            expected_head,
            error=error_data,
            operation_id=op_id,
            operation_kind="engine_failure",
            task=None,
            frontier=frontier_data,
            intent=final_intent,
        )
        return None if protocol_call else result

    async def commit_native_fork(
        self,
        fork_key: str,
        child_checkpoint_id: str,
    ) -> dict[str, str]:
        """Finish a prepared native fork saga without decoding legacy bytes."""

        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            saga = await (
                await db.execute(
                    "SELECT * FROM workflow_fork_requests WHERE fork_key=?", (fork_key,)
                )
            ).fetchone()
            if saga is None:
                raise NativeCheckpointError("fork_not_prepared", "fork request was not prepared")
            child = await self._run_row(db, str(saga["child_run_id"]))
            if str(saga["status"]) == "checkpointed":
                if str(child["head_checkpoint_id"]) != child_checkpoint_id:
                    raise NativeCheckpointError(
                        "fork_identity_conflict", "fork checkpoint id changed"
                    )
                await db.commit()
                return {
                    "run_id": str(child["run_id"]),
                    "checkpoint_ns": str(child["checkpoint_ns"]),
                    "checkpoint_id": child_checkpoint_id,
                }
            source = await (
                await db.execute(
                    """SELECT * FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=?
                    AND checkpoint_id=? AND engine_kind=?""",
                    (
                        child["thread_id"],
                        saga["source_checkpoint_ns"],
                        saga["source_checkpoint_id"],
                        NATIVE_ENGINE_KIND,
                    ),
                )
            ).fetchone()
            if source is None:
                raise NativeCheckpointError(
                    "fork_checkpoint_not_native", "source checkpoint is not native JSON"
                )
            source_snapshot = json.loads(bytes(source["checkpoint_blob"]).decode("utf-8"))
            request = json.loads(str(saga["state_patch_json"]))
            patch = dict(request.get("patch") or {})
            state = copy.deepcopy(dict(source_snapshot["state"]))
            state.update(patch)
            state.update(
                {
                    "schema_version": int(child["state_schema_version"]),
                    "workflow_name": str(child["workflow_name"]),
                    "workflow_version": str(child["workflow_version"]),
                    "thread_id": str(child["thread_id"]),
                    "run_id": str(child["run_id"]),
                    "session_id": str(child["session_id"]),
                    "trace_id": str(child["trace_id"]),
                    "parent_run_id": str(saga["source_run_id"]),
                    "source_checkpoint_id": str(saga["source_checkpoint_id"]),
                }
            )
            snapshot = self._snapshot_payload(
                run=child,
                checkpoint_id=child_checkpoint_id,
                parent_checkpoint_id=str(saga["source_checkpoint_id"]),
                step=int(source_snapshot.get("step") or 0) + 1,
                state=state,
                frontier=list(source_snapshot.get("frontier") or []),
                completed_activations=list(source_snapshot.get("completed_activations") or []),
                join_firings=dict(source_snapshot.get("join_firings") or {}),
                metadata={
                    "operation_kind": "fork",
                    "fork_key": fork_key,
                    "source_run_id": str(saga["source_run_id"]),
                    "source_checkpoint_id": str(saga["source_checkpoint_id"]),
                },
            )
            await self._insert_checkpoint(db, run=child, snapshot=snapshot, now=now)
            await db.execute(
                """WITH RECURSIVE ancestors(
                    thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) AS (
                    SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                    FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    UNION ALL
                    SELECT parent.thread_id,parent.checkpoint_ns,parent.checkpoint_id,
                           parent.parent_checkpoint_id,parent.created_at
                    FROM workflow_checkpoints parent JOIN ancestors child_checkpoint
                      ON parent.thread_id=child_checkpoint.thread_id
                     AND parent.checkpoint_ns=child_checkpoint.checkpoint_ns
                     AND parent.checkpoint_id=child_checkpoint.parent_checkpoint_id
                )
                INSERT OR IGNORE INTO workflow_checkpoint_owners(
                    run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) SELECT ?,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                  FROM ancestors""",
                (
                    child["thread_id"],
                    saga["source_checkpoint_ns"],
                    saga["source_checkpoint_id"],
                    child["run_id"],
                ),
            )
            cursor = await db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=?,head_checkpoint_id=?,
                active_nodes_json=?,updated_at=? WHERE run_id=? AND head_checkpoint_id IS NULL
                AND status='created'""",
                (
                    child["checkpoint_ns"],
                    child_checkpoint_id,
                    canonical_json([str(item.get("node_id", "")) for item in snapshot["frontier"]]),
                    now,
                    child["run_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise NativeCheckpointError("fork_child_changed", "fork child head changed")
            await db.execute(
                "UPDATE workflow_fork_requests SET status='checkpointed',updated_at=? "
                "WHERE fork_key=? AND status='prepared'",
                (now, fork_key),
            )
            await db.commit()
            return {
                "run_id": str(child["run_id"]),
                "checkpoint_ns": str(child["checkpoint_ns"]),
                "checkpoint_id": child_checkpoint_id,
            }
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
