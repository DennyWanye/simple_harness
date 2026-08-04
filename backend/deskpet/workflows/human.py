"""Durable, framework-neutral human decision state for workflows."""

from __future__ import annotations

import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

import aiosqlite

from .contracts import JsonValue, canonical_json, validate_json_value
from .errors import InvalidStatePatch, WorkflowContractError
from .store.run_store import RunFence, StaleRunFence
from .store.schema import initialize_workflow_db


ResponseValidator: TypeAlias = Callable[[JsonValue], JsonValue]


class DecisionStatus(StrEnum):
    PREPARED = "prepared"
    OPEN = "open"
    RESOLVED = "resolved"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class GrantStatus(StrEnum):
    PREPARED = "prepared"
    OPEN = "open"
    CLAIMED = "claimed"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class HumanDecisionError(WorkflowContractError):
    """A stable, machine-readable human-decision contract failure."""


@dataclass(frozen=True, slots=True)
class HumanDecision:
    decision_id: str
    run_id: str
    node_execution_id: str | None
    interrupt_id: str | None
    checkpoint_ns: str
    checkpoint_id: str
    kind: str
    status: DecisionStatus
    prompt: JsonValue
    response: JsonValue | None
    nonce: str
    version: int
    expires_at: float | None
    created_at: float
    resolved_at: float | None
    consumed_at: float | None
    consumed_checkpoint_id: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "HumanDecision":
        return cls(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            node_execution_id=(
                str(row["node_execution_id"])
                if row["node_execution_id"] is not None
                else None
            ),
            interrupt_id=(
                str(row["interrupt_id"])
                if row["interrupt_id"] is not None
                else None
            ),
            checkpoint_ns=str(row["checkpoint_ns"]),
            checkpoint_id=str(row["checkpoint_id"]),
            kind=str(row["kind"]),
            status=DecisionStatus(str(row["status"])),
            prompt=json.loads(str(row["prompt_json"])),
            response=(
                json.loads(str(row["response_json"]))
                if row["response_json"] is not None
                else None
            ),
            nonce=str(row["nonce"]),
            version=int(row["decision_version"]),
            expires_at=(
                float(row["expires_at"])
                if row["expires_at"] is not None
                else None
            ),
            created_at=float(row["created_at"]),
            resolved_at=(
                float(row["resolved_at"])
                if row["resolved_at"] is not None
                else None
            ),
            consumed_at=(
                float(row["consumed_at"])
                if row["consumed_at"] is not None
                else None
            ),
            consumed_checkpoint_id=(
                str(row["consumed_checkpoint_id"])
                if row["consumed_checkpoint_id"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class HumanGrant:
    grant_id: str
    decision_id: str
    effect_id: str | None
    scope: JsonValue
    status: GrantStatus
    created_at: float
    consumed_at: float | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "HumanGrant":
        return cls(
            grant_id=str(row["grant_id"]),
            decision_id=str(row["decision_id"]),
            effect_id=(str(row["effect_id"]) if row["effect_id"] is not None else None),
            scope=json.loads(str(row["scope_json"])),
            status=GrantStatus(str(row["status"])),
            created_at=float(row["created_at"]),
            consumed_at=(
                float(row["consumed_at"])
                if row["consumed_at"] is not None
                else None
            ),
        )


class HumanDecisionStore:
    """Own the durable prepared -> open -> resolved decision protocol."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        validators: Mapping[str, ResponseValidator] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._validators = dict(validators or {})

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    async def _assert_fence(db: aiosqlite.Connection, fence: RunFence) -> None:
        row = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=?
                AND lease_epoch=? AND run_version=? AND status='running'""",
                (
                    fence.run_id,
                    fence.owner,
                    fence.lease_epoch,
                    fence.run_version,
                ),
            )
        ).fetchone()
        if row is None:
            raise StaleRunFence(f"stale human-decision writer: {fence.run_id}")

    @staticmethod
    def _decision_error(code: str, message: str, decision_id: str) -> HumanDecisionError:
        return HumanDecisionError(code, message, details={"decision_id": decision_id})

    @staticmethod
    def _same_preparation(
        row: aiosqlite.Row,
        *,
        fence: RunFence,
        node_execution_id: str | None,
        checkpoint_ns: str,
        checkpoint_id: str,
        kind: str,
        prompt_json: str,
        expires_at: float | None,
    ) -> bool:
        return (
            row["run_id"] == fence.run_id
            and row["node_execution_id"] == node_execution_id
            and row["checkpoint_ns"] == checkpoint_ns
            and row["checkpoint_id"] == checkpoint_id
            and row["kind"] == kind
            and row["prompt_json"] == prompt_json
            and row["expires_at"] == expires_at
        )

    @staticmethod
    def graph_decision_id(
        *, run_id: str, checkpoint_ns: str, checkpoint_id: str, interrupt_id: str
    ) -> str:
        """Return the stable decision identity for one persisted graph interrupt."""

        identity = "\x1f".join(
            ("deskpet-graph-interrupt-v1", run_id, checkpoint_ns, checkpoint_id, interrupt_id)
        )
        return uuid.uuid5(uuid.NAMESPACE_URL, identity).hex

    @classmethod
    async def open_graph_interrupt_in_transaction(
        cls,
        db: aiosqlite.Connection,
        *,
        fence: RunFence,
        interrupt_id: str,
        prompt: JsonValue,
        checkpoint_id: str,
        checkpoint_ns: str = "",
        task_id: str | None = None,
        kind: str = "human_decision",
        expires_at: float | None = None,
        now: float | None = None,
    ) -> HumanDecision:
        """Create/open an interrupt and park its run inside the saver's transaction."""

        if not interrupt_id:
            raise HumanDecisionError("invalid_interrupt_id", "Interrupt id is required")
        if not checkpoint_id:
            raise HumanDecisionError(
                "invalid_checkpoint", "A decision requires a checkpoint id"
            )
        normalized_kind = str(kind).strip() or "human_decision"
        resolved_now = time.time() if now is None else float(now)
        if expires_at is not None and expires_at <= resolved_now:
            raise HumanDecisionError(
                "invalid_decision_expiry", "Decision expiry must be in the future"
            )
        try:
            prompt_json = canonical_json(prompt)
        except InvalidStatePatch as exc:
            raise HumanDecisionError(
                "invalid_decision_prompt", str(exc), details=exc.details
            ) from exc

        decision_id = cls.graph_decision_id(
            run_id=fence.run_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            interrupt_id=interrupt_id,
        )
        await cls._assert_fence(db, fence)
        existing = await (
            await db.execute(
                "SELECT * FROM workflow_decisions WHERE decision_id=?",
                (decision_id,),
            )
        ).fetchone()
        if existing is not None:
            if (
                existing["run_id"] == fence.run_id
                and existing["interrupt_id"] == interrupt_id
                and existing["checkpoint_ns"] == checkpoint_ns
                and existing["checkpoint_id"] == checkpoint_id
                and existing["kind"] == normalized_kind
                and existing["prompt_json"] == prompt_json
                and existing["status"] == DecisionStatus.OPEN.value
            ):
                return HumanDecision.from_row(existing)
            raise cls._decision_error(
                "decision_identity_conflict",
                "Graph interrupt identity is already bound to different input",
                decision_id,
            )

        other = await (
            await db.execute(
                """SELECT decision_id FROM workflow_decisions
                WHERE run_id=? AND status='open' LIMIT 1""",
                (fence.run_id,),
            )
        ).fetchone()
        if other is not None:
            raise cls._decision_error(
                "interrupt_barrier_violation",
                "Run already has an open human decision",
                decision_id,
            )

        node_execution_id: str | None = None
        if task_id:
            node = await (
                await db.execute(
                    """SELECT node_execution_id FROM workflow_nodes
                    WHERE run_id=? AND task_id=? LIMIT 1""",
                    (fence.run_id, task_id),
                )
            ).fetchone()
            if node is not None:
                node_execution_id = str(node["node_execution_id"])

        await db.execute(
            """INSERT INTO workflow_decisions(
                decision_id,run_id,node_execution_id,interrupt_id,checkpoint_ns,
                checkpoint_id,kind,status,prompt_json,response_json,nonce,
                decision_version,expires_at,created_at,resolved_at
            ) VALUES(?,?,?,?,?,?,?,'open',?,NULL,?,1,?,?,NULL)""",
            (
                decision_id,
                fence.run_id,
                node_execution_id,
                interrupt_id,
                checkpoint_ns,
                checkpoint_id,
                normalized_kind,
                prompt_json,
                secrets.token_urlsafe(24),
                expires_at,
                resolved_now,
            ),
        )
        run_cursor = await db.execute(
            """UPDATE workflow_runs SET status='waiting',head_checkpoint_ns=?,
            head_checkpoint_id=?,lease_owner=NULL,lease_expires_at=NULL,
            heartbeat_at=NULL,run_version=run_version+1,updated_at=?
            WHERE run_id=? AND lease_owner=? AND lease_epoch=?
            AND run_version=? AND status='running'""",
            (
                checkpoint_ns,
                checkpoint_id,
                resolved_now,
                fence.run_id,
                fence.owner,
                fence.lease_epoch,
                fence.run_version,
            ),
        )
        if run_cursor.rowcount != 1:
            raise StaleRunFence(f"stale graph-interrupt opener: {fence.run_id}")

        if node_execution_id is not None:
            await db.execute(
                """UPDATE workflow_nodes SET latest_status='waiting',updated_at=?
                WHERE node_execution_id=? AND run_id=?""",
                (resolved_now, node_execution_id, fence.run_id),
            )
            await db.execute(
                """UPDATE workflow_node_attempts SET status='waiting'
                WHERE node_execution_id=? AND retry_attempt=(
                    SELECT latest_attempt FROM workflow_nodes WHERE node_execution_id=?)""",
                (node_execution_id, node_execution_id),
            )

        opened = await (
            await db.execute(
                "SELECT * FROM workflow_decisions WHERE decision_id=?",
                (decision_id,),
            )
        ).fetchone()
        assert opened is not None
        return HumanDecision.from_row(opened)

    async def prepare_decision(
        self,
        *,
        fence: RunFence,
        kind: str,
        prompt: JsonValue,
        checkpoint_id: str,
        checkpoint_ns: str = "",
        node_execution_id: str | None = None,
        expires_at: float | None = None,
        decision_id: str | None = None,
        nonce: str | None = None,
    ) -> HumanDecision:
        """Persist a hidden decision before the node calls ``interrupt``.

        A caller-supplied stable ``decision_id`` makes node re-entry idempotent.
        The interrupt id remains NULL until :meth:`open_decision`.
        """

        if not kind:
            raise HumanDecisionError("invalid_decision_kind", "Decision kind is required")
        if not checkpoint_id:
            raise HumanDecisionError(
                "invalid_checkpoint", "A decision requires a checkpoint id"
            )
        now = self._clock()
        if expires_at is not None and expires_at <= now:
            raise HumanDecisionError(
                "invalid_decision_expiry", "Decision expiry must be in the future"
            )
        try:
            prompt_json = canonical_json(prompt)
        except InvalidStatePatch as exc:
            raise HumanDecisionError(
                "invalid_decision_prompt", str(exc), details=exc.details
            ) from exc

        stable_id = decision_id or uuid.uuid4().hex
        stable_nonce = nonce or secrets.token_urlsafe(24)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (stable_id,),
                )
            ).fetchone()
            if existing is not None:
                if not self._same_preparation(
                    existing,
                    fence=fence,
                    node_execution_id=node_execution_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    kind=kind,
                    prompt_json=prompt_json,
                    expires_at=expires_at,
                ):
                    raise self._decision_error(
                        "decision_identity_conflict",
                        "Decision id is already bound to different input",
                        stable_id,
                    )
                await db.commit()
                return HumanDecision.from_row(existing)

            await self._assert_fence(db, fence)
            await db.execute(
                """INSERT INTO workflow_decisions(
                    decision_id,run_id,node_execution_id,interrupt_id,checkpoint_ns,
                    checkpoint_id,kind,status,prompt_json,response_json,nonce,
                    decision_version,expires_at,created_at,resolved_at
                ) VALUES(?,?,?,NULL,?,?,?,'prepared',?,NULL,?,0,?,?,NULL)""",
                (
                    stable_id,
                    fence.run_id,
                    node_execution_id,
                    checkpoint_ns,
                    checkpoint_id,
                    kind,
                    prompt_json,
                    stable_nonce,
                    expires_at,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (stable_id,),
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return HumanDecision.from_row(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def open_decision(
        self,
        decision_id: str,
        *,
        fence: RunFence,
        interrupt_id: str,
        checkpoint_id: str | None = None,
        checkpoint_ns: str | None = None,
        task_id: str | None = None,
    ) -> HumanDecision:
        """Atomically expose an interrupt, park its run, and release the lease."""

        if not interrupt_id:
            raise self._decision_error(
                "invalid_interrupt_id", "Interrupt id is required", decision_id
            )
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            if row is None:
                raise self._decision_error(
                    "decision_not_found", "Decision does not exist", decision_id
                )

            resolved_checkpoint_id = checkpoint_id or str(row["checkpoint_id"])
            resolved_checkpoint_ns = (
                str(row["checkpoint_ns"])
                if checkpoint_ns is None
                else checkpoint_ns
            )
            if row["expires_at"] is not None and float(row["expires_at"]) <= now:
                await self._expire_rows(db, [row], now)
                await db.commit()
                raise self._decision_error(
                    "decision_expired", "Decision has expired", decision_id
                )
            if row["status"] == DecisionStatus.OPEN.value:
                if (
                    row["interrupt_id"] == interrupt_id
                    and row["checkpoint_id"] == resolved_checkpoint_id
                    and row["checkpoint_ns"] == resolved_checkpoint_ns
                ):
                    await db.commit()
                    return HumanDecision.from_row(row)
                raise self._decision_error(
                    "decision_identity_conflict",
                    "Open decision is bound to another interrupt or checkpoint",
                    decision_id,
                )
            if row["status"] != DecisionStatus.PREPARED.value:
                raise self._decision_error(
                    "decision_not_prepared",
                    f"Decision cannot be opened from status {row['status']}",
                    decision_id,
                )
            if row["run_id"] != fence.run_id:
                raise self._decision_error(
                    "decision_run_mismatch", "Decision belongs to another run", decision_id
                )
            other = await (
                await db.execute(
                    """SELECT decision_id FROM workflow_decisions
                    WHERE run_id=? AND status='open' AND decision_id<>? LIMIT 1""",
                    (fence.run_id, decision_id),
                )
            ).fetchone()
            if other is not None:
                raise self._decision_error(
                    "interrupt_barrier_violation",
                    "Run already has an open human decision",
                    decision_id,
                )

            await self._assert_fence(db, fence)
            try:
                cursor = await db.execute(
                    """UPDATE workflow_decisions SET interrupt_id=?,checkpoint_ns=?,
                    checkpoint_id=?,status='open',decision_version=decision_version+1
                    WHERE decision_id=? AND status='prepared'""",
                    (
                        interrupt_id,
                        resolved_checkpoint_ns,
                        resolved_checkpoint_id,
                        decision_id,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise self._decision_error(
                    "interrupt_identity_conflict",
                    "Interrupt id is already bound within this run",
                    decision_id,
                ) from exc
            if cursor.rowcount != 1:
                raise self._decision_error(
                    "stale_decision", "Decision changed while it was opened", decision_id
                )

            run_cursor = await db.execute(
                """UPDATE workflow_runs SET status='waiting',head_checkpoint_ns=?,
                head_checkpoint_id=?,lease_owner=NULL,lease_expires_at=NULL,
                heartbeat_at=NULL,run_version=run_version+1,updated_at=?
                WHERE run_id=? AND lease_owner=? AND lease_epoch=?
                AND run_version=? AND status='running'""",
                (
                    resolved_checkpoint_ns,
                    resolved_checkpoint_id,
                    now,
                    fence.run_id,
                    fence.owner,
                    fence.lease_epoch,
                    fence.run_version,
                ),
            )
            if run_cursor.rowcount != 1:
                raise StaleRunFence(f"stale human-decision opener: {fence.run_id}")

            node_execution_id = row["node_execution_id"]
            if node_execution_id is not None:
                node = await (
                    await db.execute(
                        "SELECT task_id FROM workflow_nodes WHERE node_execution_id=? AND run_id=?",
                        (node_execution_id, fence.run_id),
                    )
                ).fetchone()
                if (
                    node is not None
                    and task_id is not None
                    and node["task_id"] not in (None, task_id)
                ):
                    raise self._decision_error(
                        "decision_task_mismatch",
                        "Interrupt task id does not match the persisted node",
                        decision_id,
                    )
                await db.execute(
                    """UPDATE workflow_nodes SET task_id=COALESCE(task_id,?),
                    latest_status='waiting',updated_at=?
                    WHERE node_execution_id=? AND run_id=?""",
                    (task_id, now, node_execution_id, fence.run_id),
                )
                await db.execute(
                    """UPDATE workflow_node_attempts SET task_id=COALESCE(task_id,?),
                    status='waiting' WHERE node_execution_id=?
                    AND retry_attempt=(SELECT latest_attempt FROM workflow_nodes
                        WHERE node_execution_id=?)""",
                    (task_id, node_execution_id, node_execution_id),
                )

            opened = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            await db.commit()
            assert opened is not None
            return HumanDecision.from_row(opened)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_decision(self, decision_id: str) -> HumanDecision | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            return HumanDecision.from_row(row) if row is not None else None
        finally:
            await db.close()

    async def list_open_decisions(
        self, *, run_id: str | None = None, limit: int = 100
    ) -> list[HumanDecision]:
        """Return only visible, currently actionable decisions."""

        if limit < 1:
            return []
        now = self._clock()
        clauses = ["status='open'", "(expires_at IS NULL OR expires_at>?)"]
        params: list[Any] = [now]
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        params.append(limit)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_decisions
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at,decision_id LIMIT ?""",
                    params,
                )
            ).fetchall()
            return [HumanDecision.from_row(row) for row in rows]
        finally:
            await db.close()

    list_open = list_open_decisions

    def _validate_response(
        self,
        kind: str,
        response: JsonValue,
        validator: ResponseValidator | None = None,
    ) -> JsonValue:
        try:
            validate_json_value(response)
            selected = validator or self._validators.get(kind)
            normalized = selected(response) if selected is not None else response
            validate_json_value(normalized)
            # Round-trip to detach caller-owned mutable containers.
            return json.loads(canonical_json(normalized))
        except HumanDecisionError:
            raise
        except (InvalidStatePatch, TypeError, ValueError) as exc:
            details = exc.details if isinstance(exc, InvalidStatePatch) else None
            raise HumanDecisionError(
                "invalid_decision_response", str(exc), details=details
            ) from exc

    async def resolve_decision(
        self,
        decision_id: str,
        *,
        nonce: str,
        response: JsonValue,
        expected_version: int | None = None,
        version: int | None = None,
        validator: ResponseValidator | None = None,
    ) -> HumanDecision:
        """Validate and CAS one response, then make the waiting run claimable."""

        if expected_version is None:
            expected_version = version
        elif version is not None and version != expected_version:
            raise self._decision_error(
                "stale_decision", "Conflicting expected decision versions", decision_id
            )
        if expected_version is None:
            raise self._decision_error(
                "missing_decision_version", "Expected decision version is required", decision_id
            )

        current = await self.get_decision(decision_id)
        if current is None:
            raise self._decision_error(
                "decision_not_found", "Decision does not exist", decision_id
            )
        normalized = self._validate_response(current.kind, response, validator)
        response_json = canonical_json(normalized)
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            if row is None:
                raise self._decision_error(
                    "decision_not_found", "Decision does not exist", decision_id
                )
            if row["expires_at"] is not None and float(row["expires_at"]) <= now:
                await self._expire_rows(db, [row], now)
                await db.commit()
                raise self._decision_error(
                    "decision_expired", "Decision has expired", decision_id
                )

            status = str(row["status"])
            if status != DecisionStatus.OPEN.value:
                code = {
                    DecisionStatus.RESOLVED.value: "decision_already_resolved",
                    DecisionStatus.CONSUMED.value: "decision_already_consumed",
                    DecisionStatus.EXPIRED.value: "decision_expired",
                    DecisionStatus.CANCELLED.value: "decision_cancelled",
                }.get(status, "decision_not_open")
                raise self._decision_error(
                    code, f"Decision cannot be resolved from status {status}", decision_id
                )
            if (
                int(row["decision_version"]) != expected_version
                or not hmac.compare_digest(str(row["nonce"]), nonce)
            ):
                raise self._decision_error(
                    "stale_decision", "Decision nonce or version is stale", decision_id
                )

            cursor = await db.execute(
                """UPDATE workflow_decisions SET status='resolved',response_json=?,
                resolved_at=?,decision_version=decision_version+1
                WHERE decision_id=? AND status='open' AND nonce=?
                AND decision_version=? AND (expires_at IS NULL OR expires_at>?)""",
                (response_json, now, decision_id, nonce, expected_version, now),
            )
            if cursor.rowcount != 1:
                raise self._decision_error(
                    "stale_decision", "Decision changed while it was resolved", decision_id
                )
            run_cursor = await db.execute(
                """UPDATE workflow_runs SET status='retryable',run_version=run_version+1,
                recovery_action=NULL,updated_at=? WHERE run_id=? AND status='waiting'
                AND lease_owner IS NULL""",
                (now, row["run_id"]),
            )
            if run_cursor.rowcount != 1:
                raise self._decision_error(
                    "invalid_decision_run_state",
                    "Decision run is not durably waiting",
                    decision_id,
                )
            resolved = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            await db.commit()
            assert resolved is not None
            return HumanDecision.from_row(resolved)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def build_resume_payload(self, decision_id: str) -> dict[str, JsonValue]:
        """Re-read the authoritative response as ``{interrupt_id: response}``."""

        decision = await self.get_decision(decision_id)
        if decision is None:
            raise self._decision_error(
                "decision_not_found", "Decision does not exist", decision_id
            )
        if decision.status is not DecisionStatus.RESOLVED or decision.consumed_at is not None:
            raise self._decision_error(
                "decision_not_resolved", "Decision has no resumable response", decision_id
            )
        if not decision.interrupt_id:
            raise self._decision_error(
                "missing_interrupt_id", "Resolved decision has no interrupt id", decision_id
            )
        normalized = self._validate_response(decision.kind, decision.response)
        if canonical_json(normalized) != canonical_json(decision.response):
            raise self._decision_error(
                "decision_validator_drift",
                "Persisted response no longer matches its validator",
                decision_id,
            )
        return {decision.interrupt_id: normalized}

    resume_payload = build_resume_payload

    async def build_run_resume_payload(self, run_id: str) -> dict[str, JsonValue]:
        """Return every authoritative resolved interrupt response for a run."""

        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT decision_id FROM workflow_decisions "
                    "WHERE run_id=? AND status='resolved' AND consumed_at IS NULL "
                    "ORDER BY resolved_at,decision_id",
                    (run_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        payload: dict[str, JsonValue] = {}
        for row in rows:
            payload.update(await self.build_resume_payload(str(row["decision_id"])))
        return payload

    @staticmethod
    async def consume_resolved_in_transaction(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decision_id: str,
        checkpoint_id: str,
        expected_version: int | None = None,
        now: float | None = None,
    ) -> HumanDecision:
        """Consume one resolved response as part of a checkpoint transaction."""

        consumed_at = time.time() if now is None else float(now)
        row = await (
            await db.execute(
                "SELECT * FROM workflow_decisions WHERE decision_id=? AND run_id=?",
                (decision_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise HumanDecisionError(
                "decision_not_found", "Decision does not exist",
                details={"decision_id": decision_id},
            )
        if str(row["status"]) == DecisionStatus.CONSUMED.value:
            if str(row["consumed_checkpoint_id"]) == checkpoint_id:
                return HumanDecision.from_row(row)
            raise HumanDecisionError(
                "decision_already_consumed", "Decision was consumed by another checkpoint",
                details={"decision_id": decision_id},
            )
        if str(row["status"]) != DecisionStatus.RESOLVED.value or row["consumed_at"] is not None:
            raise HumanDecisionError(
                "decision_not_resolved", "Decision has no resumable response",
                details={"decision_id": decision_id},
            )
        if expected_version is not None and int(row["decision_version"]) != expected_version:
            raise HumanDecisionError(
                "stale_decision", "Decision version changed",
                details={"decision_id": decision_id},
            )
        cursor = await db.execute(
            """UPDATE workflow_decisions SET status='consumed',consumed_at=?,
            consumed_checkpoint_id=?,decision_version=decision_version+1
            WHERE decision_id=? AND run_id=? AND status='resolved' AND consumed_at IS NULL
            AND (? IS NULL OR decision_version=?)""",
            (
                consumed_at, checkpoint_id, decision_id, run_id,
                expected_version, expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise HumanDecisionError(
                "stale_decision", "Decision changed while it was consumed",
                details={"decision_id": decision_id},
            )
        consumed = await (
            await db.execute(
                "SELECT * FROM workflow_decisions WHERE decision_id=?", (decision_id,)
            )
        ).fetchone()
        assert consumed is not None
        return HumanDecision.from_row(consumed)
    open_interrupt = open_decision

    async def prepare_grant(
        self,
        *,
        decision_id: str,
        scope: JsonValue,
        effect_id: str | None = None,
        grant_id: str | None = None,
    ) -> HumanGrant:
        """Persist an unclaimed grant whose lifetime is owned by a decision."""

        try:
            scope_json = canonical_json(scope)
        except InvalidStatePatch as exc:
            raise HumanDecisionError(
                "invalid_grant_scope", str(exc), details=exc.details
            ) from exc
        stable_id = grant_id or uuid.uuid4().hex
        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            decision = await (
                await db.execute(
                    "SELECT * FROM workflow_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            if decision is None:
                raise self._decision_error(
                    "decision_not_found", "Decision does not exist", decision_id
                )
            if decision["expires_at"] is not None and float(decision["expires_at"]) <= now:
                await self._expire_rows(db, [decision], now)
                await db.commit()
                raise self._decision_error(
                    "decision_expired", "Decision has expired", decision_id
                )
            if decision["status"] not in {
                DecisionStatus.PREPARED.value,
                DecisionStatus.OPEN.value,
                DecisionStatus.RESOLVED.value,
            }:
                raise self._decision_error(
                    "decision_closed", "Closed decision cannot own a grant", decision_id
                )

            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_grants WHERE grant_id=?", (stable_id,)
                )
            ).fetchone()
            if existing is not None:
                if (
                    existing["decision_id"] != decision_id
                    or existing["effect_id"] != effect_id
                    or existing["scope_json"] != scope_json
                ):
                    raise HumanDecisionError(
                        "grant_identity_conflict",
                        "Grant id is already bound to different input",
                        details={"grant_id": stable_id},
                    )
                await db.commit()
                return HumanGrant.from_row(existing)
            await db.execute(
                """INSERT INTO workflow_grants(
                    grant_id,decision_id,effect_id,scope_json,status,created_at,consumed_at
                ) VALUES(?,?,?,?,'open',?,NULL)""",
                (stable_id, decision_id, effect_id, scope_json, now),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_grants WHERE grant_id=?", (stable_id,)
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return HumanGrant.from_row(row)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_grant(self, grant_id: str) -> HumanGrant | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_grants WHERE grant_id=?", (grant_id,)
                )
            ).fetchone()
            return HumanGrant.from_row(row) if row is not None else None
        finally:
            await db.close()

    async def _expire_rows(
        self, db: aiosqlite.Connection, rows: list[aiosqlite.Row], now: float
    ) -> int:
        expired = [
            row
            for row in rows
            if row["status"] in {DecisionStatus.PREPARED.value, DecisionStatus.OPEN.value}
            and row["expires_at"] is not None
            and float(row["expires_at"]) <= now
        ]
        for row in expired:
            await db.execute(
                """UPDATE workflow_decisions SET status='expired',
                decision_version=decision_version+1
                WHERE decision_id=? AND status IN ('prepared','open')""",
                (row["decision_id"],),
            )
            await db.execute(
                """UPDATE workflow_grants SET status='expired'
                WHERE decision_id=? AND status NOT IN ('consumed','expired','cancelled')""",
                (row["decision_id"],),
            )
            if row["status"] == DecisionStatus.OPEN.value:
                error_json = canonical_json(
                    {
                        "code": "decision_expired",
                        "decision_id": str(row["decision_id"]),
                    }
                )
                await db.execute(
                    """UPDATE workflow_runs SET status='blocked',error_json=?,
                    recovery_action='reopen_decision_or_cancel',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,
                    run_version=run_version+1,updated_at=?
                    WHERE run_id=? AND status='waiting'""",
                    (error_json, now, row["run_id"]),
                )
        return len(expired)

    async def expire_decisions(self, *, run_id: str | None = None) -> int:
        """Expire due prepared/open decisions and every unconsumed child grant."""

        now = self._clock()
        query = """SELECT * FROM workflow_decisions
            WHERE status IN ('prepared','open') AND expires_at IS NOT NULL AND expires_at<=?"""
        params: list[Any] = [now]
        if run_id is not None:
            query += " AND run_id=?"
            params.append(run_id)
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            rows = await (await db.execute(query, params)).fetchall()
            count = await self._expire_rows(db, list(rows), now)
            await db.commit()
            return count
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def cancel_run(self, run_id: str, *, reason: str = "user") -> int:
        """Fence a run and close all actionable decisions/grants in one transaction."""

        now = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute(
                    "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            if run is None:
                raise HumanDecisionError(
                    "decision_run_not_found",
                    "Decision run does not exist",
                    details={"run_id": run_id},
                )
            cursor = await db.execute(
                """UPDATE workflow_decisions SET status='cancelled',
                decision_version=decision_version+1
                WHERE run_id=? AND status IN ('prepared','open')""",
                (run_id,),
            )
            await db.execute(
                """UPDATE workflow_grants SET status='cancelled'
                WHERE decision_id IN (
                    SELECT decision_id FROM workflow_decisions WHERE run_id=?
                ) AND status NOT IN ('consumed','expired','cancelled')""",
                (run_id,),
            )
            await db.execute(
                """UPDATE workflow_runs SET status='cancel_requested',cancel_reason=?,
                lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                lease_epoch=lease_epoch+1,run_version=run_version+1,updated_at=?
                WHERE run_id=?
                AND status NOT IN ('completed','failed','cancelled','cancel_requested')""",
                (reason, now, run_id),
            )
            await db.commit()
            return max(cursor.rowcount, 0)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    cancel_decisions = cancel_run


__all__ = [
    "DecisionStatus",
    "GrantStatus",
    "HumanDecision",
    "HumanDecisionError",
    "HumanDecisionStore",
    "HumanGrant",
    "ResponseValidator",
]
