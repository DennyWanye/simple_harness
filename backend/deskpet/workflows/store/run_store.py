"""Fenced run, node and outbox operations for ``workflow.db``."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from ..errors import LeaseLostError, WorkflowContractError
from .schema import initialize_workflow_db


class StaleRunFence(LeaseLostError):
    """The caller no longer owns the durable run lease/version."""


class ForkPreparationError(WorkflowContractError):
    """A fork request cannot be prepared at the selected safe point."""


@dataclass(frozen=True, slots=True)
class RunFence:
    run_id: str
    owner: str
    lease_epoch: int
    run_version: int


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowRunStore:
    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def create_run(
        self,
        *,
        request_key: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        manifest_hash: str,
        implementation_hash: str,
        capability_hash: str,
        capability_snapshot: dict[str, Any],
        state_schema_version: int,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> tuple[str, bool]:
        """Create a run once for the exact start identity.

        Returns ``(run_id, created)``. A retry always returns the persisted run
        and its persisted capability snapshot remains authoritative.
        """

        await self.initialize()
        now = self._clock()
        new_run_id = run_id or uuid.uuid4().hex
        new_trace_id = trace_id or uuid.uuid4().hex
        new_thread_id = thread_id or new_run_id
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT run_id FROM workflow_start_requests WHERE request_key=?",
                    (request_key,),
                )
            ).fetchone()
            if existing is not None:
                await db.commit()
                return str(existing["run_id"]), False
            await db.execute(
                "INSERT OR IGNORE INTO workflow_capabilities(capability_hash,snapshot_json,created_at) VALUES(?,?,?)",
                (capability_hash, _json(capability_snapshot), now),
            )
            await db.execute(
                """INSERT INTO workflow_runs(
                    run_id,trace_id,thread_id,checkpoint_ns,session_id,request_id,turn_id,
                    workflow_name,workflow_version,manifest_hash,implementation_hash,capability_hash,
                    state_schema_version,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'created',?,?)""",
                (
                    new_run_id,
                    new_trace_id,
                    new_thread_id,
                    checkpoint_ns,
                    session_id,
                    request_id,
                    turn_id,
                    workflow_name,
                    workflow_version,
                    manifest_hash,
                    implementation_hash,
                    capability_hash,
                    state_schema_version,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_start_requests(
                    request_key,session_id,request_id,turn_id,workflow_name,capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (request_key, session_id, request_id, turn_id, workflow_name, capability_hash, new_run_id, now),
            )
            await db.commit()
            return new_run_id, True
        except BaseException:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def create_child_from_snapshot(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        """Delegate the v5 continuation transaction to its focused repository."""

        from .research_repository import ResearchWorkflowRepository

        repository = ResearchWorkflowRepository(self.path, clock=self._clock)
        return await repository.create_child_from_snapshot(**kwargs)

    async def bind_session_refs(
        self,
        run_id: str,
        refs: Iterable[tuple[str, str, int]],
    ) -> None:
        """Persist the session fences captured when a run was created.

        Bindings are immutable. A retry may write the same values again, but it
        must never silently move an existing run to a newer session epoch.
        """

        normalized = tuple(
            (str(kind).strip(), str(session_id).strip(), int(epoch))
            for kind, session_id, epoch in refs
            if str(session_id).strip()
        )
        if any(not kind or epoch < 0 for kind, _session_id, epoch in normalized):
            raise WorkflowContractError("invalid workflow session reference")
        if len({kind for kind, _session_id, _epoch in normalized}) != len(normalized):
            raise WorkflowContractError("duplicate workflow session reference kind")

        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                await db.rollback()
                raise WorkflowContractError(f"unknown workflow run: {run_id}")
            for kind, session_id, epoch in normalized:
                existing = await (
                    await db.execute(
                        "SELECT session_id,session_epoch FROM workflow_session_refs "
                        "WHERE run_id=? AND session_kind=?",
                        (run_id, kind),
                    )
                ).fetchone()
                if existing is not None:
                    if str(existing["session_id"]) != session_id or int(existing["session_epoch"]) != epoch:
                        await db.rollback()
                        raise WorkflowContractError(
                            f"workflow session reference conflict: {run_id}:{kind}"
                        )
                    continue
                await db.execute(
                    "INSERT INTO workflow_session_refs("
                    "run_id,session_kind,session_id,session_epoch,deleted_at"
                    ") VALUES(?,?,?,?,NULL)",
                    (run_id, kind, session_id, epoch),
                )
            await db.commit()
        except Exception:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_session_ref(self, run_id: str, session_kind: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT run_id,session_kind,session_id,session_epoch,deleted_at "
                    "FROM workflow_session_refs WHERE run_id=? AND session_kind=?",
                    (run_id, session_kind),
                )
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            await db.close()

    async def tombstone_session_refs(
        self,
        session_id: str,
        *,
        session_kind: str | None = None,
        deleted_at: float | None = None,
    ) -> list[str]:
        """Tombstone matching refs and return their nonterminal run ids."""

        await self.initialize()
        timestamp = self._clock() if deleted_at is None else float(deleted_at)
        where = "ref.session_id=?"
        params: list[Any] = [session_id]
        if session_kind is not None:
            where += " AND ref.session_kind=?"
            params.append(session_kind)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            rows = await (
                await db.execute(
                    "SELECT DISTINCT ref.run_id FROM workflow_session_refs ref "
                    "JOIN workflow_runs run ON run.run_id=ref.run_id "
                    f"WHERE {where} AND run.status NOT IN ('completed','failed','cancelled')",
                    tuple(params),
                )
            ).fetchall()
            update_where = "session_id=?"
            update_params: list[Any] = [timestamp, session_id]
            if session_kind is not None:
                update_where += " AND session_kind=?"
                update_params.append(session_kind)
            await db.execute(
                f"UPDATE workflow_session_refs SET deleted_at=? WHERE {update_where}",
                tuple(update_params),
            )
            await db.commit()
            return [str(row["run_id"]) for row in rows]
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))).fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def next_retry_at(self, run_id: str) -> float | None:
        """Return the latest pending node retry deadline for a run."""

        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT MAX(attempt.next_attempt_at) AS due_at
                    FROM workflow_nodes node
                    JOIN workflow_node_attempts attempt
                      ON attempt.node_execution_id=node.node_execution_id
                     AND attempt.retry_attempt=node.latest_attempt
                    WHERE node.run_id=? AND node.latest_status='retryable'""",
                    (run_id,),
                )
            ).fetchone()
            return float(row["due_at"]) if row and row["due_at"] is not None else None
        finally:
            await db.close()

    async def block_legacy_nonterminal_runs(
        self,
        *,
        native_implementation_hashes: Iterable[str] = (),
    ) -> list[str]:
        """Quiesce legacy nonterminal runs while preserving read-only history.

        Supplying native implementation hashes also catches legacy runs that
        crashed before their first checkpoint. Without that registry context,
        only an explicit non-native checkpoint head is classified as legacy.
        """

        native_hashes = tuple(str(value) for value in native_implementation_hashes)
        now = self._clock()
        error_json = _json(
            {
                "code": "legacy_engine_blocked",
                "message": "Legacy workflow history is read-only under the native engine",
            }
        )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            predicate = "c.engine_kind IS NOT NULL AND c.engine_kind!='deskpet-native'"
            params: list[Any] = []
            if native_hashes:
                placeholders = ",".join("?" for _ in native_hashes)
                predicate = f"({predicate} OR run.implementation_hash NOT IN ({placeholders}))"
                params.extend(native_hashes)
            rows = await (
                await db.execute(
                    f"""SELECT DISTINCT run.run_id FROM workflow_runs run
                    LEFT JOIN workflow_checkpoints c
                      ON c.thread_id=run.thread_id
                     AND c.checkpoint_ns=run.head_checkpoint_ns
                     AND c.checkpoint_id=run.head_checkpoint_id
                    WHERE run.status NOT IN ('completed','failed','cancelled','blocked')
                      AND ({predicate}) ORDER BY run.run_id""",
                    params,
                )
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in rows]
            for run_id in run_ids:
                await db.execute(
                    """UPDATE workflow_runs SET status='blocked',error_json=?,
                    recovery_action='read_only',lease_owner=NULL,lease_expires_at=NULL,
                    heartbeat_at=NULL,lease_epoch=lease_epoch+1,run_version=run_version+1,
                    updated_at=? WHERE run_id=?
                    AND status NOT IN ('completed','failed','cancelled','blocked')""",
                    (error_json, now, run_id),
                )
            await db.commit()
            return run_ids
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def checkpoint_engine_kind(self, run_id: str) -> str | None:
        """Return the explicit head discriminator without decoding checkpoint bytes."""

        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT c.engine_kind FROM workflow_runs run
                    LEFT JOIN workflow_checkpoints c
                      ON c.thread_id=run.thread_id
                     AND c.checkpoint_ns=run.head_checkpoint_ns
                     AND c.checkpoint_id=run.head_checkpoint_id
                    WHERE run.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            return None if row is None or row["engine_kind"] is None else str(row["engine_kind"])
        finally:
            await db.close()

    async def get_capability_snapshot(self, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT capability.snapshot_json FROM workflow_runs run
                    JOIN workflow_capabilities capability
                      ON capability.capability_hash=run.capability_hash
                    WHERE run.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            return json.loads(str(row["snapshot_json"])) if row is not None else None
        finally:
            await db.close()

    async def get_start_snapshot(self, run_id: str) -> dict[str, Any] | None:
        """Return the immutable start reference used by server-side retries.

        The capability row is joined through the run's persisted capability
        hash.  The reserved start envelope is validated here so callers never
        have to re-inject it as a user capability snapshot.
        """

        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT run.*, capability.snapshot_json,
                    start.request_key AS start_request_key
                    FROM workflow_runs run
                    JOIN workflow_capabilities capability
                      ON capability.capability_hash=run.capability_hash
                    JOIN workflow_start_requests start ON start.run_id=run.run_id
                    WHERE run.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        result = dict(row)
        try:
            snapshot = json.loads(str(result.pop("snapshot_json")))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkflowContractError(
                "invalid_start_snapshot", "workflow capability snapshot is invalid"
            ) from exc
        if not isinstance(snapshot, dict):
            raise WorkflowContractError(
                "invalid_start_snapshot", "workflow capability snapshot must be an object"
            )
        metadata = snapshot.get("_workflow_start")
        if not isinstance(metadata, dict):
            raise WorkflowContractError(
                "invalid_start_snapshot", "workflow start reference is missing"
            )
        identity = metadata.get("identity")
        start_payload = metadata.get("start_payload")
        if not isinstance(identity, dict) or not isinstance(start_payload, dict):
            raise WorkflowContractError(
                "invalid_start_snapshot", "workflow start identity or payload is invalid"
            )
        required_identity = {
            "venue", "base_session_id", "code_session_id", "delivery_session_id",
            "base_epoch", "code_epoch", "request_id", "turn_id", "workflow_name",
            "logical_slot",
        }
        if set(identity) != required_identity:
            raise WorkflowContractError(
                "invalid_start_snapshot", "workflow start identity shape is invalid"
            )
        original_capabilities = {
            str(key): value for key, value in snapshot.items() if key != "_workflow_start"
        }
        result.update(
            {
                "identity": identity,
                "start_payload": start_payload,
                "original_capabilities": original_capabilities,
                "start_metadata": metadata,
            }
        )
        return result

    async def record_node_start(
        self,
        *,
        run_id: str,
        node_id: str,
        checkpoint_id: str,
        task_id: str,
        attempt: int,
    ) -> str:
        invocation_key = task_id
        raw = f"{run_id}|{checkpoint_id}|{invocation_key}|{node_id}"
        execution_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO workflow_nodes(
                    node_execution_id,run_id,node_id,base_checkpoint_id,invocation_key,
                    task_id,latest_attempt,latest_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,'running',?)
                ON CONFLICT(node_execution_id) DO UPDATE SET
                    latest_attempt=MAX(latest_attempt,excluded.latest_attempt),
                    latest_status='running',task_id=excluded.task_id,updated_at=excluded.updated_at""",
                (execution_id, run_id, node_id, checkpoint_id, invocation_key, task_id, attempt, now),
            )
            await db.execute(
                """INSERT INTO workflow_node_attempts(
                    node_execution_id,retry_attempt,task_id,status,started_at
                ) VALUES(?,?,?,'running',?)
                ON CONFLICT(node_execution_id,retry_attempt) DO UPDATE SET
                    status='running',ended_at=NULL,error_ref=NULL""",
                (execution_id, attempt, task_id, now),
            )
            await db.commit()
            return execution_id
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def record_node_finish(
        self,
        execution_id: str,
        attempt: int,
        status: str,
        *,
        error_ref: str | None = None,
    ) -> dict[str, float] | None:
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE workflow_node_attempts SET status=?,ended_at=?,error_ref=?
                WHERE node_execution_id=? AND retry_attempt=?""",
                (status, now, error_ref, execution_id, attempt),
            )
            await db.execute(
                "UPDATE workflow_nodes SET latest_status=?,updated_at=? WHERE node_execution_id=?",
                (status, now, execution_id),
            )
            row = await (
                await db.execute(
                    """SELECT started_at,ended_at FROM workflow_node_attempts
                    WHERE node_execution_id=? AND retry_attempt=?""",
                    (execution_id, attempt),
                )
            ).fetchone()
            await db.commit()
            if row is None or row["ended_at"] is None:
                return None
            started_at = float(row["started_at"])
            ended_at = float(row["ended_at"])
            return {
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": max(0.0, (ended_at - started_at) * 1000.0),
            }
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def list_runs(self, *, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        await self.initialize()
        db = await self._connect()
        try:
            if session_id is None:
                cursor = await db.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (limit,))
            else:
                cursor = await db.execute(
                    "SELECT * FROM workflow_runs WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                )
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def prepare_fork(
        self,
        *,
        fork_key: str,
        source_run_id: str,
        source_checkpoint_ns: str,
        source_checkpoint_id: str,
        expected_version: int,
        child_run_id: str,
        child_trace_id: str,
        state_patch_json: str,
        confirm_dangerous_effects: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Prepare one fork saga and its child run in the same transaction."""

        await self.initialize()
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_fork_requests WHERE fork_key=?",
                    (fork_key,),
                )
            ).fetchone()
            if existing is not None:
                if (
                    existing["source_run_id"] != source_run_id
                    or existing["source_checkpoint_ns"] != source_checkpoint_ns
                    or existing["source_checkpoint_id"] != source_checkpoint_id
                    or existing["child_run_id"] != child_run_id
                    or existing["state_patch_json"] != state_patch_json
                ):
                    raise ForkPreparationError(
                        "fork_key_conflict",
                        "fork_key is already bound to a different fork request",
                    )
                await db.commit()
                return dict(existing), False

            source = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (source_run_id,))
            ).fetchone()
            if source is None:
                raise ForkPreparationError("fork_source_not_found", "source workflow run was not found")
            if int(source["run_version"]) != expected_version:
                raise ForkPreparationError(
                    "fork_source_version_conflict",
                    "source workflow run version changed",
                    details={"current_version": int(source["run_version"])},
                )
            if source_checkpoint_ns:
                raise ForkPreparationError(
                    "fork_namespace_unsupported",
                    "v1 fork supports only the root checkpoint namespace",
                )
            status = str(source["status"])
            if status == "waiting":
                raise ForkPreparationError(
                    "fork_waiting_unsupported",
                    "waiting checkpoints must be resumed or cancelled before fork",
                )
            if status in {"running", "cancel_requested", "cancelling"}:
                raise ForkPreparationError(
                    "fork_source_not_safe",
                    f"source run status is not a stable fork point: {status}",
                )
            active_nodes = json.loads(source["active_nodes_json"] or "[]")
            if len(active_nodes) > 1:
                raise ForkPreparationError(
                    "fork_fanout_unsupported",
                    "fan-out checkpoints cannot be forked in v1",
                )

            checkpoint = await (
                await db.execute(
                    """SELECT c.* FROM workflow_checkpoint_owners o
                    JOIN workflow_checkpoints c ON c.thread_id=o.thread_id
                      AND c.checkpoint_ns=o.checkpoint_ns AND c.checkpoint_id=o.checkpoint_id
                    WHERE o.run_id=? AND o.checkpoint_ns=? AND o.checkpoint_id=?
                      AND c.thread_id=?""",
                    (
                        source_run_id,
                        source_checkpoint_ns,
                        source_checkpoint_id,
                        source["thread_id"],
                    ),
                )
            ).fetchone()
            if checkpoint is None:
                raise ForkPreparationError(
                    "fork_checkpoint_not_found",
                    "checkpoint does not belong to the source run and namespace",
                )
            pending = await (
                await db.execute(
                    """SELECT 1 FROM workflow_pending_writes
                    WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=? LIMIT 1""",
                    (source["thread_id"], source_checkpoint_ns, source_checkpoint_id),
                )
            ).fetchone()
            if pending is not None:
                raise ForkPreparationError(
                    "fork_pending_writes_unsupported",
                    "checkpoints with pending writes cannot be forked in v1",
                )
            decision = await (
                await db.execute(
                    """SELECT 1 FROM workflow_decisions WHERE run_id=? AND checkpoint_ns=?
                    AND checkpoint_id=? AND status IN ('prepared','open') LIMIT 1""",
                    (source_run_id, source_checkpoint_ns, source_checkpoint_id),
                )
            ).fetchone()
            if decision is not None:
                raise ForkPreparationError(
                    "fork_waiting_unsupported",
                    "interrupted checkpoints cannot be forked in v1",
                )

            effects = await (
                await db.execute(
                    """WITH RECURSIVE ancestors(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id) AS (
                        SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id
                        FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                        UNION ALL
                        SELECT parent.thread_id,parent.checkpoint_ns,parent.checkpoint_id,parent.parent_checkpoint_id
                        FROM workflow_checkpoints parent JOIN ancestors child
                          ON parent.thread_id=child.thread_id
                         AND parent.checkpoint_ns=child.checkpoint_ns
                         AND parent.checkpoint_id=child.parent_checkpoint_id
                    )
                    SELECT DISTINCT effect.effect_id,effect.effect_type,effect.status,effect.policy_json
                    FROM ancestors
                    JOIN workflow_checkpoint_effects link USING(thread_id,checkpoint_ns,checkpoint_id)
                    JOIN workflow_effects effect ON effect.effect_id=link.effect_id""",
                    (source["thread_id"], source_checkpoint_ns, source_checkpoint_id),
                )
            ).fetchall()
            dangerous: list[dict[str, str]] = []
            for effect in effects:
                policy = json.loads(effect["policy_json"])
                safe = effect["status"] == "committed" and (
                    policy.get("kind") == "idempotent_read"
                    or (
                        policy.get("kind") == "deterministic_reusable"
                        and policy.get("reusable_across_branches") is True
                    )
                )
                if not safe:
                    dangerous.append(
                        {
                            "effect_id": str(effect["effect_id"]),
                            "effect_type": str(effect["effect_type"]),
                            "kind": str(policy.get("kind") or "unknown"),
                        }
                    )
            if dangerous and not confirm_dangerous_effects:
                raise ForkPreparationError(
                    "fork_dangerous_effect_confirmation_required",
                    "fork may re-execute non-reusable side effects; explicit confirmation is required",
                    details={"effects": dangerous},
                )

            await db.execute(
                """INSERT INTO workflow_runs(
                    run_id,trace_id,thread_id,checkpoint_ns,head_checkpoint_ns,head_checkpoint_id,
                    parent_run_id,source_checkpoint_id,session_id,request_id,turn_id,
                    workflow_name,workflow_version,manifest_hash,implementation_hash,capability_hash,
                    state_schema_version,status,active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,'created','[]',?,?)""",
                (
                    child_run_id,
                    child_trace_id,
                    source["thread_id"],
                    source_checkpoint_ns,
                    source_checkpoint_ns,
                    source_run_id,
                    source_checkpoint_id,
                    source["session_id"],
                    source["request_id"],
                    source["turn_id"],
                    source["workflow_name"],
                    source["workflow_version"],
                    source["manifest_hash"],
                    source["implementation_hash"],
                    source["capability_hash"],
                    source["state_schema_version"],
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_session_refs(run_id,session_kind,session_id,session_epoch,deleted_at)
                SELECT ?,session_kind,session_id,session_epoch,deleted_at
                FROM workflow_session_refs WHERE run_id=?""",
                (child_run_id, source_run_id),
            )
            await db.execute(
                """INSERT INTO workflow_fork_requests(
                    fork_key,source_run_id,source_checkpoint_ns,source_checkpoint_id,
                    child_run_id,status,state_patch_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,'prepared',?,?,?)""",
                (
                    fork_key,
                    source_run_id,
                    source_checkpoint_ns,
                    source_checkpoint_id,
                    child_run_id,
                    state_patch_json,
                    now,
                    now,
                ),
            )
            await db.commit()
            row = await self.get_fork_request(fork_key)
            assert row is not None
            return row, True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_fork_request(self, fork_key: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_fork_requests WHERE fork_key=?", (fork_key,)
                )
            ).fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def claim(
        self,
        run_id: str,
        owner: str,
        *,
        ttl_seconds: float = 90.0,
        allowed_statuses: Iterable[str] = ("created", "retryable", "running"),
    ) -> RunFence:
        await self.initialize()
        now = self._clock()
        statuses = tuple(allowed_statuses)
        placeholders = ",".join("?" for _ in statuses)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    f"""UPDATE workflow_runs
                    SET lease_owner=?, lease_epoch=lease_epoch+1, lease_expires_at=?, heartbeat_at=?,
                        run_version=run_version+1, status='running', started_at=COALESCE(started_at,?), updated_at=?
                    WHERE run_id=? AND status IN ({placeholders})
                      AND (lease_owner IS NULL OR lease_owner=? OR lease_expires_at<=?)
                    RETURNING lease_epoch,run_version""",
                    (owner, now + ttl_seconds, now, now, now, run_id, *statuses, owner, now),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                raise StaleRunFence(f"run cannot be claimed: {run_id}")
            await db.commit()
            return RunFence(run_id, owner, int(row["lease_epoch"]), int(row["run_version"]))
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def heartbeat(self, fence: RunFence, *, ttl_seconds: float = 90.0) -> RunFence:
        now = self._clock()
        db = await self._connect()
        try:
            cursor = await db.execute(
                """UPDATE workflow_runs SET lease_expires_at=?,heartbeat_at=?,updated_at=?
                WHERE run_id=? AND lease_owner=? AND lease_epoch=? AND run_version=? AND status='running'""",
                (now + ttl_seconds, now, now, fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise StaleRunFence(f"stale heartbeat: {fence.run_id}")
            await db.commit()
            return fence
        finally:
            await db.close()

    async def request_cancel(self, run_id: str, reason: str) -> int:
        """Invalidate every existing owner and return the new run version."""

        now = self._clock()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """UPDATE workflow_runs SET status='cancel_requested',cancel_reason=?,
                    lease_owner=NULL,lease_expires_at=NULL,lease_epoch=lease_epoch+1,
                    run_version=run_version+1,updated_at=?
                    WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')
                    RETURNING run_version""",
                    (reason, now, run_id),
                )
            ).fetchone()
            await db.commit()
            if row is None:
                raise StaleRunFence(f"run is already terminal or missing: {run_id}")
            return int(row["run_version"])
        finally:
            await db.close()

    async def append_event(
        self,
        fence: RunFence,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_key: str | None = None,
    ) -> dict[str, Any]:
        """Append a lifecycle event and advance the per-run sequence atomically."""

        now = self._clock()
        stable_key = event_key or uuid.uuid4().hex
        event_id = hashlib.sha256(f"{fence.run_id}|{stable_key}".encode("utf-8")).hexdigest()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_events WHERE run_id=? AND event_key=?",
                    (fence.run_id, stable_key),
                )
            ).fetchone()
            if existing is not None:
                await db.commit()
                return {
                    "event_id": existing["event_id"],
                    "event_key": existing["event_key"],
                    "run_id": existing["run_id"],
                    "seq": int(existing["seq"]),
                    "event_type": existing["event_type"],
                    "payload": json.loads(existing["payload_json"]),
                }
            row = await (
                await db.execute(
                    """UPDATE workflow_runs SET event_seq=event_seq+1,updated_at=?
                    WHERE run_id=? AND lease_owner=? AND lease_epoch=? AND run_version=?
                    RETURNING event_seq""",
                    (now, fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                raise StaleRunFence(f"stale event writer: {fence.run_id}")
            seq = int(row["event_seq"])
            await db.execute(
                "INSERT INTO workflow_events(event_id,event_key,run_id,seq,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (event_id, stable_key, fence.run_id, seq, event_type, _json(payload), now),
            )
            await db.commit()
            return {"event_id": event_id, "event_key": stable_key, "run_id": fence.run_id, "seq": seq, "event_type": event_type, "payload": payload}
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def events_after(self, run_id: str, seq: int) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT * FROM workflow_events WHERE run_id=? AND seq>? ORDER BY seq",
                    (run_id, seq),
                )
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result
        finally:
            await db.close()
