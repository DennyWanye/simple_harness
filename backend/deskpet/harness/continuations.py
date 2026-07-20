"""Durable ReAct command boundaries stored in ``workflow.db``.

The table is provided by execution schema v5.  A boundary contains the whole
prepared batch, not merely the next call, despite the legacy singular column
name ``pending_prepared_call_json``.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import aiosqlite

from deskpet.harness.ports import AttachmentPolicy, DelegateRun, JoinPolicy, OpenDecision
from deskpet.harness.tool_executor import (
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.store.schema import initialize_workflow_db


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _call_to_dict(call: PreparedExecutionCall) -> dict[str, Any]:
    return {
        "tool_name": call.tool_name,
        "model_args": call.args_copy(),
        "call_id": call.call_id,
        "effect_id": call.effect_id,
        "capability_hash": call.capability_hash,
        "scope_hash": call.scope_hash,
        "requires_authorization": call.requires_authorization,
        "recoverable_effect": call.recoverable_effect,
        "tool_spec_version": call.tool_spec_version,
        "schema_hash": call.schema_hash,
        "permission_policy_version": call.permission_policy_version,
        "args_hash": call.args_hash,
    }


def _call_from_dict(value: Mapping[str, Any]) -> PreparedExecutionCall:
    call = PreparedExecutionCall(
        tool_name=str(value["tool_name"]),
        model_args=dict(value["model_args"]),
        call_id=str(value["call_id"]),
        effect_id=str(value["effect_id"]),
        capability_hash=str(value["capability_hash"]),
        scope_hash=str(value["scope_hash"]),
        requires_authorization=bool(value.get("requires_authorization", False)),
        recoverable_effect=bool(value.get("recoverable_effect", False)),
        tool_spec_version=str(value.get("tool_spec_version", "")),
        schema_hash=str(value.get("schema_hash", "")),
        permission_policy_version=str(value.get("permission_policy_version", "")),
    )
    if value.get("args_hash") != call.args_hash:
        raise ValueError("persisted prepared call args hash mismatch")
    return call


def _context_to_dict(context: ToolExecutionContext) -> dict[str, Any]:
    return {
        "scope_id": context.scope_id,
        "session_id": context.session_id,
        "request_id": context.request_id,
        "origin": context.origin,
        "root_run_id": context.root_run_id,
        "parent_run_id": context.parent_run_id,
        "turn_id": context.turn_id,
        "venue": context.venue,
        "workspace": context.workspace,
        "write_scope_root": context.write_scope_root,
        "capability_hash": context.capability_hash,
        "scope_hash": context.scope_hash,
        "provider_plan": list(context.provider_plan),
        "run_id": context.run_id,
        "call_id": context.call_id,
        "effect_id": context.effect_id,
        "trace_id": context.trace_id,
    }


def _context_from_dict(value: Mapping[str, Any]) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id=str(value["scope_id"]),
        session_id=str(value["session_id"]),
        request_id=str(value["request_id"]),
        origin=str(value.get("origin") or "agent"),
        root_run_id=str(value["root_run_id"]),
        parent_run_id=(str(value["parent_run_id"]) if value.get("parent_run_id") else None),
        turn_id=str(value["turn_id"]),
        venue=str(value["venue"]),
        workspace=(str(value["workspace"]) if value.get("workspace") else None),
        write_scope_root=(
            str(value["write_scope_root"]) if value.get("write_scope_root") else None
        ),
        capability_hash=str(value["capability_hash"]),
        scope_hash=str(value["scope_hash"]),
        provider_plan=tuple(str(item) for item in value.get("provider_plan", ())),
        run_id=str(value["run_id"]),
        call_id=str(value["call_id"]),
        effect_id=str(value["effect_id"]),
        trace_id=str(value["trace_id"]),
    )


def _outcome_to_dict(outcome: ToolOutcome) -> dict[str, Any]:
    return {
        "call_id": outcome.call_id,
        "effect_id": outcome.effect_id,
        "status": outcome.status.value,
        "value": copy.deepcopy(outcome.value),
        "error": outcome.error,
        "receipt_ref": outcome.receipt_ref,
        "artifact_refs": list(outcome.artifact_refs),
        "retryable": outcome.retryable,
        "reconciliation": outcome.reconciliation,
    }


def _outcome_from_dict(value: Mapping[str, Any]) -> ToolOutcome:
    return ToolOutcome(
        call_id=str(value["call_id"]),
        effect_id=str(value["effect_id"]),
        status=ToolOutcomeStatus(str(value["status"])),
        value=copy.deepcopy(value.get("value")),
        error=(str(value["error"]) if value.get("error") is not None else None),
        receipt_ref=(
            str(value["receipt_ref"]) if value.get("receipt_ref") is not None else None
        ),
        artifact_refs=tuple(str(item) for item in value.get("artifact_refs", ())),
        retryable=bool(value.get("retryable", False)),
        reconciliation=(
            str(value["reconciliation"])
            if value.get("reconciliation") is not None
            else None
        ),
    )


@dataclass(frozen=True)
class ReactCommandBoundary:
    run_id: str
    session_id: str
    command_id: str
    command_kind: str
    canonical_messages: tuple[Mapping[str, Any], ...]
    session_projection_cursor: int
    prepared_context_ref: str | None
    tool_set_snapshot_ref: str | None
    pending_calls: tuple[PreparedExecutionCall, ...]
    tool_contexts: tuple[ToolExecutionContext, ...]
    outcomes: tuple[ToolOutcome | None, ...]
    provider_state: Mapping[str, Any]
    iteration: int
    completion_state: Mapping[str, Any]
    pending_decision: OpenDecision | None = None
    pending_delegate: DelegateRun | None = None
    version: int = 0

    def __post_init__(self) -> None:
        if not self.run_id or not self.session_id or not self.command_id:
            raise ValueError("run_id, session_id and command_id are required")
        if len(self.pending_calls) != len(self.tool_contexts):
            raise ValueError("pending calls and contexts must align")
        if len(self.pending_calls) != len(self.outcomes):
            raise ValueError("pending calls and outcomes must align")
        if self.pending_decision is not None and self.pending_delegate is not None:
            raise ValueError("a boundary cannot wait on a decision and child simultaneously")
        if self.version < 0 or self.iteration < 0 or self.session_projection_cursor < 0:
            raise ValueError("boundary counters must be non-negative")
        for call, context in zip(self.pending_calls, self.tool_contexts):
            if call.call_id != context.call_id or call.effect_id != context.effect_id:
                raise ValueError("prepared call and trusted context binding mismatch")
            if context.run_id != self.run_id or context.session_id != self.session_id:
                raise ValueError("boundary and trusted context binding mismatch")
        object.__setattr__(
            self,
            "canonical_messages",
            tuple(
                MappingProxyType(copy.deepcopy(dict(item)))
                for item in self.canonical_messages
            ),
        )
        object.__setattr__(
            self,
            "provider_state",
            MappingProxyType(copy.deepcopy(dict(self.provider_state))),
        )
        object.__setattr__(
            self,
            "completion_state",
            MappingProxyType(copy.deepcopy(dict(self.completion_state))),
        )

    @property
    def pending_indexes(self) -> tuple[int, ...]:
        return tuple(index for index, outcome in enumerate(self.outcomes) if outcome is None)

    def with_outcomes(self, updates: Mapping[int, ToolOutcome]) -> "ReactCommandBoundary":
        outcomes = list(self.outcomes)
        for index, outcome in updates.items():
            call = self.pending_calls[index]
            if outcome.call_id != call.call_id or outcome.effect_id != call.effect_id:
                raise ValueError("outcome binding mismatch")
            existing = outcomes[index]
            if existing is not None and existing != outcome:
                raise ValueError("outcome already recorded with different value")
            outcomes[index] = outcome
        return replace(self, outcomes=tuple(outcomes), version=self.version + 1)

    def with_messages(
        self, messages: tuple[Mapping[str, Any], ...]
    ) -> "ReactCommandBoundary":
        return replace(
            self,
            canonical_messages=tuple(copy.deepcopy(dict(item)) for item in messages),
            version=self.version + 1,
        )

    def with_backfilled_messages(
        self, messages: tuple[Mapping[str, Any], ...]
    ) -> "ReactCommandBoundary":
        state = copy.deepcopy(dict(self.completion_state))
        state["model_backfilled"] = True
        return replace(
            self,
            canonical_messages=tuple(copy.deepcopy(dict(item)) for item in messages),
            completion_state=state,
            version=self.version + 1,
        )


class ReactCommandBoundaryStore(Protocol):
    async def put(
        self,
        boundary: ReactCommandBoundary,
        *,
        open_decision: OpenDecision | None = None,
    ) -> None: ...

    async def confirm_resolved_decision(
        self,
        boundary: ReactCommandBoundary,
        *,
        decision_id: str,
    ) -> None: ...

    async def load(self, run_id: str) -> ReactCommandBoundary | None: ...
    async def delete(self, run_id: str) -> None: ...


class SqliteReactCommandBoundaryStore:
    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    async def _connect(self) -> aiosqlite.Connection:
        await initialize_workflow_db(self.path)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _payload(boundary: ReactCommandBoundary) -> dict[str, Any]:
        delegate = boundary.pending_delegate
        return {
            "session_id": boundary.session_id,
            "command_id": boundary.command_id,
            "command_kind": boundary.command_kind,
            "calls": [_call_to_dict(call) for call in boundary.pending_calls],
            "contexts": [_context_to_dict(context) for context in boundary.tool_contexts],
            "outcomes": [
                _outcome_to_dict(outcome) if outcome is not None else None
                for outcome in boundary.outcomes
            ],
            "completion_state": copy.deepcopy(dict(boundary.completion_state)),
            "delegate": (
                None
                if delegate is None
                else {
                    "run_id": delegate.run_id,
                    "command_id": delegate.command_id,
                    "child_request": dict(delegate.child_request),
                    "route_hint": delegate.route_hint,
                    "capability_subset": list(delegate.capability_subset),
                    "attachment_policy": delegate.attachment_policy.value,
                    "join_policy": delegate.join_policy.value,
                }
            ),
        }

    @staticmethod
    def _decision_to_values(decision: OpenDecision, now: float) -> tuple[Any, ...]:
        permission = decision.kind == "permission"
        if permission and not all(
            (
                decision.call_id,
                decision.effect_id,
                decision.tool_name,
                decision.args_hash,
                decision.capability_hash,
                decision.scope_hash,
            )
        ):
            raise ValueError("permission decision is missing its frozen binding")
        return (
            decision.decision_id,
            1,
            decision.run_id,
            decision.nonce,
            decision.kind,
            "open",
            decision.prompt_schema_version,
            _json(dict(decision.prompt)),
            decision.call_id,
            decision.effect_id,
            decision.tool_name,
            decision.args_hash,
            decision.capability_hash,
            decision.scope_hash,
            decision.expires_at,
            now,
        )

    async def _upsert(self, db: aiosqlite.Connection, boundary: ReactCommandBoundary) -> None:
        now = float(self._clock())
        payload = self._payload(boundary)
        cursor = await db.execute(
            """
            INSERT INTO execution_continuations(
                run_id,schema_version,command_schema_version,canonical_messages_json,
                session_projection_cursor,prepared_context_ref,tool_set_snapshot_ref,
                pending_prepared_call_json,pending_decision_id,iteration,
                provider_state_json,continuation_version,created_at,updated_at
            ) VALUES(?,1,1,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
                canonical_messages_json=excluded.canonical_messages_json,
                session_projection_cursor=excluded.session_projection_cursor,
                prepared_context_ref=excluded.prepared_context_ref,
                tool_set_snapshot_ref=excluded.tool_set_snapshot_ref,
                pending_prepared_call_json=excluded.pending_prepared_call_json,
                pending_decision_id=excluded.pending_decision_id,
                iteration=excluded.iteration,
                provider_state_json=excluded.provider_state_json,
                continuation_version=excluded.continuation_version,
                updated_at=excluded.updated_at
            WHERE execution_continuations.continuation_version=excluded.continuation_version-1
            """,
            (
                boundary.run_id,
                _json([dict(item) for item in boundary.canonical_messages]),
                boundary.session_projection_cursor,
                boundary.prepared_context_ref,
                boundary.tool_set_snapshot_ref,
                _json(payload),
                boundary.pending_decision.decision_id if boundary.pending_decision else None,
                boundary.iteration,
                _json(dict(boundary.provider_state)),
                boundary.version,
                now,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("stale_react_boundary_version")

    async def put(
        self,
        boundary: ReactCommandBoundary,
        *,
        open_decision: OpenDecision | None = None,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            if open_decision is not None:
                await db.execute(
                    """
                    INSERT INTO execution_decisions(
                        decision_id,schema_version,run_id,nonce,kind,status,
                        prompt_schema_version,prompt_json,call_id,effect_id,tool_name,
                        args_hash,capability_hash,scope_hash,expires_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    self._decision_to_values(open_decision, float(self._clock())),
                )
            await self._upsert(db, boundary)
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def confirm_resolved_decision(
        self,
        boundary: ReactCommandBoundary,
        *,
        decision_id: str,
    ) -> None:
        if (
            boundary.pending_decision is None
            or boundary.pending_decision.decision_id != decision_id
        ):
            raise ValueError("decision does not match pending boundary")
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT status FROM execution_decisions
                    WHERE decision_id=? AND run_id=?""",
                    (decision_id, boundary.run_id),
                )
            ).fetchone()
            if row is None or str(row["status"]) not in {"allowed", "denied"}:
                raise RuntimeError("decision_not_resolved_by_authoritative_store")
        finally:
            await db.close()

    async def load(self, run_id: str) -> ReactCommandBoundary | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_continuations WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(str(row["pending_prepared_call_json"]))
            decision = None
            delegate = None
            delegate_payload = payload.get("delegate")
            if isinstance(delegate_payload, Mapping):
                delegate = DelegateRun(
                    run_id=str(delegate_payload["run_id"]),
                    command_id=str(delegate_payload["command_id"]),
                    child_request=dict(delegate_payload["child_request"]),
                    route_hint=str(delegate_payload["route_hint"]),
                    capability_subset=tuple(delegate_payload["capability_subset"]),
                    attachment_policy=AttachmentPolicy(
                        str(delegate_payload["attachment_policy"])
                    ),
                    join_policy=JoinPolicy(str(delegate_payload["join_policy"])),
                )
            decision_id = row["pending_decision_id"]
            if decision_id is not None:
                drow = await (
                    await db.execute(
                        "SELECT * FROM execution_decisions WHERE decision_id=?",
                        (decision_id,),
                    )
                ).fetchone()
                if drow is not None:
                    decision = OpenDecision(
                        run_id=str(drow["run_id"]),
                        command_id=str(payload["command_id"]),
                        decision_id=str(drow["decision_id"]),
                        nonce=str(drow["nonce"]),
                        kind=str(drow["kind"]),
                        prompt=json.loads(str(drow["prompt_json"])),
                        prompt_schema_version=int(drow["prompt_schema_version"]),
                        expires_at=drow["expires_at"],
                        call_id=drow["call_id"],
                        effect_id=drow["effect_id"],
                        tool_name=drow["tool_name"],
                        args_hash=drow["args_hash"],
                        capability_hash=drow["capability_hash"],
                        scope_hash=drow["scope_hash"],
                    )
            return ReactCommandBoundary(
                run_id=str(row["run_id"]),
                session_id=str(payload["session_id"]),
                command_id=str(payload["command_id"]),
                command_kind=str(payload["command_kind"]),
                canonical_messages=tuple(json.loads(str(row["canonical_messages_json"]))),
                session_projection_cursor=int(row["session_projection_cursor"]),
                prepared_context_ref=row["prepared_context_ref"],
                tool_set_snapshot_ref=row["tool_set_snapshot_ref"],
                pending_calls=tuple(_call_from_dict(item) for item in payload["calls"]),
                tool_contexts=tuple(
                    _context_from_dict(item) for item in payload["contexts"]
                ),
                outcomes=tuple(
                    _outcome_from_dict(item) if item is not None else None
                    for item in payload["outcomes"]
                ),
                provider_state=json.loads(str(row["provider_state_json"])),
                iteration=int(row["iteration"]),
                completion_state=dict(payload.get("completion_state") or {}),
                pending_decision=decision,
                pending_delegate=delegate,
                version=int(row["continuation_version"]),
            )
        finally:
            await db.close()

    async def delete(self, run_id: str) -> None:
        db = await self._connect()
        try:
            await db.execute("DELETE FROM execution_continuations WHERE run_id=?", (run_id,))
            await db.commit()
        finally:
            await db.close()


__all__ = [
    "ReactCommandBoundary",
    "ReactCommandBoundaryStore",
    "SqliteReactCommandBoundaryStore",
]
