"""Decision reads, resolution, commit and cancellation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    ActorContext,
    AuthorizationError,
    DecisionAuthorization,
    DecisionConflict,
    DecisionKind,
    DecisionNotFound,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DecisionStatus,
    PersistenceLevel,
    PersistenceRequired,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    VersionConflict,
    canonical_json,
    stable_decision_grant_id,
    thaw_json,
)
from deskpet.execution.delivery_contracts import DeliverySpec, RunEvent, RunEventCandidate
from deskpet.permissions.policy import (
    AuthorizationPolicy,
    AuthorizationPolicyState,
    DecisionContext,
)
from deskpet.types.task_grants import PreparedAuthorizationCommit

from .core import ExecutionUowCore
from .support import ContinuationRecord


class DecisionMixin(ExecutionUowCore):
    """Decision reads, resolution, commit and cancellation."""

    __slots__ = ()

    @staticmethod
    def _assert_decision_intent(existing: DecisionRecord, requested: DecisionOpen) -> None:
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
        self._authorize_view(RunRef(record.run_id, expected_session_id), actor, record)
        return record

    async def get_decision(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord:
        async with self._read_connection() as db:
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
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,))
            ).fetchone()
            assert run_row is not None
            self._authorize_run_row(
                run_row,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            return decision

    async def get_decision_projection(
        self,
        decision_id: str,
        *,
        expected_run_id: str,
        expected_session_id: str,
    ) -> DecisionRecord:
        """Return current decision state for a session-owned history row.

        History hydration starts from a SessionDB row that already carries a
        canonical ``workflow_event_id``.  Re-check both the event's run and
        session bindings here so the product projection cannot cross either
        durable boundary while refreshing an open/settled decision card.
        """

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT execution_decisions.*,execution_runs.session_id
                    FROM execution_decisions
                    JOIN execution_runs USING(run_id)
                    WHERE execution_decisions.decision_id=?""",
                    (str(decision_id).strip(),),
                )
            ).fetchone()
            if row is None:
                raise DecisionNotFound(
                    "decision_not_found",
                    f"execution decision does not exist: {decision_id}",
                )
            if (
                str(row["run_id"]) != str(expected_run_id).strip()
                or str(row["session_id"]) != str(expected_session_id).strip()
            ):
                raise AuthorizationError(
                    "decision_projection_scope_mismatch",
                    "decision projection differs from the durable run/session binding",
                )
            return self._row_to_decision(row)

    async def list_open_decision_projections(
        self,
        session_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return session-fenced waiting decisions for UI rehydration.

        Blocking cards are live projections of durable decisions.  A control
        WebSocket reconnect must be able to rebuild those cards without
        reopening or mutating the decision itself.
        """

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT execution_decisions.*,execution_runs.session_id,
                    execution_runs.root_run_id,execution_runs.request_id,
                    execution_runs.turn_id,execution_runs.driver_kind,
                    execution_task_work_contexts.workspace_root,
                    execution_task_work_contexts.workspace_source
                    FROM execution_decisions
                    JOIN execution_runs USING(run_id)
                    LEFT JOIN execution_task_work_contexts
                      ON execution_task_work_contexts.root_run_id=
                         execution_runs.root_run_id
                    WHERE execution_runs.session_id=?
                      AND execution_runs.status='waiting'
                      AND execution_decisions.status='open'
                    ORDER BY execution_decisions.created_at,
                             execution_decisions.decision_id""",
                    (normalized_session_id,),
                )
            ).fetchall()
            return tuple(self._open_decision_projection_from_row(row) for row in rows)

    @classmethod
    def _open_decision_projection_from_row(
        cls,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "decision": cls._row_to_decision(row),
            "session_id": str(row["session_id"]),
            "root_run_id": str(row["root_run_id"]),
            "request_id": str(row["request_id"]),
            "turn_id": str(row["turn_id"]),
            "driver_kind": str(row["driver_kind"]),
            "workspace_root": (
                None if row["workspace_root"] is None else str(row["workspace_root"])
            ),
            "workspace_source": (
                None if row["workspace_source"] is None else str(row["workspace_source"])
            ),
        }

    @staticmethod
    def _assert_signal_binding(decision: DecisionRecord, signal: DecisionSignal) -> None:
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

    async def _resolve_decision_tx(
        self,
        db: aiosqlite.Connection,
        signal: DecisionSignal,
        actor: ActorContext,
        *,
        now: float,
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> tuple[
        DecisionRecord,
        DecisionAuthorization | None,
        DecisionConflict | None,
    ]:
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
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,))
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
            expired = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (decision.decision_id,),
                )
            ).fetchone()
            assert expired is not None
            return (
                self._row_to_decision(expired),
                None,
                DecisionConflict("decision_expired", "expired decision signal was rejected"),
            )
        prepared_authorization = (
            decision.request.kind is DecisionKind.PERMISSION
            and decision.request.prompt.get("authorization_intent_fingerprint") is not None
        )

        if prepared_authorization and signal.allow and authorization_commit is None:
            raise DecisionConflict(
                "authorization_commit_required",
                "prepared-tool permission allow requires a fenced authorization commit",
            )
        if authorization_commit is not None and (
            decision.request.kind is not DecisionKind.PERMISSION or not signal.allow
        ):
            raise DecisionConflict(
                "unexpected_authorization_commit",
                "authorization commit is only valid for an allowed permission decision",
            )

        authorization_provenance: dict[str, object] | None = None
        if authorization_commit is not None:
            exact = authorization_commit.exact_request
            task_grant = authorization_commit.task_grant
            expected_intent = decision.request.prompt.get("authorization_intent_fingerprint")
            if not isinstance(expected_intent, str) or expected_intent != exact.fingerprint:
                raise DecisionConflict(
                    "authorization_intent_mismatch",
                    "authorization commit differs from the immutable decision intent",
                )
            root_run_id = run.context.root_run_id
            exact_bindings = (
                exact.root_run_id,
                exact.run_id,
                exact.call_id,
                exact.effect_id,
                exact.tool_name,
                exact.args_hash,
                exact.capability_hash,
                exact.scope_hash,
            )
            decision_bindings = (
                root_run_id,
                decision.run_id,
                decision.request.call_id,
                decision.request.effect_id,
                decision.request.tool_name,
                decision.request.args_hash,
                decision.request.capability_hash,
                decision.request.scope_hash,
            )
            if exact_bindings != decision_bindings:
                raise DecisionConflict(
                    "authorization_binding_mismatch",
                    "authorization commit differs from the durable decision fences",
                )
            authorization_provenance, grant_row = await self._authorize_decision_grant(
                actor,
                authorization_commit=authorization_commit,
                db=db,
                decision=decision,
                exact=exact,
                now=now,
                root_run_id=root_run_id,
                task_grant=task_grant,
            )
            if authorization_commit.authorization_origin == "explicit_decision":
                authorization_provenance.update(
                    {
                        "confirm_only_snapshot_ref": (
                            authorization_commit.confirm_only_snapshot_ref
                        ),
                        "confirm_only_snapshot_hash": (
                            authorization_commit.confirm_only_snapshot_hash
                        ),
                    }
                )

        return await self._record_decision_resolution(
            authorization_provenance, db=db, decision=decision, now=now, signal=signal
        )

    async def _authorize_decision_grant(
        self,
        actor: ActorContext,
        *,
        authorization_commit: PreparedAuthorizationCommit | None,
        db: aiosqlite.Connection,
        decision,
        exact,
        now: float,
        root_run_id,
        task_grant,
    ):
        if exact.expires_at > float(decision.request.expires_at or 0.0) or exact.expires_at <= now:
            raise DecisionConflict(
                "authorization_expiry_mismatch",
                "exact authorization expiry is outside the durable decision",
            )
        if (
            task_grant.root_run_id != root_run_id
            or actor.root_run_id not in {None, root_run_id}
            or task_grant.principal_id != actor.principal_id
        ):
            raise DecisionConflict(
                "authorization_principal_mismatch",
                "TaskGrant principal or root differs from the authenticated run",
            )
        policy_row = await (
            await db.execute(
                """SELECT mode,generation,updated_at
                    FROM authorization_policy_state WHERE singleton_id=1"""
            )
        ).fetchone()
        if policy_row is None:
            raise DecisionConflict(
                "authorization_policy_missing",
                "authorization policy singleton is absent",
            )
        policy_state = AuthorizationPolicyState(
            mode=str(policy_row["mode"]),  # type: ignore[arg-type]
            generation=int(policy_row["generation"]),
            updated_at=float(policy_row["updated_at"]),
        )
        if (
            policy_state.mode != authorization_commit.expected_policy_mode
            or policy_state.generation != authorization_commit.expected_policy_generation
        ):
            raise DecisionConflict(
                "authorization_policy_changed",
                "authorization policy changed after the decision was planned",
            )
        if authorization_commit.authorization_origin == "explicit_decision":
            expected_ref = decision.request.prompt.get("confirm_only_snapshot_ref")
            expected_hash = decision.request.prompt.get("confirm_only_snapshot_hash")
            if (
                authorization_commit.decision_id != decision.decision_id
                or authorization_commit.decision_nonce != decision.request.nonce
                or expected_ref != authorization_commit.confirm_only_snapshot_ref
                or expected_hash != authorization_commit.confirm_only_snapshot_hash
                or decision.request.prompt.get("authorization_origin") != "explicit_decision"
            ):
                raise DecisionConflict(
                    "explicit_authorization_fence_mismatch",
                    "explicit authorization differs from the durable confirm-only decision",
                )
        grant_row = await (
            await db.execute(
                "SELECT * FROM task_grants WHERE task_grant_id=?",
                (task_grant.task_grant_id,),
            )
        ).fetchone()
        grant_json = canonical_json(task_grant.to_dict())
        if authorization_commit.proposed_task_grant:
            expected_source = (
                "user"
                if authorization_commit.authorization_origin == "explicit_decision"
                else "policy:auto"
                if policy_state.mode == "auto"
                else "user"
            )
            if task_grant.source != expected_source:
                raise DecisionConflict(
                    "authorization_source_mismatch",
                    "proposed TaskGrant source differs from the active policy mode",
                )
            if grant_row is None:
                await db.execute(
                    """INSERT INTO task_grants(
                        task_grant_id,root_run_id,source,policy_generation,version,
                        grant_fingerprint,grant_json,status,created_at,revoked_at
                        ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
                    (
                        task_grant.task_grant_id,
                        task_grant.root_run_id,
                        task_grant.source,
                        task_grant.policy_generation,
                        task_grant.version,
                        task_grant.fingerprint,
                        grant_json,
                        now,
                    ),
                )
            elif (
                str(grant_row["status"]) != "active"
                or str(grant_row["grant_fingerprint"]) != task_grant.fingerprint
                or str(grant_row["grant_json"]) != grant_json
            ):
                raise DecisionConflict(
                    "task_grant_conflict",
                    "proposed TaskGrant identity belongs to another grant",
                )
        elif (
            grant_row is None
            or str(grant_row["status"]) != "active"
            or str(grant_row["grant_fingerprint"]) != task_grant.fingerprint
            or str(grant_row["grant_json"]) != grant_json
        ):
            raise DecisionConflict(
                "task_grant_not_active",
                "referenced TaskGrant is missing, revoked, or changed",
            )

        policy_decision = AuthorizationPolicy().evaluate(
            state=policy_state,
            context=DecisionContext(
                1,
                "permission",
                str(decision.request.domain_kind or ""),
                str(decision.request.domain_id or ""),
                root_run_id,
            ),
            task_grant=task_grant,
            exact_request=exact,
            actual_root_run_id=root_run_id,
            now=now,
        )
        if policy_decision.action != "allow" or policy_decision.exact_grant is None:
            raise DecisionConflict(
                "authorization_policy_rejected",
                f"authorization policy rejected commit: {policy_decision.reason}",
            )
        authorization_provenance = {
            "task_grant_id": task_grant.task_grant_id,
            "task_grant_version": task_grant.version,
            "authorization_source": task_grant.source,
            "authorization_origin": authorization_commit.authorization_origin,
            "policy_generation": policy_state.generation,
            "exact_grant_id": policy_decision.exact_grant.grant_id,
            "authorization_intent_fingerprint": exact.fingerprint,
        }
        return authorization_provenance, grant_row

    async def _record_decision_resolution(
        self,
        authorization_provenance: dict[str, object] | None,
        *,
        db: aiosqlite.Connection,
        decision,
        now: float,
        signal: DecisionSignal,
    ) -> tuple[DecisionRecord, DecisionAuthorization | None, DecisionConflict | None]:
        resolved_status = DecisionStatus.ALLOWED if signal.allow else DecisionStatus.DENIED
        response_payload = thaw_json(signal.response)
        if authorization_provenance is not None:
            response_payload = {
                **dict(response_payload),
                **authorization_provenance,
            }
        cursor = await db.execute(
            """UPDATE execution_decisions SET status=?,response_schema_version=?,
            response_json=?,decision_version=decision_version+1,resolved_at=?
            WHERE decision_id=? AND decision_version=? AND status='open'""",
            (
                resolved_status.value,
                signal.response_schema_version,
                canonical_json(response_payload),
                now,
                decision.decision_id,
                signal.expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise DecisionConflict("stale_decision_version", "decision changed before resolve CAS")
        self._fault("decision_resolve_after_cas")
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
                await db.execute("SELECT * FROM execution_grants WHERE grant_id=?", (grant_id,))
            ).fetchone()
            assert grant_row is not None
            authorization = self._row_to_authorization(grant_row)
            self._fault("decision_resolve_after_grant")
        resolved_row = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE decision_id=?",
                (decision.decision_id,),
            )
        ).fetchone()
        assert resolved_row is not None
        return self._row_to_decision(resolved_row), authorization, None

    async def commit_decision(
        self,
        signal: DecisionOpen | DecisionSignal,
        actor: ActorContext,
        *,
        expected_run_version: int | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        resumed_event: RunEventCandidate | None = None,
        next_decision: DecisionOpen | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> (
        tuple[DecisionRecord, DecisionAuthorization | None]
        | tuple[DecisionRecord, DecisionAuthorization | None, ContinuationRecord, RunEvent]
    ):
        """Open or resolve a decision, optionally with its resumed boundary."""

        advances_boundary = continuation_payload is not None or resumed_event is not None
        if isinstance(signal, DecisionOpen):
            if authorization_commit is not None:
                raise ValueError("authorization commit cannot accompany a decision open")
            if advances_boundary or expected_run_version is None or expected_run_version < 0:
                raise ValueError("decision open requires a non-negative run version")
            async with self._write_transaction() as db:
                now = float(self._clock())
                run_row = await (
                    await db.execute(
                        "SELECT * FROM execution_runs WHERE run_id=?", (signal.run_id,)
                    )
                ).fetchone()
                if run_row is None:
                    raise RunNotFound(
                        "run_not_found", f"execution run does not exist: {signal.run_id}"
                    )
                run = self._authorize_run_row(
                    run_row, expected_session_id=actor.session_id, actor=actor
                )
                existing = await (
                    await db.execute(
                        """SELECT * FROM execution_decisions
                    WHERE decision_id=? OR (run_id=? AND nonce=?)
                    ORDER BY CASE WHEN decision_id=? THEN 0 ELSE 1 END LIMIT 1""",
                        (signal.decision_id, signal.run_id, signal.nonce, signal.decision_id),
                    )
                ).fetchone()
                if existing is not None:
                    record = self._row_to_decision(existing)
                    self._assert_decision_intent(record, signal)
                    await db.commit()
                    return record, None
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
                cursor = await db.execute(
                    """UPDATE execution_runs SET status='waiting',version=version+1,updated_at=?
                    WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                    AND status IN ('created','queued','running','waiting')""",
                    (now, signal.run_id, expected_run_version),
                )
                if cursor.rowcount != 1:
                    raise DecisionConflict(
                        "run_not_signalable",
                        "cancelled or terminal runs cannot open decisions",
                    )
                await self._insert_continuation_decision_tx(db, signal, run=run_row, now=now)
                self._fault("decision_open_before_commit")
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_decisions WHERE decision_id=?",
                        (signal.decision_id,),
                    )
                ).fetchone()
                assert row is not None
                await db.commit()
                return self._row_to_decision(row), None
        if advances_boundary:
            if (
                expected_continuation_version is None
                or resumed_event is None
                or continuation_payload is None
            ):
                raise ValueError("decision boundary requires version, payload and resumed event")
            self._validate_continuation_version(expected_continuation_version)
            payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            now = float(self._clock())
            decision, authorization, expired = await self._resolve_decision_tx(
                db,
                signal,
                actor,
                now=now,
                authorization_commit=authorization_commit,
            )
            if expired is not None:
                await db.commit()
                raise expired
            if not advances_boundary:
                self._fault("decision_resolve_before_commit")
                await db.commit()
                return decision, authorization
            run = await self._continuation_run_tx(db, signal.run_id)
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=int(expected_continuation_version),
                payload_json=payload_json,
                decision=next_decision,
                now=now,
            )
            self._fault("decision_resolve_after_boundary")
            event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=resumed_event,
                deliveries=deliveries,
            )
            self._fault("decision_resolve_before_commit")
            await db.commit()
            return decision, authorization, continuation, event

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
        async with self._write_transaction() as db:
            run_row = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,))
            ).fetchone()
            if run_row is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {ref.run_id}")
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
            open_wait_rows = await (
                await db.execute(
                    """SELECT waits.*,attempts.provider_batch_id
                    FROM execution_task_external_waits AS waits
                    JOIN execution_attempt_records AS attempts
                      ON attempts.attempt_id=waits.attempt_id
                    WHERE attempts.run_id=? AND waits.state='open'
                    ORDER BY waits.created_at,waits.wait_ref""",
                    (ref.run_id,),
                )
            ).fetchall()
            for wait_row in open_wait_rows:
                wait_ref = str(wait_row["wait_ref"])
                attempt_id = str(wait_row["attempt_id"])
                provider_batch_id = str(wait_row["provider_batch_id"])
                wait_cursor = await db.execute(
                    """UPDATE execution_task_external_waits
                    SET state='cancelled',wait_version=wait_version+1,
                        updated_at=?,resolved_at=?
                    WHERE wait_ref=? AND state='open'
                      AND wait_version=?""",
                    (
                        now,
                        now,
                        wait_ref,
                        int(wait_row["wait_version"]),
                    ),
                )
                attempt_cursor = await db.execute(
                    """UPDATE execution_attempt_records
                    SET status='cancelled',budget_eligible=0,
                        attempt_version=attempt_version+1,updated_at=?,
                        ended_at=?
                    WHERE attempt_id=? AND status='waiting_external'""",
                    (now, now, attempt_id),
                )
                if wait_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                    raise DecisionConflict(
                        "external_wait_cancel_conflict",
                        "external wait set changed during run cancellation",
                    )
                await db.execute(
                    """UPDATE execution_provider_action_calls
                    SET admission_state='settled',
                        terminal_outcome_ref=(
                            'cancelled:' || ? || ':' || call_record_id
                        ),
                        call_version=call_version+1,updated_at=?
                    WHERE provider_batch_id=? AND admission_state IN (
                        'admitted','prepared','waiting_external'
                    )""",
                    (wait_ref, now, provider_batch_id),
                )
                await db.execute(
                    """UPDATE execution_provider_action_batches
                    SET pending_call_count=0,status='settled',
                        batch_version=batch_version+1,updated_at=?,settled_at=?
                    WHERE provider_batch_id=? AND status='waiting_external'""",
                    (now, now, provider_batch_id),
                )
                await db.execute(
                    """UPDATE execution_task_goals
                    SET status='cancelled',goal_version=goal_version+1,
                        updated_at=?,ended_at=?
                    WHERE root_run_id=? AND status='waiting_external'""",
                    (
                        now,
                        now,
                        str(wait_row["root_run_id"]),
                    ),
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
