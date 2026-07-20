"""SQLite implementation of the generic execution ledger unit of work.

New durable execution facts and workflow scheduler facts share one
``workflow.db`` connection and transaction.  Legacy workflow rows are exposed
only as read-only in-memory projections and are never backfilled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import aiosqlite

from deskpet.execution.contracts import (
    ActorAction,
    ActorContext,
    AttachmentPolicy,
    AuthorizationError,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildCommandStatus,
    ChildSignalRecord,
    CreateRunResult,
    DecisionAuthorization,
    DecisionConflict,
    DecisionKind,
    DecisionNotFound,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DecisionStatus,
    DeliveryClaimConflict,
    DeliveryNotFound,
    DeliveryRecord,
    DeliverySpec,
    DeliveryStatus,
    EventNotFound,
    FinalizeRunResult,
    GrantConsume,
    GrantConsumeConflict,
    GrantNotFound,
    IdempotencyConflict,
    LegacyRunProjection,
    LinkKind,
    OutcomeStatus,
    ParentCycleError,
    PersistenceLevel,
    PersistenceRequired,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunIdentityConflict,
    RunLinkSpec,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    WorkflowRunSeed,
    assert_idempotent_run_intent,
    canonical_json,
    fingerprint_json,
    stable_delivery_id,
    stable_decision_grant_id,
    stable_event_id,
    thaw_json,
)
from deskpet.execution.ports import RunView

from .schema import initialize_workflow_db


_FaultInjector = Callable[[str], None]


class SqliteExecutionUnitOfWork:
    """CAS-based execution ledger backed by the workflow SQLite database."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: _FaultInjector | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._fault_injector = fault_injector
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await initialize_workflow_db(self.path)
            self._initialized = True

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

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
    def _row_to_record(row: Mapping[str, Any]) -> RunRecord:
        context = RunContext(
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            parent_run_id=(
                str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
            ),
            request_id=str(row["request_id"]),
            turn_id=str(row["turn_id"]),
            venue=str(row["venue"]),
            workspace=json.loads(str(row["workspace_json"])),
            capability_hash=str(row["capability_hash"]),
            provider_plan=json.loads(str(row["provider_plan_json"])),
            trace_id=str(row["trace_id"]),
            principal_id=str(row["principal_id"]),
            auth_epoch=int(row["auth_epoch"]),
            schema_version=int(row["schema_version"]),
        )
        spec = RunCreate(
            run_id=str(row["run_id"]),
            idempotency_key=str(row["idempotency_key"]),
            context=context,
            payload_fingerprint=str(row["payload_fingerprint"]),
            capability_fingerprint=str(row["capability_fingerprint"]),
            driver_kind=str(row["driver_kind"]),
            profile_key=str(row["profile_key"]),
            persistence_level=str(row["persistence_level"]),
            # Creation status is not mutable execution state.  It is kept
            # non-terminal when reconstructing the immutable creation intent.
            status=RunStatus.CREATED,
            schema_version=int(row["schema_version"]),
        )
        return RunRecord(
            spec=spec,
            status=str(row["status"]),
            persistence_level=str(row["persistence_level"]),
            version=int(row["version"]),
            durable_seq=int(row["durable_seq"]),
            terminal_event_id=(
                str(row["terminal_event_id"])
                if row["terminal_event_id"] is not None
                else None
            ),
            cancel_reason=(
                str(row["cancel_reason"]) if row["cancel_reason"] is not None else None
            ),
            created_at=float(row["created_at"]),
            started_at=(float(row["started_at"]) if row["started_at"] is not None else None),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> RunEvent:
        candidate = RunEventCandidate(
            event_key=str(row["event_key"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            driver_kind=str(row["driver_kind"]),
            correlation=json.loads(str(row["correlation_json"])),
            payload=json.loads(str(row["payload_json"])),
            error=(
                json.loads(str(row["error_json"]))
                if row["error_json"] is not None
                else None
            ),
            artifact_refs=json.loads(str(row["artifact_refs_json"])),
            schema_version=int(row["schema_version"]),
        )
        return RunEvent(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            root_run_id=str(row["root_run_id"]),
            session_id=str(row["session_id"]),
            durable_seq=int(row["durable_seq"]),
            candidate=candidate,
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_delivery(row: Mapping[str, Any]) -> DeliveryRecord:
        return DeliveryRecord(
            delivery_id=str(row["delivery_id"]),
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            sink_kind=str(row["sink_kind"]),
            sink_instance=str(row["sink_instance"]),
            target_id=str(row["target_id"]),
            policy=str(row["policy"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            delivery_version=int(row["delivery_version"]),
            next_attempt_at=(
                float(row["next_attempt_at"])
                if row["next_attempt_at"] is not None
                else None
            ),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(
                float(row["delivered_at"])
                if row["delivered_at"] is not None
                else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_child_command(row: Mapping[str, Any]) -> ChildCommandRecord:
        child_spec = RunCreate.from_dict(json.loads(str(row["child_spec_json"])))
        intent = ChildCommandIntent(
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_spec=child_spec,
            child_request=json.loads(str(row["child_request_json"])),
            capability_subset=tuple(
                str(item)
                for item in json.loads(str(row["capability_subset_json"]))
            ),
            attachment_policy=str(row["join_policy"]),
            capability_snapshot_ref=str(row["capability_snapshot_ref"]),
            schema_version=int(row["schema_version"]),
        )
        if intent.intent_fingerprint != str(row["intent_fingerprint"]):
            raise IdempotencyConflict(
                "child_intent_corrupt", "persisted child command fingerprint differs"
            )
        return ChildCommandRecord(
            intent=intent,
            status=str(row["status"]),
            schedule_lease_owner=(
                str(row["schedule_lease_owner"])
                if row["schedule_lease_owner"] is not None
                else None
            ),
            schedule_lease_epoch=int(row["schedule_lease_epoch"]),
            schedule_lease_expires_at=(
                float(row["schedule_lease_expires_at"])
                if row["schedule_lease_expires_at"] is not None
                else None
            ),
            attempts=int(row["attempts"]),
            next_attempt_at=(
                float(row["next_attempt_at"])
                if row["next_attempt_at"] is not None
                else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] else None),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ack_at=float(row["ack_at"]) if row["ack_at"] is not None else None,
        )

    @staticmethod
    def _row_to_child_signal(row: Mapping[str, Any]) -> ChildSignalRecord:
        return ChildSignalRecord(
            signal_id=str(row["signal_id"]),
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_run_id=str(row["child_run_id"]),
            kind=str(row["kind"]),
            payload=json.loads(str(row["payload_json"])),
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(
                float(row["delivered_at"])
                if row["delivered_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_decision(row: Mapping[str, Any]) -> DecisionRecord:
        request = DecisionOpen(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            nonce=str(row["nonce"]),
            kind=str(row["kind"]),
            prompt_schema_version=int(row["prompt_schema_version"]),
            prompt=json.loads(str(row["prompt_json"])),
            expires_at=(
                float(row["expires_at"]) if row["expires_at"] is not None else None
            ),
            domain_kind=(
                str(row["domain_kind"]) if row["domain_kind"] is not None else None
            ),
            domain_id=(str(row["domain_id"]) if row["domain_id"] is not None else None),
            call_id=(str(row["call_id"]) if row["call_id"] is not None else None),
            effect_id=(
                str(row["effect_id"]) if row["effect_id"] is not None else None
            ),
            tool_name=(
                str(row["tool_name"]) if row["tool_name"] is not None else None
            ),
            args_hash=(
                str(row["args_hash"]) if row["args_hash"] is not None else None
            ),
            capability_hash=(
                str(row["capability_hash"])
                if row["capability_hash"] is not None
                else None
            ),
            scope_hash=(
                str(row["scope_hash"]) if row["scope_hash"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )
        response = (
            json.loads(str(row["response_json"]))
            if row["response_json"] is not None
            else None
        )
        return DecisionRecord(
            request=request,
            status=str(row["status"]),
            response_schema_version=(
                int(row["response_schema_version"])
                if row["response_schema_version"] is not None
                else None
            ),
            response=response,
            decision_version=int(row["decision_version"]),
            created_at=float(row["created_at"]),
            resolved_at=(
                float(row["resolved_at"]) if row["resolved_at"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_authorization(row: Mapping[str, Any]) -> DecisionAuthorization:
        return DecisionAuthorization(
            grant_id=str(row["grant_id"]),
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            call_id=str(row["call_id"]),
            effect_id=str(row["effect_id"]),
            tool_name=str(row["tool_name"]),
            args_hash=str(row["args_hash"]),
            capability_hash=str(row["capability_hash"]),
            scope_hash=str(row["scope_hash"]),
            expires_at=float(row["expires_at"]),
            version=int(row["grant_version"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _assert_idempotent_intent(row: Mapping[str, Any], spec: RunCreate) -> None:
        existing = SqliteExecutionUnitOfWork._row_to_record(row).spec
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

    async def _validate_parent_tx(
        self, db: aiosqlite.Connection, spec: RunCreate
    ) -> None:
        parent_id = spec.context.parent_run_id
        if parent_id is None:
            return
        parent = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (parent_id,))
        ).fetchone()
        if parent is None:
            raise RunNotFound("parent_not_found", f"parent run does not exist: {parent_id}")
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
        existing = await self._existing_for_spec(db, spec)
        if existing is not None:
            return existing, False
        await self._validate_parent_tx(db, spec)
        now = float(self._clock())
        await db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,parent_run_id,
            request_id,turn_id,venue,workspace_json,capability_hash,provider_plan_json,
            trace_id,principal_id,auth_epoch,payload_fingerprint,capability_fingerprint,
            driver_kind,profile_key,persistence_level,status,version,durable_seq,
            created_at,started_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)""",
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

    async def create(self, spec: RunCreate) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "SQLite create is reserved for runs that have crossed a durable boundary",
            )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row, created = await self._insert_run_tx(db, spec, version=0)
            self._fault("create_after_run")
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def promote(
        self,
        spec: RunCreate,
        *,
        expected_version: int,
    ) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "invalid_promotion", "promotion target must be durable"
            )
        if expected_version < 0:
            raise VersionConflict("invalid_version", "expected_version must be non-negative")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await self._existing_for_spec(db, spec)
            if existing is not None:
                if str(existing["persistence_level"]) != PersistenceLevel.DURABLE.value:
                    raise PersistenceRequired(
                        "promotion_not_durable", "persisted promotion is not durable"
                    )
                await db.commit()
                return CreateRunResult(self._row_to_record(existing), False)
            row, created = await self._insert_run_tx(
                db, spec, version=expected_version + 1
            )
            self._fault("promote_after_run")
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    @staticmethod
    def _event_json(candidate: RunEventCandidate) -> dict[str, str | None]:
        return {
            "correlation_json": canonical_json(thaw_json(candidate.correlation)),
            "payload_json": canonical_json(thaw_json(candidate.payload)),
            "error_json": (
                canonical_json(thaw_json(candidate.error))
                if candidate.error is not None
                else None
            ),
            "artifact_refs_json": canonical_json(list(candidate.artifact_refs)),
        }

    def _assert_event_matches(
        self,
        row: Mapping[str, Any],
        run: Mapping[str, Any],
        candidate: RunEventCandidate,
    ) -> None:
        expected_id = stable_event_id(str(run["run_id"]), candidate.event_key)
        payload = self._event_json(candidate)
        expected: dict[str, object] = {
            "event_id": expected_id,
            "kind": candidate.kind,
            "status": candidate.status.value,
            "driver_kind": candidate.driver_kind,
            **payload,
        }
        for field_name, value in expected.items():
            if row[field_name] != value:
                raise IdempotencyConflict(
                    "event_intent_conflict",
                    f"event_key already names different {field_name}",
                )

    @staticmethod
    def _delivery_identity(delivery: DeliverySpec) -> tuple[str, str, str, str]:
        return (
            delivery.sink_kind,
            delivery.sink_instance,
            delivery.target_id,
            delivery.policy.value,
        )

    async def _assert_delivery_set_tx(
        self,
        db: aiosqlite.Connection,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
    ) -> None:
        rows = await (
            await db.execute(
                """SELECT sink_kind,sink_instance,target_id,policy
                FROM execution_deliveries WHERE event_id=?""",
                (event_id,),
            )
        ).fetchall()
        stored = {
            (str(row["sink_kind"]), str(row["sink_instance"]), str(row["target_id"]), str(row["policy"]))
            for row in rows
        }
        requested = {self._delivery_identity(item) for item in deliveries}
        if stored != requested:
            raise IdempotencyConflict(
                "delivery_intent_conflict",
                "event replay supplied a different delivery set",
            )

    async def _insert_deliveries_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
        now: float,
    ) -> None:
        identities = [self._delivery_identity(item) for item in deliveries]
        if len(set(identities)) != len(identities):
            raise IdempotencyConflict(
                "duplicate_delivery", "one event cannot contain duplicate sink intents"
            )
        for delivery in deliveries:
            await db.execute(
                """INSERT INTO execution_deliveries(
                delivery_id,schema_version,event_id,run_id,sink_kind,sink_instance,
                target_id,policy,status,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,'pending',?,?)""",
                (
                    stable_delivery_id(event_id, delivery),
                    event_id,
                    run_id,
                    delivery.sink_kind,
                    delivery.sink_instance,
                    delivery.target_id,
                    delivery.policy.value,
                    now,
                    now,
                ),
            )

    async def _append_event_tx(
        self,
        db: aiosqlite.Connection,
        run: aiosqlite.Row,
        *,
        expected_version: int,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec],
    ) -> tuple[RunEvent, aiosqlite.Row, bool]:
        if event.driver_kind != str(run["driver_kind"]):
            raise RunIdentityConflict(
                "driver_event_conflict", "event driver differs from the run owner"
            )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                (run["run_id"], event.event_key),
            )
        ).fetchone()
        if existing is not None:
            self._assert_event_matches(existing, run, event)
            await self._assert_delivery_set_tx(db, str(existing["event_id"]), deliveries)
            hydrated = dict(existing)
            hydrated["root_run_id"] = run["root_run_id"]
            hydrated["session_id"] = run["session_id"]
            return self._row_to_event(hydrated), run, True
        if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
            raise TerminalConflict(
                "run_already_terminal", "a terminal run cannot append more events"
            )
        if int(run["version"]) != expected_version:
            raise VersionConflict(
                "stale_run_version",
                f"expected run version {expected_version}, found {run['version']}",
            )
        now = float(self._clock())
        durable_seq = int(run["durable_seq"]) + 1
        event_id = stable_event_id(str(run["run_id"]), event.event_key)
        payload = self._event_json(event)
        await db.execute(
            """INSERT INTO execution_events(
            event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
            correlation_json,payload_json,error_json,artifact_refs_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                event.schema_version,
                event.event_key,
                run["run_id"],
                durable_seq,
                event.kind,
                event.status.value,
                event.driver_kind,
                payload["correlation_json"],
                payload["payload_json"],
                payload["error_json"],
                payload["artifact_refs_json"],
                now,
            ),
        )
        await self._insert_deliveries_tx(
            db,
            run_id=str(run["run_id"]),
            event_id=event_id,
            deliveries=deliveries,
            now=now,
        )
        cursor = await db.execute(
            """UPDATE execution_runs SET durable_seq=?,version=version+1,updated_at=?
            WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
            (durable_seq, now, run["run_id"], expected_version),
        )
        if cursor.rowcount != 1:
            raise VersionConflict(
                "stale_run_version", "run changed before event commit"
            )
        updated = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run["run_id"],))
        ).fetchone()
        stored = await (
            await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
        ).fetchone()
        assert updated is not None and stored is not None
        hydrated = dict(stored)
        hydrated["root_run_id"] = updated["root_run_id"]
        hydrated["session_id"] = updated["session_id"]
        return self._row_to_event(hydrated), updated, False

    async def append_event(
        self,
        run_id: str,
        *,
        expected_version: int,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> RunEvent:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            stored, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=expected_version,
                event=event,
                deliveries=deliveries,
            )
            self._fault("append_event_before_commit")
            await db.commit()
            return stored
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_event(self, event_id: str) -> RunEvent:
        event_key = str(event_id).strip()
        if not event_key:
            raise EventNotFound("event_not_found", "event_id must be non-empty")
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT execution_events.*,execution_runs.root_run_id,
                    execution_runs.session_id FROM execution_events
                    JOIN execution_runs USING(run_id)
                    WHERE execution_events.event_id=?""",
                    (event_key,),
                )
            ).fetchone()
            if row is None:
                raise EventNotFound(
                    "event_not_found", f"execution event does not exist: {event_key}"
                )
            return self._row_to_event(row)
        finally:
            await db.close()

    async def list_events(
        self,
        run_id: str,
        *,
        after_durable_seq: int = 0,
    ) -> tuple[RunEvent, ...]:
        if (
            not isinstance(after_durable_seq, int)
            or isinstance(after_durable_seq, bool)
            or after_durable_seq < 0
        ):
            raise ValueError("after_durable_seq must be a non-negative integer")
        db = await self._connect()
        try:
            run = await (
                await db.execute(
                    "SELECT root_run_id,session_id FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found", f"execution run does not exist: {run_id}"
                )
            rows = await (
                await db.execute(
                    """SELECT execution_events.*,? AS root_run_id,? AS session_id
                    FROM execution_events WHERE run_id=? AND durable_seq>?
                    ORDER BY durable_seq,event_id""",
                    (run["root_run_id"], run["session_id"], run_id, after_durable_seq),
                )
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)
        finally:
            await db.close()

    async def list_event_deliveries(
        self, event_id: str
    ) -> tuple[DeliveryRecord, ...]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_deliveries WHERE event_id=?
                    ORDER BY sink_kind,sink_instance,target_id""",
                    (event_id,),
                )
            ).fetchall()
            return tuple(self._row_to_delivery(row) for row in rows)
        finally:
            await db.close()

    async def claim_delivery(
        self,
        *,
        sink_keys: Sequence[tuple[str, str]] = (),
        claim_ttl_seconds: float = 30.0,
    ) -> DeliveryRecord | None:
        ttl = float(claim_ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("claim_ttl_seconds must be finite and positive")
        normalized_keys = tuple(
            (str(kind).strip(), str(instance).strip())
            for kind, instance in sink_keys
        )
        if any(not kind or not instance for kind, instance in normalized_keys):
            raise ValueError("sink keys require non-empty kind and instance")
        if len(dict.fromkeys(normalized_keys)) != len(normalized_keys):
            raise ValueError("sink keys must be unique")

        now = float(self._clock())
        eligible = """(
            status='pending'
            OR (status='failed' AND COALESCE(next_attempt_at,0)<=?)
            OR (status='delivering' AND next_attempt_at IS NOT NULL
                AND next_attempt_at<=? AND policy!='best_effort')
        )"""
        sink_clause = ""
        sink_params: list[object] = []
        if normalized_keys:
            sink_clause = " AND (" + " OR ".join(
                "(sink_kind=? AND sink_instance=?)" for _ in normalized_keys
            ) + ")"
            for kind, instance in normalized_keys:
                sink_params.extend((kind, instance))

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            # BEST_EFFORT is at-most-one physical attempt.  If its worker
            # vanished after claim, persistently discard the expired claim;
            # replaying TTS after restart is worse than dropping the cue.
            await db.execute(
                """UPDATE execution_deliveries SET status='discarded',
                delivery_version=delivery_version+1,next_attempt_at=NULL,
                last_error='best-effort claim expired',updated_at=?
                WHERE status='delivering' AND policy='best_effort'
                AND next_attempt_at IS NOT NULL AND next_attempt_at<=?""",
                (now, now),
            )
            row = await (
                await db.execute(
                    f"""SELECT * FROM execution_deliveries
                    WHERE {eligible}{sink_clause}
                    ORDER BY CASE policy
                        WHEN 'durable_required' THEN 0
                        WHEN 'retry_while_bound' THEN 1 ELSE 2 END,
                        created_at,delivery_id LIMIT 1""",
                    (now, now, *sink_params),
                )
            ).fetchone()
            if row is None:
                await db.commit()
                return None
            lease_expires_at = now + ttl
            cursor = await db.execute(
                f"""UPDATE execution_deliveries SET status='delivering',
                attempts=attempts+1,delivery_version=delivery_version+1,
                next_attempt_at=?,updated_at=?
                WHERE delivery_id=? AND delivery_version=? AND {eligible}""",
                (
                    lease_expires_at,
                    now,
                    row["delivery_id"],
                    row["delivery_version"],
                    now,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise DeliveryClaimConflict(
                    "delivery_claim_conflict", "delivery changed before claim CAS"
                )
            claimed = await (
                await db.execute(
                    "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                    (row["delivery_id"],),
                )
            ).fetchone()
            assert claimed is not None
            await db.commit()
            return self._row_to_delivery(claimed)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def complete_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
    ) -> DeliveryRecord:
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """UPDATE execution_deliveries SET status='delivered',
                delivery_version=delivery_version+1,next_attempt_at=NULL,last_error=NULL,
                updated_at=?,delivered_at=? WHERE delivery_id=?
                AND delivery_version=? AND status='delivering'""",
                (now, now, delivery_id, expected_version),
            )
            if cursor.rowcount != 1:
                existing = await (
                    await db.execute(
                        "SELECT delivery_id FROM execution_deliveries WHERE delivery_id=?",
                        (delivery_id,),
                    )
                ).fetchone()
                if existing is None:
                    raise DeliveryNotFound(
                        "delivery_not_found",
                        f"execution delivery does not exist: {delivery_id}",
                    )
                raise DeliveryClaimConflict(
                    "delivery_claim_conflict", "delivery completion lost its claim fence"
                )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_delivery(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def release_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        error: str,
        retry_at: float | None,
        discard: bool,
    ) -> DeliveryRecord:
        message = str(error).strip()
        if not message:
            raise ValueError("delivery error must be non-empty")
        if not discard and retry_at is None:
            raise ValueError("retryable delivery release requires retry_at")
        if retry_at is not None and not math.isfinite(float(retry_at)):
            raise ValueError("retry_at must be finite")
        status = DeliveryStatus.DISCARDED if discard else DeliveryStatus.FAILED
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """UPDATE execution_deliveries SET status=?,
                delivery_version=delivery_version+1,next_attempt_at=?,last_error=?,
                updated_at=?,delivered_at=NULL WHERE delivery_id=?
                AND delivery_version=? AND status='delivering'""",
                (
                    status.value,
                    None if discard else float(retry_at),
                    message,
                    now,
                    delivery_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                existing = await (
                    await db.execute(
                        "SELECT delivery_id FROM execution_deliveries WHERE delivery_id=?",
                        (delivery_id,),
                    )
                ).fetchone()
                if existing is None:
                    raise DeliveryNotFound(
                        "delivery_not_found",
                        f"execution delivery does not exist: {delivery_id}",
                    )
                raise DeliveryClaimConflict(
                    "delivery_claim_conflict", "delivery release lost its claim fence"
                )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_delivery(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def required_deliveries_complete(self, event_id: str) -> bool:
        db = await self._connect()
        try:
            event = await (
                await db.execute(
                    "SELECT event_id FROM execution_events WHERE event_id=?",
                    (event_id,),
                )
            ).fetchone()
            if event is None:
                raise EventNotFound(
                    "event_not_found", f"execution event does not exist: {event_id}"
                )
            remaining = await (
                await db.execute(
                    """SELECT COUNT(*) FROM execution_deliveries
                    WHERE event_id=? AND policy='durable_required'
                    AND status!='delivered'""",
                    (event_id,),
                )
            ).fetchone()
            return int(remaining[0]) == 0
        finally:
            await db.close()

    @staticmethod
    def _assert_decision_intent(
        existing: DecisionRecord, requested: DecisionOpen
    ) -> None:
        if existing.request != requested:
            raise DecisionConflict(
                "decision_identity_conflict",
                "decision id or run nonce is already bound to another intent",
            )

    def _authorize_run_row(
        self,
        row: Mapping[str, Any],
        *,
        expected_session_id: str,
        actor: ActorContext,
    ) -> RunRecord:
        record = self._row_to_record(row)
        self._authorize_view(
            RunRef(record.run_id, expected_session_id), actor, record
        )
        return record

    async def open_decision(
        self,
        request: DecisionOpen,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> DecisionRecord:
        if (
            not isinstance(expected_run_version, int)
            or isinstance(expected_run_version, bool)
            or expected_run_version < 0
        ):
            raise ValueError("expected_run_version must be non-negative")
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (request.run_id,)
                )
            ).fetchone()
            if run_row is None:
                raise RunNotFound(
                    "run_not_found", f"execution run does not exist: {request.run_id}"
                )
            run = self._authorize_run_row(
                run_row,
                expected_session_id=actor.session_id,
                actor=actor,
            )
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_decisions
                    WHERE decision_id=? OR (run_id=? AND nonce=?)
                    ORDER BY CASE WHEN decision_id=? THEN 0 ELSE 1 END LIMIT 1""",
                    (
                        request.decision_id,
                        request.run_id,
                        request.nonce,
                        request.decision_id,
                    ),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_decision(existing_row)
                self._assert_decision_intent(existing, request)
                await db.commit()
                return existing
            if run.persistence_level is not PersistenceLevel.DURABLE:
                raise PersistenceRequired(
                    "decision_requires_durable_run",
                    "a run must be durable before opening a decision",
                )
            if run.version != expected_run_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_run_version}, found {run.version}",
                )
            if run.status in TERMINAL_RUN_STATUSES or run.status is RunStatus.CANCEL_REQUESTED:
                raise DecisionConflict(
                    "run_not_signalable", "cancelled or terminal runs cannot open decisions"
                )
            if request.expires_at is not None and request.expires_at <= now:
                raise DecisionConflict(
                    "decision_expired", "cannot open an already expired decision"
                )
            if (
                request.capability_hash is not None
                and request.capability_hash != run.context.capability_hash
            ):
                raise DecisionConflict(
                    "decision_binding_mismatch",
                    "decision capability does not match the durable run context",
                )
            cursor = await db.execute(
                """UPDATE execution_runs SET status='waiting',version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                AND status IN ('created','queued','running','waiting')""",
                (now, request.run_id, expected_run_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_run_version", "run changed before decision open CAS"
                )
            await db.execute(
                """INSERT INTO execution_decisions(
                decision_id,schema_version,run_id,nonce,kind,status,
                prompt_schema_version,prompt_json,response_schema_version,response_json,
                domain_kind,domain_id,call_id,effect_id,tool_name,args_hash,
                capability_hash,scope_hash,decision_version,expires_at,created_at,resolved_at
                ) VALUES(?,?,?,?,?,'open',?,?,NULL,NULL,?,?,?,?,?,?,?,?,0,?,?,NULL)""",
                (
                    request.decision_id,
                    request.schema_version,
                    request.run_id,
                    request.nonce,
                    request.kind.value,
                    request.prompt_schema_version,
                    canonical_json(thaw_json(request.prompt)),
                    request.domain_kind,
                    request.domain_id,
                    request.call_id,
                    request.effect_id,
                    request.tool_name,
                    request.args_hash,
                    request.capability_hash,
                    request.scope_hash,
                    request.expires_at,
                    now,
                ),
            )
            self._fault("decision_open_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (request.decision_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_decision(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_decision(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord:
        db = await self._connect()
        try:
            decision_row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            if decision_row is None:
                raise DecisionNotFound(
                    "decision_not_found", f"execution decision does not exist: {decision_id}"
                )
            decision = self._row_to_decision(decision_row)
            if decision.run_id != ref.run_id:
                raise DecisionConflict(
                    "decision_binding_mismatch", "decision is not bound to the supplied run"
                )
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
                )
            ).fetchone()
            assert run_row is not None
            self._authorize_run_row(
                run_row,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            return decision
        finally:
            await db.close()

    @staticmethod
    def _assert_signal_binding(
        decision: DecisionRecord, signal: DecisionSignal
    ) -> None:
        request = decision.request
        scalar_pairs = (
            (signal.decision_id, request.decision_id),
            (signal.run_id, request.run_id),
            (signal.nonce, request.nonce),
            (signal.domain_kind, request.domain_kind),
            (signal.domain_id, request.domain_id),
            (signal.call_id, request.call_id),
            (signal.effect_id, request.effect_id),
            (signal.tool_name, request.tool_name),
            (signal.args_hash, request.args_hash),
            (signal.capability_hash, request.capability_hash),
            (signal.scope_hash, request.scope_hash),
        )
        if any(actual != expected for actual, expected in scalar_pairs):
            raise DecisionConflict(
                "decision_binding_mismatch",
                "signal nonce/run/domain/effect/capability/scope fence differs",
            )
        if signal.expected_version != decision.decision_version:
            raise DecisionConflict(
                "stale_decision_version",
                f"expected decision version {signal.expected_version}, "
                f"found {decision.decision_version}",
            )

    async def resolve_decision(
        self,
        signal: DecisionSignal,
        actor: ActorContext,
    ) -> tuple[DecisionRecord, DecisionAuthorization | None]:
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (signal.decision_id,),
                )
            ).fetchone()
            if row is None:
                raise DecisionNotFound(
                    "decision_not_found",
                    f"execution decision does not exist: {signal.decision_id}",
                )
            decision = self._row_to_decision(row)
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
                )
            ).fetchone()
            assert run_row is not None
            run = self._authorize_run_row(
                run_row,
                expected_session_id=signal.expected_session_id,
                actor=actor,
            )
            self._assert_signal_binding(decision, signal)
            if run.status is RunStatus.CANCEL_REQUESTED or run.status in TERMINAL_RUN_STATUSES:
                raise DecisionConflict(
                    "run_not_signalable", "cancelled or terminal run rejects decision signals"
                )
            if decision.status is not DecisionStatus.OPEN:
                raise DecisionConflict(
                    "decision_already_resolved",
                    "duplicate or late decision signal was rejected",
                )
            if decision.request.expires_at is not None and decision.request.expires_at <= now:
                cursor = await db.execute(
                    """UPDATE execution_decisions SET status='expired',
                    decision_version=decision_version+1,resolved_at=?
                    WHERE decision_id=? AND decision_version=? AND status='open'""",
                    (now, decision.decision_id, signal.expected_version),
                )
                if cursor.rowcount != 1:
                    raise DecisionConflict(
                        "stale_decision_version", "decision changed before expiry CAS"
                    )
                await db.commit()
                raise DecisionConflict(
                    "decision_expired", "expired decision signal was rejected"
                )
            resolved_status = (
                DecisionStatus.ALLOWED if signal.allow else DecisionStatus.DENIED
            )
            cursor = await db.execute(
                """UPDATE execution_decisions SET status=?,response_schema_version=?,
                response_json=?,decision_version=decision_version+1,resolved_at=?
                WHERE decision_id=? AND decision_version=? AND status='open'""",
                (
                    resolved_status.value,
                    signal.response_schema_version,
                    canonical_json(thaw_json(signal.response)),
                    now,
                    decision.decision_id,
                    signal.expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise DecisionConflict(
                    "stale_decision_version", "decision changed before resolve CAS"
                )
            authorization: DecisionAuthorization | None = None
            if decision.request.kind is DecisionKind.PERMISSION and signal.allow:
                expires_at = decision.request.expires_at
                assert expires_at is not None
                grant_id = stable_decision_grant_id(decision.decision_id)
                await db.execute(
                    """INSERT INTO execution_grants(
                    grant_id,schema_version,decision_id,run_id,call_id,effect_id,
                    tool_name,args_hash,capability_hash,scope_hash,status,
                    grant_version,expires_at,created_at,consumed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,'issued',0,?,?,NULL)""",
                    (
                        grant_id,
                        decision.request.schema_version,
                        decision.decision_id,
                        decision.run_id,
                        decision.request.call_id,
                        decision.request.effect_id,
                        decision.request.tool_name,
                        decision.request.args_hash,
                        decision.request.capability_hash,
                        decision.request.scope_hash,
                        expires_at,
                        now,
                    ),
                )
                grant_row = await (
                    await db.execute(
                        "SELECT * FROM execution_grants WHERE grant_id=?", (grant_id,)
                    )
                ).fetchone()
                assert grant_row is not None
                authorization = self._row_to_authorization(grant_row)
            self._fault("decision_resolve_before_commit")
            resolved_row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (decision.decision_id,),
                )
            ).fetchone()
            assert resolved_row is not None
            await db.commit()
            return self._row_to_decision(resolved_row), authorization
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def cancel_open_decisions(
        self,
        ref: RunRef,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> tuple[DecisionRecord, ...]:
        if (
            not isinstance(expected_run_version, int)
            or isinstance(expected_run_version, bool)
            or expected_run_version < 0
        ):
            raise ValueError("expected_run_version must be non-negative")
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,)
                )
            ).fetchone()
            if run_row is None:
                raise RunNotFound(
                    "run_not_found", f"execution run does not exist: {ref.run_id}"
                )
            run = self._authorize_run_row(
                run_row,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            if run.version != expected_run_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_run_version}, found {run.version}",
                )
            if run.status not in (RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED):
                raise DecisionConflict(
                    "run_not_cancelling",
                    "decision cleanup requires an already persisted run cancellation",
                )
            open_rows = await (
                await db.execute(
                    """SELECT * FROM execution_decisions
                    WHERE run_id=? AND status='open' ORDER BY created_at,decision_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            cancelled_ids = {str(row["decision_id"]) for row in open_rows}
            if open_rows:
                cursor = await db.execute(
                    """UPDATE execution_decisions SET status='expired',
                    decision_version=decision_version+1,resolved_at=?
                    WHERE run_id=? AND status='open'""",
                    (now, ref.run_id),
                )
                if cursor.rowcount != len(open_rows):
                    raise DecisionConflict(
                        "decision_cancel_conflict", "open decision set changed during cancel CAS"
                    )
            await db.execute(
                """UPDATE execution_grants SET status='revoked',
                grant_version=grant_version+1 WHERE run_id=? AND status='issued'""",
                (ref.run_id,),
            )
            self._fault("decision_cancel_before_commit")
            cancelled_rows = await (
                await db.execute(
                    """SELECT * FROM execution_decisions WHERE run_id=? AND status='expired'
                    ORDER BY created_at,decision_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            await db.commit()
            return tuple(
                self._row_to_decision(item)
                for item in cancelled_rows
                if str(item["decision_id"]) in cancelled_ids
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def consume_authorization(
        self,
        request: GrantConsume,
        actor: ActorContext,
    ) -> DecisionAuthorization:
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            grant_row = await (
                await db.execute(
                    "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
                )
            ).fetchone()
            if grant_row is None:
                raise GrantNotFound(
                    "grant_not_found", f"execution grant does not exist: {request.grant_id}"
                )
            decision_row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (grant_row["decision_id"],),
                )
            ).fetchone()
            assert decision_row is not None
            decision = self._row_to_decision(decision_row)
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
                )
            ).fetchone()
            assert run_row is not None
            run = self._authorize_run_row(
                run_row,
                expected_session_id=request.expected_session_id,
                actor=actor,
            )
            expected = (
                request.grant_id,
                request.decision_id,
                request.run_id,
                request.call_id,
                request.effect_id,
                request.tool_name,
                request.args_hash,
                request.capability_hash,
                request.scope_hash,
                request.decision_nonce,
            )
            actual = (
                str(grant_row["grant_id"]),
                str(grant_row["decision_id"]),
                str(grant_row["run_id"]),
                str(grant_row["call_id"]),
                str(grant_row["effect_id"]),
                str(grant_row["tool_name"]),
                str(grant_row["args_hash"]),
                str(grant_row["capability_hash"]),
                str(grant_row["scope_hash"]),
                decision.request.nonce,
            )
            if expected != actual or request.capability_hash != run.context.capability_hash:
                raise GrantConsumeConflict(
                    "grant_binding_mismatch",
                    "grant nonce/run/effect/capability/scope fence differs",
                )
            if int(grant_row["grant_version"]) != request.expected_version:
                raise GrantConsumeConflict(
                    "stale_grant_version",
                    f"expected grant version {request.expected_version}, "
                    f"found {grant_row['grant_version']}",
                )
            if run.status is RunStatus.CANCEL_REQUESTED or run.status in TERMINAL_RUN_STATUSES:
                raise GrantConsumeConflict(
                    "run_not_executable", "cancelled or terminal run rejects authorization"
                )
            if decision.status is not DecisionStatus.ALLOWED:
                raise GrantConsumeConflict(
                    "grant_not_authorized", "grant decision is not allowed"
                )
            if str(grant_row["status"]) != "issued":
                raise GrantConsumeConflict(
                    "grant_already_consumed",
                    "duplicate, expired, or revoked authorization was rejected",
                )
            if float(grant_row["expires_at"]) <= now:
                cursor = await db.execute(
                    """UPDATE execution_grants SET status='expired',
                    grant_version=grant_version+1 WHERE grant_id=?
                    AND grant_version=? AND status='issued'""",
                    (request.grant_id, request.expected_version),
                )
                if cursor.rowcount != 1:
                    raise GrantConsumeConflict(
                        "stale_grant_version", "grant changed before expiry CAS"
                    )
                await db.commit()
                raise GrantConsumeConflict(
                    "grant_expired", "expired authorization was rejected"
                )
            cursor = await db.execute(
                """UPDATE execution_grants SET status='consumed',
                grant_version=grant_version+1,consumed_at=? WHERE grant_id=?
                AND grant_version=? AND status='issued'""",
                (now, request.grant_id, request.expected_version),
            )
            if cursor.rowcount != 1:
                raise GrantConsumeConflict(
                    "grant_consume_conflict", "grant changed before consume CAS"
                )
            self._fault("grant_consume_before_commit")
            consumed = await (
                await db.execute(
                    "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
                )
            ).fetchone()
            assert consumed is not None
            await db.commit()
            return self._row_to_authorization(consumed)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    @staticmethod
    def _terminal_outcome(status: RunStatus) -> OutcomeStatus:
        return {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]

    async def finalize(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult:
        terminal_status = RunStatus(terminal_status)
        if terminal_status not in TERMINAL_RUN_STATUSES:
            raise TerminalConflict(
                "non_terminal_finalize", "finalize requires a terminal RunStatus"
            )
        if event.status != self._terminal_outcome(terminal_status):
            raise TerminalConflict(
                "terminal_outcome_mismatch", "terminal event outcome differs from run status"
            )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            if event.driver_kind != str(run["driver_kind"]):
                raise RunIdentityConflict(
                    "driver_event_conflict", "terminal event driver differs from run owner"
                )
            event_id = stable_event_id(run_id, event.event_key)
            if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
                existing = await (
                    await db.execute(
                        "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                    )
                ).fetchone()
                if (
                    str(run["status"]) != terminal_status.value
                    or str(run["terminal_event_id"] or "") != event_id
                    or existing is None
                ):
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    )
                self._assert_event_matches(existing, run, event)
                await self._assert_delivery_set_tx(db, event_id, deliveries)
                hydrated = dict(existing)
                hydrated["root_run_id"] = run["root_run_id"]
                hydrated["session_id"] = run["session_id"]
                await db.commit()
                return FinalizeRunResult(
                    record=self._row_to_record(run),
                    event=self._row_to_event(hydrated),
                    idempotent=True,
                )
            if int(run["version"]) != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {run['version']}",
                )
            now = float(self._clock())
            durable_seq = int(run["durable_seq"]) + 1
            payload = self._event_json(event)
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event.schema_version,
                    event.event_key,
                    run_id,
                    durable_seq,
                    event.kind,
                    event.status.value,
                    event.driver_kind,
                    payload["correlation_json"],
                    payload["payload_json"],
                    payload["error_json"],
                    payload["artifact_refs_json"],
                    now,
                ),
            )
            await self._insert_deliveries_tx(
                db,
                run_id=run_id,
                event_id=event_id,
                deliveries=deliveries,
                now=now,
            )
            self._fault("finalize_after_outbox")
            cursor = await db.execute(
                """UPDATE execution_runs SET status=?,terminal_event_id=?,durable_seq=?,
                version=version+1,updated_at=?,ended_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                  AND status NOT IN ('completed','failed','cancelled')""",
                (
                    terminal_status.value,
                    event_id,
                    durable_seq,
                    now,
                    now,
                    run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise TerminalConflict(
                    "terminal_conflict", "another terminal intent already won"
                )
            workflow = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if workflow is not None and str(workflow["status"]) not in {
                "completed",
                "failed",
                "cancelled",
            }:
                await db.execute(
                    """UPDATE workflow_runs SET status=?,lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,
                    updated_at=?,ended_at=COALESCE(ended_at,?) WHERE run_id=?""",
                    (terminal_status.value, now, now, run_id),
                )
            self._fault("finalize_before_commit")
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            stored = await (
                await db.execute(
                    "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                )
            ).fetchone()
            assert updated is not None and stored is not None
            await db.commit()
            hydrated = dict(stored)
            hydrated["root_run_id"] = updated["root_run_id"]
            hydrated["session_id"] = updated["session_id"]
            return FinalizeRunResult(
                record=self._row_to_record(updated),
                event=self._row_to_event(hydrated),
                idempotent=False,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def request_cancel(
        self,
        run_id: str,
        *,
        expected_version: int,
        reason: str,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> RunRecord:
        reason = str(reason).strip()
        if not reason or event.status != OutcomeStatus.CANCEL_REQUESTED:
            raise TerminalConflict(
                "invalid_cancel_request", "cancel requires a reason and cancel_requested event"
            )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            if str(run["status"]) == RunStatus.CANCEL_REQUESTED.value:
                existing = await (
                    await db.execute(
                        """SELECT * FROM execution_events
                        WHERE run_id=? AND event_key=?""",
                        (run_id, event.event_key),
                    )
                ).fetchone()
                if (
                    existing is None
                    or str(run["cancel_reason"] or "") != reason
                ):
                    raise IdempotencyConflict(
                        "cancel_intent_conflict",
                        "another cancel intent already owns the run",
                    )
                self._assert_event_matches(existing, run, event)
                await self._assert_delivery_set_tx(
                    db, str(existing["event_id"]), deliveries
                )
                await db.commit()
                return self._row_to_record(run)
            stored, updated, idempotent = await self._append_event_tx(
                db,
                run,
                expected_version=expected_version,
                event=event,
                deliveries=deliveries,
            )
            del stored
            if idempotent:
                if (
                    str(updated["status"]) != RunStatus.CANCEL_REQUESTED.value
                    or str(updated["cancel_reason"] or "") != reason
                ):
                    raise IdempotencyConflict(
                        "cancel_intent_conflict",
                        "cancel event exists without the same coarse cancel intent",
                    )
            else:
                cursor = await db.execute(
                    """UPDATE execution_runs SET status='cancel_requested',cancel_reason=?,
                    updated_at=? WHERE run_id=? AND version=?
                    AND terminal_event_id IS NULL""",
                    (reason, self._clock(), run_id, int(updated["version"])),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_run_version", "run changed before cancel commit"
                    )
                await db.execute(
                    """UPDATE workflow_runs SET status='cancel_requested',cancel_reason=?,
                    lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                    lease_epoch=lease_epoch+1,run_version=run_version+1,updated_at=?
                    WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')""",
                    (reason, self._clock(), run_id),
                )
            self._fault("cancel_before_commit")
            result = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            assert result is not None
            await db.commit()
            return self._row_to_record(result)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_child_command(
        self, intent: ChildCommandIntent
    ) -> ChildCommandRecord:
        """Commit the complete delegate intent before a child row can exist."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            parent = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (intent.parent_run_id,),
                )
            ).fetchone()
            if parent is None:
                raise RunNotFound(
                    "parent_not_found", "delegate parent must already be durable"
                )
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_child_commands
                    WHERE operation_id=? OR (parent_run_id=? AND command_id=?)
                    OR child_run_id=? ORDER BY operation_id=? DESC LIMIT 1""",
                    (
                        intent.operation_id,
                        intent.parent_run_id,
                        intent.command_id,
                        intent.child_run_id,
                        intent.operation_id,
                    ),
                )
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_id"]) != intent.operation_id
                    or str(existing["intent_fingerprint"]) != intent.intent_fingerprint
                ):
                    raise IdempotencyConflict(
                        "child_operation_conflict",
                        "operation, parent command, or child id names another intent",
                    )
                await db.commit()
                return self._row_to_child_command(existing)
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_child_commands(
                operation_id,schema_version,parent_run_id,command_id,child_run_id,
                profile_key,join_policy,capability_snapshot_ref,
                capability_subset_json,child_request_json,child_spec_json,
                intent_fingerprint,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                (
                    intent.operation_id,
                    intent.schema_version,
                    intent.parent_run_id,
                    intent.command_id,
                    intent.child_run_id,
                    intent.child_spec.profile_key,
                    intent.attachment_policy.value,
                    intent.capability_snapshot_ref,
                    canonical_json(list(intent.capability_subset)),
                    canonical_json(thaw_json(intent.child_request)),
                    canonical_json(intent.child_spec.to_dict()),
                    intent.intent_fingerprint,
                    now,
                    now,
                ),
            )
            self._fault("child_command_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (intent.operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_child_command(self, operation_id: str) -> ChildCommandRecord | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            return self._row_to_child_command(row) if row is not None else None
        finally:
            await db.close()

    async def lease_child_commands(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: float,
    ) -> tuple[ChildCommandRecord, ...]:
        if not owner or limit <= 0 or lease_seconds <= 0:
            raise ValueError("owner, positive limit and positive lease_seconds are required")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            now = float(self._clock())
            rows = await (
                await db.execute(
                    """SELECT operation_id FROM execution_child_commands
                    WHERE (
                        status='pending'
                        OR (
                            status IN ('leased','scheduled')
                            AND schedule_lease_expires_at<=?
                        )
                    ) AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    ORDER BY created_at,operation_id LIMIT ?""",
                    (now, now, limit),
                )
            ).fetchall()
            leased: list[ChildCommandRecord] = []
            for selected in rows:
                operation_id = str(selected["operation_id"])
                cursor = await db.execute(
                    """UPDATE execution_child_commands
                    SET status='leased',schedule_lease_owner=?,
                        schedule_lease_epoch=schedule_lease_epoch+1,
                        schedule_lease_expires_at=?,attempts=attempts+1,updated_at=?
                    WHERE operation_id=? AND (
                        status='pending'
                        OR (status IN ('leased','scheduled') AND schedule_lease_expires_at<=?)
                    )""",
                    (owner, now + lease_seconds, now, operation_id, now),
                )
                if cursor.rowcount != 1:
                    continue
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_child_commands WHERE operation_id=?",
                        (operation_id,),
                    )
                ).fetchone()
                assert row is not None
                leased.append(self._row_to_child_command(row))
            self._fault("child_lease_before_commit")
            await db.commit()
            return tuple(leased)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    @staticmethod
    def _stable_child_link_id(operation_id: str) -> str:
        return hashlib.sha256(
            f"execution-child-link|{operation_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _stable_child_signal_id(operation_id: str, kind: str) -> str:
        return hashlib.sha256(
            f"execution-child-signal|{operation_id}|{kind}".encode("utf-8")
        ).hexdigest()

    async def schedule_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
    ) -> ChildCommandRecord:
        """Atomically create/link the child and mark its command scheduled."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            if (
                str(command["status"]) not in {"leased", "scheduled"}
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
            ):
                raise VersionConflict(
                    "stale_child_lease", "child command lease owner or epoch changed"
                )
            intent = self._row_to_child_command(command).intent
            child, _ = await self._insert_run_tx(db, intent.child_spec, version=0)
            self._fault("child_schedule_after_run")
            link_id = self._stable_child_link_id(operation_id)
            existing_link = await (
                await db.execute(
                    "SELECT * FROM execution_run_links WHERE link_id=?", (link_id,)
                )
            ).fetchone()
            expected_link = {
                "root_run_id": intent.child_spec.context.root_run_id,
                "parent_run_id": intent.parent_run_id,
                "child_run_id": intent.child_run_id,
                "attachment_policy": intent.attachment_policy.value,
                "link_kind": LinkKind.STRUCTURAL.value,
                "domain_kind": "",
                "domain_id": "",
            }
            if existing_link is None:
                await db.execute(
                    """INSERT INTO execution_run_links(
                    link_id,schema_version,root_run_id,parent_run_id,child_run_id,
                    attachment_policy,link_kind,domain_kind,domain_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        link_id,
                        1,
                        expected_link["root_run_id"],
                        expected_link["parent_run_id"],
                        expected_link["child_run_id"],
                        expected_link["attachment_policy"],
                        expected_link["link_kind"],
                        "",
                        "",
                        self._clock(),
                    ),
                )
                cursor = await db.execute(
                    """UPDATE execution_runs SET version=version+1,updated_at=?
                    WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                    (self._clock(), intent.child_run_id, int(child["version"])),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_run_version", "child changed before schedule link commit"
                    )
            elif any(
                str(existing_link[field_name]) != value
                for field_name, value in expected_link.items()
            ):
                raise IdempotencyConflict(
                    "child_link_conflict", "child link differs from committed command"
                )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_child_commands SET status='scheduled',updated_at=?
                WHERE operation_id=? AND schedule_lease_owner=?
                AND schedule_lease_epoch=? AND status IN ('leased','scheduled')""",
                (now, operation_id, lease_owner, lease_epoch),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before schedule commit"
                )
            self._fault("child_schedule_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def acknowledge_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
    ) -> ChildCommandRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            if str(command["status"]) == ChildCommandStatus.ACKED.value:
                await db.commit()
                return self._row_to_child_command(command)
            if (
                str(command["status"]) != ChildCommandStatus.SCHEDULED.value
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
            ):
                raise VersionConflict(
                    "stale_child_lease", "only the scheduling lease may ack the child"
                )
            now = float(self._clock())
            signal_id = self._stable_child_signal_id(operation_id, "accepted")
            await db.execute(
                """INSERT INTO execution_child_signal_inbox(
                signal_id,schema_version,operation_id,parent_run_id,command_id,
                child_run_id,kind,payload_json,created_at,updated_at
                ) VALUES(?,1,?,?,?,?, 'accepted',?, ?,?)
                ON CONFLICT(operation_id,kind) DO NOTHING""",
                (
                    signal_id,
                    operation_id,
                    str(command["parent_run_id"]),
                    str(command["command_id"]),
                    str(command["child_run_id"]),
                    canonical_json({"accepted": True}),
                    now,
                    now,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_child_commands
                SET status='acked',schedule_lease_owner=NULL,
                    schedule_lease_expires_at=NULL,ack_at=?,updated_at=?
                WHERE operation_id=? AND status='scheduled'
                AND schedule_lease_owner=? AND schedule_lease_epoch=?""",
                (now, now, operation_id, lease_owner, lease_epoch),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before ack commit"
                )
            self._fault("child_ack_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def record_child_terminal(
        self,
        operation_id: str,
        *,
        terminal_status: str,
        value: Any = None,
    ) -> ChildSignalRecord:
        if terminal_status not in {"completed", "failed", "cancelled"}:
            raise ValueError("child terminal status is invalid")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None or str(command["status"]) != ChildCommandStatus.ACKED.value:
                raise RunNotFound(
                    "acked_child_command_not_found",
                    "terminal signal requires an acknowledged child command",
                )
            child = await (
                await db.execute(
                    "SELECT status FROM execution_runs WHERE run_id=?",
                    (str(command["child_run_id"]),),
                )
            ).fetchone()
            if child is None or str(child["status"]) != terminal_status:
                raise RunIdentityConflict(
                    "child_not_terminal",
                    "terminal inbox status must match the authoritative child run",
                )
            signal_id = self._stable_child_signal_id(operation_id, "terminal")
            payload_json = canonical_json(
                {"status": terminal_status, "value": value}
            )
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_child_signal_inbox
                    WHERE operation_id=? AND kind='terminal'""",
                    (operation_id,),
                )
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise IdempotencyConflict(
                        "child_terminal_conflict",
                        "terminal signal replay differs from the first outcome",
                    )
                await db.commit()
                return self._row_to_child_signal(existing)
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_child_signal_inbox(
                signal_id,schema_version,operation_id,parent_run_id,command_id,
                child_run_id,kind,payload_json,created_at,updated_at
                ) VALUES(?,1,?,?,?,?, 'terminal',?,?,?)""",
                (
                    signal_id,
                    operation_id,
                    str(command["parent_run_id"]),
                    str(command["command_id"]),
                    str(command["child_run_id"]),
                    payload_json,
                    now,
                    now,
                ),
            )
            self._fault("child_terminal_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_signal(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def list_pending_child_signals(
        self, parent_run_id: str
    ) -> tuple[ChildSignalRecord, ...]:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_child_signal_inbox
                SET attempts=attempts+1,updated_at=?
                WHERE parent_run_id=? AND delivered_at IS NULL""",
                (now, parent_run_id),
            )
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_child_signal_inbox
                    WHERE parent_run_id=? AND delivered_at IS NULL
                    ORDER BY created_at,signal_id""",
                    (parent_run_id,),
                )
            ).fetchall()
            await db.commit()
            return tuple(self._row_to_child_signal(row) for row in rows)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def acknowledge_child_signal(self, signal_id: str) -> ChildSignalRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound("child_signal_not_found", "child signal does not exist")
            if row["delivered_at"] is None:
                now = float(self._clock())
                await db.execute(
                    """UPDATE execution_child_signal_inbox
                    SET delivered_at=?,updated_at=? WHERE signal_id=?
                    AND delivered_at IS NULL""",
                    (now, now, signal_id),
                )
                self._fault("child_signal_ack_before_commit")
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                        (signal_id,),
                    )
                ).fetchone()
                assert row is not None
            await db.commit()
            return self._row_to_child_signal(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def link(
        self,
        link: RunLinkSpec,
        *,
        expected_child_version: int,
    ) -> RunRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            parent = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (link.parent_run_id,)
                )
            ).fetchone()
            child = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (link.child_run_id,)
                )
            ).fetchone()
            if parent is None or child is None:
                raise RunNotFound(
                    "run_not_found", "both parent and child must exist before linking"
                )
            if (
                str(parent["session_id"]) != str(child["session_id"])
                or str(parent["root_run_id"]) != link.root_run_id
                or str(child["root_run_id"]) != link.root_run_id
            ):
                raise RunIdentityConflict(
                    "link_scope_conflict", "run link crosses a session or root boundary"
                )
            if link.link_kind is LinkKind.STRUCTURAL:
                cycle = await (
                    await db.execute(
                        """WITH RECURSIVE descendants(run_id) AS (
                            SELECT child_run_id FROM execution_run_links
                            WHERE parent_run_id=? AND link_kind='structural'
                            UNION
                            SELECT link.child_run_id FROM execution_run_links AS link
                            JOIN descendants ON link.parent_run_id=descendants.run_id
                            WHERE link.link_kind='structural'
                        ) SELECT 1 FROM descendants WHERE run_id=? LIMIT 1""",
                        (link.child_run_id, link.parent_run_id),
                    )
                ).fetchone()
                if cycle is not None:
                    raise ParentCycleError(
                        "parent_cycle", "run link would create a parent cycle"
                    )
                if str(child["parent_run_id"] or "") != link.parent_run_id:
                    raise RunIdentityConflict(
                        "parent_link_conflict",
                        "child RunContext names a different parent",
                    )
            existing = await (
                await db.execute(
                    "SELECT * FROM execution_run_links WHERE link_id=?", (link.link_id,)
                )
            ).fetchone()
            expected_link = {
                "root_run_id": link.root_run_id,
                "parent_run_id": link.parent_run_id,
                "child_run_id": link.child_run_id,
                "attachment_policy": link.attachment_policy.value,
                "link_kind": link.link_kind.value,
                "domain_kind": link.domain_kind or "",
                "domain_id": link.domain_id or "",
            }
            if existing is not None:
                if any(str(existing[key]) != value for key, value in expected_link.items()):
                    raise IdempotencyConflict(
                        "link_intent_conflict", "link_id already names another relation"
                    )
                await db.commit()
                return self._row_to_record(child)
            if int(child["version"]) != expected_child_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected child version {expected_child_version}, found {child['version']}",
                )
            await db.execute(
                """INSERT INTO execution_run_links(
                link_id,schema_version,root_run_id,parent_run_id,child_run_id,
                attachment_policy,link_kind,domain_kind,domain_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    link.link_id,
                    link.schema_version,
                    link.root_run_id,
                    link.parent_run_id,
                    link.child_run_id,
                    link.attachment_policy.value,
                    link.link_kind.value,
                    link.domain_kind or "",
                    link.domain_id or "",
                    self._clock(),
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_runs SET version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                (self._clock(), link.child_run_id, expected_child_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_run_version", "child changed before link commit"
                )
            self._fault("link_before_commit")
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (link.child_run_id,),
                )
            ).fetchone()
            assert updated is not None
            await db.commit()
            return self._row_to_record(updated)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    @staticmethod
    def _legacy_status(value: str) -> RunStatus:
        return {
            "created": RunStatus.CREATED,
            "running": RunStatus.RUNNING,
            "waiting": RunStatus.WAITING,
            "retryable": RunStatus.QUEUED,
            "cancel_requested": RunStatus.CANCEL_REQUESTED,
            "cancelling": RunStatus.CANCEL_REQUESTED,
            "blocked": RunStatus.WAITING,
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }.get(value, RunStatus.FAILED)

    async def _legacy_projection_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> LegacyRunProjection | None:
        row = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            return None
        root_id = run_id
        parent_id = str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
        current = parent_id
        seen = {run_id}
        while current is not None:
            if current in seen:
                raise ParentCycleError(
                    "legacy_parent_cycle", "legacy workflow lineage contains a cycle"
                )
            seen.add(current)
            ancestor = await (
                await db.execute(
                    "SELECT run_id,parent_run_id FROM workflow_runs WHERE run_id=?", (current,)
                )
            ).fetchone()
            if ancestor is None:
                break
            root_id = str(ancestor["run_id"])
            current = (
                str(ancestor["parent_run_id"])
                if ancestor["parent_run_id"] is not None
                else None
            )
        return LegacyRunProjection(
            run_id=run_id,
            session_id=str(row["session_id"]),
            root_run_id=root_id,
            parent_run_id=parent_id,
            status=self._legacy_status(str(row["status"])),
            driver_kind="workflow",
            profile_key=f"{row['workflow_name']}/{row['workflow_version']}",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        )

    def _authorize_view(
        self,
        ref: RunRef,
        actor: ActorContext,
        view: RunView,
    ) -> None:
        session_id = view.context.session_id if isinstance(view, RunRecord) else view.session_id
        root_run_id = view.context.root_run_id if isinstance(view, RunRecord) else view.root_run_id
        if ref.expected_session_id != session_id or actor.session_id != session_id:
            raise AuthorizationError(
                "actor_not_authorized", "actor does not own the run session"
            )
        if isinstance(view, LegacyRunProjection):
            if actor.internal:
                raise AuthorizationError(
                    "legacy_internal_authority_unavailable",
                    "legacy projections do not carry a verifiable internal authority",
                )
            return
        if actor.internal:
            if (
                actor.root_run_id != root_run_id
                or actor.capability_hash != view.context.capability_hash
                or actor.expires_at is None
                or actor.expires_at <= self._clock()
            ):
                raise AuthorizationError(
                    "actor_not_authorized", "internal authority scope is invalid or expired"
                )
            return
        if (
            actor.principal_id != view.context.principal_id
            or actor.auth_epoch != view.context.auth_epoch
        ):
            raise AuthorizationError(
                "actor_not_authorized", "actor principal or authentication epoch differs"
            )

    async def authorize(
        self,
        ref: RunRef,
        actor: ActorContext,
        action: ActorAction | str,
    ) -> RunView:
        ActorAction(action)
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,)
                )
            ).fetchone()
            view: RunView | None
            if row is not None:
                view = self._row_to_record(row)
            else:
                view = await self._legacy_projection_tx(db, ref.run_id)
            if view is None:
                raise RunNotFound("run_not_found", "execution run does not exist")
            self._authorize_view(ref, actor, view)
            return view
        finally:
            await db.close()

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView:
        return await self.authorize(ref, actor, ActorAction.OBSERVE)

    async def list_children(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunRecord, ...]:
        parent = await self.authorize(ref, actor, ActorAction.OBSERVE)
        if isinstance(parent, LegacyRunProjection):
            return ()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT run.* FROM execution_run_links AS link
                    JOIN execution_runs AS run ON run.run_id=link.child_run_id
                    WHERE link.parent_run_id=? AND link.link_kind='structural'
                    ORDER BY run.created_at,run.run_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            children = tuple(self._row_to_record(row) for row in rows)
            for child in children:
                self._authorize_view(
                    RunRef(child.run_id, ref.expected_session_id), actor, child
                )
            return children
        finally:
            await db.close()

    async def _insert_workflow_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: RunRecord,
        workflow: WorkflowRunSeed,
    ) -> None:
        snapshot = thaw_json(workflow.capability_snapshot)
        if fingerprint_json(snapshot) != workflow.capability_hash:
            raise RunIdentityConflict(
                "workflow_capability_conflict",
                "workflow capability hash does not match its immutable snapshot",
            )
        if workflow.trace_id != run.context.trace_id:
            raise RunIdentityConflict(
                "workflow_trace_conflict", "workflow and execution trace ids differ"
            )
        capability_json = canonical_json(snapshot)
        existing_capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (workflow.capability_hash,),
            )
        ).fetchone()
        if existing_capability is not None:
            if canonical_json(json.loads(str(existing_capability["snapshot_json"]))) != capability_json:
                raise IdempotencyConflict(
                    "workflow_capability_conflict",
                    "capability hash already names different workflow capability content",
                )
        else:
            await db.execute(
                """INSERT INTO workflow_capabilities(capability_hash,snapshot_json,created_at)
                VALUES(?,?,?)""",
                (workflow.capability_hash, capability_json, self._clock()),
            )
        existing_start = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE request_key=?",
                (workflow.request_key,),
            )
        ).fetchone()
        if existing_start is not None and str(existing_start["run_id"]) != run.run_id:
            raise IdempotencyConflict(
                "workflow_start_conflict", "workflow request key already names another run"
            )
        existing_run = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run.run_id,))
        ).fetchone()
        if existing_run is not None:
            expected = {
                "trace_id": workflow.trace_id,
                "thread_id": workflow.thread_id,
                "checkpoint_ns": workflow.checkpoint_ns,
                "session_id": run.context.session_id,
                "request_id": run.context.request_id,
                "turn_id": run.context.turn_id,
                "workflow_name": workflow.workflow_name,
                "workflow_version": workflow.workflow_version,
                "manifest_hash": workflow.manifest_hash,
                "implementation_hash": workflow.implementation_hash,
                "capability_hash": workflow.capability_hash,
                "state_schema_version": workflow.state_schema_version,
            }
            for field_name, value in expected.items():
                stored = existing_run[field_name]
                stored = int(stored) if field_name == "state_schema_version" else str(stored or "")
                if stored != value:
                    raise IdempotencyConflict(
                        "workflow_start_conflict",
                        f"workflow run differs at {field_name}",
                    )
        else:
            now = float(self._clock())
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,parent_run_id,source_checkpoint_id,
                session_id,request_id,turn_id,workflow_name,workflow_version,manifest_hash,
                implementation_hash,capability_hash,state_schema_version,status,
                active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'created','[]',?,?)""",
                (
                    run.run_id,
                    workflow.trace_id,
                    workflow.thread_id,
                    workflow.checkpoint_ns,
                    run.context.parent_run_id,
                    workflow.source_checkpoint_id,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.workflow_version,
                    workflow.manifest_hash,
                    workflow.implementation_hash,
                    workflow.capability_hash,
                    workflow.state_schema_version,
                    now,
                    now,
                ),
            )
        if existing_start is None:
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,
                capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    workflow.request_key,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.capability_hash,
                    run.run_id,
                    self._clock(),
                ),
            )
        for ref in workflow.session_refs:
            existing_ref = await (
                await db.execute(
                    """SELECT session_id,session_epoch FROM workflow_session_refs
                    WHERE run_id=? AND session_kind=?""",
                    (run.run_id, ref.session_kind),
                )
            ).fetchone()
            if existing_ref is not None:
                if (
                    str(existing_ref["session_id"]) != ref.session_id
                    or int(existing_ref["session_epoch"]) != ref.session_epoch
                ):
                    raise IdempotencyConflict(
                        "workflow_session_ref_conflict",
                        "workflow session kind already names another session fence",
                    )
                continue
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at
                ) VALUES(?,?,?,?,NULL)""",
                (run.run_id, ref.session_kind, ref.session_id, ref.session_epoch),
            )

    async def start_workflow(
        self,
        spec: RunCreate,
        workflow: WorkflowRunSeed,
        *,
        accepted_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "workflow_must_be_durable", "workflow runs are durable from creation"
            )
        if accepted_event is None and deliveries:
            raise IdempotencyConflict(
                "delivery_without_event", "workflow deliveries require an accepted event"
            )
        if accepted_event is not None and accepted_event.status != OutcomeStatus.ACCEPTED:
            raise IdempotencyConflict(
                "invalid_accepted_event", "workflow start event must be accepted"
            )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row, created = await self._insert_run_tx(db, spec, version=0)
            record = self._row_to_record(row)
            self._fault("start_workflow_after_execution")
            await self._insert_workflow_tx(db, run=record, workflow=workflow)
            self._fault("start_workflow_after_workflow")
            if accepted_event is not None:
                _, updated, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=record.version,
                    event=accepted_event,
                    deliveries=deliveries,
                )
                record = self._row_to_record(updated)
            self._fault("start_workflow_before_commit")
            await db.commit()
            return CreateRunResult(record=record, created=created)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()


__all__ = ["SqliteExecutionUnitOfWork"]
