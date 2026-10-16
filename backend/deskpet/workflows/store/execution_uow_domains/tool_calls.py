"""Authorization provenance and tool-call claims."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    ActorContext,
    AuthorizationError,
    DecisionAuthorization,
    DecisionStatus,
    GrantConsume,
    GrantConsumeConflict,
    GrantNotFound,
    IdempotencyConflict,
    RecoveryLease,
    RunStatus,
    canonical_json,
    fingerprint_json,
    thaw_json,
)

from .core import ExecutionUowCore


@dataclass(frozen=True, slots=True)
class ExecutionEffectClaim:
    effect_id: str
    run_id: str
    attempt_no: int
    status: str
    worker_owner: str
    worker_epoch: int
    effect_version: int
    action: str = "execute"
    authorization: DecisionAuthorization | None = None


class ToolCallMixin(ExecutionUowCore):
    """Authorization provenance and tool-call claims."""

    __slots__ = ()

    async def _inspect_authorization_tx(
        self,
        db: aiosqlite.Connection,
        request: GrantConsume,
        actor: ActorContext,
        *,
        allow_consumed: bool = False,
    ) -> aiosqlite.Row:
        grant_row = await (
            await db.execute("SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,))
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
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,))
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
        expected_version = request.expected_version + (1 if allow_consumed else 0)
        if int(grant_row["grant_version"]) != expected_version:
            raise GrantConsumeConflict(
                "stale_grant_version",
                f"expected grant version {expected_version}, found {grant_row['grant_version']}",
            )
        if run.status is RunStatus.CANCEL_REQUESTED or run.status in TERMINAL_RUN_STATUSES:
            raise GrantConsumeConflict(
                "run_not_executable", "cancelled or terminal run rejects authorization"
            )
        if decision.status is not DecisionStatus.ALLOWED:
            raise GrantConsumeConflict("grant_not_authorized", "grant decision is not allowed")
        expected_status = "consumed" if allow_consumed else "issued"
        if str(grant_row["status"]) != expected_status:
            raise GrantConsumeConflict(
                "grant_already_consumed",
                "duplicate, expired, or revoked authorization was rejected",
            )
        return grant_row

    async def get_prepared_authorization_provenance(
        self,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
    ) -> Mapping[str, Any] | None:
        """Return host-committed TaskGrant provenance for one exact call."""

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT response_json FROM execution_decisions
                       WHERE run_id=? AND call_id=? AND effect_id=?
                         AND kind='permission' AND status='allowed'
                       ORDER BY resolved_at DESC,created_at DESC
                       LIMIT 1""",
                    (run_id, call_id, effect_id),
                )
            ).fetchone()
            if row is None:
                return None
            response = json.loads(str(row["response_json"] or "{}"))
            if not isinstance(response, dict):
                return None
            task_grant_id = str(response.get("task_grant_id") or "")
            if not task_grant_id:
                return None
            grant_row = await (
                await db.execute(
                    """SELECT grant_json,status FROM task_grants
                       WHERE task_grant_id=?""",
                    (task_grant_id,),
                )
            ).fetchone()
            if grant_row is None or str(grant_row["status"]) != "active":
                return None
            grant = json.loads(str(grant_row["grant_json"] or "{}"))
            if not isinstance(grant, dict):
                return None
            policy = await (
                await db.execute(
                    """SELECT mode,generation,updated_at
                       FROM authorization_policy_state WHERE singleton_id=1"""
                )
            ).fetchone()
            if policy is None:
                return None
            return {
                "task_grant": grant,
                "policy_state": {
                    "mode": str(policy["mode"]),
                    "generation": int(policy["generation"]),
                    "updated_at": float(policy["updated_at"]),
                },
                "authorization_source": str(response.get("authorization_source") or ""),
                "authorization_intent_fingerprint": str(
                    response.get("authorization_intent_fingerprint") or ""
                ),
            }

    async def _consume_authorization_tx(
        self,
        db: aiosqlite.Connection,
        request: GrantConsume,
        actor: ActorContext,
        *,
        now: float,
    ) -> tuple[DecisionAuthorization, GrantConsumeConflict | None]:
        grant_row = await self._inspect_authorization_tx(db, request, actor)
        if float(grant_row["expires_at"]) <= now:
            cursor = await db.execute(
                """UPDATE execution_grants SET status='expired',
                grant_version=grant_version+1 WHERE grant_id=?
                AND grant_version=? AND status='issued'""",
                (request.grant_id, request.expected_version),
            )
            if cursor.rowcount != 1:
                raise GrantConsumeConflict("stale_grant_version", "grant changed before expiry CAS")
            expired = await (
                await db.execute(
                    "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
                )
            ).fetchone()
            assert expired is not None
            return (
                self._row_to_authorization(expired),
                GrantConsumeConflict("grant_expired", "expired authorization was rejected"),
            )
        cursor = await db.execute(
            """UPDATE execution_grants SET status='consumed',
            grant_version=grant_version+1,consumed_at=? WHERE grant_id=?
            AND grant_version=? AND status='issued'""",
            (now, request.grant_id, request.expected_version),
        )
        if cursor.rowcount != 1:
            raise GrantConsumeConflict("grant_consume_conflict", "grant changed before consume CAS")
        consumed = await (
            await db.execute("SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,))
        ).fetchone()
        assert consumed is not None
        return self._row_to_authorization(consumed), None

    async def claim_tool_call(
        self,
        request: GrantConsume | None,
        actor: ActorContext,
        *,
        run_id: str | None = None,
        expected_session_id: str | None = None,
        call_id: str | None = None,
        effect_id: str | None = None,
        tool_name: str | None = None,
        args_hash: str | None = None,
        capability_hash: str | None = None,
        scope_hash: str | None = None,
        effect_type: str | None = None,
        policy: Mapping[str, Any] | None = None,
        prepared: Mapping[str, Any] | None = None,
        worker_owner: str = "",
        worker_epoch: int = 0,
        recovery_lease: RecoveryLease | None = None,
    ) -> DecisionAuthorization | ExecutionEffectClaim:
        """Consume authorization, optionally claiming its fenced tool effect."""

        if effect_type is None:
            if request is None:
                raise ValueError("authorization consumption requires a grant")
            async with self._write_transaction() as db:
                authorization, expired = await self._consume_authorization_tx(
                    db, request, actor, now=float(self._clock())
                )
                self._fault("grant_consume_before_commit")
                await db.commit()
                if expired is not None:
                    raise expired
                return authorization

        if not effect_type or not worker_owner or worker_epoch < 1:
            raise ValueError("effect type, worker owner and positive epoch are required")
        if policy is None or prepared is None:
            raise ValueError("effect claim requires policy and prepared payload")
        if request is not None:
            run_id, expected_session_id, call_id, effect_id, tool_name = (
                request.run_id,
                request.expected_session_id,
                request.call_id,
                request.effect_id,
                request.tool_name,
            )
            args_hash, capability_hash, scope_hash = (
                request.args_hash,
                request.capability_hash,
                request.scope_hash,
            )
        identity = (
            run_id,
            expected_session_id,
            call_id,
            effect_id,
            tool_name,
            args_hash,
            capability_hash,
            scope_hash,
        )
        if any(not str(value or "").strip() for value in identity):
            raise ValueError("complete effect identity is required")
        if actor.session_id != expected_session_id:
            raise AuthorizationError(
                "effect_actor_mismatch", "effect actor does not own the session"
            )
        policy_json = canonical_json(thaw_json(policy))
        prepared_json = canonical_json(thaw_json(prepared))
        effect_fingerprint = fingerprint_json(
            {
                "run_id": run_id,
                "call_id": call_id,
                "effect_id": effect_id,
                "tool_name": tool_name,
                "args_hash": args_hash,
                "capability_hash": capability_hash,
                "scope_hash": scope_hash,
                "effect_type": effect_type,
            }
        )
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, str(run_id))
            run = await (
                await db.execute(
                    "SELECT session_id,root_run_id FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None or (str(run["session_id"]), str(run["root_run_id"])) != (
                actor.session_id,
                actor.root_run_id,
            ):
                raise AuthorizationError(
                    "effect_actor_mismatch", "effect actor does not own the run tree"
                )
            existing = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?",
                    (effect_id,),
                )
            ).fetchone()
            if existing is not None:
                expected = (
                    run_id,
                    effect_fingerprint,
                    call_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    effect_type,
                    policy_json,
                    prepared_json,
                )
                actual = tuple(
                    str(existing[name] or "")
                    for name in (
                        "run_id",
                        "effect_fingerprint",
                        "call_id",
                        "tool_name",
                        "args_hash",
                        "capability_hash",
                        "scope_hash",
                        "effect_type",
                        "policy_json",
                        "prepared_json",
                    )
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "effect_claim_conflict", "effect id already names another intent"
                    )
                attempt = await (
                    await db.execute(
                        """SELECT * FROM execution_effect_attempts
                        WHERE effect_id=? AND attempt_no=1""",
                        (effect_id,),
                    )
                ).fetchone()
                assert attempt is not None
                authorization = None
                if request is not None:
                    consumed = await self._inspect_authorization_tx(
                        db, request, actor, allow_consumed=True
                    )
                    authorization = self._row_to_authorization(consumed)
                await db.commit()
                return ExecutionEffectClaim(
                    str(effect_id),
                    str(run_id),
                    1,
                    str(existing["status"]),
                    str(attempt["worker_owner"]),
                    int(attempt["worker_epoch"]),
                    int(existing["effect_version"]),
                    (
                        "reconcile"
                        if recovery_lease is not None
                        and str(existing["status"]) in {"running", "unknown"}
                        else "reuse"
                        if str(existing["status"]) != "running"
                        else "in_flight"
                    ),
                    authorization,
                )
            authorization = None
            if request is not None:
                authorization, expired = await self._consume_authorization_tx(
                    db, request, actor, now=float(self._clock())
                )
                if expired is not None:
                    await db.commit()
                    raise expired
            self._fault("effect_claim_after_grant")
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_effects(
                effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
                args_hash,capability_hash,scope_hash,effect_type,status,
                handoff_state,completion_disposition,policy_json,
                prepared_json,effect_version,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,?,?, 'running','unresolved','normal',
                    ?,?,0,?,?)""",
                (
                    effect_id,
                    run_id,
                    effect_fingerprint,
                    call_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    effect_type,
                    policy_json,
                    prepared_json,
                    now,
                    now,
                ),
            )
            self._fault("effect_claim_after_effect")
            await db.execute(
                """INSERT INTO execution_effect_attempts(
                effect_id,attempt_no,status,handoff_state,
                completion_disposition,worker_owner,worker_epoch,
                started_at,updated_at
                ) VALUES(?,1,'running','unresolved','normal',?,?,?,?)""",
                (effect_id, worker_owner, worker_epoch, now, now),
            )
            self._fault("effect_claim_before_commit")
            await db.commit()
            return ExecutionEffectClaim(
                str(effect_id),
                str(run_id),
                1,
                "running",
                worker_owner,
                worker_epoch,
                0,
                "execute",
                authorization,
            )
