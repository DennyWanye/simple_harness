"""Connection, execution adapters, fences, operations and checkpoint rows."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from ...contracts import JsonValue, canonical_json, validate_json_value
from ...execution_ports import CheckpointExecutionAdapter
from ..run_store import RunFence, StaleRunFence
from ..schema import initialize_workflow_db
from .support import NATIVE_ENGINE_KIND, NativeCheckpointError, _native_mapping

NATIVE_CHECKPOINT_TYPE = "deskpet-native-json-v1"
NATIVE_SNAPSHOT_VERSION = 1


class NativeCheckpointCore:
    """Connection, execution adapters, fences, operations and checkpoint rows."""

    if TYPE_CHECKING:

        @staticmethod
        async def _consume_decisions(
            db: aiosqlite.Connection,
            *,
            run_id: str,
            decisions: Sequence[str | Mapping[str, Any]],
            checkpoint_id: str,
            now: float,
        ) -> list[str]: ...
        @staticmethod
        async def _materialize_intent(
            db: aiosqlite.Connection,
            *,
            run: Mapping[str, Any],
            intent: Mapping[str, Any],
            now: float,
        ) -> str: ...
        async def load_head(self, run_id: str) -> dict[str, Any] | None: ...

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
        execution_adapter: CheckpointExecutionAdapter | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._fault_injector = fault_injector
        self._execution_adapter = execution_adapter

    def configure_execution_adapter(self, adapter: CheckpointExecutionAdapter) -> None:
        """Bind the opt-in generic adapter before a test-only execution starts."""

        if self._execution_adapter is not None and self._execution_adapter is not adapter:
            raise ValueError("native checkpoint store already has another execution adapter")
        self._execution_adapter = adapter

    async def setup(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        await self.setup()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=FULL")
        return db

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def _execution_adapter_for(
        self, db: aiosqlite.Connection, run_id: str
    ) -> CheckpointExecutionAdapter | None:
        """Select by durable row presence; a new run may never fall back to legacy."""

        exists = await (
            await db.execute("SELECT 1 FROM execution_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if exists is None:
            return None
        if self._execution_adapter is None:
            raise NativeCheckpointError(
                "execution_adapter_required",
                "generic execution run cannot use legacy checkpoint ownership",
            )
        return self._execution_adapter

    @staticmethod
    def _stable_id(*parts: object) -> str:
        return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _request_hash(value: Mapping[str, JsonValue]) -> str:
        return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()

    @staticmethod
    def _fence_from_config(configurable: Mapping[str, Any]) -> RunFence:
        try:
            return RunFence(
                str(configurable["deskpet_run_id"]),
                str(configurable["deskpet_lease_owner"]),
                int(configurable["deskpet_lease_epoch"]),
                int(configurable["deskpet_run_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NativeCheckpointError(
                "missing_run_fence", "native commit requires the DeskPet run fence"
            ) from exc

    @staticmethod
    def _error_payload(error: object) -> dict[str, JsonValue]:
        to_envelope = getattr(error, "to_envelope", None)
        if callable(to_envelope):
            return copy.deepcopy(dict(to_envelope()))
        return _native_mapping(error, name="error")

    @staticmethod
    def _snapshot_object(payload: Mapping[str, Any]) -> object:
        # 按需导入：循环依赖（checkpointer ↔ native）
        from ...native import NativeSnapshotEnvelope, NativeTask

        completed = payload.get("completed_activations") or {}
        joins = payload.get("join_firings") or []
        writes = payload.get("node_writes") or {}
        return NativeSnapshotEnvelope(
            thread_id=str(payload["thread_id"]),
            checkpoint_ns=str(payload["checkpoint_ns"]),
            checkpoint_id=str(payload["checkpoint_id"]),
            parent_checkpoint_id=(
                str(payload["parent_checkpoint_id"])
                if payload.get("parent_checkpoint_id") is not None
                else None
            ),
            run_id=str(payload["run_id"]),
            state_schema_version=int(payload["state_schema_version"]),
            step=int(payload["step"]),
            state=copy.deepcopy(dict(payload["state"])),
            frontier=tuple(NativeTask(**dict(item)) for item in payload.get("frontier") or []),
            completed_activations={
                str(key): tuple(str(item) for item in value)
                for key, value in dict(completed).items()
            }
            if isinstance(completed, Mapping)
            else {},
            join_firings=tuple(str(item) for item in joins)
            if not isinstance(joins, Mapping)
            else tuple(joins),
            node_writes=copy.deepcopy(dict(writes)) if isinstance(writes, Mapping) else {},
            interrupt=(
                copy.deepcopy(dict(payload["interrupt"])) if payload.get("interrupt") else None
            ),
            metadata=copy.deepcopy(dict(payload.get("metadata") or {})),
        )

    @staticmethod
    async def _operation(
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        run_id: str,
        operation_kind: str,
        request_hash: str,
        request_conflict_code: str = "operation_identity_conflict",
    ) -> dict[str, Any] | None:
        row = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            return None
        if str(row["run_id"]) != run_id or str(row["operation_kind"]) != operation_kind:
            raise NativeCheckpointError(
                "operation_identity_conflict",
                "operation_id is already bound to different input",
                details={"operation_id": operation_id},
            )
        if str(row["request_hash"]) != request_hash:
            raise NativeCheckpointError(
                request_conflict_code,
                "operation_id is already bound to different input",
                details={"operation_id": operation_id},
            )
        return json.loads(str(row["result_json"]))

    @staticmethod
    async def _record_operation(
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        run_id: str,
        operation_kind: str,
        request_hash: str,
        result: Mapping[str, JsonValue],
        now: float,
    ) -> None:
        await db.execute(
            "INSERT INTO workflow_operations(operation_id,run_id,operation_kind,request_hash,result_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (operation_id, run_id, operation_kind, request_hash, canonical_json(dict(result)), now),
        )

    @staticmethod
    async def _run_row(db: aiosqlite.Connection, run_id: str) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            raise NativeCheckpointError("run_not_found", f"workflow run not found: {run_id}")
        return row

    @staticmethod
    def _assert_fence_row(row: Mapping[str, Any], fence: RunFence) -> None:
        if (
            str(row["status"]) != "running"
            or row["lease_owner"] != fence.owner
            or int(row["lease_epoch"]) != fence.lease_epoch
            or int(row["run_version"]) != fence.run_version
        ):
            raise StaleRunFence(f"stale native checkpoint writer: {fence.run_id}")

    @staticmethod
    def _assert_head(row: Mapping[str, Any], expected_head: str | None) -> None:
        current = row["head_checkpoint_id"]
        if current != expected_head:
            raise NativeCheckpointError(
                "stale_checkpoint_head",
                "workflow checkpoint head changed",
                details={"expected_head": expected_head, "current_head": current},
            )

    @staticmethod
    def _snapshot_payload(
        *,
        run: Mapping[str, Any],
        checkpoint_id: str,
        parent_checkpoint_id: str | None,
        step: int,
        state: Mapping[str, JsonValue],
        frontier: Sequence[Mapping[str, JsonValue]],
        completed_activations: Sequence[str] | Mapping[str, Sequence[str]] = (),
        join_firings: Sequence[str] | Mapping[str, JsonValue] = (),
        node_writes: Sequence[Mapping[str, JsonValue]] | Mapping[str, Mapping[str, JsonValue]] = (),
        interrupt: Mapping[str, JsonValue] | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "checkpoint_type": NATIVE_CHECKPOINT_TYPE,
            "engine_kind": NATIVE_ENGINE_KIND,
            "snapshot_version": NATIVE_SNAPSHOT_VERSION,
            "state_schema_version": int(run["state_schema_version"]),
            "thread_id": str(run["thread_id"]),
            "checkpoint_ns": str(run["checkpoint_ns"]),
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "run_id": str(run["run_id"]),
            "step": int(step),
            "state": copy.deepcopy(dict(state)),
            "frontier": [copy.deepcopy(dict(item)) for item in frontier],
            "completed_activations": (
                {key: list(value) for key, value in completed_activations.items()}
                if isinstance(completed_activations, Mapping)
                else list(completed_activations)
            ),
            "join_firings": (
                copy.deepcopy(dict(join_firings))
                if isinstance(join_firings, Mapping)
                else list(join_firings)
            ),
            "node_writes": (
                {key: copy.deepcopy(dict(value)) for key, value in node_writes.items()}
                if isinstance(node_writes, Mapping)
                else [copy.deepcopy(dict(item)) for item in node_writes]
            ),
            "interrupt": copy.deepcopy(dict(interrupt)) if interrupt is not None else None,
            "metadata": copy.deepcopy(dict(metadata or {})),
        }
        validate_json_value(payload, path="$.native_snapshot")
        return payload

    @staticmethod
    async def _insert_checkpoint(
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        snapshot: Mapping[str, JsonValue],
        now: float,
    ) -> None:
        checkpoint_id = str(snapshot["checkpoint_id"])
        parent = snapshot.get("parent_checkpoint_id")
        blob = canonical_json(dict(snapshot)).encode("utf-8")
        metadata_blob = canonical_json(dict(snapshot.get("metadata") or {})).encode("utf-8")
        await db.execute(
            """INSERT INTO workflow_checkpoints(
                thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run["thread_id"],
                run["checkpoint_ns"],
                checkpoint_id,
                parent,
                run["run_id"],
                NATIVE_CHECKPOINT_TYPE,
                blob,
                metadata_blob,
                NATIVE_ENGINE_KIND,
                NATIVE_SNAPSHOT_VERSION,
                now,
            ),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
                run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (run["run_id"], run["thread_id"], run["checkpoint_ns"], checkpoint_id, parent, now),
        )

    @staticmethod
    async def _write_pending(
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        base_checkpoint_id: str,
        task_id: str,
        write_index: int,
        write_kind: str,
        payload: Mapping[str, JsonValue],
        node_execution_id: str | None,
        task_path: str = "",
    ) -> bool:
        payload_json = canonical_json(dict(payload))
        existing = await (
            await db.execute(
                """SELECT write_kind,payload_json,node_execution_id FROM workflow_pending_writes
                WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?
                  AND task_id=? AND write_index=?""",
                (run["thread_id"], run["checkpoint_ns"], base_checkpoint_id, task_id, write_index),
            )
        ).fetchone()
        if existing is not None:
            if (
                str(existing["write_kind"] or "") == write_kind
                and str(existing["payload_json"] or "") == payload_json
                and existing["node_execution_id"] == node_execution_id
            ):
                return False
            raise NativeCheckpointError(
                "pending_write_conflict",
                "native pending write identity has different content",
                details={"task_id": task_id, "write_index": write_index},
            )
        await db.execute(
            """INSERT INTO workflow_pending_writes(
                thread_id,checkpoint_ns,base_checkpoint_id,task_id,write_index,
                channel,value_type,value_blob,task_path,write_kind,payload_json,node_execution_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run["thread_id"],
                run["checkpoint_ns"],
                base_checkpoint_id,
                task_id,
                write_index,
                f"__native__:{write_kind}",
                "json",
                payload_json.encode("utf-8"),
                task_path,
                write_kind,
                payload_json,
                node_execution_id,
            ),
        )
        return True
