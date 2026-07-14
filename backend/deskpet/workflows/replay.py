"""Read-only checkpoint replay and crash-recoverable root checkpoint forks."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .contracts import JsonValue, canonical_json, validate_json_value
from .errors import WorkflowContractError
from .store.checkpointer import (
    NATIVE_ENGINE_KIND,
    LegacyCheckpointStore,
    NativeCheckpointStore,
)
from .store.run_store import ForkPreparationError, WorkflowRunStore


_RUN_NAMESPACE = uuid.UUID("43e3158d-908d-49f6-881d-f830c72fba40")
_TRACE_NAMESPACE = uuid.UUID("c927ec1a-b5fa-4a73-b0b2-90d57d8f23c8")
_CHECKPOINT_NAMESPACE = uuid.UUID("a949969a-f687-4b92-9a92-8f1ed0cb1323")
_RESERVED_PATCH_KEYS = frozenset(
    {
        "schema_version",
        "workflow_name",
        "workflow_version",
        "thread_id",
        "run_id",
        "session_id",
        "trace_id",
        "parent_run_id",
        "source_checkpoint_id",
    }
)


class WorkflowReplayError(WorkflowContractError):
    """Stable replay/fork rejection exposed through the service and IPC."""


def deterministic_fork_key(
    *,
    source_run_id: str,
    source_checkpoint_ns: str,
    source_checkpoint_id: str,
    source_version: int,
    state_patch: Mapping[str, JsonValue],
) -> str:
    payload: dict[str, JsonValue] = {
        "source_run_id": source_run_id,
        "source_checkpoint_ns": source_checkpoint_ns,
        "source_checkpoint_id": source_checkpoint_id,
        "source_version": source_version,
        "state_patch": copy.deepcopy(dict(state_patch)),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class WorkflowReplay:
    """Coordinates immutable history reads and the fork request saga."""

    def __init__(
        self,
        store: WorkflowRunStore,
        saver: LegacyCheckpointStore | NativeCheckpointStore,
        registry: object,
        *,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.saver = saver
        self.registry = registry
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def history(self, run_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return checkpoint history without changing run or recovery state."""

        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"workflow run not found: {run_id}")
        if limit is not None and limit < 0:
            raise ValueError("history limit cannot be negative")
        db = await self.store._connect()
        try:
            query = """SELECT c.* FROM workflow_checkpoint_owners o
                JOIN workflow_checkpoints c ON c.thread_id=o.thread_id
                  AND c.checkpoint_ns=o.checkpoint_ns AND c.checkpoint_id=o.checkpoint_id
                WHERE o.run_id=? ORDER BY o.created_at DESC,o.checkpoint_id DESC"""
            params: list[Any] = [run_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = [dict(row) for row in await (await db.execute(query, params)).fetchall()]
        finally:
            await db.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("engine_kind") or "") == NATIVE_ENGINE_KIND:
                if not isinstance(self.saver, NativeCheckpointStore):
                    native_saver = NativeCheckpointStore(self.store.path)
                else:
                    native_saver = self.saver
                snapshot = await native_saver.get_checkpoint(
                    run_id,
                    str(row["checkpoint_id"]),
                    checkpoint_ns=str(row["checkpoint_ns"]),
                )
                if snapshot is None:
                    continue
                result.append(
                    {
                        "checkpoint_id": str(row["checkpoint_id"]),
                        "checkpoint_ns": str(row["checkpoint_ns"]),
                        "parent_checkpoint_id": row["parent_checkpoint_id"],
                        "state": snapshot,
                        "metadata": dict(snapshot.get("metadata") or {}),
                        "pending_writes": list(snapshot.get("node_writes") or []),
                        "engine_kind": NATIVE_ENGINE_KIND,
                    }
                )
                continue
            if isinstance(self.saver, NativeCheckpointStore):
                legacy_saver = LegacyCheckpointStore(self.store.path)
            else:
                legacy_saver = self.saver
            item = await legacy_saver.aget_tuple(
                {
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["checkpoint_id"],
                        "deskpet_run_id": run_id,
                    }
                }
            )
            if item is None:
                continue
            result.append(
                {
                    "checkpoint_id": str(row["checkpoint_id"]),
                    "checkpoint_ns": str(row["checkpoint_ns"]),
                    "parent_checkpoint_id": row["parent_checkpoint_id"],
                    "state": item.checkpoint,
                    "metadata": item.metadata,
                    "pending_writes": list(item.pending_writes or []),
                }
            )
        return result

    async def fork_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint_id: str,
        expected_version: int,
        state_patch: Mapping[str, JsonValue] | None = None,
        fork_key: str | None = None,
        confirm_dangerous_effects: bool = False,
    ) -> dict[str, Any]:
        if isinstance(expected_version, bool) or expected_version < 0:
            raise WorkflowReplayError(
                "fork_invalid_version", "expected_version must be a non-negative integer"
            )
        patch = copy.deepcopy(dict(state_patch or {}))
        validate_json_value(patch, path="$.state_patch")
        source = await self.store.get_run(run_id)
        if source is None:
            raise WorkflowReplayError("fork_source_not_found", f"workflow run not found: {run_id}")
        namespace = str(source["checkpoint_ns"])
        calculated_key = deterministic_fork_key(
            source_run_id=run_id,
            source_checkpoint_ns=namespace,
            source_checkpoint_id=checkpoint_id,
            source_version=expected_version,
            state_patch=patch,
        )
        if fork_key is not None and fork_key != calculated_key:
            raise WorkflowReplayError(
                "fork_key_mismatch", "fork_key does not match the deterministic request identity"
            )
        resolved_key = calculated_key
        child_run_id = uuid.uuid5(_RUN_NAMESPACE, resolved_key).hex
        child_trace_id = uuid.uuid5(_TRACE_NAMESPACE, resolved_key).hex
        child_checkpoint_id = str(uuid.uuid5(_CHECKPOINT_NAMESPACE, resolved_key))
        request_payload: dict[str, JsonValue] = {
            "source_version": expected_version,
            "patch_hash": hashlib.sha256(canonical_json(patch).encode("utf-8")).hexdigest(),
            "patch": patch,
        }
        existing_saga = await self.store.get_fork_request(resolved_key)
        if existing_saga is None:
            if int(source["run_version"]) != expected_version:
                raise WorkflowReplayError(
                    "fork_source_version_conflict",
                    "source workflow run version changed",
                    details={"current_version": int(source["run_version"])},
                )
            require = getattr(self.registry, "require")
            try:
                registration = require(
                    str(source["workflow_name"]),
                    str(source["workflow_version"]),
                    expected_manifest_hash=str(source["manifest_hash"]),
                    expected_implementation_hash=str(source["implementation_hash"]),
                )
            except Exception as exc:
                raise WorkflowReplayError(
                    "fork_workflow_version_unavailable",
                    "the source workflow implementation is unavailable or changed",
                ) from exc

            owner_rows = await self._checkpoint_owners(run_id, checkpoint_id)
            if not owner_rows:
                raise WorkflowReplayError(
                    "fork_checkpoint_not_found", "checkpoint does not belong to the source run"
                )
            if len(owner_rows) != 1:
                raise WorkflowReplayError(
                    "fork_checkpoint_ambiguous", "checkpoint id exists in multiple namespaces"
                )
            namespace = str(owner_rows[0]["checkpoint_ns"])
            if namespace:
                raise WorkflowReplayError(
                    "fork_namespace_unsupported",
                    "v1 fork supports only the root checkpoint namespace",
                )
            engine_kind = await self._checkpoint_engine_kind(
                run_id, namespace, checkpoint_id
            )
            if engine_kind == NATIVE_ENGINE_KIND:
                native_saver = (
                    self.saver
                    if isinstance(self.saver, NativeCheckpointStore)
                    else NativeCheckpointStore(self.store.path)
                )
                snapshot = await native_saver.get_checkpoint(
                    run_id, checkpoint_id, checkpoint_ns=namespace
                )
                if snapshot is None:
                    raise WorkflowReplayError(
                        "fork_checkpoint_not_found", "source checkpoint is missing"
                    )
                if len(list(snapshot.get("frontier") or [])) > 1:
                    raise WorkflowReplayError(
                        "fork_fanout_unsupported", "fan-out checkpoints cannot be forked"
                    )
                if await self._has_native_pending(run_id, namespace, checkpoint_id):
                    raise WorkflowReplayError(
                        "fork_pending_writes_unsupported",
                        "checkpoints with pending writes cannot be forked",
                    )
                state = dict(snapshot.get("state") or {})
                self._validate_source_identity(source, state, registration)
                self._validate_patch(patch, state)
            else:
                legacy_saver = (
                    LegacyCheckpointStore(self.store.path)
                    if isinstance(self.saver, NativeCheckpointStore)
                    else self.saver
                )
                item = await legacy_saver.aget_tuple(
                {
                    "configurable": {
                        "thread_id": source["thread_id"],
                        "checkpoint_ns": namespace,
                        "checkpoint_id": checkpoint_id,
                        "deskpet_run_id": run_id,
                    }
                }
                )
                if item is None:
                    raise WorkflowReplayError("fork_checkpoint_not_found", "source checkpoint is missing")
                if item.pending_writes:
                    raise WorkflowReplayError(
                        "fork_pending_writes_unsupported",
                        "checkpoints with pending writes cannot be forked in v1",
                    )
                checkpoint = item.checkpoint
                if checkpoint.get("pending_sends"):
                    raise WorkflowReplayError(
                        "fork_fanout_unsupported", "fan-out checkpoints cannot be forked in v1"
                    )
                channel_values = dict(checkpoint.get("channel_values") or {})
                active_nodes = channel_values.get("active_nodes")
                if isinstance(active_nodes, list) and len(active_nodes) > 1:
                    raise WorkflowReplayError(
                        "fork_fanout_unsupported", "fan-out checkpoints cannot be forked in v1"
                    )
                self._validate_source_identity(source, channel_values, registration)
                self._validate_patch(patch, channel_values)
        try:
            _, prepared = await self.store.prepare_fork(
                fork_key=resolved_key,
                source_run_id=run_id,
                source_checkpoint_ns=namespace,
                source_checkpoint_id=checkpoint_id,
                expected_version=expected_version,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
                state_patch_json=canonical_json(request_payload),
                confirm_dangerous_effects=confirm_dangerous_effects,
            )
        except ForkPreparationError as exc:
            raise WorkflowReplayError(exc.code, str(exc), details=exc.details) from exc
        await self._fault("after_prepared")
        await self._fault("before_checkpoint_commit")
        engine_kind = await self._checkpoint_engine_kind(run_id, namespace, checkpoint_id)
        if engine_kind == NATIVE_ENGINE_KIND:
            native_saver = (
                self.saver
                if isinstance(self.saver, NativeCheckpointStore)
                else NativeCheckpointStore(self.store.path)
            )
            committed = await native_saver.commit_native_fork(resolved_key, child_checkpoint_id)
        else:
            legacy_saver = (
                LegacyCheckpointStore(self.store.path)
                if isinstance(self.saver, NativeCheckpointStore)
                else self.saver
            )
            committed = await legacy_saver.afork_checkpoint(resolved_key, child_checkpoint_id)
        await self._fault("after_checkpoint_commit")
        child = await self.store.get_run(child_run_id)
        assert child is not None
        current_saga = await self.store.get_fork_request(resolved_key)
        assert current_saga is not None
        return {
            "fork_key": resolved_key,
            "source_run_id": run_id,
            "source_checkpoint_ns": namespace,
            "source_checkpoint_id": checkpoint_id,
            "run_id": child_run_id,
            "trace_id": str(child["trace_id"]),
            "thread_id": str(child["thread_id"]),
            "checkpoint_ns": committed["checkpoint_ns"],
            "checkpoint_id": committed["checkpoint_id"],
            "status": str(child["status"]),
            "saga_status": str(current_saga["status"]),
            "created": prepared,
            "idempotent": not prepared,
        }

    async def _checkpoint_owners(self, run_id: str, checkpoint_id: str) -> list[dict[str, Any]]:
        db = await self.store._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_checkpoint_owners
                    WHERE run_id=? AND checkpoint_id=? ORDER BY checkpoint_ns""",
                    (run_id, checkpoint_id),
                )
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def _checkpoint_engine_kind(
        self, run_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> str | None:
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT c.engine_kind FROM workflow_checkpoint_owners o
                    JOIN workflow_checkpoints c USING(thread_id,checkpoint_ns,checkpoint_id)
                    WHERE o.run_id=? AND o.checkpoint_ns=? AND o.checkpoint_id=?""",
                    (run_id, checkpoint_ns, checkpoint_id),
                )
            ).fetchone()
            return None if row is None or row["engine_kind"] is None else str(row["engine_kind"])
        finally:
            await db.close()

    async def _has_native_pending(
        self, run_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> bool:
        run = await self.store.get_run(run_id)
        if run is None:
            return False
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_pending_writes WHERE thread_id=?
                    AND checkpoint_ns=? AND base_checkpoint_id=? AND write_kind IS NOT NULL LIMIT 1""",
                    (run["thread_id"], checkpoint_ns, checkpoint_id),
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    @staticmethod
    def _validate_source_identity(
        source: Mapping[str, Any], channel_values: Mapping[str, Any], registration: object
    ) -> None:
        expected = {
            "schema_version": int(source["state_schema_version"]),
            "workflow_name": str(source["workflow_name"]),
            "workflow_version": str(source["workflow_version"]),
            "thread_id": str(source["thread_id"]),
            "run_id": str(source["run_id"]),
        }
        manifest = getattr(registration, "manifest")
        if int(getattr(manifest, "state_schema_version")) != expected["schema_version"]:
            raise WorkflowReplayError(
                "fork_state_schema_mismatch", "source state schema does not match the workflow manifest"
            )
        for key, value in expected.items():
            if key in channel_values and channel_values[key] != value:
                raise WorkflowReplayError(
                    "fork_source_identity_mismatch",
                    f"source checkpoint {key} does not match its run identity",
                )

    @staticmethod
    def _validate_patch(patch: Mapping[str, JsonValue], channel_values: Mapping[str, Any]) -> None:
        for key, value in patch.items():
            if key in _RESERVED_PATCH_KEYS:
                raise WorkflowReplayError(
                    "fork_reserved_identity_patch", f"state_patch cannot write runtime identity: {key}"
                )
            if key.startswith("$") or "/" in key or "." in key:
                raise WorkflowReplayError(
                    "fork_non_root_patch_unsupported",
                    "v1 fork accepts only root state channel replacements",
                )
            if key not in channel_values:
                raise WorkflowReplayError(
                    "fork_unknown_state_channel", f"state_patch channel does not exist: {key}"
                )
            current = channel_values[key]
            if current is not None and value is not None:
                current_type = type(current)
                value_type = type(value)
                numeric_pair = current_type in {int, float} and value_type in {int, float}
                if current_type is not value_type and not numeric_pair:
                    raise WorkflowReplayError(
                        "fork_state_channel_type_mismatch",
                        f"state_patch changes the type of channel: {key}",
                    )


__all__ = [
    "WorkflowReplay",
    "WorkflowReplayError",
    "deterministic_fork_key",
]
