"""SQLite Effect journal sharing the execution ledger transaction boundary."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import aiosqlite

from deskpet.execution.contracts import DecisionAuthorization
from deskpet.harness.tool_executor import (
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.store.schema import initialize_workflow_db


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class SqliteExecutionEffectJournal:
    """Consume a grant and create its Effect attempt in one transaction."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._fault_injector = fault_injector

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

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    @staticmethod
    def _fingerprint(call: PreparedExecutionCall) -> str:
        return hashlib.sha256(
            _json(
                {
                    "effect_id": call.effect_id,
                    "tool_name": call.tool_name,
                    "args_hash": call.args_hash,
                    "capability_hash": call.capability_hash,
                    "scope_hash": call.scope_hash,
                }
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _assert_effect_row(
        row: aiosqlite.Row,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
    ) -> None:
        expected = (
            call.effect_id,
            context.run_id,
            call.call_id,
            call.tool_name,
            call.args_hash,
            call.capability_hash,
            call.scope_hash,
        )
        actual = tuple(
            str(row[name] or "")
            for name in (
                "effect_id",
                "run_id",
                "call_id",
                "tool_name",
                "args_hash",
                "capability_hash",
                "scope_hash",
            )
        )
        if actual != expected:
            raise RuntimeError("effect identity conflict")

    async def prepare_effect(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        authorization: DecisionAuthorization | None,
    ) -> ToolOutcome | None:
        now = float(self._clock())
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (context.run_id,)
                )
            ).fetchone()
            if run is None or str(run["terminal_event_id"] or ""):
                raise RuntimeError("effect requires a live durable execution run")
            if (
                str(run["session_id"]) != context.session_id
                or str(run["capability_hash"]) != call.capability_hash
                or str(run["status"]) in {"cancel_requested", "completed", "failed", "cancelled"}
            ):
                raise RuntimeError("effect run binding is stale")

            existing = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?", (call.effect_id,)
                )
            ).fetchone()
            if existing is not None:
                self._assert_effect_row(existing, call, context)
                await db.commit()
                return self._outcome_from_row(existing) or ToolOutcome.unknown(
                    call, "effect_in_flight_reconciliation_required"
                )

            if authorization is not None:
                grant = await (
                    await db.execute(
                        "SELECT * FROM execution_grants WHERE grant_id=?",
                        (authorization.grant_id,),
                    )
                ).fetchone()
                if grant is None:
                    raise RuntimeError("effect authorization grant is missing")
                expected = (
                    authorization.decision_id,
                    context.run_id,
                    call.call_id,
                    call.effect_id,
                    call.tool_name,
                    call.args_hash,
                    call.capability_hash,
                    call.scope_hash,
                    authorization.version,
                )
                actual = (
                    str(grant["decision_id"]),
                    str(grant["run_id"]),
                    str(grant["call_id"]),
                    str(grant["effect_id"]),
                    str(grant["tool_name"]),
                    str(grant["args_hash"]),
                    str(grant["capability_hash"]),
                    str(grant["scope_hash"]),
                    int(grant["grant_version"]),
                )
                if expected != actual or str(grant["status"]) != "issued":
                    raise RuntimeError("effect authorization grant is stale")
                if float(grant["expires_at"]) <= now:
                    raise RuntimeError("effect authorization grant expired")
                cursor = await db.execute(
                    """UPDATE execution_grants SET status='consumed',
                    grant_version=grant_version+1,consumed_at=?
                    WHERE grant_id=? AND grant_version=? AND status='issued'""",
                    (now, authorization.grant_id, authorization.version),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("effect authorization consume conflict")

            await db.execute(
                """INSERT INTO execution_effects(
                effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
                args_hash,capability_hash,scope_hash,effect_type,status,policy_json,
                prepared_json,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,?,?,'running',?,?,?,?)""",
                (
                    call.effect_id,
                    context.run_id,
                    self._fingerprint(call),
                    call.call_id,
                    call.tool_name,
                    call.args_hash,
                    call.capability_hash,
                    call.scope_hash,
                    "recoverable" if call.recoverable_effect else "opaque",
                    _json(
                        {
                            "tool_spec_version": call.tool_spec_version,
                            "schema_hash": call.schema_hash,
                            "permission_policy_version": call.permission_policy_version,
                        }
                    ),
                    _json(call.args_copy()),
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_effect_attempts(
                effect_id,attempt_no,status,started_at,updated_at
                ) VALUES(?,1,'running',?,?)""",
                (call.effect_id, now, now),
            )
            self._fault("effect_prepare_before_commit")
            await db.commit()
            return None
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def mark_unknown(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        reason: str,
    ) -> None:
        await self._settle(call, ToolOutcome.unknown(call, reason), late=False)

    async def finalize_effect(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        outcome: ToolOutcome,
        *,
        late: bool,
    ) -> None:
        del context
        await self._settle(call, outcome, late=late)

    async def _settle(
        self,
        call: PreparedExecutionCall,
        outcome: ToolOutcome,
        *,
        late: bool,
    ) -> None:
        now = float(self._clock())
        status = "late_reconciled" if late else outcome.status.value
        if status not in {"succeeded", "failed", "unknown", "cancelled", "late_reconciled"}:
            raise ValueError(f"effect outcome is not terminal: {status}")
        outcome_json = _json(
            {
                "call_id": outcome.call_id,
                "effect_id": outcome.effect_id,
                "status": outcome.status.value,
                "value": outcome.value,
                "error": outcome.error,
                "retryable": outcome.retryable,
                "reconciliation": outcome.reconciliation,
            }
        )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?", (call.effect_id,)
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("effect was not prepared")
            if str(row["status"]) in {"succeeded", "failed", "cancelled", "late_reconciled"}:
                if str(row["outcome_json"] or "") != outcome_json:
                    raise RuntimeError("effect outcome conflict")
                await db.commit()
                return
            await db.execute(
                """UPDATE execution_effects SET status=?,outcome_json=?,receipt_ref=?,
                artifact_refs_json=?,effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=?""",
                (
                    status,
                    outcome_json,
                    outcome.receipt_ref,
                    _json(list(outcome.artifact_refs)),
                    now,
                    now,
                    call.effect_id,
                ),
            )
            await db.execute(
                """UPDATE execution_effect_attempts SET status=?,updated_at=?,ended_at=?,
                error_json=?,outcome_json=? WHERE effect_id=? AND attempt_no=1""",
                (
                    status,
                    now,
                    now,
                    _json({"message": outcome.error}) if outcome.error else None,
                    outcome_json,
                    call.effect_id,
                ),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_outcome(self, effect_id: str) -> ToolOutcome | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,)
                )
            ).fetchone()
            if row is None:
                return None
            return self._outcome_from_row(row) or ToolOutcome(
                call_id=str(row["call_id"]),
                effect_id=str(row["effect_id"]),
                status=ToolOutcomeStatus.UNKNOWN,
                error="effect_in_flight_reconciliation_required",
                retryable=False,
                reconciliation="required",
            )
        finally:
            await db.close()

    @staticmethod
    def _outcome_from_row(row: aiosqlite.Row) -> ToolOutcome | None:
        if row["outcome_json"] is None:
            return None
        value = json.loads(str(row["outcome_json"]))
        return ToolOutcome(
            call_id=str(value["call_id"]),
            effect_id=str(value["effect_id"]),
            status=ToolOutcomeStatus(str(value["status"])),
            value=value.get("value"),
            error=value.get("error"),
            receipt_ref=row["receipt_ref"],
            artifact_refs=tuple(json.loads(str(row["artifact_refs_json"] or "[]"))),
            retryable=bool(value.get("retryable", False)),
            reconciliation=value.get("reconciliation"),
        )


__all__ = ["SqliteExecutionEffectJournal"]
