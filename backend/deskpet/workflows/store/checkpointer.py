"""DeskPet-native checkpoint protocol backed by fenced ``workflow.db`` writes."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from ..execution_ports import CheckpointExecutionAdapter

# Local type shims keep the removed protocol class importable for old database
# diagnostics. Production construction is aliased to NativeCheckpointStore at
# the end of this module, so these shims never execute workflow code.
WRITES_IDX_MAP: dict[str, int] = {}
ChannelVersions = Checkpoint = CheckpointMetadata = RunnableConfig = Any


@dataclass(frozen=True)
class CheckpointTuple:
    config: dict[str, Any]
    checkpoint: dict[str, Any]
    metadata: dict[str, Any]
    parent_config: dict[str, Any] | None
    pending_writes: list[tuple[str, str, Any]]


class BaseCheckpointSaver:
    @classmethod
    def __class_getitem__(cls, _item: object) -> type["BaseCheckpointSaver"]:
        return cls

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.serde = _kwargs.get("serde") or _StrictJsonSerializer()

    @staticmethod
    def get_next_version(current: object, _channel: object) -> int:
        try:
            return int(current or 0) + 1
        except (TypeError, ValueError):
            return 1


class _StrictJsonSerializer:
    """Small JSON codec for reading pre-native DeskPet checkpoints."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def dumps_typed(self, value: object) -> tuple[str, bytes]:
        validate_json_value(value, path="$.legacy_checkpoint")
        return "json", canonical_json(value).encode("utf-8")

    def loads_typed(self, value: object) -> object:
        value_type, blob = value  # type: ignore[misc]
        if str(value_type) not in {"json", "msgpack"}:
            raise ValueError(f"unsupported legacy checkpoint value type: {value_type}")
        return json.loads(bytes(blob).decode("utf-8"))


JsonPlusSerializer = _StrictJsonSerializer


def get_checkpoint_id(config: object) -> str | None:
    if isinstance(config, Mapping):
        values = config.get("configurable")
        if isinstance(values, Mapping) and values.get("checkpoint_id"):
            return str(values["checkpoint_id"])
    return None


def get_checkpoint_metadata(_config: object, metadata: object) -> dict[str, Any]:
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def empty_legacy_checkpoint() -> dict[str, Any]:
    """Create the old graph-shaped envelope used only by migration tests."""

    return {
        "v": 1,
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }

from ..errors import AsyncOnlyWorkflowError, UnsupportedDeltaChannelError
from ..contracts import JsonValue, canonical_json, validate_json_value
from ..errors import WorkflowContractError
from .run_store import RunFence, StaleRunFence
from .schema import initialize_workflow_db


def _config_values(config: RunnableConfig) -> dict[str, Any]:
    values = dict(config.get("configurable") or {})
    if not values.get("thread_id"):
        raise ValueError("workflow checkpoint config requires thread_id")
    values.setdefault("checkpoint_ns", "")
    return values


def _graph_interrupts(
    writes: Sequence[tuple[str, Any]],
) -> list[tuple[str, Any, str, float | None]]:
    interrupts: list[tuple[str, Any, str, float | None]] = []
    for channel, value in writes:
        if channel != "__interrupt__":
            continue
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            interrupt_id = str(getattr(item, "id", "")).strip()
            if not interrupt_id:
                raise ValueError("graph interrupt write requires an interrupt id")
            prompt = getattr(item, "value", None)
            kind = "human_decision"
            expires_at: float | None = None
            if isinstance(prompt, Mapping):
                kind = str(prompt.get("kind") or kind).strip() or kind
                raw_expiry = prompt.get("expires_at")
                if raw_expiry is not None:
                    if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (int, float)):
                        raise ValueError("graph interrupt expires_at must be a number")
                    expires_at = float(raw_expiry)
            interrupts.append((interrupt_id, prompt, kind, expires_at))
    if len(interrupts) > 1:
        raise ValueError("an exclusive workflow superstep may persist only one interrupt")
    return interrupts


class FencedAsyncSqliteSaver(BaseCheckpointSaver[int]):
    """Async-only saver whose writes require a current DeskPet run fence."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.path = Path(path)

    def __getattr__(self, name: str) -> Any:
        native_methods = {
            "ensure_genesis", "load_execution", "commit_task_result",
            "commit_route_selection", "commit_frontier", "commit_retry",
            "commit_interrupt", "commit_failure", "commit_engine_failure",
        }
        if name in native_methods:
            return getattr(NativeCheckpointStore(self.path), name)
        raise AttributeError(name)

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

    @staticmethod
    def _fence(values: dict[str, Any]) -> tuple[str, str, int, int]:
        try:
            return (
                str(values["deskpet_run_id"]),
                str(values["deskpet_lease_owner"]),
                int(values["deskpet_lease_epoch"]),
                int(values["deskpet_run_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("workflow checkpoint write requires a complete DeskPet run fence") from exc

    @staticmethod
    async def _assert_fence(
        db: aiosqlite.Connection,
        run_id: str,
        owner: str,
        epoch: int,
        version: int,
    ) -> None:
        row = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=?
                AND lease_epoch=? AND run_version=? AND status='running'""",
                (run_id, owner, epoch, version),
            )
        ).fetchone()
        if row is None:
            raise StaleRunFence(f"stale checkpoint writer: {run_id}")

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        values = _config_values(config)
        thread_id = str(values["thread_id"])
        namespace = str(values["checkpoint_ns"])
        checkpoint_id = get_checkpoint_id(config)
        run_id = values.get("deskpet_run_id")
        db = await self._connect()
        try:
            if checkpoint_id:
                row = await (
                    await db.execute(
                        """SELECT * FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        (thread_id, namespace, str(checkpoint_id)),
                    )
                ).fetchone()
            elif run_id:
                row = await (
                    await db.execute(
                        """SELECT c.* FROM workflow_runs r JOIN workflow_checkpoints c
                        ON c.thread_id=r.thread_id AND c.checkpoint_ns=r.head_checkpoint_ns
                        AND c.checkpoint_id=r.head_checkpoint_id WHERE r.run_id=?""",
                        (str(run_id),),
                    )
                ).fetchone()
            else:
                row = await (
                    await db.execute(
                        """SELECT * FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=?
                        ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1""",
                        (thread_id, namespace),
                    )
                ).fetchone()
            if row is None:
                return None
            resolved_config: RunnableConfig = {
                "configurable": {
                    **values,
                    "thread_id": row["thread_id"],
                    "checkpoint_ns": row["checkpoint_ns"],
                    "checkpoint_id": row["checkpoint_id"],
                }
            }
            pending_rows = await (
                await db.execute(
                    """SELECT task_id,channel,value_type,value_blob FROM workflow_pending_writes
                    WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?
                    ORDER BY task_id,write_index""",
                    (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
                )
            ).fetchall()
            parent = None
            if row["parent_checkpoint_id"]:
                parent = {
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }
            return CheckpointTuple(
                resolved_config,
                self.serde.loads_typed((row["checkpoint_type"], row["checkpoint_blob"])),
                json.loads(bytes(row["metadata_blob"]).decode("utf-8")),
                parent,
                [
                    (item["task_id"], item["channel"], self.serde.loads_typed((item["value_type"], item["value_blob"])))
                    for item in pending_rows
                ],
            )
        finally:
            await db.close()

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        values = _config_values(config or {"configurable": {"thread_id": "*"}})
        clauses: list[str] = []
        params: list[Any] = []
        if values["thread_id"] != "*":
            clauses.extend(["thread_id=?", "checkpoint_ns=?"])
            params.extend([str(values["thread_id"]), str(values["checkpoint_ns"])])
        if before and get_checkpoint_id(before):
            clauses.append("checkpoint_id<?")
            params.append(str(get_checkpoint_id(before)))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT thread_id,checkpoint_ns,checkpoint_id FROM workflow_checkpoints {where} ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        db = await self._connect()
        try:
            rows = await (await db.execute(query, params)).fetchall()
        finally:
            await db.close()
        for row in rows:
            item = await self.aget_tuple(
                {
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["checkpoint_id"],
                    }
                }
            )
            if item is not None and (not filter or all(item.metadata.get(k) == v for k, v in filter.items())):
                yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        values = _config_values(config)
        run_id, owner, epoch, version = self._fence(values)
        thread_id = str(values["thread_id"])
        namespace = str(values["checkpoint_ns"])
        checkpoint_id = str(checkpoint["id"])
        parent_id = values.get("checkpoint_id")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_blob = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        now = time.time()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, run_id, owner, epoch, version)
            head = await (
                await db.execute(
                    "SELECT head_checkpoint_ns,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            assert head is not None
            current_head = head["head_checkpoint_id"]
            if current_head is not None and (
                str(head["head_checkpoint_ns"]) != namespace
                or str(current_head) not in {str(parent_id or ""), checkpoint_id}
            ):
                raise StaleRunFence(f"checkpoint parent is not the current branch head: {run_id}")
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                    thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                    checkpoint_type,checkpoint_blob,metadata_blob,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(thread_id,checkpoint_ns,checkpoint_id) DO UPDATE SET
                    checkpoint_blob=excluded.checkpoint_blob,metadata_blob=excluded.metadata_blob""",
                (thread_id, namespace, checkpoint_id, parent_id, run_id, checkpoint_type, checkpoint_blob, metadata_blob, now),
            )
            await db.execute(
                """INSERT OR IGNORE INTO workflow_checkpoint_owners(
                    run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (run_id, thread_id, namespace, checkpoint_id, parent_id, now),
            )
            cursor = await db.execute(
                """UPDATE workflow_runs SET head_checkpoint_id=?,head_checkpoint_ns=?,updated_at=?
                WHERE run_id=? AND lease_owner=? AND lease_epoch=? AND run_version=? AND status='running'""",
                (checkpoint_id, namespace, now, run_id, owner, epoch, version),
            )
            if cursor.rowcount != 1:
                raise StaleRunFence(f"stale checkpoint commit: {run_id}")
            await db.execute(
                """UPDATE workflow_node_attempts SET status='succeeded',ended_at=?
                WHERE node_execution_id IN (
                    SELECT node_execution_id FROM workflow_nodes WHERE run_id=? AND latest_status='succeeded_pending'
                ) AND status='succeeded_pending'""",
                (now, run_id),
            )
            await db.execute(
                "UPDATE workflow_nodes SET latest_status='succeeded',updated_at=? WHERE run_id=? AND latest_status='succeeded_pending'",
                (now, run_id),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
        return {
            "configurable": {
                **values,
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def afork_checkpoint(self, fork_key: str, child_checkpoint_id: str) -> dict[str, str]:
        """Commit a prepared root fork checkpoint and saga status atomically."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            saga = await (
                await db.execute(
                    "SELECT * FROM workflow_fork_requests WHERE fork_key=?", (fork_key,)
                )
            ).fetchone()
            if saga is None:
                raise ValueError(f"fork request not found: {fork_key}")
            child = await (
                await db.execute(
                    "SELECT * FROM workflow_runs WHERE run_id=?", (saga["child_run_id"],)
                )
            ).fetchone()
            if child is None:
                raise ValueError(f"fork child run not found: {saga['child_run_id']}")
            namespace = str(saga["source_checkpoint_ns"])
            if namespace:
                raise ValueError("v1 fork supports only the root checkpoint namespace")

            committed = await (
                await db.execute(
                    """SELECT checkpoint_id FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? AND run_id=?""",
                    (child["thread_id"], namespace, child_checkpoint_id, child["run_id"]),
                )
            ).fetchone()
            if committed is not None:
                now = time.time()
                await db.execute(
                    """UPDATE workflow_runs SET head_checkpoint_ns=?,head_checkpoint_id=?,updated_at=?
                    WHERE run_id=? AND (head_checkpoint_id IS NULL OR head_checkpoint_id=?)""",
                    (namespace, child_checkpoint_id, now, child["run_id"], child_checkpoint_id),
                )
                await db.execute(
                    "UPDATE workflow_fork_requests SET status='checkpointed',updated_at=? WHERE fork_key=?",
                    (now, fork_key),
                )
                await db.commit()
                return {
                    "run_id": str(child["run_id"]),
                    "checkpoint_ns": namespace,
                    "checkpoint_id": child_checkpoint_id,
                }

            if saga["status"] != "prepared":
                raise ValueError(f"unsupported fork saga status: {saga['status']}")
            source = await (
                await db.execute(
                    """SELECT * FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                    (child["thread_id"], namespace, saga["source_checkpoint_id"]),
                )
            ).fetchone()
            if source is None:
                raise ValueError("fork source checkpoint is missing")
            source_checkpoint = self.serde.loads_typed(
                (source["checkpoint_type"], source["checkpoint_blob"])
            )
            checkpoint = copy.deepcopy(source_checkpoint)
            if checkpoint.get("pending_sends"):
                raise ValueError("fan-out checkpoints cannot be forked in v1")
            payload = json.loads(saga["state_patch_json"] or "{}")
            patch = dict(payload.get("patch") or {})
            channel_values = dict(checkpoint.get("channel_values") or {})
            changed = set(patch)
            channel_values.update(copy.deepcopy(patch))
            identities = {
                "thread_id": str(child["thread_id"]),
                "run_id": str(child["run_id"]),
            }
            for key, value in identities.items():
                if channel_values.get(key) != value:
                    channel_values[key] = value
                    changed.add(key)
            optional_identities = {
                "trace_id": str(child["trace_id"]),
                "parent_run_id": str(saga["source_run_id"]),
                "source_checkpoint_id": str(saga["source_checkpoint_id"]),
            }
            for key, value in optional_identities.items():
                if key in channel_values and channel_values.get(key) != value:
                    channel_values[key] = value
                    changed.add(key)
            versions = dict(checkpoint.get("channel_versions") or {})
            for channel in changed:
                versions[channel] = self.get_next_version(versions.get(channel), None)
            checkpoint["id"] = child_checkpoint_id
            checkpoint["ts"] = datetime.now(timezone.utc).isoformat()
            checkpoint["channel_values"] = channel_values
            checkpoint["channel_versions"] = versions
            checkpoint["pending_sends"] = []
            checkpoint["updated_channels"] = sorted(changed)

            metadata = json.loads(bytes(source["metadata_blob"]).decode("utf-8"))
            metadata.update(
                {
                    "source": "fork",
                    "fork_key": fork_key,
                    "deskpet_run_id": str(child["run_id"]),
                    "deskpet_parent_run_id": str(saga["source_run_id"]),
                    "deskpet_source_checkpoint_id": str(saga["source_checkpoint_id"]),
                }
            )
            checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
            metadata_blob = json.dumps(
                metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            now = time.time()
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                    thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                    checkpoint_type,checkpoint_blob,metadata_blob,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    child["thread_id"],
                    namespace,
                    child_checkpoint_id,
                    saga["source_checkpoint_id"],
                    child["run_id"],
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_blob,
                    now,
                ),
            )
            await db.execute(
                """WITH RECURSIVE ancestors(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at) AS (
                    SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                    FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
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
                (child["thread_id"], namespace, saga["source_checkpoint_id"], child["run_id"]),
            )
            await db.execute(
                """INSERT INTO workflow_checkpoint_owners(
                    run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    child["run_id"],
                    child["thread_id"],
                    namespace,
                    child_checkpoint_id,
                    saga["source_checkpoint_id"],
                    now,
                ),
            )
            cursor = await db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=?,head_checkpoint_id=?,updated_at=?
                WHERE run_id=? AND head_checkpoint_id IS NULL AND status='created'""",
                (namespace, child_checkpoint_id, now, child["run_id"]),
            )
            if cursor.rowcount != 1:
                raise StaleRunFence(f"fork child head changed before commit: {child['run_id']}")
            await db.execute(
                """UPDATE workflow_fork_requests SET status='checkpointed',updated_at=?
                WHERE fork_key=? AND status='prepared'""",
                (now, fork_key),
            )
            await db.commit()
            return {
                "run_id": str(child["run_id"]),
                "checkpoint_ns": namespace,
                "checkpoint_id": child_checkpoint_id,
            }
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        values = _config_values(config)
        run_id, owner, epoch, version = self._fence(values)
        checkpoint_id = str(values.get("checkpoint_id") or "")
        if not checkpoint_id:
            raise ValueError("pending writes require checkpoint_id")
        interrupts = _graph_interrupts(writes)
        replace = all(channel in WRITES_IDX_MAP for channel, _ in writes)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, run_id, owner, epoch, version)
            for index, (channel, value) in enumerate(writes):
                value_type, value_blob = self.serde.dumps_typed(value)
                write_index = WRITES_IDX_MAP.get(channel, index)
                verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
                await db.execute(
                    f"""{verb} INTO workflow_pending_writes(
                        thread_id,checkpoint_ns,base_checkpoint_id,task_id,write_index,
                        channel,value_type,value_blob,task_path
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        str(values["thread_id"]),
                        str(values["checkpoint_ns"]),
                        checkpoint_id,
                        task_id,
                        write_index,
                        channel,
                        value_type,
                        value_blob,
                        task_path,
                    ),
                )
            if interrupts:
                from ..human import HumanDecisionStore

                interrupt_id, prompt, kind, expires_at = interrupts[0]
                await HumanDecisionStore.open_graph_interrupt_in_transaction(
                    db,
                    fence=RunFence(run_id, owner, epoch, version),
                    interrupt_id=interrupt_id,
                    prompt=prompt,
                    checkpoint_id=checkpoint_id,
                    checkpoint_ns=str(values["checkpoint_ns"]),
                    task_id=task_id,
                    kind=kind,
                    expires_at=expires_at,
                )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def adelete_thread(self, thread_id: str) -> None:
        """Only delete an unshared thread; shared lineage must use run-scoped GC."""

        db = await self._connect()
        try:
            owners = await (
                await db.execute(
                    "SELECT COUNT(DISTINCT run_id) AS n FROM workflow_checkpoint_owners WHERE thread_id=?",
                    (thread_id,),
                )
            ).fetchone()
            if owners and int(owners["n"]) > 1:
                raise ValueError("cannot delete a shared workflow thread")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM workflow_pending_writes WHERE thread_id=?", (thread_id,))
            await db.execute("DELETE FROM workflow_checkpoint_owners WHERE thread_id=?", (thread_id,))
            await db.execute("DELETE FROM workflow_checkpoints WHERE thread_id=?", (thread_id,))
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        """Remove run ownership and collect checkpoints no remaining run owns."""

        if not run_ids:
            return
        placeholders = ",".join("?" for _ in run_ids)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            candidates = await (
                await db.execute(
                    f"""SELECT DISTINCT thread_id,checkpoint_ns,checkpoint_id
                    FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})""",
                    tuple(run_ids),
                )
            ).fetchall()
            await db.execute(
                f"DELETE FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})",
                tuple(run_ids),
            )
            for row in candidates:
                still_owned = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_checkpoint_owners
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? LIMIT 1""",
                        (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
                    )
                ).fetchone()
                if still_owned is None:
                    await db.execute(
                        """DELETE FROM workflow_pending_writes
                        WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?""",
                        (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
                    )
                    await db.execute(
                        """DELETE FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
                    )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        if source_thread_id == target_thread_id:
            return
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            target_exists = await (
                await db.execute("SELECT 1 FROM workflow_checkpoints WHERE thread_id=? LIMIT 1", (target_thread_id,))
            ).fetchone()
            if target_exists:
                raise ValueError(f"target checkpoint thread already exists: {target_thread_id}")
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                    thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                    checkpoint_type,checkpoint_blob,metadata_blob,created_at
                ) SELECT ?,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                    checkpoint_type,checkpoint_blob,metadata_blob,created_at
                  FROM workflow_checkpoints WHERE thread_id=?""",
                (target_thread_id, source_thread_id),
            )
            await db.execute(
                """INSERT INTO workflow_pending_writes(
                    thread_id,checkpoint_ns,base_checkpoint_id,task_id,write_index,channel,value_type,value_blob,task_path
                ) SELECT ?,checkpoint_ns,base_checkpoint_id,task_id,write_index,channel,value_type,value_blob,task_path
                  FROM workflow_pending_writes WHERE thread_id=?""",
                (target_thread_id, source_thread_id),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def aprune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy not in {"keep_latest", "delete"}:
            raise ValueError(f"unsupported checkpoint prune strategy: {strategy}")
        for thread_id in thread_ids:
            if strategy == "delete":
                await self.adelete_thread(thread_id)
                continue
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                keep = await (
                    await db.execute(
                        """SELECT checkpoint_ns,checkpoint_id FROM workflow_checkpoints
                        WHERE thread_id=? ORDER BY checkpoint_ns,created_at DESC,checkpoint_id DESC""",
                        (thread_id,),
                    )
                ).fetchall()
                keep_ids: set[tuple[str, str]] = set()
                remove: list[tuple[str, str]] = []
                for row in keep:
                    key = (str(row["checkpoint_ns"]), str(row["checkpoint_id"]))
                    if key[0] not in {item[0] for item in keep_ids}:
                        keep_ids.add(key)
                    else:
                        remove.append(key)
                for namespace, checkpoint_id in remove:
                    await db.execute(
                        "DELETE FROM workflow_pending_writes WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?",
                        (thread_id, namespace, checkpoint_id),
                    )
                    await db.execute(
                        "DELETE FROM workflow_checkpoint_owners WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                        (thread_id, namespace, checkpoint_id),
                    )
                    await db.execute(
                        "DELETE FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                        (thread_id, namespace, checkpoint_id),
                    )
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()

    async def aget_delta_channel_history(self, *, config: RunnableConfig, channels: Sequence[str]):
        if not channels:
            return {}
        raise UnsupportedDeltaChannelError("DeltaChannel is not supported by DeskPet workflow v1")

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def list(self, config: RunnableConfig | None, **kwargs: Any):
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions):
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = ""):
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def delete_thread(self, thread_id: str) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")


NATIVE_ENGINE_KIND = "deskpet-native"
NATIVE_CHECKPOINT_TYPE = "deskpet-native-json-v1"
NATIVE_SNAPSHOT_VERSION = 1


class NativeCheckpointError(WorkflowContractError):
    """Stable persistence failure raised by the native workflow engine."""


def _native_mapping(value: object, *, name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return copy.deepcopy(dict(result))
    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        return {key: copy.deepcopy(getattr(value, key)) for key in fields}
    raise TypeError(f"{name} must be a mapping or expose to_dict()")


class NativeCheckpointStore:
    """Strict-JSON checkpoint and commit protocol for DeskPet's native runner.

    Each mutating API owns one ``BEGIN IMMEDIATE`` transaction.  The operation
    ledger is checked before the fence so a caller that lost the lease after a
    successful commit can still recover the authoritative result.
    """

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
        from ..native import NativeSnapshotEnvelope, NativeTask

        completed = payload.get("completed_activations") or {}
        joins = payload.get("join_firings") or []
        writes = payload.get("node_writes") or {}
        return NativeSnapshotEnvelope(
            thread_id=str(payload["thread_id"]),
            checkpoint_ns=str(payload["checkpoint_ns"]),
            checkpoint_id=str(payload["checkpoint_id"]),
            parent_checkpoint_id=(
                str(payload["parent_checkpoint_id"])
                if payload.get("parent_checkpoint_id") is not None else None
            ),
            run_id=str(payload["run_id"]),
            state_schema_version=int(payload["state_schema_version"]),
            step=int(payload["step"]),
            state=copy.deepcopy(dict(payload["state"])),
            frontier=tuple(NativeTask(**dict(item)) for item in payload.get("frontier") or []),
            completed_activations={
                str(key): tuple(str(item) for item in value)
                for key, value in dict(completed).items()
            } if isinstance(completed, Mapping) else {},
            join_firings=tuple(str(item) for item in joins) if not isinstance(joins, Mapping) else tuple(joins),
            node_writes=copy.deepcopy(dict(writes)) if isinstance(writes, Mapping) else {},
            interrupt=(copy.deepcopy(dict(payload["interrupt"])) if payload.get("interrupt") else None),
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
                run["thread_id"], run["checkpoint_ns"], checkpoint_id, parent,
                run["run_id"], NATIVE_CHECKPOINT_TYPE, blob, metadata_blob,
                NATIVE_ENGINE_KIND, NATIVE_SNAPSHOT_VERSION, now,
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
                run["thread_id"], run["checkpoint_ns"], base_checkpoint_id, task_id,
                write_index, f"__native__:{write_kind}", "json", payload_json.encode("utf-8"),
                task_path, write_kind, payload_json, node_execution_id,
            ),
        )
        return True

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
                    WHERE o.run_id=? AND o.checkpoint_id=? AND c.engine_kind=?""" + clause,
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
            raise NativeCheckpointError("run_identity_mismatch", "native checkpoint namespace changed")
        from ..contracts import StatePatch
        from ..native import NativeExecution

        pending_results = {
            str(item["task_id"]): StatePatch(dict(item["payload"]["patch"]))
            for item in pending
            if item["write_kind"] == "state_patch"
        }
        pending_consumed_interrupt_ids = tuple(sorted({
            str(interrupt_id)
            for item in pending if item["write_kind"] == "state_patch"
            for interrupt_id in item["payload"].get("consumed_interrupt_ids", ())
        }))
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
                for row in rows if row["task_id"] is not None and row["first_started"] is not None
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
                db, operation_id=op_id, run_id=run_id, operation_kind="genesis",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                return self._snapshot_object(existing["snapshot"]) if protocol_call else existing
            run_row = await self._run_row(db, run_id)
            self._assert_fence_row(run_row, fence)
            self._assert_head(run_row, None)
            checkpoint_id = self._stable_id(
                run_id, str(run_row["checkpoint_ns"]), "genesis"
            )
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
                db, operation_id=op_id, run_id=run_id,
                operation_kind="genesis", request_hash=request_hash, result=result, now=now,
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
        if fence is None or expected_head is None or task is None or attempt is None or patch is None:
            raise TypeError("commit_task_result requires a fence, head, task, attempt and patch")
        task_data = _native_mapping(task, name="task")
        patch_data = _native_mapping(patch, name="patch")
        task_id = str(task_data.get("task_id") or "").strip()
        node_id = str(task_data.get("node_id") or "").strip()
        activation_id = str(task_data.get("activation_id") or task_id).strip()
        if not task_id or not node_id or attempt < 1:
            raise NativeCheckpointError("invalid_task_result", "task_id, node_id and attempt>=1 are required")
        validate_json_value(patch_data, path="$.state_patch")
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "task_result", task_id, attempt
        )
        execution_id = str(task_data.get("node_execution_id") or self._stable_id(
            fence.run_id, expected_head, task_id, node_id
        ))
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
                db, operation_id=op_id, run_id=fence.run_id, operation_kind="task_result",
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
                    execution_id, fence.run_id, node_id, expected_head, activation_id,
                    task_id, str(task_data.get("task_path") or ""), attempt, now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_node_attempts(
                    node_execution_id,retry_attempt,task_id,task_path,status,started_at,ended_at
                ) VALUES(?,?,?,?, 'succeeded_pending',?,?)
                ON CONFLICT(node_execution_id,retry_attempt) DO UPDATE SET
                    status='succeeded_pending',ended_at=excluded.ended_at,error_ref=NULL,next_attempt_at=NULL""",
                (
                    execution_id, attempt, task_id, str(task_data.get("task_path") or ""),
                    float(task_data.get("started_at") or now), now,
                ),
            )
            await self._write_pending(
                db, run=run, base_checkpoint_id=expected_head, task_id=task_id,
                write_index=0, write_kind="state_patch", payload=payload,
                node_execution_id=execution_id, task_path=str(task_data.get("task_path") or ""),
            )
            pending_owner = f"{fence.run_id}:{expected_head}:{task_id}"
            for sha256 in payload["blob_refs"]:
                if len(str(sha256)) != 64 or any(ch not in "0123456789abcdef" for ch in str(sha256)):
                    raise NativeCheckpointError("invalid_blob_ref", "task result contains an invalid blob digest")
                exists = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (str(sha256),))
                ).fetchone()
                if exists is None:
                    raise NativeCheckpointError("blob_not_found", f"unknown blob reference: {sha256}")
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
                db, operation_id=op_id, run_id=fence.run_id,
                operation_kind="task_result", request_hash=request_hash, result=result, now=now,
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
                db, operation_id=op_id, run_id=fence.run_id, operation_kind="route_selection",
                request_hash=request_hash, request_conflict_code="route_nondeterminism",
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
                db, run=run, base_checkpoint_id=expected_head, task_id=route_task_id,
                write_index=1, write_kind="route_selection", payload=payload,
                node_execution_id=None,
            )
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id,
                "checkpoint_id": expected_head,
                "operation_id": op_id,
                "selection": payload,
            }
            await self._record_operation(
                db, operation_id=op_id, run_id=fence.run_id,
                operation_kind="route_selection", request_hash=request_hash, result=result, now=now,
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
            if expected_version is not None and int(row["decision_version"]) != int(expected_version):
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
        from ..outbox import stable_delivery_id, stable_event_id

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
                (event_id, event_key, run["run_id"], int(seq["event_seq"]), event_type, payload_json, now),
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
                    raw_deliveries.append(
                        {"channel": business_channel, "target_id": target_id}
                    )
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
                raise NativeCheckpointError("stale_checkpoint_head", "workflow checkpoint head changed")
            step = int(current["step"]) + 1
        resolved_decisions: Sequence[str | Mapping[str, Any]] = (
            tuple(decision_ids) + tuple(consumed_interrupt_ids)
        )
        op_id = operation_id or self._stable_id(
            fence.run_id, expected_head, "frontier", step,
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
                    if isinstance(completed_activations, Mapping) else list(completed_activations)
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
        if terminal_status is not None and (frontier_data or terminal_status not in {"completed", "failed", "cancelled"}):
            raise NativeCheckpointError(
                "invalid_terminal_frontier", "terminal status requires an empty frontier and an allowed status"
            )
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._operation(
                db, operation_id=op_id, run_id=fence.run_id, operation_kind="frontier",
                request_hash=request_hash,
            )
            if existing is not None:
                await db.commit()
                if protocol_call:
                    from ..native import NativeCommitResult
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
                str(item.get("task_id") or "")
                for item in list(head_snapshot.get("frontier") or [])
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
                    for item in pending_write_items if item["write_kind"] == "state_patch"
                }
            snapshot = self._snapshot_payload(
                run=run, checkpoint_id=checkpoint_id, parent_checkpoint_id=expected_head,
                step=step, state=state, frontier=frontier_data,
                completed_activations=completed_activations, join_firings=join_firings,
                node_writes=node_writes,
                metadata={**dict(metadata or {}), "operation_id": op_id, "operation_kind": "frontier"},
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
                str(row["node_execution_id"]) for row in pending_rows
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
                    raise NativeCheckpointError("blob_not_found", f"unknown blob reference: {sha256}")
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
                    AND owner_id IN ({','.join('?' for _ in pending_owner_ids)})""",
                    pending_owner_ids,
                )
            event_ids = [
                await (
                    execution_adapter.materialize_intent(
                        db, run=run, intent=intent, now=now
                    )
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
                        now, fence.run_id, expected_head, fence.owner,
                        fence.lease_epoch, fence.run_version,
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
                        checkpoint_id, terminal_status, error_json, recovery_action, now, now,
                        fence.run_id, expected_head, fence.owner, fence.lease_epoch, fence.run_version,
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
                db, operation_id=op_id, run_id=fence.run_id,
                operation_kind="frontier", request_hash=request_hash, result=result, now=now,
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
            from ..native import NativeCommitResult
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
            raise NativeCheckpointError("invalid_retry", "task_id, node_id and attempt>=1 are required")
        execution_id = str(task_data.get("node_execution_id") or self._stable_id(
            fence.run_id, expected_head, task_id, node_id
        ))
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
                db, operation_id=op_id, run_id=fence.run_id, operation_kind="retry",
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
                    execution_id, fence.run_id, node_id, expected_head, activation_id,
                    task_id, str(task_data.get("task_path") or ""), attempt, now,
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
                    execution_id, attempt, task_id, str(task_data.get("task_path") or ""),
                    float(task_data.get("started_at") or now), now, error_ref, float(next_attempt_at),
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
                        now, fence.run_id, expected_head, fence.owner,
                        fence.lease_epoch, fence.run_version,
                    ),
                )
            ).fetchone()
            if updated is None:
                raise StaleRunFence(f"stale retry writer: {fence.run_id}")
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id, "checkpoint_id": expected_head,
                "task_id": task_id, "node_execution_id": execution_id,
                "attempt": attempt, "next_attempt_at": float(next_attempt_at),
                "run_version": int(updated["run_version"]), "operation_id": op_id,
            }
            await self._record_operation(
                db, operation_id=op_id, run_id=fence.run_id,
                operation_kind="retry", request_hash=request_hash, result=result, now=now,
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
        from ..human import HumanDecisionStore

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
                raise NativeCheckpointError("stale_checkpoint_head", "workflow checkpoint head changed")
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
                db, operation_id=op_id, run_id=fence.run_id, operation_kind="interrupt",
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
                run=run, checkpoint_id=checkpoint_id, parent_checkpoint_id=expected_head,
                step=step, state=state, frontier=frontier,
                completed_activations=(current or {}).get("completed_activations", ()),
                join_firings=(current or {}).get("join_firings", ()),
                node_writes=(current or {}).get("node_writes", ()),
                interrupt=interrupt_data,
                metadata={"operation_id": op_id, "operation_kind": "interrupt"},
            )
            await self._insert_checkpoint(db, run=run, snapshot=snapshot, now=now)
            await self._write_pending(
                db, run=run, base_checkpoint_id=checkpoint_id,
                task_id=task_id or f"interrupt:{interrupt_id}", write_index=-1,
                write_kind="interrupt", payload=interrupt_data, node_execution_id=None,
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
                    db, fence=fence, interrupt_id=interrupt_id, prompt=prompt,
                    checkpoint_id=checkpoint_id, checkpoint_ns=str(run["checkpoint_ns"]),
                    task_id=task_id or None, kind=kind, expires_at=expires_at, now=now,
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
                else self._materialize_intent(
                    db, run=run, intent=decision_event_intent, now=now
                )
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
                    raise StaleRunFence(
                        f"stale interrupt writer: {fence.run_id}"
                    )
            result: dict[str, JsonValue] = {
                "run_id": fence.run_id, "checkpoint_id": checkpoint_id,
                "decision_id": str(decision_data["decision_id"]), "interrupt_id": interrupt_id,
                "operation_id": op_id, "snapshot": snapshot,
                "event_ids": [decision_event_id],
            }
            await self._record_operation(
                db, operation_id=op_id, run_id=fence.run_id,
                operation_kind="interrupt", request_hash=request_hash, result=result, now=now,
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
                db, operation_id=operation_id, run_id=fence.run_id,
                operation_kind=operation_kind, request_hash=request_hash,
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
                execution_ids.append(str(task_data.get("node_execution_id") or self._stable_id(
                    fence.run_id, expected_head, task_id, node_id
                )))
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
                        execution_adapter.materialize_intent(
                            db, run=run, intent=intent, now=now
                        )
                        if execution_adapter is not None
                        else self._materialize_intent(
                            db, run=run, intent=intent, now=now
                        )
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
                        canonical_json(dict(error)), now, now, fence.run_id, expected_head,
                        fence.owner, fence.lease_epoch, fence.run_version,
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
                "run_id": fence.run_id, "checkpoint_id": expected_head,
                "status": "failed", "run_version": int(updated["run_version"]),
                "event_ids": event_ids, "operation_id": operation_id,
            }
            await self._record_operation(
                db, operation_id=operation_id, run_id=fence.run_id,
                operation_kind=operation_kind, request_hash=request_hash,
                result=result, now=now,
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
        request_hash = self._request_hash(
            {"error": error_data, "recovery_action": recovery_action}
        )
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
                execution_adapter.materialize_intent(
                    db, run=run, intent=final_intent, now=now
                )
                if execution_adapter is not None
                else self._materialize_intent(
                    db, run=run, intent=final_intent, now=now
                )
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
                "card": {"run_id": fence.run_id, "status": "failed", "error": copy.deepcopy(error_data)},
            },
        }
        result = await self._commit_failure_kind(
            fence, expected_head, error=error_data, operation_id=op_id,
            operation_kind="failure", task=task_data, frontier=(), intent=final_intent,
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
                "card": {"run_id": fence.run_id, "status": "failed", "error": copy.deepcopy(error_data)},
            },
        }
        result = await self._commit_failure_kind(
            fence, expected_head, error=error_data, operation_id=op_id,
            operation_kind="engine_failure", task=None, frontier=frontier_data, intent=final_intent,
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
                    raise NativeCheckpointError("fork_identity_conflict", "fork checkpoint id changed")
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
                        child["thread_id"], saga["source_checkpoint_ns"],
                        saga["source_checkpoint_id"], NATIVE_ENGINE_KIND,
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
                run=child, checkpoint_id=child_checkpoint_id,
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
                    child["thread_id"], saga["source_checkpoint_ns"],
                    saga["source_checkpoint_id"], child["run_id"],
                ),
            )
            cursor = await db.execute(
                """UPDATE workflow_runs SET head_checkpoint_ns=?,head_checkpoint_id=?,
                active_nodes_json=?,updated_at=? WHERE run_id=? AND head_checkpoint_id IS NULL
                AND status='created'""",
                (
                    child["checkpoint_ns"], child_checkpoint_id,
                    canonical_json([str(item.get("node_id", "")) for item in snapshot["frontier"]]),
                    now, child["run_id"],
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


# The legacy reader remains project-local so old completed checkpoints can be
# inspected or migrated without installing the framework that created them.
LegacyCheckpointStore = FencedAsyncSqliteSaver

# Backward-compatible construction name now resolves to the native protocol.
FencedAsyncSqliteSaver = NativeCheckpointStore
