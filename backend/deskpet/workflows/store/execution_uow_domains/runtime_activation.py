"""Skill-scope activation, runtime state, legacy drain and runtime activation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite

from deskpet.execution.contracts import IdempotencyConflict, canonical_json

from .core import ExecutionUowCore
from .support import ContinuationRecord, RuntimeActivationError


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeState:
    """Durable deployment fence for execution-row ownership."""

    generation: int
    phase: str
    drain_manifest_hash: str | None
    drain_count: int
    created_at: float
    activated_at: float | None
    updated_at: float


@dataclass(frozen=True, order=True, slots=True)
class LegacyDrainRef:
    """Stable identity for one legacy durable run that must be drained."""

    source_kind: str
    source_run_id: str

    @property
    def drain_item_id(self) -> str:
        payload = f"{self.source_kind}\0{self.source_run_id}".encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyDrainLease:
    """Fenced claim for one item in the deployment drain manifest."""

    ref: LegacyDrainRef
    manifest_generation: int
    owner: str
    epoch: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class RuntimeActivationCommand:
    """Typed operation routed through the single activation transaction starter."""

    kind: str
    owner: str | None = None
    lease_seconds: float = 30.0
    lease: LegacyDrainLease | None = None
    error: str | None = None

    @classmethod
    def advance(cls) -> RuntimeActivationCommand:
        return cls("advance")

    @classmethod
    def claim(cls, owner: str, *, lease_seconds: float = 30.0) -> RuntimeActivationCommand:
        return cls("claim_drain", owner=owner, lease_seconds=lease_seconds)

    @classmethod
    def settle(
        cls, lease: LegacyDrainLease, *, error: str | None = None
    ) -> RuntimeActivationCommand:
        return cls("settle_drain", lease=lease, error=error)

    @classmethod
    def rollback(cls) -> RuntimeActivationCommand:
        return cls("rollback")


class RuntimeActivationMixin(ExecutionUowCore):
    """Skill-scope activation, runtime state, legacy drain and runtime activation."""

    __slots__ = ()

    async def get_skill_scope_activation(self, activation_id: str) -> Mapping[str, Any] | None:
        """Resolve commit-unknown without opening a new continuation writer."""

        async with self._read_connection() as db:
            return await self.bind(db).read_skill_scope_activation(activation_id)

    async def commit_skill_scope_activation(
        self,
        run_id: str,
        *,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        activation_values: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ContinuationRecord]:
        """Commit a scope receipt and the continuation that activates it."""

        self._validate_continuation_version(expected_continuation_version)
        expected_activation_version = expected_continuation_version + 1
        if activation_values.get("continuation_version") != expected_activation_version:
            raise IdempotencyConflict(
                "skill_scope_continuation_conflict",
                "scope receipt does not name the committed continuation",
            )
        if str(activation_values.get("run_id") or "") != run_id:
            raise IdempotencyConflict(
                "skill_scope_run_conflict",
                "scope receipt belongs to another Run",
            )
        continuation = await self.persist_react_boundary(
            run_id,
            expected_continuation_version,
            continuation_payload,
            skill_scope_activation_values=activation_values,
        )
        if not isinstance(continuation, ContinuationRecord):
            raise RuntimeError("skill scope activation returned a promoted Run")
        activation = await self.get_skill_scope_activation(str(activation_values["activation_id"]))
        if activation is None:
            raise RuntimeError("skill scope activation receipt missing after commit")
        return activation, continuation

    @staticmethod
    def _row_to_runtime_state(row: Mapping[str, Any]) -> ExecutionRuntimeState:
        return ExecutionRuntimeState(
            generation=int(row["generation"]),
            phase=str(row["phase"]),
            drain_manifest_hash=(
                str(row["drain_manifest_hash"]) if row["drain_manifest_hash"] is not None else None
            ),
            drain_count=int(row["drain_count"]),
            created_at=float(row["created_at"]),
            activated_at=(float(row["activated_at"]) if row["activated_at"] is not None else None),
            updated_at=float(row["updated_at"]),
        )

    async def _runtime_state_tx(self, db: aiosqlite.Connection) -> ExecutionRuntimeState:
        row = await (
            await db.execute("SELECT * FROM execution_runtime_state WHERE singleton_id=1")
        ).fetchone()
        if row is None:
            raise RuntimeActivationError(
                "runtime_state_missing", "execution runtime state is not initialized"
            )
        return self._row_to_runtime_state(row)

    async def get_runtime_state(self) -> ExecutionRuntimeState:
        async with self._read_connection() as db:
            return await self._runtime_state_tx(db)

    @staticmethod
    def _manifest_hash(items: Sequence[LegacyDrainRef]) -> str:
        payload = [
            {"source_kind": item.source_kind, "source_run_id": item.source_run_id}
            for item in sorted(items)
        ]
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    async def _scan_legacy_drain_tx(self, db: aiosqlite.Connection) -> tuple[LegacyDrainRef, ...]:
        execution_rows = await (
            await db.execute(
                """SELECT run_id FROM execution_runs
                WHERE owner_kind='legacy' AND owner_generation=0
                AND terminal_event_id IS NULL
                ORDER BY run_id"""
            )
        ).fetchall()
        workflow_rows = await (
            await db.execute(
                """SELECT workflow_runs.run_id FROM workflow_runs
                LEFT JOIN execution_runs
                    ON execution_runs.run_id=workflow_runs.run_id
                WHERE execution_runs.run_id IS NULL
                AND workflow_runs.ended_at IS NULL
                AND lower(workflow_runs.status) NOT IN (
                    'completed','failed','cancelled','canceled'
                )
                AND NOT (
                    lower(workflow_runs.status)='blocked'
                    AND COALESCE(workflow_runs.recovery_action, '')='read_only'
                )
                ORDER BY workflow_runs.run_id"""
            )
        ).fetchall()
        return tuple(
            sorted(
                (LegacyDrainRef("execution_run", str(row["run_id"])) for row in execution_rows),
            )
        ) + tuple(
            sorted(LegacyDrainRef("workflow_run", str(row["run_id"])) for row in workflow_rows)
        )

    async def _claim_legacy_drain_item_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
        *,
        owner: str,
        lease_seconds: float,
    ) -> LegacyDrainLease | None:
        """Lease the next persisted legacy item while runtime ingress is closed."""

        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("drain owner and positive lease_seconds are required")
        if state.phase != "draining" or state.generation != 0:
            raise RuntimeActivationError(
                "legacy_drain_not_active", "legacy drain claims require draining phase"
            )
        await self._assert_persisted_manifest_tx(db, state)
        now = float(self._clock())
        expiry = now + float(lease_seconds)
        row = await (
            await db.execute(
                """UPDATE execution_legacy_drain_items
                SET status='leased',lease_owner=?,lease_epoch=lease_epoch+1,
                    lease_expires_at=?,last_error=NULL,updated_at=?
                WHERE drain_item_id=(
                    SELECT drain_item_id FROM execution_legacy_drain_items
                    WHERE status IN ('pending','failed')
                       OR (status='leased' AND lease_expires_at<=?)
                    ORDER BY created_at,drain_item_id LIMIT 1
                )
                RETURNING manifest_generation,source_kind,source_run_id,
                          lease_epoch,lease_expires_at""",
                (owner, expiry, now, now),
            )
        ).fetchone()
        if row is None:
            return None
        return LegacyDrainLease(
            LegacyDrainRef(str(row["source_kind"]), str(row["source_run_id"])),
            int(row["manifest_generation"]),
            owner,
            int(row["lease_epoch"]),
            float(row["lease_expires_at"]),
        )

    async def _settle_legacy_drain_item_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
        lease: LegacyDrainLease,
        *,
        error: str | None,
    ) -> bool:
        """CAS a leased item to drained/failed after checking source liveness."""

        if state.phase != "draining" or state.generation != 0:
            raise RuntimeActivationError(
                "legacy_drain_not_active",
                "legacy drain settlement requires draining phase",
            )
        await self._assert_persisted_manifest_tx(db, state)
        now = float(self._clock())
        if error is None:
            if lease.ref in set(await self._scan_legacy_drain_tx(db)):
                raise RuntimeActivationError(
                    "legacy_drain_item_active",
                    "legacy drain item cannot settle while its source is active",
                )
            status, drained_at, last_error = "drained", now, None
        else:
            status, drained_at, last_error = "failed", None, str(error)
        cursor = await db.execute(
            """UPDATE execution_legacy_drain_items
            SET status=?,lease_owner=NULL,lease_expires_at=NULL,last_error=?,
                drained_at=?,updated_at=?
            WHERE drain_item_id=? AND manifest_generation=? AND status='leased'
              AND lease_owner=? AND lease_epoch=? AND lease_expires_at>?""",
            (
                status,
                last_error,
                drained_at,
                now,
                lease.ref.drain_item_id,
                lease.manifest_generation,
                lease.owner,
                lease.epoch,
                now,
            ),
        )
        return cursor.rowcount == 1

    async def _assert_persisted_manifest_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
    ) -> tuple[LegacyDrainRef, ...]:
        rows = await (
            await db.execute(
                """SELECT drain_item_id,manifest_generation,source_kind,source_run_id
                FROM execution_legacy_drain_items
                ORDER BY source_kind,source_run_id"""
            )
        ).fetchall()
        refs = tuple(
            LegacyDrainRef(str(row["source_kind"]), str(row["source_run_id"])) for row in rows
        )
        expected_generation = state.generation if state.generation > 0 else 1
        if any(int(row["manifest_generation"]) != expected_generation for row in rows):
            raise RuntimeActivationError(
                "drain_manifest_generation_mismatch",
                "legacy drain manifest contains another activation generation",
            )
        if any(str(row["drain_item_id"]) != ref.drain_item_id for row, ref in zip(rows, refs)):
            raise RuntimeActivationError(
                "drain_manifest_identity_mismatch",
                "legacy drain manifest contains an invalid stable identity",
            )
        if len(refs) != state.drain_count or self._manifest_hash(refs) != state.drain_manifest_hash:
            raise RuntimeActivationError(
                "drain_manifest_mismatch",
                "legacy drain manifest no longer matches the durable runtime fence",
            )
        return refs

    async def _advance_runtime_activation_tx(
        self, db: aiosqlite.Connection, state: ExecutionRuntimeState
    ) -> ExecutionRuntimeState:
        """Advance exactly one durable activation phase in the open transaction."""

        if state.phase == "legacy":
            items = await self._scan_legacy_drain_tx(db)
            manifest_hash = self._manifest_hash(items)
            now = float(self._clock())
            target_generation = 1
            for item in items:
                await db.execute(
                    """INSERT INTO execution_legacy_drain_items(
                    drain_item_id,manifest_generation,source_kind,source_run_id,status,
                    created_at,updated_at
                    ) VALUES(?,?,?,?, 'pending',?,?)""",
                    (
                        item.drain_item_id,
                        target_generation,
                        item.source_kind,
                        item.source_run_id,
                        now,
                        now,
                    ),
                )
            self._fault("activation_after_manifest")
            cursor = await db.execute(
                """UPDATE execution_runtime_state
                SET phase='draining',drain_manifest_hash=?,drain_count=?,updated_at=?
                WHERE singleton_id=1 AND phase='legacy' AND generation=0""",
                (manifest_hash, len(items), now),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "legacy runtime activation CAS lost"
                )
            self._fault("activation_after_draining")
            return await self._runtime_state_tx(db)
        if state.phase == "draining":
            await self._assert_persisted_manifest_tx(db, state)
            pending = await (
                await db.execute(
                    """SELECT COUNT(*) FROM execution_legacy_drain_items
                    WHERE status<>'drained'"""
                )
            ).fetchone()
            if pending is None or int(pending[0]) != 0:
                raise RuntimeActivationError(
                    "legacy_drain_incomplete",
                    "all registered legacy durable rows must drain before activation",
                )
            if await self._scan_legacy_drain_tx(db):
                raise RuntimeActivationError(
                    "legacy_drain_changed",
                    "active legacy rows appeared after the drain manifest was frozen",
                )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_runtime_state
                SET phase='activated',generation=1,activated_at=?,updated_at=?
                WHERE singleton_id=1 AND phase='draining' AND generation=0""",
                (now, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "draining runtime activation CAS lost"
                )
            self._fault("activation_after_activated")
            return await self._runtime_state_tx(db)
        if state.phase == "activated" and state.generation > 0:
            await self._assert_persisted_manifest_tx(db, state)
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_runtime_state SET phase='open',updated_at=?
                WHERE singleton_id=1 AND phase='activated' AND generation=?""",
                (now, state.generation),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "activated runtime open CAS lost"
                )
            self._fault("activation_after_open")
            return await self._runtime_state_tx(db)
        if state.phase == "open":
            await self._assert_persisted_manifest_tx(db, state)
            return state
        raise RuntimeActivationError(
            "invalid_runtime_phase", f"unsupported runtime phase: {state.phase}"
        )

    async def activate_runtime(
        self,
        *,
        require_empty: bool = True,
        command: RuntimeActivationCommand | None = None,
    ) -> ExecutionRuntimeState | LegacyDrainLease | bool | None:
        """Roll the durable runtime fence forward through its typed phases."""

        if command is not None:
            async with self._write_transaction() as db:
                state = await self._runtime_state_tx(db)
                if command.kind == "advance":
                    result = await self._advance_runtime_activation_tx(db, state)
                elif command.kind == "claim_drain":
                    result = await self._claim_legacy_drain_item_tx(
                        db,
                        state,
                        owner=str(command.owner or ""),
                        lease_seconds=command.lease_seconds,
                    )
                elif command.kind == "settle_drain":
                    if command.lease is None:
                        raise ValueError("settle_drain requires a lease")
                    result = await self._settle_legacy_drain_item_tx(
                        db, state, command.lease, error=command.error
                    )
                elif command.kind == "rollback":
                    result = await self._rollback_runtime_activation_tx(db, state)
                else:
                    raise ValueError(f"unknown runtime activation command: {command.kind}")
                await db.commit()
                return result

        for _ in range(3):
            async with self._write_transaction() as db:
                state = await self._runtime_state_tx(db)
                if state.phase == "open":
                    await self._assert_persisted_manifest_tx(db, state)
                    await db.commit()
                    return state
                state = await self._advance_runtime_activation_tx(db, state)
                await db.commit()
            if state.phase == "draining" and state.drain_count:
                if require_empty:
                    raise RuntimeActivationError(
                        "legacy_drain_required",
                        "empty-runtime activation cannot bypass registered legacy durable rows",
                    )
                return state
        return state

    async def _rollback_runtime_activation_tx(
        self, db: aiosqlite.Connection, state: ExecutionRuntimeState
    ) -> ExecutionRuntimeState:
        """Return an unused closed activation to legacy before any kernel row exists."""

        if state.phase == "legacy":
            return state
        if state.phase == "open":
            raise RuntimeActivationError(
                "runtime_ingress_open", "open runtime activation cannot be rolled back"
            )
        kernel_rows = await (
            await db.execute(
                """SELECT COUNT(*) FROM execution_runs
                WHERE owner_kind='kernel' AND owner_generation=?""",
                (state.generation,),
            )
        ).fetchone()
        if kernel_rows is not None and int(kernel_rows[0]):
            raise RuntimeActivationError(
                "activation_irreversible",
                "runtime generation already owns execution rows; roll forward is required",
            )
        active_drain = await (
            await db.execute(
                """SELECT COUNT(*) FROM execution_legacy_drain_items
                WHERE status IN ('pending','leased')"""
            )
        ).fetchone()
        if active_drain is not None and int(active_drain[0]):
            raise RuntimeActivationError(
                "legacy_drain_active", "active legacy drain claims prevent rollback"
            )
        await db.execute("DELETE FROM execution_legacy_drain_items")
        now = float(self._clock())
        cursor = await db.execute(
            """UPDATE execution_runtime_state
            SET generation=0,phase='legacy',drain_manifest_hash=NULL,drain_count=0,
                activated_at=NULL,updated_at=?
            WHERE singleton_id=1 AND phase IN ('draining','activated')""",
            (now,),
        )
        if cursor.rowcount != 1:
            raise RuntimeActivationError("activation_cas_conflict", "activation rollback CAS lost")
        return await self._runtime_state_tx(db)

    async def _required_start_owner_tx(self, db: aiosqlite.Connection) -> tuple[str, int]:
        state = await self._runtime_state_tx(db)
        if state.phase == "legacy" and state.generation == 0:
            return "legacy", 0
        if state.phase == "open" and state.generation > 0:
            return "kernel", state.generation
        raise RuntimeActivationError(
            "runtime_ingress_closed",
            f"execution starts are closed while runtime phase is {state.phase}",
        )

    async def _required_recovery_owner_tx(self, db: aiosqlite.Connection) -> tuple[str, int]:
        """Return the fenced owner whose durable runs may be recovered.

        Kernel bootstrap deliberately recovers already-owned work while the
        runtime is ``activated`` but before ingress becomes ``open``.  This is
        narrower than the start boundary: activation must keep rejecting new
        runs until bootstrap reconciliation completes.
        """

        state = await self._runtime_state_tx(db)
        if state.phase == "legacy" and state.generation == 0:
            return "legacy", 0
        if state.phase in {"activated", "open"} and state.generation > 0:
            return "kernel", state.generation
        raise RuntimeActivationError(
            "runtime_recovery_closed",
            f"execution recovery is closed while runtime phase is {state.phase}",
        )
