"""Genesis, task results, route selection, decisions and intent materialisation."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from ...contracts import JsonValue, canonical_json, validate_json_value
from ..run_store import RunFence, StaleRunFence
from .core import NativeCheckpointCore
from .support import NativeCheckpointError, _native_mapping


class TaskCommitMixin(NativeCheckpointCore):
    """Genesis, task results, route selection, decisions and intent materialisation."""

    __slots__ = ()

    async def ensure_genesis(
        self,
        fence: RunFence | None = None,
        run: Mapping[str, Any] | str | None = None,
        initial_state: Mapping[str, JsonValue] | None = None,
        entry_frontier: Sequence[Mapping[str, JsonValue]] = (),
        *,
        operation_id: str | None = None,
        snapshot: object | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | object:
        protocol_call = snapshot is not None
        provided_snapshot: dict[str, Any] | None = None
        if snapshot is not None:
            provided_snapshot = _native_mapping(snapshot, name="snapshot")
            fence = self._fence_from_config(configurable or {})
            run = str(provided_snapshot["run_id"])
            initial_state = dict(provided_snapshot["state"])
            entry_frontier = list(provided_snapshot.get("frontier") or [])
        if fence is None or run is None or initial_state is None:
            raise TypeError("ensure_genesis requires snapshot/configurable or fence/run/state")
        run_id = str(run if isinstance(run, str) else run["run_id"])
        op_id = operation_id or self._stable_id(run_id, "genesis")
        request_hash = self._request_hash(
            {
                "state": dict(initial_state),
                "frontier": [dict(item) for item in entry_frontier],
                "checkpoint_id": (
                    str(provided_snapshot["checkpoint_id"]) if provided_snapshot else None
                ),
            }
        )
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=op_id,
                run_id=run_id,
                operation_kind="genesis",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return self._snapshot_object(existing["snapshot"]) if protocol_call else existing
            run_row = await self._run_row(db, run_id)
            self._assert_fence_row(run_row, fence)
            self._assert_head(run_row, None)
            checkpoint_id = self._stable_id(run_id, str(run_row["checkpoint_ns"]), "genesis")
            if provided_snapshot is not None:
                checkpoint_id = str(provided_snapshot["checkpoint_id"])
            snapshot = self._snapshot_payload(
                run=run_row,
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=None,
                step=0,
                state=initial_state,
                frontier=entry_frontier,
                metadata={"operation_id": op_id, "operation_kind": "genesis"},
            )
            await self._insert_checkpoint(db, run=run_row, snapshot=snapshot, now=now)
            cursor = await db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=checkpoint_ns,
                head_checkpoint_id=?,active_nodes_json=?,updated_at=?
                WHERE run_id=? AND head_checkpoint_id IS NULL AND status='running'
                AND lease_owner=? AND lease_epoch=? AND run_version=?""",
                (
                    checkpoint_id,
                    canonical_json([str(item.get("node_id", "")) for item in entry_frontier]),
                    now,
                    run_id,
                    fence.owner,
                    fence.lease_epoch,
                    fence.run_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleRunFence(f"native genesis head changed: {run_id}")
            result: dict[str, JsonValue] = {
                "run_id": run_id,
                "checkpoint_id": checkpoint_id,
                "operation_id": op_id,
                "snapshot": snapshot,
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=run_id,
                operation_kind="genesis",
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
        await self._fault("genesis.after_db_commit_before_return")
        return self._snapshot_object(result["snapshot"]) if protocol_call else result

    async def commit_task_result(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        task: Mapping[str, Any] | object | None = None,
        attempt: int | None = None,
        patch: Mapping[str, JsonValue] | object | None = None,
        *,
        operation_id: str | None = None,
        execution_info: object | None = None,
        blob_refs: Sequence[str] = (),
        consumed_interrupt_ids: Sequence[str] = (),
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any] | None:
        protocol_call = configurable is not None
        if protocol_call:
            fence = self._fence_from_config(configurable or {})
            if execution_info is not None:
                info = _native_mapping(execution_info, name="execution_info")
                attempt = int(info["node_attempt"])
        if (
            fence is None
            or expected_head is None
            or task is None
            or attempt is None
            or patch is None
        ):
            raise TypeError("commit_task_result requires a fence, head, task, attempt and patch")
        task_data = _native_mapping(task, name="task")
        patch_data = _native_mapping(patch, name="patch")
        task_id = str(task_data.get("task_id") or "").strip()
        node_id = str(task_data.get("node_id") or "").strip()
        activation_id = str(task_data.get("activation_id") or task_id).strip()
        if not task_id or not node_id or attempt < 1:
            raise NativeCheckpointError(
                "invalid_task_result", "task_id, node_id and attempt>=1 are required"
            )
        validate_json_value(patch_data, path="$.state_patch")
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "task_result", task_id, attempt
        )
        execution_id = str(
            task_data.get("node_execution_id")
            or self._stable_id(fence.run_id, expected_head, task_id, node_id)
        )
        payload: dict[str, JsonValue] = {
            "task_id": task_id,
            "activation_id": activation_id,
            "node_id": node_id,
            "attempt": attempt,
            "patch": patch_data,
            "blob_refs": sorted({str(value) for value in blob_refs}),
            "consumed_interrupt_ids": sorted({str(value) for value in consumed_interrupt_ids}),
        }
        request_hash = self._request_hash(payload)
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="task_result",
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
                ) VALUES(?,?,?,?,?,?,?,?,'succeeded_pending',?)
                ON CONFLICT(node_execution_id) DO UPDATE SET
                    task_id=excluded.task_id,task_path=excluded.task_path,
                    latest_attempt=MAX(latest_attempt,excluded.latest_attempt),
                    latest_status='succeeded_pending',updated_at=excluded.updated_at""",
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
                    node_execution_id,retry_attempt,task_id,task_path,status,started_at,ended_at
                ) VALUES(?,?,?,?, 'succeeded_pending',?,?)
                ON CONFLICT(node_execution_id,retry_attempt) DO UPDATE SET
                    status='succeeded_pending',ended_at=excluded.ended_at,error_ref=NULL,next_attempt_at=NULL""",
                (
                    execution_id,
                    attempt,
                    task_id,
                    str(task_data.get("task_path") or ""),
                    float(task_data.get("started_at") or now),
                    now,
                ),
            )
            await self._write_pending(
                db,
                run=run,
                base_checkpoint_id=expected_head,
                task_id=task_id,
                write_index=0,
                write_kind="state_patch",
                payload=payload,
                node_execution_id=execution_id,
                task_path=str(task_data.get("task_path") or ""),
            )
            pending_owner = f"{fence.run_id}:{expected_head}:{task_id}"
            for sha256 in payload["blob_refs"]:
                if len(str(sha256)) != 64 or any(
                    ch not in "0123456789abcdef" for ch in str(sha256)
                ):
                    raise NativeCheckpointError(
                        "invalid_blob_ref", "task result contains an invalid blob digest"
                    )
                exists = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (str(sha256),))
                ).fetchone()
                if exists is None:
                    raise NativeCheckpointError(
                        "blob_not_found", f"unknown blob reference: {sha256}"
                    )
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'pending_task',?,?)""",
                    (str(sha256), pending_owner, now),
                )
                await db.execute(
                    "DELETE FROM workflow_blob_refs WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?",
                    (str(sha256), fence.run_id),
                )
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": expected_head,
                "task_id": task_id,
                "node_execution_id": execution_id,
                "attempt": attempt,
                "operation_id": op_id,
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="task_result",
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
        await self._fault("task_result.after_db_commit_before_return")
        return None if protocol_call else result

    async def commit_route_selection(
        self,
        fence: RunFence | None = None,
        expected_head: str | None = None,
        *,
        source: str,
        selected_route: str,
        next_frontier_payload_hash: str,
        task_id: str | None = None,
        operation_id: str | None = None,
        configurable: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        if configurable is not None:
            fence = self._fence_from_config(configurable)
        if fence is None or expected_head is None:
            raise TypeError("commit_route_selection requires a fence and expected head")
        route_task_id = task_id or f"route:{source}"
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "route_selection", source
        )
        payload: dict[str, JsonValue] = {
            "source": source,
            "selected_route": selected_route,
            "next_frontier_payload_hash": next_frontier_payload_hash,
        }
        request_hash = self._request_hash(payload)
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="route_selection",
                request_hash=request_hash,
                request_conflict_code="route_nondeterminism",
            )
            if existing is not None:
                stored = existing.get("selection")
                if stored != payload:
                    raise NativeCheckpointError(
                        "route_nondeterminism", "route selection changed for the same operation"
                    )
                await db.commit()
                return existing
            run = await self._run_row(db, fence.run_id)
            self._assert_fence_row(run, fence)
            self._assert_head(run, expected_head)
            await self._write_pending(
                db,
                run=run,
                base_checkpoint_id=expected_head,
                task_id=route_task_id,
                write_index=1,
                write_kind="route_selection",
                payload=payload,
                node_execution_id=None,
            )
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": expected_head,
                "operation_id": op_id,
                "selection": payload,
            }
            await self._record_operation(
                db,
                operation_id=op_id,
                run_id=fence.run_id,
                operation_kind="route_selection",
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
        await self._fault("route_selection.after_db_commit_before_return")
        return result

    @staticmethod
    async def _consume_decisions(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        consumed: list[str] = []
        for item in decisions:
            decision_id = str(item if isinstance(item, str) else item["decision_id"])
            expected_version = None if isinstance(item, str) else item.get("expected_version")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=? AND run_id=?",
                    (decision_id, run_id),
                )
            ).fetchone()
            if row is None and isinstance(item, str):
                row = await (
                    await db.execute(
                        """SELECT * FROM workflow_decisions
                        WHERE interrupt_id=? AND run_id=? AND status IN ('resolved','consumed')""",
                        (decision_id, run_id),
                    )
                ).fetchone()
                if row is not None:
                    decision_id = str(row["decision_id"])
            if row is None:
                raise NativeCheckpointError("decision_not_found", "resume decision was not found")
            if str(row["status"]) == "consumed":
                if str(row["consumed_checkpoint_id"]) != checkpoint_id:
                    raise NativeCheckpointError(
                        "decision_already_consumed", "decision belongs to another checkpoint"
                    )
                consumed.append(decision_id)
                continue
            if str(row["status"]) != "resolved" or row["consumed_at"] is not None:
                raise NativeCheckpointError("decision_not_resolved", "decision is not resumable")
            if expected_version is not None and int(row["decision_version"]) != int(
                expected_version
            ):
                raise NativeCheckpointError("stale_decision", "decision version changed")
            cursor = await db.execute(
                """UPDATE workflow_decisions SET status='consumed',consumed_at=?,
                consumed_checkpoint_id=?,decision_version=decision_version+1
                WHERE decision_id=? AND run_id=? AND status='resolved' AND consumed_at IS NULL
                AND (? IS NULL OR decision_version=?)""",
                (now, checkpoint_id, decision_id, run_id, expected_version, expected_version),
            )
            if cursor.rowcount != 1:
                raise NativeCheckpointError("stale_decision", "decision changed while consumed")
            consumed.append(decision_id)
        return consumed

    @staticmethod
    async def _materialize_intent(
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str:
        # 按需导入：循环依赖（checkpointer ↔ outbox）
        from ...outbox import stable_delivery_id, stable_event_id

        intent_id = str(intent.get("intent_id") or "").strip()
        event_type = str(intent.get("event_type") or "").strip()
        if not intent_id or not event_type:
            raise NativeCheckpointError(
                "invalid_delivery_intent", "intent_id and event_type are required"
            )
        event_key = str(intent.get("event_key") or f"intent:{intent_id}").strip()
        payload = copy.deepcopy(dict(intent.get("payload") or {}))
        payload.update(
            {
                "run_id": str(run["run_id"]),
                "request_id": run["request_id"],
                "turn_id": run["turn_id"],
                "workflow_name": run["workflow_name"],
                "workflow_version": run["workflow_version"],
            }
        )
        validate_json_value(payload, path="$.intent.payload")
        payload_json = canonical_json(payload)
        event_id = stable_event_id(str(run["run_id"]), event_key)
        existing = await (
            await db.execute(
                "SELECT * FROM workflow_events WHERE run_id=? AND event_key=?",
                (run["run_id"], event_key),
            )
        ).fetchone()
        if existing is None:
            seq = await (
                await db.execute(
                    "UPDATE workflow_runs SET event_seq=event_seq+1,updated_at=? "
                    "WHERE run_id=? RETURNING event_seq",
                    (now, run["run_id"]),
                )
            ).fetchone()
            assert seq is not None
            await db.execute(
                """INSERT INTO workflow_events(
                    event_id,event_key,run_id,seq,event_type,payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event_key,
                    run["run_id"],
                    int(seq["event_seq"]),
                    event_type,
                    payload_json,
                    now,
                ),
            )
        elif (
            str(existing["event_id"]) != event_id
            or str(existing["event_type"]) != event_type
            or canonical_json(json.loads(str(existing["payload_json"]))) != payload_json
        ):
            raise NativeCheckpointError(
                "outbox_intent_conflict", "intent_id is bound to different event content"
            )

        delivery_specs = list(intent.get("delivery_specs") or [])
        raw_deliveries = list(intent.get("deliveries") or [])
        manifest_ref: str | None = None
        if delivery_specs:
            # v6 terminal projections describe physical delivery without
            # embedding a session id in the immutable manifest.  Resolve both
            # roles against the run's original delivery binding; the
            # SessionDB/websocket handlers still enforce the captured epoch at
            # dispatch time, so a deleted or recreated session is never
            # revived by a late delivery.
            ref = await (
                await db.execute(
                    """SELECT session_id,session_epoch FROM workflow_session_refs
                    WHERE run_id=? AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run["run_id"],),
                )
            ).fetchone()
            if ref is None:
                raise NativeCheckpointError(
                    "delivery_session_not_bound",
                    "delivery_specs require an active delivery session binding",
                )
            manifest_ref = str(payload.get("manifest_ref") or "").strip()
            if not manifest_ref:
                raise NativeCheckpointError(
                    "invalid_delivery_intent",
                    "delivery_specs require payload.manifest_ref",
                )
            target_id = str(ref["session_id"])
            for spec in delivery_specs:
                if not isinstance(spec, Mapping):
                    raise NativeCheckpointError(
                        "invalid_delivery_intent", "delivery_specs must contain objects"
                    )
                target_role = str(spec.get("target_role") or "").strip().lower()
                if target_role not in {"original_session", "current_epoch"}:
                    raise NativeCheckpointError(
                        "invalid_delivery_intent",
                        f"unsupported delivery target_role: {target_role!r}",
                    )
                channel = str(spec.get("channel") or "").strip().lower()
                if not channel:
                    raise NativeCheckpointError(
                        "invalid_delivery_intent", "delivery spec channel is required"
                    )
                required_durable = spec.get("required_durable")
                if not isinstance(required_durable, bool):
                    raise NativeCheckpointError(
                        "invalid_delivery_intent",
                        "delivery spec required_durable must be boolean",
                    )
                raw_deliveries.append(
                    {
                        "channel": channel,
                        "target_id": target_id,
                        "required_durable": required_durable,
                    }
                )
        elif not raw_deliveries:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs
                    WHERE run_id=? AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run["run_id"],),
                )
            ).fetchone()
            if ref is not None:
                target_id = str(ref["session_id"])
                raw_deliveries.extend(
                    (
                        {"channel": "session_message", "target_id": target_id},
                        {"channel": "websocket", "target_id": target_id},
                    )
                )
                business_channel = str(intent.get("channel") or "").strip().lower()
                if business_channel in {"artifact", "receipt"}:
                    raw_deliveries.append({"channel": business_channel, "target_id": target_id})
                if business_channel == "final":
                    raw_deliveries.append({"channel": "receipt", "target_id": target_id})
        for delivery in raw_deliveries:
            channel = str(delivery["channel"]).strip().lower()
            target_id = str(delivery["target_id"]).strip()
            delivery_id = stable_delivery_id(event_id, channel, target_id)
            await db.execute(
                """INSERT INTO workflow_deliveries(
                    delivery_id,event_id,run_id,channel,target_id,status,created_at,updated_at,
                    intent_id,manifest_ref,required_durable
                ) VALUES(?,?,?,?,?,'pending',?,?,?,?,?)
                ON CONFLICT(event_id,channel,target_id) DO NOTHING""",
                (
                    delivery_id,
                    event_id,
                    run["run_id"],
                    channel,
                    target_id,
                    now,
                    now,
                    intent_id if delivery_specs else None,
                    manifest_ref,
                    1 if delivery.get("required_durable") is True else 0,
                ),
            )
        return event_id
