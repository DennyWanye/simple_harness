"""Run creation, start snapshots and run reads."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import aiosqlite

from deskpet.execution.contracts import (
    CreateRunResult,
    IdempotencyConflict,
    PersistenceLevel,
    PersistenceRequired,
    RunCreate,
    RunEventCandidate,
    RunIdentityConflict,
    RunNotFound,
    RunStartSnapshotRecord,
    RunStatus,
    assert_idempotent_run_intent,
    canonical_json,
    thaw_json,
)

from .core import ExecutionUowCore
from .support import RuntimeActivationError

if TYPE_CHECKING:
    from ..execution_uow import SqliteExecutionUnitOfWork


class RunLifecycleMixin(ExecutionUowCore):
    """Run creation, start snapshots and run reads."""

    __slots__ = ()

    @staticmethod
    def _workspace_json(spec: RunCreate) -> str:
        return canonical_json(thaw_json(spec.context.workspace))

    @staticmethod
    def _provider_plan_json(spec: RunCreate) -> str:
        return canonical_json(thaw_json(spec.context.provider_plan))

    @staticmethod
    def _initial_started_at(spec: RunCreate, now: float) -> float | None:
        return now if spec.status is RunStatus.RUNNING else None

    @staticmethod
    def _assert_idempotent_intent(row: Mapping[str, Any], spec: RunCreate) -> None:
        existing = ExecutionUowCore._row_to_record(row).spec
        assert_idempotent_run_intent(existing, spec)

    async def _existing_for_spec(
        self, db: aiosqlite.Connection, spec: RunCreate
    ) -> aiosqlite.Row | None:
        by_key = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE idempotency_key=?",
                (spec.idempotency_key,),
            )
        ).fetchone()
        if by_key is not None:
            self._assert_idempotent_intent(by_key, spec)
            return by_key
        by_id = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
        ).fetchone()
        if by_id is not None:
            raise RunIdentityConflict(
                "run_identity_conflict", "run_id already names another idempotency intent"
            )
        return None

    @staticmethod
    def _assert_run_owner(row: Mapping[str, Any], expected_owner: tuple[str, int]) -> None:
        stored_owner = (str(row["owner_kind"]), int(row["owner_generation"]))
        if stored_owner != expected_owner:
            raise RuntimeActivationError(
                "run_owner_conflict",
                "execution row belongs to another runtime owner generation",
            )

    async def _validate_parent_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        expected_owner: tuple[str, int],
    ) -> None:
        parent_id = spec.context.parent_run_id
        if parent_id is None:
            return
        parent = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (parent_id,))
        ).fetchone()
        if parent is None:
            raise RunNotFound("parent_not_found", f"parent run does not exist: {parent_id}")
        self._assert_run_owner(parent, expected_owner)
        expected = {
            "root_run_id": spec.context.root_run_id,
            "session_id": spec.context.session_id,
            "principal_id": spec.context.principal_id,
            "auth_epoch": spec.context.auth_epoch,
        }
        for field_name, value in expected.items():
            stored = parent[field_name]
            stored = int(stored) if field_name == "auth_epoch" else str(stored)
            if stored != value:
                raise RunIdentityConflict(
                    "parent_scope_conflict",
                    f"parent and child differ at {field_name}",
                )

    async def _insert_run_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        *,
        version: int,
    ) -> tuple[aiosqlite.Row, bool]:
        expected_owner = await self._required_start_owner_tx(db)
        return await self._insert_run_for_owner_tx(
            db, spec, version=version, expected_owner=expected_owner
        )

    async def _insert_run_for_owner_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        *,
        version: int,
        expected_owner: tuple[str, int],
    ) -> tuple[aiosqlite.Row, bool]:
        """Insert or reuse a run for an owner already fenced by the caller."""

        owner_kind, owner_generation = expected_owner
        existing = await self._existing_for_spec(db, spec)
        if existing is not None:
            self._assert_run_owner(existing, expected_owner)
            return existing, False
        await self._validate_parent_tx(db, spec, expected_owner)
        now = float(self._clock())
        await db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,parent_run_id,
            request_id,turn_id,venue,workspace_json,capability_hash,provider_plan_json,
            trace_id,principal_id,auth_epoch,payload_fingerprint,capability_fingerprint,
            driver_kind,profile_key,persistence_level,status,version,durable_seq,
            owner_kind,owner_generation,context_owner_key,
            context_profile_generation,context_binding_epoch,
            created_at,started_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
            (
                spec.run_id,
                spec.schema_version,
                spec.idempotency_key,
                spec.context.session_id,
                spec.context.root_run_id,
                spec.context.parent_run_id,
                spec.context.request_id,
                spec.context.turn_id,
                spec.context.venue,
                self._workspace_json(spec),
                spec.context.capability_hash,
                self._provider_plan_json(spec),
                spec.context.trace_id,
                spec.context.principal_id,
                spec.context.auth_epoch,
                spec.payload_fingerprint,
                spec.capability_fingerprint,
                spec.driver_kind,
                spec.profile_key,
                spec.persistence_level.value,
                spec.status.value,
                version,
                owner_kind,
                owner_generation,
                spec.context.owner_key,
                spec.context.profile_generation,
                spec.context.binding_epoch,
                now,
                self._initial_started_at(spec, now),
                now,
            ),
        )
        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
        ).fetchone()
        assert row is not None
        return row, True

    async def create(
        self,
        spec: RunCreate,
        *,
        initial_event: RunEventCandidate | None = None,
    ) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "SQLite create is reserved for runs that have crossed a durable boundary",
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            self._fault("create_after_run")
            if initial_event is not None:
                _, row, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=int(row["version"]),
                    event=initial_event,
                    deliveries=(),
                )
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)

    @staticmethod
    def _row_to_start_snapshot(row: Mapping[str, Any]) -> RunStartSnapshotRecord:
        return RunStartSnapshotRecord(
            run_id=str(row["run_id"]),
            snapshot_schema_version=int(row["snapshot_schema_version"]),
            start_fingerprint=str(row["start_fingerprint"]),
            canonical_messages_json=str(row["canonical_messages_json"]),
            session_cursor_json=str(row["session_cursor_json"]),
            prepared_refs_json=str(row["prepared_refs_json"]),
            sanitized_request_json=str(row["sanitized_request_json"]),
            run_context_json=str(row["run_context_json"]),
            run_spec_json=str(row["run_spec_json"]),
            capability_snapshot_json=str(row["capability_snapshot_json"]),
            capability_snapshot_hash=str(row["capability_snapshot_hash"]),
            provider_launch_policy_json=str(row["provider_launch_policy_json"]),
            terminal_deliveries_json=str(row["terminal_deliveries_json"]),
            terminal_deliveries_hash=str(row["terminal_deliveries_hash"]),
            capability_lease_intent_ref=(
                None
                if row["capability_lease_intent_ref"] is None
                else str(row["capability_lease_intent_ref"])
            ),
            capability_lease_intent_hash=(
                None
                if row["capability_lease_intent_hash"] is None
                else str(row["capability_lease_intent_hash"])
            ),
            created_at=float(row["created_at"]),
            start_extension_receipts_json=str(row["start_extension_receipts_json"]),
            start_extension_receipts_hash=str(row["start_extension_receipts_hash"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _start_snapshot_values(
        snapshot: RunStartSnapshotRecord,
    ) -> tuple[object, ...]:
        return (
            snapshot.run_id,
            snapshot.schema_version,
            snapshot.snapshot_schema_version,
            snapshot.start_fingerprint,
            snapshot.canonical_messages_json,
            snapshot.session_cursor_json,
            snapshot.prepared_refs_json,
            snapshot.sanitized_request_json,
            snapshot.run_context_json,
            snapshot.run_spec_json,
            snapshot.capability_snapshot_json,
            snapshot.capability_snapshot_hash,
            snapshot.provider_launch_policy_json,
            snapshot.terminal_deliveries_json,
            snapshot.terminal_deliveries_hash,
            snapshot.capability_lease_intent_ref,
            snapshot.capability_lease_intent_hash,
            snapshot.start_extension_receipts_json,
            snapshot.start_extension_receipts_hash,
            snapshot.created_at,
        )

    async def _ensure_start_snapshot_tx(
        self,
        db: aiosqlite.Connection,
        *,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        run_created: bool,
        start_commit_extensions: Sequence[Any],
    ) -> RunStartSnapshotRecord:
        existing_snapshot_row = await (
            await db.execute(
                """SELECT * FROM execution_run_start_snapshots
                WHERE run_id=?""",
                (spec.run_id,),
            )
        ).fetchone()
        if not run_created:
            if existing_snapshot_row is None:
                raise IdempotencyConflict(
                    "run_start_snapshot_missing",
                    "existing durable Run has no immutable start snapshot",
                )
            existing = self._row_to_start_snapshot(existing_snapshot_row)
            comparable = replace(
                existing,
                start_extension_receipts_json=(start_snapshot.start_extension_receipts_json),
                start_extension_receipts_hash=(start_snapshot.start_extension_receipts_hash),
            )
            if comparable != start_snapshot:
                raise IdempotencyConflict(
                    "run_start_snapshot_conflict",
                    "Run replay supplied a different start snapshot",
                )
            return existing

        receipts: list[Mapping[str, Any]] = []
        transaction = self.bind(db)
        for extension in start_commit_extensions:
            apply = getattr(extension, "apply_start_commit", None)
            if not callable(apply):
                raise TypeError("start commit extension has no apply_start_commit")
            receipt = apply(
                transaction,
                spec=spec,
                start_snapshot=start_snapshot,
            )
            if inspect.isawaitable(receipt):
                receipt = await receipt
            if hasattr(receipt, "to_dict"):
                receipt = receipt.to_dict()
            if not isinstance(receipt, Mapping):
                raise TypeError("start commit extension must return a typed descriptor")
            receipts.append(dict(receipt))
        receipts_json = canonical_json(receipts)
        receipts_hash = hashlib.sha256(receipts_json.encode("utf-8")).hexdigest()
        snapshot = replace(
            start_snapshot,
            start_extension_receipts_json=receipts_json,
            start_extension_receipts_hash=receipts_hash,
        )
        await db.execute(
            """INSERT INTO execution_run_start_snapshots(
            run_id,schema_version,snapshot_schema_version,
            start_fingerprint,canonical_messages_json,
            session_cursor_json,prepared_refs_json,
            sanitized_request_json,run_context_json,run_spec_json,
            capability_snapshot_json,capability_snapshot_hash,
            provider_launch_policy_json,terminal_deliveries_json,
            terminal_deliveries_hash,capability_lease_intent_ref,
            capability_lease_intent_hash,
            start_extension_receipts_json,
            start_extension_receipts_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            self._start_snapshot_values(snapshot),
        )
        return snapshot

    async def create_with_start_snapshot(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        initial_event: RunEventCandidate | None = None,
        start_commit_extensions: Sequence[Any] = (),
    ) -> CreateRunResult:
        """Atomically create a durable Run, immutable start and host receipts."""

        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "a Run start snapshot requires durable persistence",
            )
        if start_snapshot.run_id != spec.run_id:
            raise IdempotencyConflict(
                "run_start_scope_conflict",
                "start snapshot is bound to another Run",
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            start_snapshot = await self._ensure_start_snapshot_tx(
                db,
                spec=spec,
                start_snapshot=start_snapshot,
                run_created=created,
                start_commit_extensions=start_commit_extensions,
            )
            self._fault("create_with_start_after_snapshot")
            if initial_event is not None:
                _, row, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=int(row["version"]),
                    event=initial_event,
                    deliveries=(),
                )
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)

    async def read_run_start_snapshot(self, run_id: str) -> RunStartSnapshotRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_run_start_snapshots
                    WHERE run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_start_snapshot(row)
