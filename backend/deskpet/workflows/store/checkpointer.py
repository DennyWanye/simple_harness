"""DeskPet-native checkpoint protocol backed by fenced ``workflow.db`` writes."""

from __future__ import annotations

import copy
import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from ..contracts import canonical_json, validate_json_value
from ..errors import AsyncOnlyWorkflowError, UnsupportedDeltaChannelError
from .checkpointer_domains.commits import TaskCommitMixin

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .checkpointer_domains.core import NATIVE_CHECKPOINT_TYPE as NATIVE_CHECKPOINT_TYPE
from .checkpointer_domains.core import NATIVE_ENGINE_KIND as NATIVE_ENGINE_KIND
from .checkpointer_domains.core import NATIVE_SNAPSHOT_VERSION as NATIVE_SNAPSHOT_VERSION
from .checkpointer_domains.failures import InterruptFailureMixin
from .checkpointer_domains.frontier import FrontierRetryMixin
from .checkpointer_domains.reads import CheckpointReadMixin
from .checkpointer_domains.support import NativeCheckpointError as NativeCheckpointError
from .run_store import RunFence, StaleRunFence
from .schema import initialize_workflow_db

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
    def __class_getitem__(cls, _item: object) -> type[BaseCheckpointSaver]:
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
        "ts": datetime.now(UTC).isoformat(),
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }


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
            "ensure_genesis",
            "load_execution",
            "commit_task_result",
            "commit_route_selection",
            "commit_frontier",
            "commit_retry",
            "commit_interrupt",
            "commit_failure",
            "commit_engine_failure",
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
            raise ValueError(
                "workflow checkpoint write requires a complete DeskPet run fence"
            ) from exc

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
                    (
                        item["task_id"],
                        item["channel"],
                        self.serde.loads_typed((item["value_type"], item["value_blob"])),
                    )
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
            if item is not None and (
                not filter or all(item.metadata.get(k) == v for k, v in filter.items())
            ):
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
            get_checkpoint_metadata(config, metadata),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
                (
                    thread_id,
                    namespace,
                    checkpoint_id,
                    parent_id,
                    run_id,
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_blob,
                    now,
                ),
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
            checkpoint["ts"] = datetime.now(UTC).isoformat()
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
                # 按需导入：循环依赖（checkpointer ↔ human）
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
            await db.execute(
                "DELETE FROM workflow_checkpoint_owners WHERE thread_id=?", (thread_id,)
            )
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
                await db.execute(
                    "SELECT 1 FROM workflow_checkpoints WHERE thread_id=? LIMIT 1",
                    (target_thread_id,),
                )
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

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ):
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ):
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def delete_thread(self, thread_id: str) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        raise AsyncOnlyWorkflowError("workflow checkpoint saver is async-only")


class NativeCheckpointStore(
    CheckpointReadMixin, TaskCommitMixin, FrontierRetryMixin, InterruptFailureMixin
):
    """Strict-JSON checkpoint and commit protocol for DeskPet's native runner.

    Each mutating API owns one ``BEGIN IMMEDIATE`` transaction.  The operation
    ledger is checked before the fence so a caller that lost the lease after a
    successful commit can still recover the authoritative result.
    """


# The legacy reader remains project-local so old completed checkpoints can be
# inspected or migrated without installing the framework that created them.
LegacyCheckpointStore = FencedAsyncSqliteSaver

# Backward-compatible construction name now resolves to the native protocol.
FencedAsyncSqliteSaver = NativeCheckpointStore
