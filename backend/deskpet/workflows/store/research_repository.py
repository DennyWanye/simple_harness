"""Durable DeepResearch v5 controls, snapshots, and immutable lineage.

The schema for these records is owned by the workflow.db v3 migration.  This
module deliberately contains repository semantics only so an already-created
v3 database never depends on a second same-version DDL pass.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from ..errors import WorkflowContractError
from .schema import initialize_workflow_db

_CONTROL_ACTIONS = {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}
_CONTROL_TERMINAL = {"consumed", "rejected", "expired"}
_CONTINUABLE_DELIVERY = {"partial", "insufficient_evidence"}
_V6_CONTINUATION_NAMESPACE = uuid.UUID("a7e5f48a-9b2b-549f-a67c-3ebdee7b9c78")
_V6_POLICY_VERSION = 1
_V6_SNAPSHOT_KEYS = {
    "schema_version", "snapshot_id", "snapshot_hash", "run_id", "workflow_name",
    "workflow_version", "spec_ref", "spec_hash", "fact_batch_refs",
    "evidence_head_hash", "assessment_ref", "assessment_hash",
    "assessment_input_hash", "claim_batch_ref", "provenance_refs", "policy_refs",
    "closure_refs",
}
_V6_POLICY_KEYS = {
    "compiler", "route", "extraction", "llm_extract", "llm_repair", "llm_inference",
    "admission", "inference", "assessment", "claim", "quality",
}
_V6_ROUTE_POLICY_V1_KEYS = {
    "policy_id",
    "parent_deadline_ms",
    "page_deadline_ms",
    "max_queries",
    "max_fetches",
    "max_fetch_concurrency",
    "max_lanes",
    "official_source_policy_hash",
}


class ResearchRepositoryError(WorkflowContractError):
    """A research repository invariant or compare-and-swap failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResearchReachability:
    protected_run_ids: frozenset[str]
    protected_snapshot_hashes: frozenset[str]
    protected_pin_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ContinuationCreateResultV1:
    schema_version: int
    parent_run_id: str
    child_run_id: str
    child_operation_id: str
    created: bool
    start_payload: dict[str, Any]
    start_request_hash: str
    audit_operation_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "child_operation_id": self.child_operation_id,
            "created": self.created,
            "start_payload": copy.deepcopy(self.start_payload),
            "start_request_hash": self.start_request_hash,
            "audit_operation_id": self.audit_operation_id,
        }


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _command(row: aiosqlite.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(str(result.pop("payload_json")))
    return result


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wire_digest(value: object, field: str) -> str:
    text = str(value or "")
    digest = text.removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ResearchRepositoryError("invalid_v6_blob_ref", f"{field} is not a SHA-256 reference")
    return digest


def _walk_v6_wire_refs(value: object) -> set[str]:
    """Return forward registered-blob edges from one canonical v6 object.

    Rejected inference proposals deliberately retain unresolved strings under
    ``proposed_premise_fact_refs`` for audit only; they are not closure edges.
    This mirrors the terminal snapshot builder's one permitted exception.
    """

    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "proposed_premise_fact_refs":
                continue
            if key.endswith("_ref") and isinstance(item, str) and item.startswith("sha256:"):
                refs.add(item)
            elif key.endswith("_refs") and isinstance(item, list):
                refs.update(
                    str(ref)
                    for ref in item
                    if isinstance(ref, str) and ref.startswith("sha256:")
                )
            else:
                refs.update(_walk_v6_wire_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_walk_v6_wire_refs(item))
    return refs


def _v6_continuation_identity(parent_run_id: str) -> dict[str, str]:
    stable = uuid.uuid5(
        _V6_CONTINUATION_NAMESPACE,
        _canonical({"parent_run_id": parent_run_id, "policy_version": _V6_POLICY_VERSION}),
    )
    child_run_id = str(stable)
    child_operation_id = f"research:{child_run_id}"
    child_request_key = f"research-continuation-v6:{parent_run_id}:1"
    return {
        "child_run_id": child_run_id,
        "child_operation_id": child_operation_id,
        "child_start_operation_id": f"{child_operation_id}:start",
        "child_request_key": child_request_key,
        "child_request_id": f"continue:v6:{child_run_id}",
        "child_turn_id": uuid.uuid5(stable, "turn").hex,
        "child_trace_id": uuid.uuid5(stable, "trace").hex,
        "child_thread_id": uuid.uuid5(stable, "thread").hex,
        "budget_lease_id": uuid.uuid5(stable, "budget").hex,
    }


class ResearchWorkflowRepository:
    """Transactional repository for the workflow.db v3 research tables."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock=time.time,
        blob_root: str | Path | None = None,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self.blob_root = Path(blob_root) if blob_root is not None else None
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def open_control(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        action: str,
        expected_run_version: int,
        expected_head_checkpoint_ns: str,
        expected_head_checkpoint_id: str | None,
        payload: Mapping[str, Any] | None = None,
        command_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Open one idempotent command while recording its later CAS inputs."""

        if action not in _CONTROL_ACTIONS:
            raise ResearchRepositoryError("unsupported_control_action", f"unsupported action: {action}")
        if not run_id or not idempotency_key or expected_run_version < 0:
            raise ResearchRepositoryError("invalid_control_command", "invalid control identity")
        now = float(self._clock())
        command_id = command_id or hashlib.sha256(
            f"{run_id}|{idempotency_key}".encode()
        ).hexdigest()
        envelope = copy.deepcopy(dict(payload or {}))
        envelope["_repository"] = {
            "expected_run_version": int(expected_run_version),
            "expected_head_checkpoint_ns": str(expected_head_checkpoint_ns),
            "expected_head_checkpoint_id": expected_head_checkpoint_id,
        }
        encoded = _canonical(envelope)
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE run_id=? AND idempotency_key=?",
                    (run_id, idempotency_key),
                )
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["action"]) != action
                    or str(existing["head_checkpoint_ns"]) != expected_head_checkpoint_ns
                    or existing["head_checkpoint_id"] != expected_head_checkpoint_id
                    or str(existing["payload_json"]) != encoded
                ):
                    raise ResearchRepositoryError(
                        "control_idempotency_conflict",
                        "idempotency key is already bound to a different command",
                    )
                await db.commit()
                return _command(existing), False
            active = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE run_id=? AND status IN ('open','accepted','observed','settled') LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
            if active is not None:
                raise ResearchRepositoryError(
                    "active_control_conflict", "run already has an active control command"
                )
            run = await (
                await db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise ResearchRepositoryError("run_not_found", "control run was not found")
            await db.execute(
                """INSERT INTO workflow_run_control_commands(
                    command_id,run_id,idempotency_key,action,status,head_checkpoint_ns,
                    head_checkpoint_id,payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,'open',?,?,?,?,?)""",
                (
                    command_id,
                    run_id,
                    idempotency_key,
                    action,
                    expected_head_checkpoint_ns,
                    expected_head_checkpoint_id,
                    encoded,
                    now,
                    now,
                ),
            )
            await db.commit()
            row = await self.get_control(command_id)
            assert row is not None
            return row, True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_control(self, command_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            return _command(row) if row is not None else None
        finally:
            await db.close()

    @staticmethod
    async def _is_owned_ancestor(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        head_ns: str,
        head_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> bool:
        if checkpoint_ns != head_ns:
            return False
        row = await (
            await db.execute(
                """WITH RECURSIVE ancestors(checkpoint_id,parent_checkpoint_id) AS (
                    SELECT checkpoint_id,parent_checkpoint_id FROM workflow_checkpoints
                    WHERE run_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    UNION ALL
                    SELECT parent.checkpoint_id,parent.parent_checkpoint_id
                    FROM workflow_checkpoints parent JOIN ancestors child
                      ON parent.checkpoint_id=child.parent_checkpoint_id
                    WHERE parent.run_id=? AND parent.checkpoint_ns=?
                )
                SELECT 1 FROM ancestors a JOIN workflow_checkpoint_owners o
                  ON o.run_id=? AND o.checkpoint_ns=? AND o.checkpoint_id=a.checkpoint_id
                WHERE a.checkpoint_id=? LIMIT 1""",
                (
                    run_id,
                    head_ns,
                    head_id,
                    run_id,
                    head_ns,
                    run_id,
                    checkpoint_ns,
                    checkpoint_id,
                ),
            )
        ).fetchone()
        return row is not None

    async def accept_generate_now(
        self,
        command_id: str,
        *,
        brief_checkpoint_ns: str,
        brief_checkpoint_id: str,
        settle_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Accept generate-now only after durable run/head/brief CAS checks."""

        if settle_seconds != 30.0:
            raise ResearchRepositoryError("invalid_settle_window", "generate-now settle window is fixed at 30s")
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError("control_not_found", "control command was not found")
            if row["action"] != "generate_now":
                raise ResearchRepositoryError("wrong_control_action", "command is not generate_now")
            envelope = json.loads(str(row["payload_json"]))
            meta = envelope.get("_repository") if isinstance(envelope, dict) else None
            if not isinstance(meta, dict):
                raise ResearchRepositoryError("invalid_control_metadata", "command CAS metadata is missing")
            if row["status"] == "accepted":
                if (
                    meta.get("brief_checkpoint_ns") != brief_checkpoint_ns
                    or meta.get("brief_checkpoint_id") != brief_checkpoint_id
                ):
                    raise ResearchRepositoryError(
                        "control_replay_conflict", "accepted command is bound to another brief checkpoint"
                    )
                await db.commit()
                return _command(row)
            if row["status"] != "open":
                raise ResearchRepositoryError("invalid_control_transition", "only open commands can be accepted")
            run = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],))
            ).fetchone()
            workflow_version = str(run["workflow_version"]) if run is not None else ""
            accepted = bool(
                run is not None
                and run["workflow_name"] == "deep_research"
                and workflow_version in {"v5", "v6"}
                and run["status"] == "running"
                and int(run["run_version"]) == int(meta.get("expected_run_version", -1))
                and str(run["head_checkpoint_ns"]) == str(meta.get("expected_head_checkpoint_ns", ""))
                and run["head_checkpoint_id"] == meta.get("expected_head_checkpoint_id")
                and run["head_checkpoint_ns"] == row["head_checkpoint_ns"]
                and run["head_checkpoint_id"] == row["head_checkpoint_id"]
            )
            if accepted and run["head_checkpoint_id"] is not None:
                accepted = await self._is_owned_ancestor(
                    db,
                    run_id=str(run["run_id"]),
                    head_ns=str(run["head_checkpoint_ns"]),
                    head_id=str(run["head_checkpoint_id"]),
                    checkpoint_ns=brief_checkpoint_ns,
                    checkpoint_id=brief_checkpoint_id,
                )
            wall_not_after: float | None = None
            automatic_deadline_missing = False
            if accepted and workflow_version == "v6":
                deadline = await (
                    await db.execute(
                        """SELECT wall_not_after,status FROM workflow_research_deadlines
                        WHERE run_id=? AND logical_scope='run:automatic'
                          AND parent_deadline_id IS NULL""",
                        (row["run_id"],),
                    )
                ).fetchone()
                if deadline is None:
                    automatic_deadline_missing = True
                    accepted = False
                else:
                    wall_not_after = float(deadline["wall_not_after"])
                    if deadline["status"] != "open" or now >= wall_not_after:
                        accepted = False
            if not accepted:
                envelope["_result"] = {
                    "reason": (
                        "automatic_deadline_missing"
                        if automatic_deadline_missing
                        else (
                            "wall_guard"
                            if workflow_version == "v6" and wall_not_after is not None and now >= wall_not_after
                            else "run_head_terminal_or_brief_cas_failed"
                        )
                    )
                }
                await db.execute(
                    "UPDATE workflow_run_control_commands SET status='rejected',payload_json=?,updated_at=? "
                    "WHERE command_id=? AND status='open'",
                    (_canonical(envelope), now, command_id),
                )
            else:
                envelope["_repository"]["brief_checkpoint_ns"] = brief_checkpoint_ns
                envelope["_repository"]["brief_checkpoint_id"] = brief_checkpoint_id
                await db.execute(
                    """UPDATE workflow_run_control_commands SET status='accepted',payload_json=?,
                    accepted_at=?,settle_deadline=?,updated_at=?
                    WHERE command_id=? AND status='open'""",
                    (
                        _canonical(envelope), now,
                        min(now + 30.0, wall_not_after) if wall_not_after is not None else now + 30.0,
                        now, command_id,
                    ),
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            await db.commit()
            assert updated is not None
            return _command(updated)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def transition_control(
        self,
        command_id: str,
        *,
        expected_status: str,
        new_status: str,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            ("open", "rejected"),
            ("open", "expired"),
            ("accepted", "observed"),
            ("observed", "settled"),
            ("settled", "consumed"),
        }
        if (expected_status, new_status) not in allowed:
            raise ResearchRepositoryError("invalid_control_transition", "control transition is not allowed")
        if new_status in {"observed", "settled", "consumed"} and not checkpoint_id:
            raise ResearchRepositoryError("checkpoint_required", "observed control states require a checkpoint")
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError("control_not_found", "control command was not found")
            if row["status"] == new_status:
                if checkpoint_ns is not None and row["head_checkpoint_ns"] != checkpoint_ns:
                    raise ResearchRepositoryError("control_replay_conflict", "checkpoint namespace changed")
                if checkpoint_id is not None and row["head_checkpoint_id"] != checkpoint_id:
                    raise ResearchRepositoryError("control_replay_conflict", "checkpoint identity changed")
                if result is not None:
                    replay_payload = json.loads(str(row["payload_json"]))
                    if replay_payload.get("_result") != dict(result):
                        raise ResearchRepositoryError("control_replay_conflict", "control result changed")
                await db.commit()
                return _command(row)
            if row["status"] != expected_status:
                raise ResearchRepositoryError("invalid_control_transition", "control state changed")
            envelope = json.loads(str(row["payload_json"]))
            if result is not None:
                envelope["_result"] = copy.deepcopy(dict(result))
            ns = str(row["head_checkpoint_ns"]) if checkpoint_ns is None else checkpoint_ns
            cp = row["head_checkpoint_id"] if checkpoint_id is None else checkpoint_id
            run = await (
                await db.execute(
                    "SELECT status,head_checkpoint_ns,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
                    (row["run_id"],),
                )
            ).fetchone()
            owner = await (
                await db.execute(
                    """SELECT 1 FROM workflow_checkpoint_owners
                    WHERE run_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                    (row["run_id"], ns, cp),
                )
            ).fetchone()
            if (
                run is None
                or owner is None
                or run["head_checkpoint_ns"] != ns
                or run["head_checkpoint_id"] != cp
                or (new_status in {"observed", "settled"} and run["status"] != "running")
            ):
                raise ResearchRepositoryError(
                    "control_checkpoint_cas_failed", "control checkpoint is not the durable run head"
                )
            columns = {
                "observed": "observed_at",
                "settled": "settled_at",
                "consumed": "consumed_at",
            }
            timestamp_column = columns.get(new_status)
            timestamp_sql = f",{timestamp_column}=?" if timestamp_column else ""
            params: list[Any] = [new_status, ns, cp, _canonical(envelope), now]
            if timestamp_column:
                params.append(now)
            params.extend([command_id, expected_status])
            cursor = await db.execute(
                f"""UPDATE workflow_run_control_commands SET status=?,head_checkpoint_ns=?,
                head_checkpoint_id=?,payload_json=?,updated_at=?{timestamp_sql}
                WHERE command_id=? AND status=?""",
                tuple(params),
            )
            if cursor.rowcount != 1:
                raise ResearchRepositoryError("control_cas_failed", "control transition lost its CAS")
            updated = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            await db.commit()
            assert updated is not None
            return _command(updated)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def active_control(self, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_run_control_commands
                    WHERE run_id=? AND status IN ('open','accepted','observed','settled')
                    ORDER BY created_at,command_id""",
                    (run_id,),
                )
            ).fetchall()
            if len(rows) > 1:
                raise ResearchRepositoryError("multiple_active_controls", "run has multiple active controls")
            return _command(rows[0]) if rows else None
        finally:
            await db.close()

    async def request_cancel_settle(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        expected_run_version: int,
    ) -> dict[str, Any]:
        """Arm cancel-settle on the existing generate-now fence.

        A second command row would make node polling ambiguous.  The bounded
        marker therefore lives on the single accepted/observed command, while
        this method returns an action projection suitable for the control hub.
        """

        if not idempotency_key or expected_run_version < 0:
            raise ResearchRepositoryError("invalid_cancel_settle", "invalid cancel-settle identity")
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_run_control_commands
                    WHERE run_id=? AND action='generate_now'
                      AND status IN ('accepted','observed')
                    ORDER BY created_at,command_id""",
                    (run_id,),
                )
            ).fetchall()
            if len(row) != 1:
                raise ResearchRepositoryError(
                    "cancel_settle_not_available", "run has no unique accepted generate-now fence"
                )
            command_row = row[0]
            run = await (
                await db.execute(
                    """SELECT workflow_name,workflow_version,status,run_version,
                    head_checkpoint_ns,head_checkpoint_id FROM workflow_runs WHERE run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if (
                run is None
                or run["workflow_name"] != "deep_research"
                or run["workflow_version"] not in {"v5", "v6"}
                or run["status"] != "running"
                or int(run["run_version"]) != expected_run_version
                or run["head_checkpoint_ns"] != command_row["head_checkpoint_ns"]
                or run["head_checkpoint_id"] != command_row["head_checkpoint_id"]
            ):
                raise ResearchRepositoryError(
                    "cancel_settle_cas_failed", "cancel-settle lost the run/head/version CAS"
                )
            if run["workflow_version"] == "v6":
                deadline = await (
                    await db.execute(
                        """SELECT wall_not_after,status FROM workflow_research_deadlines
                        WHERE run_id=? AND logical_scope='run:automatic'
                          AND parent_deadline_id IS NULL""",
                        (run_id,),
                    )
                ).fetchone()
                if (
                    deadline is None
                    or deadline["status"] != "open"
                    or now >= float(deadline["wall_not_after"])
                ):
                    raise ResearchRepositoryError(
                        "cancel_settle_wall_guard", "v6 automatic wall guard has expired"
                    )
            envelope = json.loads(str(command_row["payload_json"]))
            marker = envelope.get("_cancel_settle")
            created = marker is None
            if marker is not None:
                if not isinstance(marker, dict) or marker.get("idempotency_key") != idempotency_key:
                    raise ResearchRepositoryError(
                        "cancel_settle_idempotency_conflict",
                        "generate-now fence is already bound to another cancel-settle request",
                    )
            else:
                marker = {
                    "idempotency_key": idempotency_key,
                    "requested_at": now,
                    "expected_run_version": expected_run_version,
                    "head_checkpoint_ns": str(run["head_checkpoint_ns"]),
                    "head_checkpoint_id": run["head_checkpoint_id"],
                }
                envelope["_cancel_settle"] = marker
                cursor = await db.execute(
                    """UPDATE workflow_run_control_commands SET payload_json=?,updated_at=?
                    WHERE command_id=? AND status IN ('accepted','observed')""",
                    (_canonical(envelope), now, command_row["command_id"]),
                )
                if cursor.rowcount != 1:
                    raise ResearchRepositoryError(
                        "cancel_settle_cas_failed", "generate-now fence changed"
                    )
                command_row = await (
                    await db.execute(
                        "SELECT * FROM workflow_run_control_commands WHERE command_id=?",
                        (command_row["command_id"],),
                    )
                ).fetchone()
                assert command_row is not None
            await db.commit()
            projection = _command(command_row)
            projection["source_action"] = "generate_now"
            projection["action"] = "cancel_settle"
            projection["idempotency_key"] = idempotency_key
            projection["cancel_settle"] = copy.deepcopy(marker)
            projection["created"] = created
            return projection
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def expired_control_candidates(self, *, now: float | None = None) -> dict[str, tuple[str, ...]]:
        """Return watchdog and cleanup candidates without mutating retention state."""

        instant = float(self._clock() if now is None else now)
        await self.initialize()
        db = await self._connect()
        try:
            settle = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE status IN ('accepted','observed') AND settle_deadline IS NOT NULL
                      AND settle_deadline<=? ORDER BY command_id""",
                    (instant,),
                )
            ).fetchall()
            cleanup = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE status IN ('consumed','rejected','expired') AND updated_at<=?
                    ORDER BY command_id""",
                    (instant,),
                )
            ).fetchall()
            return {
                "settle_deadline_exceeded": tuple(str(row[0]) for row in settle),
                "cleanup_eligible": tuple(str(row[0]) for row in cleanup),
            }
        finally:
            await db.close()

    async def persist_snapshot_manifest(
        self,
        *,
        run_id: str,
        operation_id: str,
        manifest: Mapping[str, Any],
        manifest_ref: str,
        continue_until: float,
        pin_kind: str = "continue_parent",
        pin_id: str | None = None,
    ) -> tuple[str, bool]:
        """Persist and parent-pin a content-addressed manifest atomically."""

        content = copy.deepcopy(dict(manifest))
        declared_hash = content.pop("snapshot_hash", None)
        encoded = _canonical(content)
        snapshot_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if declared_hash is not None and declared_hash != snapshot_hash:
            raise ResearchRepositoryError("snapshot_hash_mismatch", "manifest hash is not canonical")
        schema_version = content.get("schema_version")
        if not isinstance(schema_version, int) or schema_version <= 0:
            raise ResearchRepositoryError("snapshot_schema_invalid", "snapshot schema_version must be positive")
        if content.get("parent_run_id") != run_id:
            raise ResearchRepositoryError("snapshot_parent_mismatch", "manifest parent does not match run")
        if not manifest_ref or continue_until <= float(self._clock()):
            raise ResearchRepositoryError("snapshot_expiry_invalid", "snapshot must have a future continue_until")
        if len(manifest_ref) != 64 or any(ch not in "0123456789abcdef" for ch in manifest_ref):
            raise ResearchRepositoryError("snapshot_manifest_ref_invalid", "manifest_ref must be a blob SHA-256")
        if manifest_ref != snapshot_hash:
            raise ResearchRepositoryError(
                "snapshot_manifest_ref_mismatch",
                "manifest_ref must address the exact canonical snapshot manifest",
            )
        passage_refs = content.get("passage_blob_refs")
        if not isinstance(passage_refs, list) or any(
            not isinstance(ref, str)
            or len(ref) != 64
            or any(ch not in "0123456789abcdef" for ch in ref)
            for ref in passage_refs
        ):
            raise ResearchRepositoryError(
                "snapshot_passage_refs_invalid", "snapshot passages must be blob SHA-256 references"
            )
        now = float(self._clock())
        pin_id = pin_id or hashlib.sha256(
            f"{snapshot_hash}|{run_id}|{pin_kind}".encode()
        ).hexdigest()
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise ResearchRepositoryError("run_not_found", "snapshot run was not found")
            blob_refs = {manifest_ref, *passage_refs}
            placeholders = ",".join("?" for _ in blob_refs)
            registered = await (
                await db.execute(
                    f"SELECT sha256 FROM workflow_blobs WHERE sha256 IN ({placeholders})",
                    tuple(sorted(blob_refs)),
                )
            ).fetchall()
            registered_refs = {str(row[0]) for row in registered}
            if registered_refs != blob_refs:
                raise ResearchRepositoryError(
                    "snapshot_blob_missing", "snapshot manifest or passage blob is not registered"
                )
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=?", (snapshot_hash,)
                )
            ).fetchone()
            created = existing is None
            if existing is not None and (
                existing["run_id"] != run_id
                or existing["operation_id"] != operation_id
                or int(existing["schema_version"]) != schema_version
                or existing["manifest_ref"] != manifest_ref
                or float(existing["expires_at"]) != float(continue_until)
            ):
                raise ResearchRepositoryError("snapshot_conflict", "snapshot hash has conflicting metadata")
            if existing is None:
                await db.execute(
                    """INSERT INTO workflow_research_snapshots(
                    snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (snapshot_hash, run_id, operation_id, schema_version, manifest_ref, now, continue_until),
                )
            for blob_ref in sorted(blob_refs):
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'research_snapshot',?,?)""",
                    (blob_ref, snapshot_hash, now),
                )
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(snapshot_hash,run_id,pin_kind) DO NOTHING""",
                (pin_id, snapshot_hash, run_id, pin_kind, continue_until, now),
            )
            pin = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind=?""",
                    (snapshot_hash, run_id, pin_kind),
                )
            ).fetchone()
            if pin is None or float(pin["expires_at"]) != float(continue_until):
                raise ResearchRepositoryError("snapshot_pin_conflict", "parent snapshot pin conflicts")
            await db.commit()
            return snapshot_hash, created
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def resolve_continuation_source(self, parent_run_id: str) -> dict[str, Any]:
        """Resolve the only authoritative, currently continuable parent snapshot.

        The UI deliberately has no role in choosing lineage or snapshot identity.
        A later create transaction revalidates every value returned here, so an
        expiry or terminal-state change between resolution and creation fails
        closed.
        """

        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            parent = await (
                await db.execute(
                    "SELECT status,workflow_version FROM workflow_runs WHERE run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if (
                parent is None
                or str(parent["status"]) != "completed"
                or str(parent["workflow_version"]) != "v5"
            ):
                raise ResearchRepositoryError(
                    "parent_not_continuable", "parent is not a completed v5 run"
                )
            lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if lineage is None:
                raise ResearchRepositoryError(
                    "parent_lineage_missing", "parent lineage was not found"
                )
            snapshot = await (
                await db.execute(
                    """SELECT s.* FROM workflow_research_snapshots s
                    JOIN workflow_research_snapshot_pins p
                      ON p.snapshot_hash=s.snapshot_hash AND p.run_id=s.run_id
                    WHERE s.run_id=? AND s.operation_id=?
                      AND p.pin_kind='continue_parent'
                      AND (p.expires_at IS NULL OR p.expires_at>?)
                      AND (s.expires_at IS NULL OR s.expires_at>?)
                    ORDER BY s.created_at DESC,s.snapshot_hash DESC LIMIT 1""",
                    (parent_run_id, str(lineage["operation_id"]), now, now),
                )
            ).fetchone()
            if snapshot is None:
                raise ResearchRepositoryError(
                    "snapshot_expired", "continuation snapshot is missing or expired"
                )
            events = await (
                await db.execute(
                    """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                    (parent_run_id,),
                )
            ).fetchall()
            final_payload: dict[str, Any] | None = None
            for event in events:
                payload = json.loads(str(event["payload_json"]))
                if payload.get("delivery_status") in _CONTINUABLE_DELIVERY:
                    final_payload = payload
                    break
            if final_payload is None:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable", "parent delivery is not continuable"
                )
            return {
                "parent_run_id": parent_run_id,
                "parent_operation_id": str(lineage["operation_id"]),
                "snapshot_hash": str(snapshot["snapshot_hash"]),
                "manifest_ref": str(snapshot["manifest_ref"]),
                "parent_report_ref": (
                    str(final_payload["report_ref"])
                    if final_payload.get("report_ref") is not None
                    else None
                ),
                "delivery_status": str(final_payload["delivery_status"]),
            }
        finally:
            await db.close()

    async def ensure_root_lineage(
        self, *, run_id: str, operation_id: str, budget_lease_id: str
    ) -> tuple[dict[str, Any], bool]:
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? OR run_id=?",
                    (operation_id, run_id),
                )
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] != operation_id
                    or existing["run_id"] != run_id
                    or existing["parent_run_id"] is not None
                    or existing["budget_lease_id"] != budget_lease_id
                ):
                    raise ResearchRepositoryError("lineage_conflict", "root lineage is immutable")
                await db.commit()
                return dict(existing), False
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at
                ) VALUES(?,?,NULL,NULL,NULL,NULL,?,?)""",
                (operation_id, run_id, budget_lease_id, now),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=?", (operation_id,)
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return dict(row), True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def ensure_snapshot_lineage(
        self, *, run_id: str, operation_id: str, budget_lease_id: str
    ) -> tuple[dict[str, Any], bool]:
        """Ensure a snapshot owner exists without rewriting child lineage."""

        await self.initialize()
        db = await self._connect()
        try:
            existing = await (
                await db.execute(
                    """SELECT * FROM workflow_research_lineage
                    WHERE operation_id=? OR run_id=?""",
                    (operation_id, run_id),
                )
            ).fetchone()
        finally:
            await db.close()
        if existing is not None:
            if (
                str(existing["operation_id"]) != operation_id
                or str(existing["run_id"]) != run_id
            ):
                raise ResearchRepositoryError(
                    "lineage_conflict", "snapshot lineage identity is immutable"
                )
            return dict(existing), False
        return await self.ensure_root_lineage(
            run_id=run_id,
            operation_id=operation_id,
            budget_lease_id=budget_lease_id,
        )

    async def create_child_from_snapshot(
        self,
        *,
        parent_run_id: str,
        parent_operation_id: str,
        snapshot_hash: str,
        child_run_id: str,
        child_operation_id: str,
        child_trace_id: str,
        child_thread_id: str,
        child_request_key: str,
        child_request_id: str,
        child_turn_id: str,
        budget_lease_id: str,
        start_payload: Mapping[str, Any],
        parent_report_ref: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create a continuation run, pin, lineage and start payload in one txn."""

        now = float(self._clock())
        request_hash = hashlib.sha256(_canonical(dict(start_payload)).encode("utf-8")).hexdigest()
        start_operation_id = f"{child_operation_id}:start"
        if start_payload.get("snapshot_hash") != snapshot_hash:
            raise ResearchRepositoryError(
                "child_start_snapshot_mismatch", "child start payload must bind the snapshot hash"
            )
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? OR run_id=?",
                    (child_operation_id, child_run_id),
                )
            ).fetchone()
            if existing is not None:
                child = await (
                    await db.execute(
                        """SELECT trace_id,thread_id,request_id,turn_id
                        FROM workflow_runs WHERE run_id=?""",
                        (child_run_id,),
                    )
                ).fetchone()
                start = await (
                    await db.execute(
                        """SELECT request_hash FROM workflow_operations
                        WHERE operation_id=? AND run_id=? AND operation_kind='research_continue_start'""",
                        (start_operation_id, child_run_id),
                    )
                ).fetchone()
                start_request = await (
                    await db.execute(
                        "SELECT request_key FROM workflow_start_requests WHERE run_id=?",
                        (child_run_id,),
                    )
                ).fetchone()
                if (
                    existing["operation_id"] != child_operation_id
                    or existing["run_id"] != child_run_id
                    or existing["parent_run_id"] != parent_run_id
                    or existing["parent_operation_id"] != parent_operation_id
                    or existing["snapshot_hash"] != snapshot_hash
                    or existing["parent_report_ref"] != parent_report_ref
                    or existing["budget_lease_id"] != budget_lease_id
                    or child is None
                    or child["trace_id"] != child_trace_id
                    or child["thread_id"] != child_thread_id
                    or child["request_id"] != child_request_id
                    or child["turn_id"] != child_turn_id
                    or start is None
                    or start["request_hash"] != request_hash
                    or start_request is None
                    or start_request["request_key"] != child_request_key
                ):
                    raise ResearchRepositoryError("lineage_conflict", "child lineage is immutable")
                await db.commit()
                return dict(existing), False
            parent = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (parent_run_id,))
            ).fetchone()
            if parent is None or parent["status"] != "completed" or str(parent["workflow_version"]) != "v5":
                raise ResearchRepositoryError("parent_not_continuable", "parent is not a completed v5 run")
            parent_capability = await (
                await db.execute(
                    "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                    (parent["capability_hash"],),
                )
            ).fetchone()
            if parent_capability is None:
                raise ResearchRepositoryError(
                    "parent_start_snapshot_missing", "parent start snapshot was not found"
                )
            parent_snapshot = json.loads(str(parent_capability["snapshot_json"]))
            parent_start = (
                parent_snapshot.get("_workflow_start")
                if isinstance(parent_snapshot, Mapping)
                else None
            )
            parent_identity = (
                parent_start.get("identity") if isinstance(parent_start, Mapping) else None
            )
            if not isinstance(parent_snapshot, Mapping):
                raise ResearchRepositoryError(
                    "parent_start_snapshot_invalid", "parent start snapshot is invalid"
                )
            if not isinstance(parent_identity, Mapping):
                parent_identity = {
                    "venue": "chat",
                    "base_session_id": str(parent["session_id"]),
                    "code_session_id": "",
                    "delivery_session_id": str(parent["session_id"]),
                    "base_epoch": 0,
                    "code_epoch": 0,
                    "request_id": str(parent["request_id"]),
                    "turn_id": str(parent["turn_id"]),
                    "workflow_name": str(parent["workflow_name"]),
                    "logical_slot": f"legacy:{parent_run_id}",
                }
            original_capabilities = {
                str(key): copy.deepcopy(value)
                for key, value in parent_snapshot.items()
                if key != "_workflow_start"
            }
            child_identity = {
                **copy.deepcopy(dict(parent_identity)),
                "request_id": child_request_id,
                "turn_id": child_turn_id,
                "logical_slot": child_request_key,
            }
            capability_content_hash = hashlib.sha256(
                _canonical(original_capabilities).encode("utf-8")
            ).hexdigest()
            args_hash = hashlib.sha256(
                _canonical(dict(start_payload)).encode("utf-8")
            ).hexdigest()
            child_start = {
                **original_capabilities,
                "_workflow_start": {
                    "identity": child_identity,
                    "identity_key": hashlib.sha256(
                        _canonical(child_identity).encode("utf-8")
                    ).hexdigest(),
                    "request_hash": request_hash,
                    "args_hash": args_hash,
                    "capability_hash": capability_content_hash,
                    "start_payload": copy.deepcopy(dict(start_payload)),
                },
            }
            child_snapshot_json = _canonical(child_start)
            child_capability_hash = hashlib.sha256(
                child_snapshot_json.encode("utf-8")
            ).hexdigest()
            parent_lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? AND run_id=?",
                    (parent_operation_id, parent_run_id),
                )
            ).fetchone()
            if parent_lineage is None:
                raise ResearchRepositoryError("parent_lineage_missing", "parent lineage was not found")
            snapshot = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=? AND run_id=?",
                    (snapshot_hash, parent_run_id),
                )
            ).fetchone()
            pin = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'
                      AND (expires_at IS NULL OR expires_at>?)""",
                    (snapshot_hash, parent_run_id, now),
                )
            ).fetchone()
            if snapshot is None or pin is None or (
                snapshot["expires_at"] is not None and float(snapshot["expires_at"]) <= now
            ):
                raise ResearchRepositoryError("snapshot_expired", "continuation snapshot is missing or expired")
            if snapshot["operation_id"] != parent_operation_id:
                raise ResearchRepositoryError(
                    "snapshot_operation_mismatch", "snapshot is not owned by the parent operation"
                )
            manifest_blob = await (
                await db.execute(
                    "SELECT 1 FROM workflow_blobs WHERE sha256=?", (snapshot["manifest_ref"],)
                )
            ).fetchone()
            if manifest_blob is None:
                raise ResearchRepositoryError("snapshot_blob_missing", "snapshot manifest blob is missing")
            events = await (
                await db.execute(
                    """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                    (parent_run_id,),
                )
            ).fetchall()
            final_payload: dict[str, Any] | None = None
            for event in events:
                payload = json.loads(str(event["payload_json"]))
                if payload.get("delivery_status") in _CONTINUABLE_DELIVERY:
                    final_payload = payload
                    break
            if final_payload is None:
                raise ResearchRepositoryError("parent_delivery_not_continuable", "parent delivery is not continuable")
            persisted_report = final_payload.get("report_ref")
            if persisted_report is not None and parent_report_ref != persisted_report:
                raise ResearchRepositoryError("parent_report_mismatch", "parent report reference changed")
            await db.execute(
                """INSERT OR IGNORE INTO workflow_capabilities(
                capability_hash,snapshot_json,created_at
                ) VALUES(?,?,?)""",
                (child_capability_hash, child_snapshot_json, now),
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
                    child_thread_id,
                    parent["checkpoint_ns"],
                    parent["checkpoint_ns"],
                    parent_run_id,
                    parent["head_checkpoint_id"],
                    parent["session_id"],
                    child_request_id,
                    child_turn_id,
                    parent["workflow_name"],
                    parent["workflow_version"],
                    parent["manifest_hash"],
                    parent["implementation_hash"],
                    child_capability_hash,
                    parent["state_schema_version"],
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    child_request_key,
                    parent["session_id"],
                    child_request_id,
                    child_turn_id,
                    parent["workflow_name"],
                    parent["capability_hash"],
                    child_run_id,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at
                ) SELECT ?,session_kind,session_id,session_epoch,NULL
                FROM workflow_session_refs WHERE run_id=? AND deleted_at IS NULL""",
                (child_run_id, parent_run_id),
            )
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    child_operation_id,
                    child_run_id,
                    parent_run_id,
                    parent_operation_id,
                    snapshot_hash,
                    parent_report_ref,
                    budget_lease_id,
                    now,
                ),
            )
            child_pin_id = hashlib.sha256(
                f"{snapshot_hash}|{child_run_id}|continue_child".encode()
            ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_child',NULL,?)""",
                (child_pin_id, snapshot_hash, child_run_id, now),
            )
            start_result = {
                "schema_version": 1,
                "parent_run_id": parent_run_id,
                "parent_operation_id": parent_operation_id,
                "snapshot_hash": snapshot_hash,
                "payload": copy.deepcopy(dict(start_payload)),
            }
            await db.execute(
                """INSERT INTO workflow_operations(
                operation_id,run_id,operation_kind,request_hash,result_json,created_at
                ) VALUES(?,?,'research_continue_start',?,?,?)""",
                (start_operation_id, child_run_id, request_hash, _canonical(start_result), now),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=?", (child_operation_id,)
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return dict(row), True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _read_registered_bytes_v6(
        self, db: aiosqlite.Connection, wire_ref: object, *, field: str
    ) -> tuple[str, bytes]:
        if self.blob_root is None:
            raise ResearchRepositoryError(
                "v6_blob_root_missing", "v6 continuation repository requires a registered blob root"
            )
        digest = _wire_digest(wire_ref, field)
        row = await (
            await db.execute(
                "SELECT size_bytes,relative_path FROM workflow_blobs WHERE sha256=?", (digest,)
            )
        ).fetchone()
        if row is None:
            raise ResearchRepositoryError("v6_blob_missing", f"{field} is not registered")
        root = self.blob_root.resolve()
        target = (root / str(row["relative_path"])).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ResearchRepositoryError("v6_blob_path_invalid", f"{field} escaped blob root") from exc
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ResearchRepositoryError("v6_blob_missing", f"{field} file is unavailable") from exc
        if len(data) != int(row["size_bytes"]) or hashlib.sha256(data).hexdigest() != digest:
            raise ResearchRepositoryError("v6_blob_digest_mismatch", f"{field} failed digest validation")
        return digest, data

    async def _read_registered_json_v6(
        self, db: aiosqlite.Connection, wire_ref: object, *, field: str
    ) -> tuple[str, dict[str, Any]]:
        """Decode one registered canonical JSON blob through repository-owned storage."""

        digest, data = await self._read_registered_bytes_v6(db, wire_ref, field=field)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchRepositoryError("v6_blob_json_invalid", f"{field} is not JSON") from exc
        if not isinstance(value, dict) or _canonical(value).encode("utf-8") != data:
            raise ResearchRepositoryError("v6_blob_json_noncanonical", f"{field} is not canonical JSON")
        return digest, value

    @staticmethod
    def _validate_v6_snapshot(value: Mapping[str, Any], *, parent_run_id: str) -> None:
        """Synchronous wire-shape validator retained for retention compatibility."""

        if set(value) != _V6_SNAPSHOT_KEYS:
            raise ResearchRepositoryError("v6_snapshot_keys_invalid", "v6 snapshot exact keys differ")
        if (
            value.get("schema_version") != 1
            or value.get("run_id") != parent_run_id
            or value.get("workflow_name") != "deep_research"
            or value.get("workflow_version") != "v6"
        ):
            raise ResearchRepositoryError("v6_snapshot_identity_invalid", "v6 snapshot identity differs")
        semantic = dict(value)
        snapshot_id = semantic.pop("snapshot_id", None)
        snapshot_hash = semantic.pop("snapshot_hash", None)
        expected = _sha256_text(_canonical(semantic))
        if snapshot_hash != expected or snapshot_id != "rcs_" + expected[:24]:
            raise ResearchRepositoryError("v6_snapshot_hash_invalid", "v6 snapshot semantic hash differs")
        policies = value.get("policy_refs")
        if not isinstance(policies, dict) or set(policies) != _V6_POLICY_KEYS:
            raise ResearchRepositoryError("v6_snapshot_policy_invalid", "v6 snapshot policy refs differ")
        lists = ("fact_batch_refs", "provenance_refs", "closure_refs")
        if any(not isinstance(value.get(name), list) for name in lists):
            raise ResearchRepositoryError("v6_snapshot_refs_invalid", "v6 snapshot refs are not arrays")
        expected_closure = {
            str(value["spec_ref"]), str(value["assessment_ref"]), str(value["claim_batch_ref"]),
            *[str(item) for item in value["fact_batch_refs"]],
            *[str(item) for item in value["provenance_refs"]],
            *[str(item) for item in policies.values()],
        }
        closure = [str(item) for item in value["closure_refs"]]
        if closure != sorted(set(closure)) or set(closure) != expected_closure:
            raise ResearchRepositoryError("v6_snapshot_closure_invalid", "v6 snapshot closure is not exact")
        for field in ("spec_ref", "assessment_ref", "claim_batch_ref"):
            _wire_digest(value[field], field)
        for ref in closure:
            _wire_digest(ref, "closure_ref")

    async def _validate_v6_snapshot_graph(
        self,
        db: aiosqlite.Connection,
        value: Mapping[str, Any],
        *,
        parent_run_id: str,
    ) -> None:
        """Decode and recompute the exact registered v6 continuation graph."""

        if set(value) != _V6_SNAPSHOT_KEYS:
            raise ResearchRepositoryError("v6_snapshot_keys_invalid", "v6 snapshot exact keys differ")
        if (
            value.get("schema_version") != 1
            or value.get("run_id") != parent_run_id
            or value.get("workflow_name") != "deep_research"
            or value.get("workflow_version") != "v6"
        ):
            raise ResearchRepositoryError("v6_snapshot_identity_invalid", "v6 snapshot identity differs")
        semantic = dict(value)
        snapshot_id = semantic.pop("snapshot_id", None)
        snapshot_hash = semantic.pop("snapshot_hash", None)
        expected = _sha256_text(_canonical(semantic))
        if snapshot_hash != expected or snapshot_id != "rcs_" + expected[:24]:
            raise ResearchRepositoryError("v6_snapshot_hash_invalid", "v6 snapshot semantic hash differs")
        policies = value.get("policy_refs")
        if not isinstance(policies, dict) or set(policies) != _V6_POLICY_KEYS:
            raise ResearchRepositoryError("v6_snapshot_policy_invalid", "v6 snapshot policy refs differ")
        lists = ("fact_batch_refs", "provenance_refs", "closure_refs")
        if any(not isinstance(value.get(name), list) for name in lists):
            raise ResearchRepositoryError("v6_snapshot_refs_invalid", "v6 snapshot refs are not arrays")
        batches_raw = [str(item) for item in value["fact_batch_refs"]]
        provenance = [str(item) for item in value["provenance_refs"]]
        if not batches_raw or len(batches_raw) != len(set(batches_raw)):
            raise ResearchRepositoryError("v6_snapshot_refs_invalid", "v6 fact batch refs are empty or repeated")
        if provenance != sorted(set(provenance)):
            raise ResearchRepositoryError("v6_snapshot_refs_invalid", "v6 provenance refs are not sorted unique")
        expected_closure = {
            str(value["spec_ref"]), str(value["assessment_ref"]), str(value["claim_batch_ref"]),
            *[str(item) for item in value["fact_batch_refs"]],
            *[str(item) for item in value["provenance_refs"]],
            *[str(item) for item in policies.values()],
        }
        closure = [str(item) for item in value["closure_refs"]]
        if closure != sorted(set(closure)) or set(closure) != expected_closure:
            raise ResearchRepositoryError("v6_snapshot_closure_invalid", "v6 snapshot closure is not exact")
        for field in ("spec_ref", "assessment_ref", "claim_batch_ref"):
            _wire_digest(value[field], field)
        for ref in closure:
            _wire_digest(ref, "closure_ref")

        top_level = {
            str(value["spec_ref"]), str(value["assessment_ref"]),
            str(value["claim_batch_ref"]), *batches_raw,
        }
        policy_values = {str(ref) for ref in policies.values()}
        if top_level & policy_values or set(provenance) & (top_level | policy_values):
            raise ResearchRepositoryError(
                "v6_snapshot_ref_class_invalid",
                "v6 snapshot places a registered ref in the wrong class",
            )

        from ..definitions.deep_research_v6_contracts import (
            ResearchSpecV1,
            RouteDecisionV1,
        )
        from ..definitions.deep_research_v6_evidence import (
            GENESIS_EVIDENCE_HEAD,
            AdmittedResearchFactV1,
            AnswerAssessmentV1,
            EvidenceFactBatchV1,
            RegisteredInferenceV1,
            derive_assessment_input_hash,
        )
        from ..definitions.deep_research_v6_evidence_contracts import (
            CandidateProducerOutcomeV1,
            EvidenceCandidateBundleV1,
            EvidenceRepairRequestV1,
            InferenceProposalBundleV1,
            ResearchLLMEffectOutcomeV1,
        )
        from ..definitions.deep_research_v6_integrity import ClaimBatchV1
        from ..definitions.deep_research_v6_retrieval_contracts import (
            OfficialSearchResultV1,
            PageAttemptOutcomeV1,
            PageExtractionResultV1,
            SourceLocatorV1,
        )

        async def read_json(ref: object, field: str) -> dict[str, Any]:
            _, item = await self._read_registered_json_v6(db, ref, field=field)
            return item

        try:
            spec = ResearchSpecV1.from_json(await read_json(value["spec_ref"], "spec_ref"))
            assessment_value = await read_json(value["assessment_ref"], "assessment_ref")
            assessment = AnswerAssessmentV1.from_json(assessment_value)
            claim_value = await read_json(value["claim_batch_ref"], "claim_batch_ref")
            claim = ClaimBatchV1.from_json(claim_value)
        except (TypeError, ValueError) as exc:
            raise ResearchRepositoryError(
                "v6_snapshot_object_invalid", "v6 snapshot top-level object is invalid"
            ) from exc

        policy_objects: dict[str, dict[str, Any]] = {}
        for policy_name, policy_ref in policies.items():
            policy = await read_json(policy_ref, f"{policy_name}_policy_ref")
            # Production v6 route-policy-v1 predates the generic policy
            # ``schema_version`` field and encodes its version in the immutable
            # policy_id.  Those content-addressed bytes may already be reachable
            # from completed runs, so restart recovery must recognize that one
            # exact historical shape.  Every other policy remains strict v1.
            legacy_route_v1 = (
                policy_name == "route"
                and set(policy) == _V6_ROUTE_POLICY_V1_KEYS
                and policy.get("policy_id")
                == "deep-research-v6-official-retrieval-v1"
                and all(
                    isinstance(policy.get(name), int)
                    and not isinstance(policy.get(name), bool)
                    and int(policy[name]) > 0
                    for name in (
                        "parent_deadline_ms",
                        "page_deadline_ms",
                        "max_queries",
                        "max_fetches",
                        "max_fetch_concurrency",
                        "max_lanes",
                    )
                )
                and isinstance(policy.get("official_source_policy_hash"), str)
                and len(str(policy["official_source_policy_hash"])) == 64
                and not set(str(policy["official_source_policy_hash"]))
                - set("0123456789abcdef")
            )
            if policy.get("schema_version") != 1 and not legacy_route_v1:
                raise ResearchRepositoryError(
                    "v6_snapshot_policy_invalid", f"{policy_name} policy version differs"
                )
            policy_objects[str(policy_name)] = policy

        batches: list[Any] = []
        previous_head = GENESIS_EVIDENCE_HEAD
        ordered_fact_refs: list[str] = []
        ordered_inference_refs: list[str] = []
        expected_kinds: dict[str, str] = {}

        def expect(ref: object, kind: str) -> None:
            wire = str(ref)
            _wire_digest(wire, f"{kind}_ref")
            existing = expected_kinds.get(wire)
            if existing is not None and existing != kind:
                raise ResearchRepositoryError(
                    "v6_snapshot_ref_class_invalid",
                    f"one v6 ref is classified as both {existing} and {kind}",
                )
            expected_kinds[wire] = kind

        for ordinal, batch_ref in enumerate(batches_raw):
            try:
                batch = EvidenceFactBatchV1.from_json(
                    await read_json(batch_ref, f"fact_batch_refs[{ordinal}]")
                )
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError(
                    "v6_snapshot_batch_invalid", "v6 evidence batch is invalid"
                ) from exc
            if (
                batch.ordinal != ordinal
                or batch.previous_head_hash != previous_head
                or batch.spec_hash != value["spec_hash"]
                or batch.policy_refs != policies
            ):
                raise ResearchRepositoryError(
                    "v6_snapshot_batch_chain_invalid", "v6 evidence batch chain differs"
                )
            previous_head = batch.head_hash
            batches.append(batch)
            ordered_fact_refs.extend(batch.admitted_fact_refs)
            ordered_inference_refs.extend(batch.registered_inference_refs)
            for ref in batch.page_result_refs:
                expect(ref, "page")
            for ref in batch.admitted_fact_refs:
                expect(ref, "fact")
            for ref in batch.registered_inference_refs:
                expect(ref, "inference")
            for slot in batch.candidate_slot_results:
                expect(slot["producer_outcome_ref"], "producer")
                if slot["bundle_ref"] is not None:
                    expect(slot["bundle_ref"], "candidate_bundle")
            for slot in batch.inference_slot_results:
                expect(slot["effect_outcome_ref"], "llm_outcome")
                if slot["proposal_bundle_ref"] is not None:
                    expect(slot["proposal_bundle_ref"], "inference_bundle")

        if batches[-1].run_id != parent_run_id or previous_head != value["evidence_head_hash"]:
            raise ResearchRepositoryError(
                "v6_snapshot_batch_chain_invalid", "v6 final evidence head differs"
            )
        assessment_policy_hash = _wire_digest(policies["assessment"], "assessment_policy_ref")
        expected_input_hash = derive_assessment_input_hash(
            spec_hash=str(value["spec_hash"]),
            evidence_head_hash=str(value["evidence_head_hash"]),
            ordered_fact_refs=ordered_fact_refs,
            ordered_inference_refs=ordered_inference_refs,
            assessment_policy_hash=assessment_policy_hash,
        )
        if any((
            spec.spec_hash != value["spec_hash"],
            assessment.spec_hash != value["spec_hash"],
            assessment.evidence_head_hash != value["evidence_head_hash"],
            assessment.assessment_hash != value["assessment_hash"],
            assessment.assessment_input_hash != value["assessment_input_hash"],
            assessment.assessment_input_hash != expected_input_hash,
            assessment.policy_hash != assessment_policy_hash,
            claim.run_id != parent_run_id,
            claim.spec_hash != value["spec_hash"],
            claim.evidence_head_hash != value["evidence_head_hash"],
            claim.assessment_hash != value["assessment_hash"],
            claim.claim_policy_hash != _wire_digest(policies["claim"], "claim_policy_ref"),
        )):
            raise ResearchRepositoryError(
                "v6_snapshot_cross_hash_invalid", "v6 snapshot semantic cross-hash differs"
            )

        async def decode_expected(ref: str, kind: str, item: Mapping[str, Any]) -> None:
            if kind == "page":
                PageExtractionResultV1.from_json(item)
            elif kind == "fact":
                AdmittedResearchFactV1.from_json(item)
            elif kind == "inference":
                RegisteredInferenceV1.from_json(item)
            elif kind == "producer":
                CandidateProducerOutcomeV1.from_json(item)
            elif kind == "inference_bundle":
                InferenceProposalBundleV1.from_json(item)
            elif kind == "llm_outcome":
                ResearchLLMEffectOutcomeV1.from_json(item)
            elif kind == "candidate_bundle":
                page_ref = item.get("page_result_ref")
                page_value = await read_json(page_ref, "candidate_bundle.page_result_ref")
                page = PageExtractionResultV1.from_json(page_value)
                body = b""
                if page.page_record is not None:
                    _, body = await self._read_registered_bytes_v6(
                        db, page.page_record["body_ref"], field="candidate_bundle.body_ref"
                    )
                EvidenceCandidateBundleV1.from_json(item, body_bytes=body)
            else:  # pragma: no cover - expect() owns this closed set
                raise AssertionError(kind)

        def decode_known(item: Mapping[str, Any]) -> None:
            """Strictly decode every registered v6 object kind with a public decoder."""

            if "route_id" in item:
                RouteDecisionV1.from_json(item)
            elif "locator_id" in item:
                SourceLocatorV1.from_json(item)
            elif "outcome_id" in item and "ordinal" in item and "canonical_effect_id" in item:
                PageAttemptOutcomeV1.from_json(item)
            elif "repair_id" in item:
                EvidenceRepairRequestV1.from_json(item)
            elif "result_id" in item and "logical_page_id" in item and "page_record" in item:
                PageExtractionResultV1.from_json(item)
            elif "result_id" in item and "candidates" in item and "logical_effect_id" in item:
                OfficialSearchResultV1.from_json(item)
            elif "fact_id" in item:
                AdmittedResearchFactV1.from_json(item)
            elif "inference_id" in item:
                RegisteredInferenceV1.from_json(item)
            elif "outcome_id" in item and "origin" in item:
                CandidateProducerOutcomeV1.from_json(item)
            elif "outcome_id" in item and "result_kind" in item and "logical_effect_id" in item:
                ResearchLLMEffectOutcomeV1.from_json(item)
            elif "bundle_id" in item and "proposals" in item:
                InferenceProposalBundleV1.from_json(item)

        roots = set(expected_kinds)
        roots.update(_walk_v6_wire_refs(assessment_value) - policy_values)
        roots.update(_walk_v6_wire_refs(claim_value) - policy_values)
        seen: set[str] = set()
        pending = sorted(roots)
        while pending:
            ref = pending.pop(0)
            if ref in seen or ref in policy_values:
                continue
            if ref in top_level:
                raise ResearchRepositoryError(
                    "v6_snapshot_ref_class_invalid", "v6 provenance reaches a top-level object"
                )
            _, data = await self._read_registered_bytes_v6(db, ref, field="provenance_ref")
            seen.add(ref)
            try:
                item = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict) or _canonical(item).encode("utf-8") != data:
                raise ResearchRepositoryError(
                    "v6_provenance_json_invalid", "v6 provenance JSON is not canonical"
                )
            try:
                kind = expected_kinds.get(ref)
                if kind is not None:
                    await decode_expected(ref, kind, item)
                else:
                    decode_known(item)
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError(
                    "v6_provenance_object_invalid", "v6 provenance object is invalid"
                ) from exc
            for child in sorted(_walk_v6_wire_refs(item) - policy_values - seen):
                _wire_digest(child, "provenance_child_ref")
                pending.append(child)

        if seen != set(provenance):
            raise ResearchRepositoryError(
                "v6_snapshot_provenance_invalid",
                "v6 provenance is not the exact transitive closure",
            )

    async def persist_v6_continuation_snapshot(
        self,
        *,
        run_id: str,
        operation_id: str,
        terminal_manifest_ref: str,
        continue_until: float,
        pin_id: str | None = None,
    ) -> tuple[str, bool]:
        """Persist the v6 semantic snapshot row and live parent pin atomically.

        ``workflow_research_snapshots.manifest_ref`` intentionally stores the bare
        snapshot blob digest for v6; v5 rows retain their legacy manifest meaning.
        """

        now = float(self._clock())
        if continue_until <= now:
            raise ResearchRepositoryError("snapshot_expiry_invalid", "snapshot pin must be live")
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute(
                    "SELECT workflow_name,workflow_version,status FROM workflow_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None or run["workflow_name"] != "deep_research" or run["workflow_version"] != "v6" or run["status"] != "completed":
                raise ResearchRepositoryError("parent_not_continuable", "snapshot owner is not completed deep_research/v6")
            manifest_digest, manifest = await self._read_registered_json_v6(
                db, terminal_manifest_ref, field="terminal_manifest_ref"
            )
            from ..definitions.deep_research_v6_delivery import (
                TerminalDeliveryManifestV1,
            )

            try:
                decoded_manifest = TerminalDeliveryManifestV1.from_json(manifest)
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError("v6_manifest_invalid", "terminal manifest is invalid") from exc
            if (
                decoded_manifest.manifest_hash != manifest_digest
                or manifest.get("run_id") != run_id
                or manifest.get("workflow_version") != "v6"
                or manifest.get("engine_terminal") != {
                    "status": "completed", "error_code": None, "recovery_action": None
                }
            ):
                raise ResearchRepositoryError("v6_manifest_identity_invalid", "terminal manifest identity differs")
            if manifest.get("answer_status") not in _CONTINUABLE_DELIVERY:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable",
                    "completed evidence answer does not admit a continuation",
                )
            snapshot_digest, snapshot = await self._read_registered_json_v6(
                db, manifest.get("continuation_snapshot_ref"), field="continuation_snapshot_ref"
            )
            await self._validate_v6_snapshot_graph(db, snapshot, parent_run_id=run_id)
            snapshot_hash = str(snapshot["snapshot_hash"])
            if manifest.get("continuation_snapshot_hash") != snapshot_hash:
                raise ResearchRepositoryError("v6_snapshot_manifest_mismatch", "manifest snapshot hash differs")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=?", (snapshot_hash,)
                )
            ).fetchone()
            created = existing is None
            if existing is not None and (
                existing["run_id"] != run_id
                or existing["operation_id"] != operation_id
                or int(existing["schema_version"]) != 1
                or existing["manifest_ref"] != snapshot_digest
                or float(existing["expires_at"]) != continue_until
            ):
                raise ResearchRepositoryError("snapshot_conflict", "v6 snapshot row conflicts")
            if existing is None:
                await db.execute(
                    """INSERT INTO workflow_research_snapshots(
                    snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (snapshot_hash, run_id, operation_id, 1, snapshot_digest, now, continue_until),
                )
            for digest in sorted({manifest_digest, snapshot_digest, *(_wire_digest(ref, "closure_ref") for ref in snapshot["closure_refs"])}):
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'research_snapshot',?,?)""",
                    (digest, snapshot_hash, now),
                )
            actual_pin_id = pin_id or _sha256_text(f"{snapshot_hash}|{run_id}|continue_parent")
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_parent',?,?)
                ON CONFLICT(snapshot_hash,run_id,pin_kind) DO NOTHING""",
                (actual_pin_id, snapshot_hash, run_id, continue_until, now),
            )
            pin = await (
                await db.execute(
                    """SELECT expires_at FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'""",
                    (snapshot_hash, run_id),
                )
            ).fetchone()
            if pin is None or float(pin["expires_at"]) != continue_until:
                raise ResearchRepositoryError("snapshot_pin_conflict", "v6 parent pin conflicts")
            await db.commit()
            return snapshot_hash, created
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _v6_audit_result(
        self,
        db: aiosqlite.Connection,
        *,
        parent_run_id: str,
        caller_idempotency_key: str,
        child_run_id: str,
        child_operation_id: str,
        created: bool,
        now: float,
    ) -> tuple[str, bool]:
        if not caller_idempotency_key:
            raise ResearchRepositoryError("continuation_audit_key_invalid", "caller idempotency key is empty")
        caller_hash = _sha256_text(caller_idempotency_key)
        audit_operation_id = _sha256_text(
            "deep-research-v6-continuation-audit-v1|"
            + parent_run_id + "|" + caller_idempotency_key
        )
        request = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "caller_idempotency_key_hash": caller_hash,
        }
        request_hash = _sha256_text(_canonical(request))
        result = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "child_run_id": child_run_id,
            "child_operation_id": child_operation_id,
            "created": created,
        }
        existing = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?", (audit_operation_id,)
            )
        ).fetchone()
        if existing is not None:
            try:
                stored = json.loads(str(existing["result_json"]))
            except json.JSONDecodeError as exc:
                raise ResearchRepositoryError("continuation_audit_conflict", "audit result is corrupt") from exc
            if (
                existing["run_id"] != child_run_id
                or existing["operation_kind"] != "research_continue_audit_v1"
                or existing["request_hash"] != request_hash
                or not isinstance(stored, dict)
                or set(stored) != set(result)
                or any(stored.get(key) != result[key] for key in result if key != "created")
                or not isinstance(stored.get("created"), bool)
            ):
                raise ResearchRepositoryError("continuation_audit_conflict", "audit identity conflicts")
            return audit_operation_id, bool(stored["created"])
        await db.execute(
            """INSERT INTO workflow_operations(
            operation_id,run_id,operation_kind,request_hash,result_json,created_at
            ) VALUES(?,?,'research_continue_audit_v1',?,?,?)""",
            (audit_operation_id, child_run_id, request_hash, _canonical(result), now),
        )
        return audit_operation_id, created

    async def _validate_existing_continuation_v6(
        self,
        db: aiosqlite.Connection,
        *,
        parent_run_id: str,
        identity: Mapping[str, str],
        head: aiosqlite.Row,
    ) -> tuple[dict[str, Any], str]:
        child_run_id = identity["child_run_id"]
        child_operation_id = identity["child_operation_id"]
        if (
            head["parent_run_id"] != parent_run_id
            or head["child_run_id"] != child_run_id
            or head["child_operation_id"] != child_operation_id
            or int(head["policy_version"]) != _V6_POLICY_VERSION
        ):
            raise ResearchRepositoryError("continuation_head_conflict", "continuation head identity differs")
        child = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (child_run_id,))
        ).fetchone()
        if child is None or any(
            (
                child["parent_run_id"] != parent_run_id,
                child["workflow_name"] != "deep_research",
                child["workflow_version"] != "v6",
                child["trace_id"] != identity["child_trace_id"],
                child["thread_id"] != identity["child_thread_id"],
                child["request_id"] != identity["child_request_id"],
                child["turn_id"] != identity["child_turn_id"],
            )
        ):
            raise ResearchRepositoryError("continuation_child_conflict", "continuation child identity differs")
        lineage = await (
            await db.execute(
                "SELECT * FROM workflow_research_lineage WHERE operation_id=? AND run_id=?",
                (child_operation_id, child_run_id),
            )
        ).fetchone()
        if lineage is None or any(
            (
                lineage["parent_run_id"] != parent_run_id,
                lineage["parent_operation_id"] != head["parent_operation_id"],
                lineage["snapshot_hash"] != head["source_snapshot_hash"],
                lineage["budget_lease_id"] != identity["budget_lease_id"],
            )
        ):
            raise ResearchRepositoryError("continuation_lineage_conflict", "continuation lineage differs")
        start = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id=?",
                (identity["child_start_operation_id"],),
            )
        ).fetchone()
        if start is None or start["run_id"] != child_run_id or start["operation_kind"] != "research_continue_start_v1":
            raise ResearchRepositoryError("continuation_start_missing", "continuation start operation differs")
        try:
            start_payload = json.loads(str(start["result_json"]))
        except json.JSONDecodeError as exc:
            raise ResearchRepositoryError("continuation_start_invalid", "continuation start payload is corrupt") from exc
        expected_payload = {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "source_snapshot_hash": str(head["source_snapshot_hash"]),
        }
        expected_hash = _sha256_text(_canonical(expected_payload))
        if start_payload != expected_payload or start["request_hash"] != expected_hash:
            raise ResearchRepositoryError("continuation_start_conflict", "continuation start payload differs")
        start_request = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE request_key=?",
                (identity["child_request_key"],),
            )
        ).fetchone()
        if start_request is None or any(
            (
                start_request["run_id"] != child_run_id,
                start_request["request_id"] != identity["child_request_id"],
                start_request["turn_id"] != identity["child_turn_id"],
                start_request["workflow_name"] != "deep_research",
                start_request["capability_hash"] != child["capability_hash"],
            )
        ):
            raise ResearchRepositoryError("continuation_start_request_conflict", "continuation start request differs")
        capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (child["capability_hash"],),
            )
        ).fetchone()
        try:
            capability_value = json.loads(str(capability["snapshot_json"])) if capability else None
            start_meta = capability_value.get("_workflow_start") if isinstance(capability_value, dict) else None
            capability_identity = start_meta.get("identity") if isinstance(start_meta, dict) else None
        except json.JSONDecodeError:
            capability_identity = None
        if not isinstance(capability_identity, dict) or any(
            (
                capability_identity.get("request_id") != identity["child_request_id"],
                capability_identity.get("turn_id") != identity["child_turn_id"],
                capability_identity.get("logical_slot") != identity["child_request_key"],
            )
        ):
            raise ResearchRepositoryError("continuation_capability_conflict", "continuation capability differs")
        refs = await (
            await db.execute(
                "SELECT session_kind,session_id,session_epoch FROM workflow_session_refs WHERE run_id=? AND deleted_at IS NULL",
                (child_run_id,),
            )
        ).fetchall()
        if not refs or len({str(row["session_kind"]) for row in refs}) != len(refs):
            raise ResearchRepositoryError("continuation_session_refs_invalid", "continuation session refs differ")
        return expected_payload, expected_hash

    async def create_or_get_continuation_v6(
        self, parent_run_id: str, caller_idempotency_key: str
    ) -> ContinuationCreateResultV1:
        """Atomically claim the one policy-v1 v6 continuation of ``parent_run_id``."""

        if not parent_run_id or not caller_idempotency_key:
            raise ResearchRepositoryError("continuation_request_invalid", "continuation identity is empty")
        identity = _v6_continuation_identity(parent_run_id)
        child_run_id = identity["child_run_id"]
        child_operation_id = identity["child_operation_id"]
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_research_continuation_heads WHERE parent_run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if head is not None:
                start_payload, start_hash = await self._validate_existing_continuation_v6(
                    db, parent_run_id=parent_run_id, identity=identity, head=head
                )
                audit_id, stored_created = await self._v6_audit_result(
                    db,
                    parent_run_id=parent_run_id,
                    caller_idempotency_key=caller_idempotency_key,
                    child_run_id=child_run_id,
                    child_operation_id=child_operation_id,
                    created=False,
                    now=now,
                )
                await self._fault("after_audit_operation_insert")
                await self._fault("before_commit")
                await db.commit()
                return ContinuationCreateResultV1(
                    1, parent_run_id, child_run_id, child_operation_id, stored_created,
                    start_payload, start_hash, audit_id,
                )

            parent = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (parent_run_id,))
            ).fetchone()
            if parent is None or any(
                (
                    parent["workflow_name"] != "deep_research",
                    parent["workflow_version"] != "v6",
                    parent["status"] != "completed",
                    parent["head_checkpoint_id"] is None,
                )
            ):
                raise ResearchRepositoryError("parent_not_continuable", "parent is not a terminal deep_research/v6 run")
            parent_lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE run_id=?", (parent_run_id,)
                )
            ).fetchone()
            if parent_lineage is None:
                raise ResearchRepositoryError("parent_lineage_missing", "parent lineage is missing")
            events = await (
                await db.execute(
                    """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                    (parent_run_id,),
                )
            ).fetchall()
            terminal_payload: dict[str, Any] | None = None
            for event in events:
                try:
                    candidate = json.loads(str(event["payload_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and candidate.get("manifest_ref"):
                    terminal_payload = candidate
                    break
            if terminal_payload is None:
                raise ResearchRepositoryError("v6_terminal_event_missing", "parent terminal manifest event is missing")
            manifest_digest, manifest = await self._read_registered_json_v6(
                db, terminal_payload["manifest_ref"], field="terminal_manifest_ref"
            )
            from ..definitions.deep_research_v6_delivery import (
                TerminalDeliveryManifestV1,
            )

            try:
                decoded_manifest = TerminalDeliveryManifestV1.from_json(manifest)
            except (TypeError, ValueError) as exc:
                raise ResearchRepositoryError("v6_manifest_invalid", "terminal manifest is invalid") from exc
            if (
                decoded_manifest.manifest_hash != manifest_digest
                or manifest.get("run_id") != parent_run_id
                or manifest.get("workflow_name") != "deep_research"
                or manifest.get("workflow_version") != "v6"
                or manifest.get("engine_terminal") != {
                    "status": "completed", "error_code": None, "recovery_action": None
                }
            ):
                raise ResearchRepositoryError("v6_manifest_identity_invalid", "terminal manifest identity differs")
            if manifest.get("answer_status") not in _CONTINUABLE_DELIVERY:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable",
                    "parent answer status does not admit a continuation",
                )
            snapshot_digest, snapshot = await self._read_registered_json_v6(
                db, manifest.get("continuation_snapshot_ref"), field="continuation_snapshot_ref"
            )
            await self._validate_v6_snapshot_graph(db, snapshot, parent_run_id=parent_run_id)
            snapshot_hash = str(snapshot["snapshot_hash"])
            if manifest.get("continuation_snapshot_hash") != snapshot_hash:
                raise ResearchRepositoryError("v6_snapshot_manifest_mismatch", "manifest snapshot pointer differs")
            snapshot_row = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshots
                    WHERE snapshot_hash=? AND run_id=? AND operation_id=?""",
                    (snapshot_hash, parent_run_id, str(parent_lineage["operation_id"])),
                )
            ).fetchone()
            if (
                snapshot_row is None
                or snapshot_row["manifest_ref"] != snapshot_digest
                or snapshot_row["expires_at"] is None
                or float(snapshot_row["expires_at"]) <= now
            ):
                raise ResearchRepositoryError("snapshot_expired", "v6 continuation snapshot row is missing or expired")
            refs_to_validate = {
                f"sha256:{manifest_digest}", f"sha256:{snapshot_digest}",
                *[str(ref) for ref in snapshot["closure_refs"]],
            }
            terminal_checkpoint_id = str(parent["head_checkpoint_id"])
            for wire_ref in sorted(refs_to_validate):
                digest, _ = await self._read_registered_bytes_v6(db, wire_ref, field="snapshot_closure_ref")
                owner = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_blob_refs r
                        JOIN workflow_checkpoint_owners o ON o.checkpoint_id=r.owner_id
                        WHERE r.sha256=? AND r.owner_kind='checkpoint' AND r.owner_id=?
                          AND o.run_id=? AND o.checkpoint_ns=? LIMIT 1""",
                        (digest, terminal_checkpoint_id, parent_run_id, str(parent["head_checkpoint_ns"])),
                    )
                ).fetchone()
                if owner is None:
                    raise ResearchRepositoryError("v6_closure_owner_invalid", "snapshot ref lacks parent terminal checkpoint owner")
            spec_digest, spec = await self._read_registered_json_v6(db, snapshot["spec_ref"], field="spec_ref")
            _, assessment = await self._read_registered_json_v6(db, snapshot["assessment_ref"], field="assessment_ref")
            _, claim = await self._read_registered_json_v6(db, snapshot["claim_batch_ref"], field="claim_batch_ref")
            batches = list(snapshot["fact_batch_refs"])
            if not batches:
                raise ResearchRepositoryError("v6_fact_head_missing", "snapshot has no evidence batches")
            _, final_batch = await self._read_registered_json_v6(db, batches[-1], field="fact_batch_ref")
            if any(
                (
                    spec.get("spec_hash") != snapshot["spec_hash"],
                    manifest.get("spec_hash") != snapshot["spec_hash"],
                    final_batch.get("head_hash") != snapshot["evidence_head_hash"],
                    assessment.get("spec_hash") != snapshot["spec_hash"],
                    assessment.get("evidence_head_hash") != snapshot["evidence_head_hash"],
                    assessment.get("assessment_hash") != snapshot["assessment_hash"],
                    assessment.get("assessment_input_hash") != snapshot["assessment_input_hash"],
                    manifest.get("assessment_hash") != snapshot["assessment_hash"],
                    claim.get("assessment_hash") != snapshot["assessment_hash"],
                )
            ):
                raise ResearchRepositoryError("v6_snapshot_cross_hash_invalid", "snapshot semantic cross-hash differs")
            for policy_name, manifest_field in (("claim", "claim_policy_hash"), ("quality", "quality_policy_hash")):
                _, policy = await self._read_registered_json_v6(
                    db, snapshot["policy_refs"][policy_name], field=f"{policy_name}_policy_ref"
                )
                if _sha256_text(_canonical(policy)) != manifest.get(manifest_field):
                    raise ResearchRepositoryError("v6_snapshot_policy_hash_invalid", "snapshot policy hash differs")
            pin = await (
                await db.execute(
                    """SELECT 1 FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'
                      AND expires_at>?""",
                    (snapshot_hash, parent_run_id, now),
                )
            ).fetchone()
            if pin is None:
                raise ResearchRepositoryError("snapshot_expired", "v6 continuation parent pin is missing or expired")

            parent_capability = await (
                await db.execute(
                    "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                    (parent["capability_hash"],),
                )
            ).fetchone()
            if parent_capability is None:
                raise ResearchRepositoryError("parent_start_snapshot_missing", "parent capability is missing")
            try:
                parent_capability_value = json.loads(str(parent_capability["snapshot_json"]))
            except json.JSONDecodeError as exc:
                raise ResearchRepositoryError("parent_start_snapshot_invalid", "parent capability is invalid") from exc
            if not isinstance(parent_capability_value, dict):
                raise ResearchRepositoryError("parent_start_snapshot_invalid", "parent capability is invalid")
            original_capabilities = {
                str(key): copy.deepcopy(value)
                for key, value in parent_capability_value.items() if key != "_workflow_start"
            }
            parent_start = parent_capability_value.get("_workflow_start")
            parent_identity = parent_start.get("identity") if isinstance(parent_start, dict) else None
            if not isinstance(parent_identity, dict):
                parent_identity = {
                    "venue": "chat", "base_session_id": str(parent["session_id"]),
                    "code_session_id": "", "delivery_session_id": str(parent["session_id"]),
                    "base_epoch": 0, "code_epoch": 0,
                    "workflow_name": "deep_research",
                }
            child_identity = {
                **copy.deepcopy(parent_identity),
                "request_id": identity["child_request_id"],
                "turn_id": identity["child_turn_id"],
                "logical_slot": identity["child_request_key"],
                "workflow_name": "deep_research",
            }
            start_payload = {
                "schema_version": 1,
                "parent_run_id": parent_run_id,
                "source_snapshot_hash": snapshot_hash,
            }
            start_hash = _sha256_text(_canonical(start_payload))
            capabilities_hash = _sha256_text(_canonical(original_capabilities))
            child_capability = {
                **original_capabilities,
                "_workflow_start": {
                    "identity": child_identity,
                    "identity_key": _sha256_text(_canonical(child_identity)),
                    "request_hash": start_hash,
                    "args_hash": start_hash,
                    "capability_hash": capabilities_hash,
                    "start_payload": copy.deepcopy(start_payload),
                },
            }
            child_capability_json = _canonical(child_capability)
            child_capability_hash = _sha256_text(child_capability_json)
            await db.execute(
                """INSERT OR IGNORE INTO workflow_capabilities(
                capability_hash,snapshot_json,created_at) VALUES(?,?,?)""",
                (child_capability_hash, child_capability_json, now),
            )
            capability_row = await (
                await db.execute(
                    "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                    (child_capability_hash,),
                )
            ).fetchone()
            if capability_row is None or capability_row["snapshot_json"] != child_capability_json:
                raise ResearchRepositoryError("continuation_capability_conflict", "child capability conflicts")
            await self._fault("after_capability_insert")
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,head_checkpoint_ns,head_checkpoint_id,
                parent_run_id,source_checkpoint_id,session_id,request_id,turn_id,
                workflow_name,workflow_version,manifest_hash,implementation_hash,capability_hash,
                state_schema_version,status,active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,'deep_research','v6',?,?,?,?,
                'created','[]',?,?)""",
                (
                    child_run_id, identity["child_trace_id"], identity["child_thread_id"],
                    parent["checkpoint_ns"], parent["checkpoint_ns"], parent_run_id,
                    parent["head_checkpoint_id"], parent["session_id"], identity["child_request_id"],
                    identity["child_turn_id"], parent["manifest_hash"], parent["implementation_hash"],
                    child_capability_hash, parent["state_schema_version"], now, now,
                ),
            )
            await self._fault("after_run_insert")
            audit_id, stored_created = await self._v6_audit_result(
                db,
                parent_run_id=parent_run_id,
                caller_idempotency_key=caller_idempotency_key,
                child_run_id=child_run_id,
                child_operation_id=child_operation_id,
                created=True,
                now=now,
            )
            await self._fault("after_audit_operation_insert")
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    identity["child_request_key"], parent["session_id"], identity["child_request_id"],
                    identity["child_turn_id"], "deep_research", child_capability_hash, child_run_id, now,
                ),
            )
            await self._fault("after_start_request_insert")
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at)
                SELECT ?,session_kind,session_id,session_epoch,NULL FROM workflow_session_refs
                WHERE run_id=? AND deleted_at IS NULL""",
                (child_run_id, parent_run_id),
            )
            child_ref_count = await (
                await db.execute(
                    "SELECT COUNT(*) FROM workflow_session_refs WHERE run_id=?", (child_run_id,)
                )
            ).fetchone()
            if child_ref_count is None or int(child_ref_count[0]) == 0:
                raise ResearchRepositoryError("continuation_session_refs_invalid", "parent has no live session refs")
            await self._fault("after_session_refs_insert")
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at) VALUES(?,?,?,?,?,NULL,?,?)""",
                (
                    child_operation_id, child_run_id, parent_run_id,
                    parent_lineage["operation_id"], snapshot_hash, identity["budget_lease_id"], now,
                ),
            )
            await self._fault("after_lineage_insert")
            child_pin_id = _sha256_text(f"{snapshot_hash}|{child_run_id}|continue_child")
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_child',NULL,?)""",
                (child_pin_id, snapshot_hash, child_run_id, now),
            )
            await self._fault("after_snapshot_pin_insert")
            await db.execute(
                """INSERT INTO workflow_operations(
                operation_id,run_id,operation_kind,request_hash,result_json,created_at
                ) VALUES(?,?,'research_continue_start_v1',?,?,?)""",
                (
                    identity["child_start_operation_id"], child_run_id, start_hash,
                    _canonical(start_payload), now,
                ),
            )
            await self._fault("after_start_operation_insert")
            for wire_ref in snapshot["closure_refs"]:
                await db.execute(
                    """INSERT INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'run_staging',?,?)""",
                    (_wire_digest(wire_ref, "closure_ref"), child_run_id, now),
                )
            await self._fault("after_inherited_staging_owners_insert")
            await db.execute(
                """INSERT INTO workflow_research_continuation_heads(
                parent_run_id,child_run_id,parent_operation_id,child_operation_id,
                source_snapshot_hash,spec_hash,spec_blob_digest,evidence_head_hash,
                policy_version,claimed_at) VALUES(?,?,?,?,?,?,?,?,1,?)""",
                (
                    parent_run_id, child_run_id, parent_lineage["operation_id"], child_operation_id,
                    snapshot_hash, snapshot["spec_hash"], spec_digest,
                    snapshot["evidence_head_hash"], now,
                ),
            )
            await self._fault("after_continuation_head_insert")
            await self._fault("before_commit")
            await db.commit()
            return ContinuationCreateResultV1(
                1, parent_run_id, child_run_id, child_operation_id, stored_created,
                start_payload, start_hash, audit_id,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def load_continuation_snapshot_v6(self, child_run_id: str) -> dict[str, Any]:
        """Hydrate an existing child strictly through its claimed head and inherited owners."""

        await self.initialize()
        db = await self._connect()
        try:
            lineage = await (
                await db.execute(
                    """SELECT l.*,h.parent_run_id AS head_parent,h.child_run_id AS head_child
                    FROM workflow_research_lineage l
                    JOIN workflow_research_continuation_heads h
                      ON h.child_operation_id=l.operation_id
                    WHERE l.run_id=?""",
                    (child_run_id,),
                )
            ).fetchone()
            if lineage is None or lineage["head_child"] != child_run_id or lineage["head_parent"] != lineage["parent_run_id"]:
                raise ResearchRepositoryError("continuation_head_missing", "child continuation head is missing")
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshots
                    WHERE snapshot_hash=? AND run_id=?""",
                    (lineage["snapshot_hash"], lineage["parent_run_id"]),
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError("continuation_snapshot_missing", "child source snapshot is missing")
            _, snapshot = await self._read_registered_json_v6(
                db, row["manifest_ref"], field="continuation_snapshot_ref"
            )
            await self._validate_v6_snapshot_graph(
                db, snapshot, parent_run_id=str(lineage["parent_run_id"])
            )
            if snapshot["snapshot_hash"] != lineage["snapshot_hash"]:
                raise ResearchRepositoryError("continuation_snapshot_conflict", "child source snapshot differs")
            for wire_ref in snapshot["closure_refs"]:
                digest = _wire_digest(wire_ref, "closure_ref")
                owner = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_blob_refs
                        WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                        (digest, child_run_id),
                    )
                ).fetchone()
                if owner is None:
                    raise ResearchRepositoryError("continuation_owner_missing", "child inherited owner is missing")
            return copy.deepcopy(snapshot)
        finally:
            await db.close()

    async def settle_run_snapshot_pins(self, run_id: str, *, expires_at: float) -> int:
        """Give non-expiring child pins their terminal-retention expiry."""

        now = float(self._clock())
        if expires_at <= now:
            raise ResearchRepositoryError("pin_expiry_invalid", "terminal pin expiry must be in the future")
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None or run["status"] not in {"completed", "failed", "cancelled"}:
                raise ResearchRepositoryError("run_not_terminal", "only terminal run pins can expire")
            cursor = await db.execute(
                """UPDATE workflow_research_snapshot_pins SET expires_at=?
                WHERE run_id=? AND pin_kind='continue_child' AND expires_at IS NULL""",
                (expires_at, run_id),
            )
            await db.commit()
            return cursor.rowcount
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def lineage_reachability(self, *, now: float | None = None) -> ResearchReachability:
        """Recompute protected runs/snapshots/pins from durable roots each pass."""

        instant = float(self._clock() if now is None else now)
        await self.initialize()
        db = await self._connect()
        try:
            roots = await (
                await db.execute(
                    """SELECT run_id FROM workflow_runs WHERE status NOT IN ('completed','failed','cancelled')
                    UNION SELECT run_id FROM workflow_run_control_commands
                      WHERE status IN ('open','accepted','observed','settled')
                    UNION SELECT run_id FROM workflow_research_snapshot_pins
                      WHERE expires_at IS NULL OR expires_at>?""",
                    (instant,),
                )
            ).fetchall()
            protected = {str(row[0]) for row in roots}
            changed = True
            while changed:
                changed = False
                if not protected:
                    break
                rows = await (
                    await db.execute(
                        f"""SELECT run_id,parent_run_id FROM workflow_research_lineage
                        WHERE run_id IN ({','.join('?' for _ in protected)})""",
                        tuple(sorted(protected)),
                    )
                ).fetchall()
                for row in rows:
                    parent = row["parent_run_id"]
                    if parent is not None and str(parent) not in protected:
                        protected.add(str(parent))
                        changed = True
            pin_rows: list[aiosqlite.Row] = []
            if protected:
                pin_rows = await (
                    await db.execute(
                        f"""SELECT pin_id,snapshot_hash FROM workflow_research_snapshot_pins
                        WHERE run_id IN ({','.join('?' for _ in protected)})
                           OR expires_at IS NULL OR expires_at>?""",
                        (*tuple(sorted(protected)), instant),
                    )
                ).fetchall()
            else:
                pin_rows = await (
                    await db.execute(
                        """SELECT pin_id,snapshot_hash FROM workflow_research_snapshot_pins
                        WHERE expires_at IS NULL OR expires_at>?""",
                        (instant,),
                    )
                ).fetchall()
            snapshots = {str(row["snapshot_hash"]) for row in pin_rows}
            if protected:
                lineage_rows = await (
                    await db.execute(
                        f"""SELECT snapshot_hash FROM workflow_research_lineage
                        WHERE run_id IN ({','.join('?' for _ in protected)}) AND snapshot_hash IS NOT NULL""",
                        tuple(sorted(protected)),
                    )
                ).fetchall()
                snapshots.update(str(row[0]) for row in lineage_rows)
            return ResearchReachability(
                protected_run_ids=frozenset(protected),
                protected_snapshot_hashes=frozenset(snapshots),
                protected_pin_ids=frozenset(str(row["pin_id"]) for row in pin_rows),
            )
        finally:
            await db.close()


__all__ = [
    "ContinuationCreateResultV1",
    "ResearchReachability",
    "ResearchRepositoryError",
    "ResearchWorkflowRepository",
]
