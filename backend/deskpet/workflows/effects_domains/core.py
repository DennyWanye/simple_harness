# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Connection, active context, fences and shared budget and target helpers."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from ..contracts import EffectKind, canonical_json
from ..deadlines import DurableDeadlineV1
from ..store.run_store import RunFence, StaleRunFence
from ..store.schema import initialize_workflow_db
from .support import (
    BudgetReservationRecord,
    CheckpointEffectLink,
    EffectAction,
    EffectJournalError,
    EffectRecord,
    EffectStateConflict,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedToolCall,
)

if TYPE_CHECKING:
    from ..effects import EffectJournal


class TargetReservationConflict(EffectJournalError):
    """A final target is already reserved by a different stable call."""


@dataclass(frozen=True, slots=True)
class EffectExecutionContext:
    """Fenced runtime identity required by production effect adapters."""

    journal: EffectJournal
    fence: RunFence
    node_execution_id: str
    workflow_name: str
    workflow_version: str
    node_id: str
    reuse_checkpoint: CheckpointEffectLink | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.node_execution_id,
                self.workflow_name,
                self.workflow_version,
                self.node_id,
            )
        ):
            raise ValueError("effect execution context identity is incomplete")
        if self.fence.run_id == "":
            raise ValueError("effect execution context requires a run fence")


class EffectJournalCore:
    """Connection, active context, fences and shared budget and target helpers."""

    if TYPE_CHECKING:

        async def _persist_v6_deadline_transition(
            self,
            db: aiosqlite.Connection,
            *,
            run_id: str,
            previous_revision: int,
            state: DurableDeadlineV1,
        ) -> None: ...
        @staticmethod
        def _v6_deadline_from_row(row: Mapping[str, object]) -> DurableDeadlineV1: ...

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        await initialize_workflow_db(self.path)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=FULL")
        return db

    async def resolve_active_context(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        session_id: str | None = None,
    ) -> EffectExecutionContext:
        """Resolve exactly one live production run or fail closed."""

        db = await self._connect()
        try:
            clauses = [
                "workflow_name=?",
                "workflow_version=?",
                "status='running'",
                "lease_owner IS NOT NULL",
                "lease_expires_at>?",
            ]
            params: list[object] = [
                workflow_name,
                workflow_version,
                self._clock(),
            ]
            if session_id is not None:
                clauses.append("session_id=?")
                params.append(session_id)
            rows = await (
                await db.execute(
                    f"""SELECT run_id,lease_owner,lease_epoch,run_version,
                    head_checkpoint_ns,head_checkpoint_id
                    FROM workflow_runs WHERE {" AND ".join(clauses)}
                    ORDER BY updated_at DESC LIMIT 2""",
                    params,
                )
            ).fetchall()
        finally:
            await db.close()
        if len(rows) != 1:
            qualifier = f" for session {session_id}" if session_id is not None else ""
            raise EffectStateConflict(
                f"expected one live {workflow_name}@{workflow_version} run{qualifier}; found {len(rows)}"
            )
        row = rows[0]
        node_digest = hashlib.sha256(
            canonical_json(
                {
                    "run_id": str(row["run_id"]),
                    "node_id": node_id,
                    "checkpoint_ns": str(row["head_checkpoint_ns"] or ""),
                    "checkpoint_id": str(row["head_checkpoint_id"] or "root"),
                }
            ).encode("utf-8")
        ).hexdigest()
        return EffectExecutionContext(
            journal=self,
            fence=RunFence(
                str(row["run_id"]),
                str(row["lease_owner"]),
                int(row["lease_epoch"]),
                int(row["run_version"]),
            ),
            node_execution_id=f"effect-node-{node_digest[:32]}",
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
        )

    @staticmethod
    async def _assert_fence(db: aiosqlite.Connection, fence: RunFence) -> None:
        row = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=?
                AND lease_epoch=? AND run_version=? AND status='running'""",
                (fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
            )
        ).fetchone()
        if row is None:
            raise StaleRunFence(f"stale effect writer: {fence.run_id}")

    async def _reserve_targets(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        prepared: PreparedToolCall,
        status: str,
        ttl_seconds: float,
    ) -> None:
        now = self._clock()
        for target in prepared.prepared_targets:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_target_reservations WHERE reservation_key=?",
                    (target.reservation_key,),
                )
            ).fetchone()
            if row is None:
                await db.execute(
                    """INSERT INTO workflow_target_reservations(
                    reservation_key,display_path,run_id,call_id,status,lease_expires_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        target.reservation_key,
                        target.final_path,
                        fence.run_id,
                        prepared.stable_call_id,
                        status,
                        now + ttl_seconds,
                        now,
                    ),
                )
                continue
            same_call = row["run_id"] == fence.run_id and row["call_id"] == prepared.stable_call_id
            reusable_status = row["status"] in {"prepared", "claimed"}
            terminal_status = row["status"] in {"released", "committed"}
            if not ((same_call and reusable_status) or terminal_status):
                raise TargetReservationConflict(
                    f"target already reserved by {row['run_id']}:{row['call_id']}"
                )
            await db.execute(
                """UPDATE workflow_target_reservations SET display_path=?,run_id=?,call_id=?,
                status=?,lease_expires_at=?,updated_at=?
                WHERE reservation_key=?""",
                (
                    target.final_path,
                    fence.run_id,
                    prepared.stable_call_id,
                    status,
                    now + ttl_seconds,
                    now,
                    target.reservation_key,
                ),
            )

    @staticmethod
    async def _budget_limits_for_update(
        db: aiosqlite.Connection, run_id: str, capability_key: str
    ) -> tuple[int, int, int]:
        row = await (
            await db.execute(
                """SELECT capability.snapshot_json FROM workflow_runs run
                JOIN workflow_capabilities capability
                  ON capability.capability_hash=run.capability_hash
                WHERE run.run_id=?""",
                (run_id,),
            )
        ).fetchone()
        if row is None:
            raise EffectStateConflict(f"run capability snapshot is missing: {run_id}")
        snapshot = json.loads(str(row["snapshot_json"]))
        raw_limits = snapshot.get(capability_key) if isinstance(snapshot, dict) else None
        if not isinstance(raw_limits, dict):
            raise EffectStateConflict(
                f"run capability snapshot is missing budget key {capability_key}"
            )
        names = ("max_input_tokens", "max_output_tokens", "max_cost_micros")
        limits: list[int] = []
        for name in names:
            value = raw_limits.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EffectStateConflict(
                    f"run capability snapshot has invalid {capability_key}.{name}"
                )
            limits.append(value)
        return limits[0], limits[1], limits[2]

    @staticmethod
    async def _charged_budget_for_update(
        db: aiosqlite.Connection, run_id: str, ledger_kind: str
    ) -> tuple[int, int, int]:
        row = await (
            await db.execute(
                """SELECT
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN input_reserved
                    WHEN status='committed' THEN COALESCE(input_actual,input_reserved)
                    ELSE 0 END),0),
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN output_reserved
                    WHEN status='committed' THEN COALESCE(output_actual,output_reserved)
                    ELSE 0 END),0),
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN cost_reserved_micros
                    WHEN status='committed' THEN COALESCE(cost_actual_micros,cost_reserved_micros)
                    ELSE 0 END),0)
                FROM workflow_effect_budget_reservations
                WHERE run_id=? AND ledger_kind=?""",
                (run_id, ledger_kind),
            )
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1]), int(row[2])

    @staticmethod
    async def _budget_reservation_for_update(
        db: aiosqlite.Connection, run_id: str, effect_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute(
                """SELECT * FROM workflow_effect_budget_reservations
                WHERE effect_id=? AND run_id=?""",
                (effect_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise EffectStateConflict(f"effect has no budget reservation: {effect_id}")
        return row

    @staticmethod
    def _budget_reservation_record(row: aiosqlite.Row) -> BudgetReservationRecord:
        return BudgetReservationRecord(
            effect_id=str(row["effect_id"]),
            run_id=str(row["run_id"]),
            ledger_kind=str(row["ledger_kind"]),
            input_reserved=int(row["input_reserved"]),
            output_reserved=int(row["output_reserved"]),
            cost_reserved_micros=int(row["cost_reserved_micros"]),
            input_actual=(None if row["input_actual"] is None else int(row["input_actual"])),
            output_actual=(None if row["output_actual"] is None else int(row["output_actual"])),
            cost_actual_micros=(
                None if row["cost_actual_micros"] is None else int(row["cost_actual_micros"])
            ),
            status=str(row["status"]),
            dispatch_state=str(row["dispatch_state"]),
            upstream_started_at=(
                None if row["upstream_started_at"] is None else float(row["upstream_started_at"])
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    async def _effect_for_update(
        db: aiosqlite.Connection, run_id: str, effect_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute(
                "SELECT * FROM workflow_effects WHERE effect_id=? AND run_id=?",
                (effect_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise KeyError(effect_id)
        return row

    @staticmethod
    async def _assert_staged_targets_committed(
        db: aiosqlite.Connection, effect: aiosqlite.Row
    ) -> None:
        policy = json.loads(effect["policy_json"])
        if policy["kind"] != EffectKind.STAGED_FILE.value:
            return
        rows = await (
            await db.execute(
                "SELECT target_json FROM workflow_effect_targets WHERE effect_id=?",
                (effect["effect_id"],),
            )
        ).fetchall()
        if not rows or any(json.loads(row["target_json"])["status"] != "committed" for row in rows):
            raise EffectStateConflict(
                "staged file effect cannot commit before every target commits"
            )

    @staticmethod
    async def _find_checkpoint_effect(
        db: aiosqlite.Connection,
        checkpoint: CheckpointEffectLink,
        fingerprint: str,
    ) -> aiosqlite.Row | None:
        return await (
            await db.execute(
                """WITH RECURSIVE ancestors(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id) AS (
                    SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id
                    FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    UNION ALL
                    SELECT parent.thread_id,parent.checkpoint_ns,parent.checkpoint_id,parent.parent_checkpoint_id
                    FROM workflow_checkpoints parent
                    JOIN ancestors child ON parent.thread_id=child.thread_id
                      AND parent.checkpoint_ns=child.checkpoint_ns
                      AND parent.checkpoint_id=child.parent_checkpoint_id
                )
                SELECT effect.* FROM ancestors
                JOIN workflow_checkpoint_effects link USING(thread_id,checkpoint_ns,checkpoint_id)
                JOIN workflow_effects effect ON effect.effect_id=link.effect_id
                WHERE effect.effect_fingerprint=? AND effect.status='committed'
                ORDER BY effect.ended_at DESC LIMIT 1""",
                (
                    checkpoint.thread_id,
                    checkpoint.checkpoint_ns,
                    checkpoint.checkpoint_id,
                    fingerprint,
                ),
            )
        ).fetchone()

    @staticmethod
    def _existing_action(status: EffectStatus) -> EffectAction:
        if status is EffectStatus.COMMITTED:
            return EffectAction.REUSE
        if status is EffectStatus.RUNNING:
            return EffectAction.IN_FLIGHT
        if status in {EffectStatus.UNCERTAIN, EffectStatus.LATE_ORPHAN}:
            return EffectAction.RECONCILE
        return EffectAction.FAILED

    @staticmethod
    def _record(row: aiosqlite.Row) -> EffectRecord:
        return EffectRecord(
            effect_id=str(row["effect_id"]),
            run_id=str(row["run_id"]),
            node_execution_id=str(row["node_execution_id"]),
            effect_fingerprint=str(row["effect_fingerprint"]),
            effect_type=str(row["effect_type"]),
            args_hash=str(row["args_hash"]),
            status=EffectStatus(row["status"]),
            prepared=PreparedToolCall.from_dict(json.loads(row["prepared_json"])),
            outcome=(
                NormalizedToolOutcome.from_dict(json.loads(row["outcome_json"]))
                if row["outcome_json"] is not None
                else None
            ),
            receipt_ref=row["receipt_ref"],
            artifact_refs=tuple(json.loads(row["artifact_refs_json"])),
            lease_epoch=int(row["lease_epoch"]),
        )
