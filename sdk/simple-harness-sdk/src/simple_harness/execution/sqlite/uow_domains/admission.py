# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Root start creation, admission, decisions and continuations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    AdmissionRecord,
    AdmissionState,
    ContinuationProgressResult,
    ContinuationRecord,
    DecisionRecord,
    DecisionState,
    ExecutionLease,
    FaultHook,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)

from ..audit_witness import record_claim_witness
from ...context_use_requirements import bind, require
from ..decision_terminal import append_decision_terminal
from .core import ExecutionUnitOfWorkCore
from .support import _continuation_progress_receipt, _fault, _object_json, _required, _thaw, _time


def _stage_pair(stage_id: str | None, stage_hash: str | None) -> None:
    if (stage_id is None) != (stage_hash is None):
        raise ValueError("context stage id/hash must be present together")
    if stage_id is not None:
        _required(stage_id, "context_stage_id")
        if len(stage_hash or "") != 64:
            raise ValueError("context_stage_hash must be a SHA-256 digest")


def _cursor_input_hash(turn_id: str, user_text: str) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "protocol": "simple-harness/legacy-turn-cursor/v1",
                "turn_id": turn_id,
                "user_text": user_text,
            }
        ).encode("utf-8")
    ).hexdigest()


def _supersede_legacy_cursor(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    continuation_id: str,
    payload: Mapping[str, JsonValue],
    context_stage_id: str | None,
    now: float,
    fault: FaultHook | None,
) -> None:
    cursor = connection.execute(
        "SELECT * FROM legacy_turn_cursors WHERE run_id=? AND state='active'", (run_id,)
    ).fetchone()
    if cursor is None:
        return
    conversation = payload.get("conversation")
    if not isinstance(conversation, Mapping):
        raise UnitOfWorkConflict("migrated continuation lacks conversation input")
    user_text = conversation.get("memory_text")
    if not isinstance(user_text, str) or not user_text.strip() or "\x00" in user_text:
        raise UnitOfWorkConflict("migrated continuation lacks textual Memory input")
    if context_stage_id is None:
        raise UnitOfWorkConflict("migrated continuation lacks Context stage")
    stage = connection.execute(
        "SELECT memory_write_fence,turn_started_at FROM context_preparation_staging "
        "WHERE stage_id=? AND state='staged'",
        (context_stage_id,),
    ).fetchone()
    if stage is None or stage["turn_started_at"] is None:
        raise UnitOfWorkConflict("migrated continuation lacks durable recall lineage")
    source_key = str(cursor["source_key"])
    _fault(fault, "continuation_enqueue.legacy_disposition.before_write")
    if str(cursor["source_namespace"]) == "legacy-source":
        changed = connection.execute(
            "UPDATE legacy_memory_dispositions SET disposition='suppress_tentative',updated_at=? "
            "WHERE source_key=? AND disposition='deferred_turn'",
            (now, source_key),
        ).rowcount
    else:
        changed = connection.execute(
            "INSERT INTO legacy_memory_dispositions(source_key,source_namespace,source_event_id,"
            "run_id,turn_id,disposition,payload_hash,canonical_turn_json,canonical_turn_hash,"
            "causal_terminal_event_id,causal_continuation_id,causal_claim_epoch,"
            "created_at,updated_at) "
            "VALUES(?,'turn-input',NULL,?,?,'suppress_tentative',?,NULL,NULL,NULL,NULL,NULL,?,?)",
            (
                source_key,
                run_id,
                str(cursor["turn_id"]),
                str(cursor["input_hash"]),
                now,
                now,
            ),
        ).rowcount
    if changed != 1:
        raise UnitOfWorkConflict("legacy cursor disposition CAS failed")
    _fault(fault, "continuation_enqueue.legacy_disposition.after_write")
    version = int(cursor["cursor_version"])
    _fault(fault, "continuation_enqueue.legacy_cursor.before_write")
    changed = connection.execute(
        "UPDATE legacy_turn_cursors SET cursor_version=cursor_version+1,source_key=?,"
        "source_namespace='turn-input',source_event_id=NULL,turn_id=?,user_text=?,input_hash=?,"
        "write_fence=?,turn_started_at=?,updated_at=? WHERE run_id=? AND state='active' "
        "AND cursor_version=? AND source_key=?",
        (
            f"turn-input:{continuation_id}",
            continuation_id,
            user_text,
            _cursor_input_hash(continuation_id, user_text),
            None if stage["memory_write_fence"] is None else str(stage["memory_write_fence"]),
            float(stage["turn_started_at"]),
            now,
            run_id,
            version,
            source_key,
        ),
    ).rowcount
    if changed != 1:
        raise UnitOfWorkConflict("legacy cursor supersession CAS failed")
    _fault(fault, "continuation_enqueue.legacy_cursor.after_write")


def _consume_context_stage(
    connection: sqlite3.Connection,
    *,
    stage_id: str,
    stage_hash: str,
    kind: str,
    expected_snapshot: JsonValue | None,
    consumed_run_id: str | None,
    consumed_continuation_id: str | None,
    now: float,
) -> None:
    row = connection.execute(
        "SELECT kind,state,private_snapshot,private_snapshot_hash FROM "
        "context_preparation_staging WHERE stage_id=?",
        (stage_id,),
    ).fetchone()
    if row is None or str(row["kind"]) != kind or str(row["state"]) != "staged":
        raise UnitOfWorkConflict("context stage is not available for this command")
    raw = bytes(row["private_snapshot"])
    if str(row["private_snapshot_hash"]) != stage_hash:
        raise UnitOfWorkConflict("context stage hash differs")
    if hashlib.sha256(raw).hexdigest() != stage_hash:
        raise UnitOfWorkConflict("context stage bytes are corrupt")
    if not isinstance(expected_snapshot, Mapping) or (
        canonical_json(expected_snapshot).encode("utf-8") != raw
    ):
        raise UnitOfWorkConflict("command private context differs from durable stage")
    changed = connection.execute(
        "UPDATE context_preparation_staging SET state='consumed',private_snapshot=NULL,"
        "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,consumed_run_id=?,"
        "consumed_continuation_id=?,updated_at=? WHERE stage_id=? AND state='staged' "
        "AND private_snapshot_hash=?",
        (
            consumed_run_id,
            consumed_continuation_id,
            now,
            stage_id,
            stage_hash,
        ),
    ).rowcount
    if changed != 1:
        raise UnitOfWorkConflict("context stage consume CAS failed")


def _verify_stage_replay(
    connection: sqlite3.Connection,
    *,
    stage_id: str | None,
    stage_hash: str | None,
    consumed_run_id: str | None,
    consumed_continuation_id: str | None,
) -> None:
    _stage_pair(stage_id, stage_hash)
    if stage_id is None:
        return
    row = connection.execute(
        "SELECT state,private_snapshot_hash,consumed_run_id,consumed_continuation_id "
        "FROM context_preparation_staging WHERE stage_id=?",
        (stage_id,),
    ).fetchone()
    expected = ("consumed", stage_hash, consumed_run_id, consumed_continuation_id)
    if row is None or tuple(row) != expected:
        raise UnitOfWorkConflict("context stage replay differs")


class StartAdmissionMixin(ExecutionUnitOfWorkCore):
    """Root start creation, admission, decisions and continuations."""

    __slots__ = ()

    def _create_start_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        execution_session_id: str,
        run_id: str,
        request_id: str,
        profile_key: str,
        driver_kind: str,
        snapshot: Mapping[str, JsonValue],
        snapshot_json: str,
        snapshot_hash: str,
        event_id: str,
        now: float,
        user_id: str,
        context_stage_id: str | None,
        context_stage_hash: str | None,
        fault: FaultHook | None,
    ) -> None:
        _fault(fault, "root_start.session.before_write")
        connection.execute(
            "INSERT OR IGNORE INTO execution_users(user_id,created_at) VALUES(?,?)",
            (user_id, now),
        )
        owner = connection.execute(
            "SELECT user_id FROM execution_sessions WHERE session_id=?",
            (execution_session_id,),
        ).fetchone()
        if owner is not None and str(owner[0]) != user_id:
            raise UnitOfWorkConflict("execution session belongs to another user")
        connection.execute(
            "INSERT OR IGNORE INTO execution_sessions(session_id,user_id,created_at) VALUES(?,?,?)",
            (execution_session_id, user_id, now),
        )
        _fault(fault, "root_start.session.after_write")
        _fault(fault, "root_start.run.before_write")
        connection.execute(
            """
            INSERT INTO runs(
                run_id,execution_session_id,request_id,root_run_id,parent_run_id,
                profile_key,driver_kind,state,version,created_at,updated_at
            ) VALUES (?,?,?,?,NULL,?,?,'created',0,?,?)
            """,
            (
                run_id,
                execution_session_id,
                request_id,
                run_id,
                profile_key,
                driver_kind,
                now,
                now,
            ),
        )
        _fault(fault, "root_start.run.after_write")
        _fault(fault, "root_start.snapshot.before_write")
        connection.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json,snapshot_hash,created_at) "
            "VALUES (?,?,?,?)",
            (run_id, snapshot_json, snapshot_hash, now),
        )

        bind(connection, run_id, self._context_use_scope, "run_start", run_id, snapshot_hash)
        _fault(fault, "root_start.snapshot.after_write")
        if context_stage_id is not None and context_stage_hash is not None:
            _consume_context_stage(
                connection,
                stage_id=context_stage_id,
                stage_hash=context_stage_hash,
                kind="root",
                expected_snapshot=snapshot.get("prepared_context"),
                consumed_run_id=run_id,
                consumed_continuation_id=None,
                now=now,
            )
        _fault(fault, "root_start.event.before_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=run_id,
            kind="run.created",
            payload={"profile_key": profile_key, "driver_kind": driver_kind},
            now=now,
        )
        _fault(fault, "root_start.event.after_write")

    def create_with_start_snapshot(
        self,
        *,
        execution_session_id: str,
        run_id: str,
        request_id: str,
        profile_key: str,
        driver_kind: str,
        snapshot: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        user_id: str = "harness-system",
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> RunRecord:
        execution_session_id = _required(execution_session_id, "execution_session_id")
        run_id = _required(run_id, "run_id")
        request_id = _required(request_id, "request_id")
        profile_key = _required(profile_key, "profile_key")
        driver_kind = _required(driver_kind, "driver_kind")
        event_id = _required(event_id, "event_id")
        user_id = _required(user_id, "user_id")
        _stage_pair(context_stage_id, context_stage_hash)
        now = _time(now)
        snapshot_json = _object_json(snapshot, "snapshot")
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        existing = self._run_by_request(execution_session_id, request_id)
        if existing is not None:
            require(self.database.connection, existing.run_id, self._context_use_scope)
            self._verify_existing_start(
                existing,
                run_id=run_id,
                profile_key=profile_key,
                driver_kind=driver_kind,
                snapshot_hash=snapshot_hash,
            )
            owner = self.database.connection.execute(
                "SELECT user_id FROM execution_sessions WHERE session_id=?",
                (execution_session_id,),
            ).fetchone()
            if owner is None or str(owner[0]) != user_id:
                raise UnitOfWorkConflict("execution session belongs to another user")
            _verify_stage_replay(
                self.database.connection,
                stage_id=context_stage_id,
                stage_hash=context_stage_hash,
                consumed_run_id=run_id,
                consumed_continuation_id=None,
            )
            return existing
        with self.database.transaction() as connection:
            self._create_start_on_connection(
                connection,
                execution_session_id=execution_session_id,
                run_id=run_id,
                request_id=request_id,
                profile_key=profile_key,
                driver_kind=driver_kind,
                snapshot=snapshot,
                snapshot_json=snapshot_json,
                snapshot_hash=snapshot_hash,
                event_id=event_id,
                now=now,
                user_id=user_id,
                context_stage_id=context_stage_id,
                context_stage_hash=context_stage_hash,
                fault=fault,
            )
        _fault(fault, "root_start.after_commit")
        record = self.read_run(run_id)
        assert record is not None
        return record

    def start_admission(
        self,
        *,
        admission_id: str,
        run_id: str,
        prompt: Mapping[str, JsonValue],
        expires_at: float | None,
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> AdmissionRecord:
        admission_id = _required(admission_id, "admission_id")
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        if expires_at is not None:
            expires_at = _time(expires_at, "expires_at")
        prompt_json = _object_json(prompt, "prompt")
        existing = self.read_admission(admission_id)
        if existing is not None:
            if (
                existing.run_id != run_id
                or canonical_json(json.loads(prompt_json)) != canonical_json(_thaw(existing.prompt))
                or existing.expires_at != expires_at
            ):
                raise UnitOfWorkConflict("admission identity reused with different intent")
            return existing
        with self.database.transaction() as connection:
            _fault(fault, "admission_start.run.before_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = 'admission_pending', version = version + 1, updated_at = ?
                WHERE run_id = ? AND state = 'created'
                """,
                (now, run_id),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("run is not eligible for admission")
            _fault(fault, "admission_start.run.after_write")
            _fault(fault, "admission_start.admission.before_write")
            connection.execute(
                """
                INSERT INTO run_admissions(
                    admission_id, run_id, state, prompt_json, response_json,
                    expires_at, version, created_at, resolved_at
                ) VALUES (?, ?, 'pending', ?, NULL, ?, 0, ?, NULL)
                """,
                (admission_id, run_id, prompt_json, expires_at, now),
            )
            _fault(fault, "admission_start.admission.after_write")
            _fault(fault, "admission_start.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind="admission.started",
                payload={"admission_id": admission_id},
                now=now,
            )
            _fault(fault, "admission_start.event.after_write")
        _fault(fault, "admission_start.after_commit")
        result = self.read_admission(admission_id)
        assert result is not None
        return result

    def resolve_admission(
        self,
        *,
        admission_id: str,
        state: AdmissionState,
        response: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> AdmissionRecord:
        admission_id = _required(admission_id, "admission_id")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        state = AdmissionState(state)
        if state is AdmissionState.PENDING:
            raise ValueError("resolve_admission requires a terminal admission state")
        response_json = _object_json(response, "response")
        existing = self.read_admission(admission_id)
        if existing is None:
            raise UnitOfWorkNotFound(admission_id)
        if existing.state is not AdmissionState.PENDING:
            if (
                existing.state is state
                and canonical_json(_thaw(existing.response)) == response_json
            ):
                return existing
            raise UnitOfWorkConflict("admission already resolved differently")
        run_state = {
            AdmissionState.ALLOWED: RunState.QUEUED,
            AdmissionState.DENIED: RunState.FAILED,
            AdmissionState.EXPIRED: RunState.FAILED,
            AdmissionState.CANCELLED: RunState.CANCELLED,
        }[state]
        with self.database.transaction() as connection:
            _fault(fault, "admission_resolve.admission.before_write")
            changed = connection.execute(
                """
                UPDATE run_admissions
                SET state = ?, response_json = ?, version = version + 1, resolved_at = ?
                WHERE admission_id = ? AND state = 'pending' AND version = ?
                """,
                (state.value, response_json, now, admission_id, existing.version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("admission CAS failed")
            _fault(fault, "admission_resolve.admission.after_write")
            _fault(fault, "admission_resolve.run.before_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND state = 'admission_pending'
                """,
                (run_state.value, now, existing.run_id),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("run admission state CAS failed")
            _fault(fault, "admission_resolve.run.after_write")
            _fault(fault, "admission_resolve.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=existing.run_id,
                kind=f"admission.{state.value}",
                payload={"admission_id": admission_id},
                now=now,
            )
            _fault(fault, "admission_resolve.event.after_write")
        _fault(fault, "admission_resolve.after_commit")
        result = self.read_admission(admission_id)
        assert result is not None
        return result

    def commit_decision(
        self,
        *,
        decision_id: str,
        run_id: str,
        kind: str,
        state: DecisionState,
        request: Mapping[str, JsonValue],
        response: Mapping[str, JsonValue] | None,
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> DecisionRecord:
        decision_id = _required(decision_id, "decision_id")
        run_id = _required(run_id, "run_id")
        kind = _required(kind, "kind")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        state = DecisionState(state)
        request_json = _object_json(request, "request")
        response_json = None if response is None else _object_json(response, "response")
        if (state is DecisionState.OPEN) != (response is None):
            raise ValueError("open decision must omit response; resolved decision requires it")
        existing = self.read_decision(decision_id)
        if existing is not None:
            if (
                existing.run_id == run_id
                and existing.kind == kind
                and existing.state is state
                and canonical_json(_thaw(existing.request)) == request_json
                and (
                    (existing.response is None and response_json is None)
                    or canonical_json(_thaw(existing.response)) == response_json
                )
            ):
                return existing
            if (
                existing.run_id == run_id
                and existing.kind == kind
                and existing.state is DecisionState.OPEN
                and state is not DecisionState.OPEN
                and canonical_json(_thaw(existing.request)) == request_json
                and response_json is not None
            ):
                run_state = {
                    DecisionState.ALLOWED: RunState.QUEUED,
                    DecisionState.DENIED: RunState.FAILED,
                    DecisionState.EXPIRED: RunState.FAILED,
                    DecisionState.CANCELLED: RunState.CANCELLED,
                }[state]
                with self.database.transaction() as connection:
                    _fault(fault, "decision_resolve.decision.before_write")
                    changed = connection.execute(
                        """
                        UPDATE decisions
                        SET state=?,response_json=?,version=version+1,resolved_at=?
                        WHERE decision_id=? AND run_id=? AND state='open' AND version=?
                        """,
                        (
                            state.value,
                            response_json,
                            now,
                            decision_id,
                            run_id,
                            existing.version,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise UnitOfWorkConflict("decision resolution CAS failed")
                    _fault(fault, "decision_resolve.decision.after_write")
                    changed = connection.execute(
                        """
                        UPDATE runs SET state=?,version=version+1,updated_at=?
                        WHERE run_id=? AND state='waiting'
                        """,
                        (run_state.value, now, run_id),
                    ).rowcount
                    if changed != 1:
                        raise UnitOfWorkConflict("decision Run resolution CAS failed")
                    _fault(fault, "decision_resolve.run.after_write")
                    self._insert_event(
                        connection,
                        event_id=event_id,
                        run_id=run_id,
                        kind=f"decision.{state.value}",
                        payload={"decision_id": decision_id, "kind": kind},
                        now=now,
                    )
                    _fault(fault, "decision_resolve.event.after_write")

                    append_decision_terminal(
                        self,
                        connection,
                        run_id=run_id,
                        decision_id=decision_id,
                        event_id=event_id,
                        now=now,
                    )
                    _fault(fault, "decision_terminal.after_write")
                _fault(fault, "decision_resolve.after_commit")
                result = self.read_decision(decision_id)
                assert result is not None
                return result
            raise UnitOfWorkConflict("decision identity reused with different intent")
        run_state = {
            DecisionState.OPEN: RunState.WAITING,
            DecisionState.ALLOWED: RunState.RUNNING,
            DecisionState.DENIED: RunState.FAILED,
            DecisionState.EXPIRED: RunState.FAILED,
            DecisionState.CANCELLED: RunState.CANCELLED,
        }[state]
        with self.database.transaction() as connection:
            _fault(fault, "decision.decision.before_write")
            connection.execute(
                """
                INSERT INTO decisions(
                    decision_id, run_id, kind, state, request_json, response_json,
                    version, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    decision_id,
                    run_id,
                    kind,
                    state.value,
                    request_json,
                    response_json,
                    now,
                    None if state is DecisionState.OPEN else now,
                ),
            )
            _fault(fault, "decision.decision.after_write")
            _fault(fault, "decision.run.before_write")
            changed = connection.execute(
                "UPDATE runs SET state = ?, version = version + 1, updated_at = ? WHERE run_id = ?",
                (run_state.value, now, run_id),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkNotFound(run_id)
            _fault(fault, "decision.run.after_write")
            _fault(fault, "decision.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind=f"decision.{state.value}",
                payload={"decision_id": decision_id, "kind": kind},
                now=now,
            )
            _fault(fault, "decision.event.after_write")

            append_decision_terminal(
                self, connection, run_id=run_id, decision_id=decision_id, event_id=event_id, now=now
            )
            _fault(fault, "decision_terminal.after_write")
        _fault(fault, "decision.after_commit")
        result = self.read_decision(decision_id)
        assert result is not None
        return result

    def _enqueue_continuation_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        continuation_id: str,
        run_id: str,
        payload: Mapping[str, JsonValue],
        payload_json: str,
        now: float,
        context_stage_id: str | None,
        context_stage_hash: str | None,
        fault: FaultHook | None,
    ) -> None:
        run_row = connection.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run_row is None:
            raise UnitOfWorkNotFound(run_id)
        if str(run_row["state"]) in {"completed", "failed", "cancelled"}:
            raise UnitOfWorkConflict("terminal Run rejects new continuations")
        row = connection.execute(
            "SELECT COALESCE(MAX(fifo_seq),0)+1 FROM continuations WHERE run_id=?",
            (run_id,),
        ).fetchone()
        sequence = int(row[0])
        _fault(fault, "continuation_enqueue.continuation.before_write")
        connection.execute(
            """
            INSERT INTO continuations(
                continuation_id,run_id,fifo_seq,payload_json,state,version,claimed_by,
                runtime_lease_epoch,claim_epoch,ack_receipt_id,created_at,claimed_at,acked_at,
                context_stage_id,context_stage_hash
            ) VALUES (?, ?, ?, ?, 'pending', 0, NULL, NULL, 0, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                continuation_id,
                run_id,
                sequence,
                payload_json,
                now,
                context_stage_id,
                context_stage_hash,
            ),
        )
        _fault(fault, "continuation_enqueue.continuation.after_write")
        _supersede_legacy_cursor(
            connection,
            run_id=run_id,
            continuation_id=continuation_id,
            payload=payload,
            context_stage_id=context_stage_id,
            now=now,
            fault=fault,
        )
        if context_stage_id is not None and context_stage_hash is not None:
            _consume_context_stage(
                connection,
                stage_id=context_stage_id,
                stage_hash=context_stage_hash,
                kind="continuation",
                expected_snapshot=payload.get("prepared_context"),
                consumed_run_id=None,
                consumed_continuation_id=continuation_id,
                now=now,
            )

    def enqueue_continuation(
        self,
        *,
        continuation_id: str,
        run_id: str,
        payload: Mapping[str, JsonValue],
        now: float,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> ContinuationRecord:
        continuation_id = _required(continuation_id, "continuation_id")
        run_id = _required(run_id, "run_id")
        now = _time(now)
        payload_json = _object_json(payload, "payload")
        _stage_pair(context_stage_id, context_stage_hash)
        existing = self.read_continuation(continuation_id)
        if existing is not None:
            if (
                existing.run_id == run_id
                and canonical_json(_thaw(existing.payload)) == payload_json
            ):
                _verify_stage_replay(
                    self.database.connection,
                    stage_id=context_stage_id,
                    stage_hash=context_stage_hash,
                    consumed_run_id=None,
                    consumed_continuation_id=continuation_id,
                )
                return existing
            raise UnitOfWorkConflict("continuation identity reused with different payload")
        with self.database.transaction() as connection:
            self._enqueue_continuation_on_connection(
                connection,
                continuation_id=continuation_id,
                run_id=run_id,
                payload=payload,
                payload_json=payload_json,
                now=now,
                context_stage_id=context_stage_id,
                context_stage_hash=context_stage_hash,
                fault=fault,
            )
        _fault(fault, "continuation_enqueue.after_commit")
        result = self.read_continuation(continuation_id)
        assert result is not None
        return result

    def claim_continuation(
        self,
        *,
        run_id: str,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> ContinuationRecord | None:
        run_id = _required(run_id, "run_id")
        now = _time(now)
        if execution_lease.run_id != run_id or execution_lease.namespace != RUNTIME_LEASE_NAMESPACE:
            raise UnitOfWorkConflict("continuation requires canonical Runtime lease")
        claimed_id: str | None = None
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            run_row = connection.execute(
                "SELECT state FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                raise UnitOfWorkNotFound(run_id)
            if str(run_row["state"]) in {"completed", "failed", "cancelled"}:
                connection.execute(
                    "UPDATE continuations SET state='quarantined', acked_at=?, "
                    "version=version+1 WHERE run_id=? AND state IN ('pending','claimed')",
                    (now, run_id),
                )
                return None
            row = connection.execute(
                """
                SELECT * FROM continuations
                WHERE run_id = ? AND state IN ('pending','claimed')
                ORDER BY fifo_seq ASC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row is not None:
                claimed_id = str(row["continuation_id"])
                if str(row["state"]) == "claimed":
                    lease_row = connection.execute(
                        "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                        "WHERE run_id=? AND namespace=?",
                        (run_id, RUNTIME_LEASE_NAMESPACE),
                    ).fetchone()
                    if (
                        lease_row is not None
                        and str(lease_row["owner_id"]) == str(row["claimed_by"])
                        and int(lease_row["epoch"]) == int(row["runtime_lease_epoch"])
                        and float(lease_row["expires_at"]) > now
                    ):
                        return None
                _fault(fault, "continuation_claim.continuation.before_write")
                changed = connection.execute(
                    """
                    UPDATE continuations
                    SET state = 'claimed', version = version + 1,
                        claimed_by = ?, runtime_lease_epoch = ?, claimed_at = ?,
                        claim_epoch = claim_epoch + 1
                    WHERE continuation_id = ? AND state IN ('pending','claimed')
                      AND version = ?
                    """,
                    (
                        execution_lease.owner_id,
                        execution_lease.epoch,
                        now,
                        claimed_id,
                        int(row["version"]),
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("continuation claim CAS failed")

                record_claim_witness(connection, "continuations", claimed_id, now=now)
                _fault(fault, "continuation_claim.continuation.after_write")
                spawn_wait = connection.execute(
                    "SELECT * FROM workflow_spawn_child_wait_receipts "
                    "WHERE continuation_id=? AND state='woken'",
                    (claimed_id,),
                ).fetchone()
                if spawn_wait is not None:
                    next_wait_version = int(spawn_wait["version"]) + 1
                    lifecycle_hash = hashlib.sha256(
                        canonical_json(
                            {
                                "identity_hash": str(spawn_wait["identity_hash"]),
                                "state": "claimed",
                                "version": next_wait_version,
                                "child_signal_id": spawn_wait["child_signal_id"],
                                "continuation_id": claimed_id,
                                "claim_owner": execution_lease.owner_id,
                                "runtime_lease_epoch": execution_lease.epoch,
                            }
                        ).encode()
                    ).hexdigest()
                    _fault(fault, "continuation_claim.spawn_wait.before_write")
                    changed = connection.execute(
                        """
                        UPDATE workflow_spawn_child_wait_receipts
                        SET state='claimed',version=?,lifecycle_hash=?
                        WHERE parent_wait_receipt_id=? AND state='woken'
                          AND version=?
                        """,
                        (
                            next_wait_version,
                            lifecycle_hash,
                            str(spawn_wait["parent_wait_receipt_id"]),
                            int(spawn_wait["version"]),
                        ),
                    ).rowcount
                    if changed != 1:
                        raise UnitOfWorkConflict("workflow spawn child-wait claim CAS failed")
                    _fault(fault, "continuation_claim.spawn_wait.after_write")
        _fault(fault, "continuation_claim.after_commit")
        return None if claimed_id is None else self.read_continuation(claimed_id)

    def commit_runtime_state_and_ack_continuation(
        self,
        *,
        run_id: str,
        expected_version: int,
        state: RunState,
        event_id: str,
        payload: Mapping[str, JsonValue],
        continuation_claim: ContinuationRecord,
        execution_lease: ExecutionLease,
        receipt_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> ContinuationProgressResult:
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        receipt_id = _required(receipt_id, "receipt_id")
        state = RunState(state)
        if state is not RunState.WAITING:
            raise ValueError("continuation progress command requires WAITING")
        now = _time(now)
        payload_json = _object_json(payload, "payload")
        outcome_json = canonical_json(
            {
                "run_id": run_id,
                "expected_version": expected_version,
                "state": state.value,
                "event_id": event_id,
                "payload": json.loads(payload_json),
            }
        )
        outcome_hash = hashlib.sha256(outcome_json.encode()).hexdigest()
        existing_receipt = self._read_continuation_progress_receipt(receipt_id)
        if existing_receipt is not None:
            return self._replay_continuation_progress(
                existing_receipt,
                continuation_claim=continuation_claim,
                execution_lease=execution_lease,
                outcome_hash=outcome_hash,
            )
        with self.database.transaction() as connection:
            receipt_row = connection.execute(
                "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if receipt_row is not None:
                return self._replay_continuation_progress(
                    _continuation_progress_receipt(receipt_row),
                    continuation_claim=continuation_claim,
                    execution_lease=execution_lease,
                    outcome_hash=outcome_hash,
                )
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_continuation_claim(connection, continuation_claim, execution_lease)
            _fault(fault, "continuation_progress.receipt.before_write")
            connection.execute(
                "INSERT INTO continuation_progress_receipts("
                "receipt_id,continuation_id,run_id,owner_id,runtime_lease_epoch,"
                "claim_epoch,outcome_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    continuation_claim.continuation_id,
                    run_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    outcome_hash,
                    now,
                ),
            )
            _fault(fault, "continuation_progress.receipt.after_write")
            _fault(fault, "continuation_progress.continuation.before_write")
            changed = connection.execute(
                "UPDATE continuations SET state='acked',acked_at=?,ack_receipt_id=?,"
                "version=version+1 WHERE continuation_id=? AND state='claimed' "
                "AND claimed_by=? AND runtime_lease_epoch=? AND claim_epoch=? AND version=?",
                (
                    now,
                    receipt_id,
                    continuation_claim.continuation_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    continuation_claim.version,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("continuation progress claim CAS failed")
            _fault(fault, "continuation_progress.continuation.after_write")
            _fault(fault, "continuation_progress.run.before_write")
            changed = connection.execute(
                "UPDATE runs SET state='waiting',version=version+1,updated_at=? "
                "WHERE run_id=? AND version=? AND state NOT IN ('completed','failed','cancelled')",
                (now, run_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("continuation progress Run CAS failed")
            _fault(fault, "continuation_progress.run.after_write")
            _fault(fault, "continuation_progress.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind="run.waiting",
                payload=dict(payload),
                now=now,
            )
            _fault(fault, "continuation_progress.event.after_write")
        _fault(fault, "continuation_progress.after_commit")
        receipt = self._read_continuation_progress_receipt(receipt_id)
        run = self.read_run(run_id)
        continuation = self.read_continuation(continuation_claim.continuation_id)
        assert receipt is not None and run is not None and continuation is not None
        return ContinuationProgressResult(run, continuation, receipt)
