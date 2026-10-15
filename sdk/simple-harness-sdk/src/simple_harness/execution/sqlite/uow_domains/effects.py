# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Tool effect preparation, hand-off, settlement and reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import (
    CallId,
    EffectId,
    FrozenJsonValue,
    JsonValue,
    RunId,
    canonical_json,
    thaw_json,
)
from simple_harness.execution.effects import EffectRecord, EffectState, TaskExecutionEnvelope
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.recovery import (
    ReconciliationResolution,
    RecoveryKind,
    ResolutionOutcome,
    recovery_identity,
)
from simple_harness.execution.uow import (
    ExecutionLease,
    FaultHook,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.tools.contracts import ToolResult
from simple_harness.workflow.lease import WorkflowLease

from .core import ExecutionUnitOfWorkCore
from .support import (
    _effect_record,
    _fault,
    _reconciliation_resolution,
    _required,
    _time,
    _tool_result_json,
)


class EffectMixin(ExecutionUnitOfWorkCore):
    """Tool effect preparation, hand-off, settlement and reconciliation."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _audit_operation_head(self, connection, kind, identity, now): ...
        @staticmethod
        def _require_workflow_handoff_authority(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            execution_lease: ExecutionLease,
            workflow_lease: WorkflowLease | None,
            now: float,
        ) -> None: ...

    def prepare_effect(
        self,
        *,
        effect_id: EffectId,
        run_id: RunId,
        call_id: CallId,
        tool_name: str,
        arguments: dict[str, object],
        request_hash: str,
        authorization_receipt_ref: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        raw_call_id: str | None = None,
        turn_ordinal: int = 0,
        call_ordinal: int = 0,
        task_execution_envelope: TaskExecutionEnvelope | None = None,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        tool_name = _required(tool_name, "tool_name")
        authorization_receipt_ref = _required(
            authorization_receipt_ref, "authorization_receipt_ref"
        )
        if len(request_hash) != 64 or any(
            character not in "0123456789abcdef" for character in request_hash
        ):
            raise ValueError("request_hash must be lowercase SHA-256")
        if (
            run_fence.run_id != run_id
            or run_fence.owner_id != execution_lease.owner_id
            or execution_lease.run_id != run_id.value
        ):
            raise UnitOfWorkConflict("effect runtime lease and Run fence differ")
        now = _time(now)
        if raw_call_id is not None:
            raw_call_id = _required(raw_call_id, "raw_call_id")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (turn_ordinal, call_ordinal)
        ):
            raise ValueError("effect ordinals must be non-negative integers")
        if task_execution_envelope is not None:
            if not isinstance(task_execution_envelope, TaskExecutionEnvelope):
                raise TypeError("task_execution_envelope must use TaskExecutionEnvelope")
            if (
                task_execution_envelope.run_id != run_id
                or task_execution_envelope.call_id != call_id
                or task_execution_envelope.effect_id != effect_id
                or task_execution_envelope.raw_call_id != raw_call_id
                or task_execution_envelope.turn_ordinal != turn_ordinal
                or task_execution_envelope.call_ordinal != call_ordinal
                or task_execution_envelope.tool_name != tool_name
                or task_execution_envelope.idempotency_key != effect_id.value
            ):
                raise ValueError("TaskExecutionEnvelope differs from effect identity")
        arguments_json = canonical_json(arguments)  # type: ignore[arg-type]
        envelope_json = (
            None
            if task_execution_envelope is None
            else canonical_json(task_execution_envelope.to_json())
        )
        envelope_hash = (
            None if task_execution_envelope is None else task_execution_envelope.envelope_hash
        )
        existing = self.read_effect(effect_id)
        if existing is not None:
            if (
                existing.run_id != run_id
                or existing.call_id != call_id
                or existing.tool_name != tool_name
                or existing.request_hash != request_hash
                or canonical_json(thaw_json(existing.arguments)) != arguments_json
                or existing.raw_call_id != raw_call_id
                or existing.turn_ordinal != turn_ordinal
                or existing.call_ordinal != call_ordinal
                or existing.task_execution_envelope != task_execution_envelope
            ):
                raise UnitOfWorkConflict("effect identity reused with different intent")
            if existing.state is EffectState.PREPARED and (
                existing.fence_epoch != run_fence.epoch
                or existing.authorization_receipt_ref != authorization_receipt_ref
            ):
                raise UnitOfWorkConflict("prepared effect authority must use explicit refresh CAS")
            return existing
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(
                connection,
                run_fence,
                execution_lease=execution_lease,
            )
            conflict = connection.execute(
                "SELECT effect_id FROM execution_effects WHERE run_id = ? AND call_id = ?",
                (run_id.value, call_id.value),
            ).fetchone()
            if conflict is not None:
                raise UnitOfWorkConflict("call_id is already bound to another effect")
            _fault(fault, "effect_prepare.before_write")
            connection.execute(
                """
                INSERT INTO execution_effects(
                    effect_id, run_id, call_id, raw_call_id, turn_ordinal,
                    call_ordinal, tool_name, arguments_json,
                    request_hash, authorization_receipt_ref, handoff_receipt_ref,
                    evidence_ref, fence_epoch, state, result_json, prepared_at,
                    handed_off_at, settled_at, version
                    , task_execution_envelope_json, task_execution_envelope_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 'prepared',
                          NULL, ?, NULL, NULL, 0, ?, ?)
                """,
                (
                    effect_id.value,
                    run_id.value,
                    call_id.value,
                    raw_call_id,
                    turn_ordinal,
                    call_ordinal,
                    tool_name,
                    arguments_json,
                    request_hash,
                    authorization_receipt_ref,
                    run_fence.epoch,
                    now,
                    envelope_json,
                    envelope_hash,
                ),
            )
            _fault(fault, "effect_prepare.after_write")
            self._audit_operation_head(connection, "effect", effect_id.value, now)
        _fault(fault, "effect_prepare.after_commit")
        record = self.read_effect(effect_id)
        assert record is not None
        return record

    def list_effects_for_run(self, run_id: str) -> tuple[EffectRecord, ...]:
        """Complete effect inventory; settlement readers must not filter UNKNOWN only."""
        rows = self.database.connection.execute(
            "SELECT * FROM execution_effects WHERE run_id = ? "
            "ORDER BY turn_ordinal, call_ordinal, effect_id",
            (_required(run_id, "run_id"),),
        ).fetchall()
        return tuple(_effect_record(row) for row in rows)

    def list_unknown_effects_for_run(self, run_id: str) -> tuple[EffectRecord, ...]:
        """Effects of one Run whose outcome is still UNKNOWN (oldest first)."""

        rows = self.database.connection.execute(
            "SELECT * FROM execution_effects WHERE run_id = ? AND state = 'unknown' "
            "ORDER BY turn_ordinal, call_ordinal",
            (_required(run_id, "run_id"),),
        ).fetchall()
        return tuple(_effect_record(row) for row in rows)

    def read_effect(self, effect_id: EffectId) -> EffectRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id = ?",
            (effect_id.value,),
        ).fetchone()
        return None if row is None else _effect_record(row)

    def mark_effect_handed_off(
        self,
        effect_id: EffectId,
        *,
        expected_version: int,
        run_fence: RunFenceLease,
        handoff_receipt_ref: str,
        execution_lease: ExecutionLease,
        workflow_lease: WorkflowLease | None = None,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        handoff_receipt_ref = _required(handoff_receipt_ref, "handoff_receipt_ref")
        now = _time(now)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(
                connection,
                run_fence,
                execution_lease=execution_lease,
            )
            self._require_workflow_handoff_authority(
                connection,
                run_id=execution_lease.run_id,
                execution_lease=execution_lease,
                workflow_lease=workflow_lease,
                now=now,
            )
            if (
                run_fence.owner_id != execution_lease.owner_id
                or run_fence.run_id.value != execution_lease.run_id
            ):
                raise UnitOfWorkConflict("effect runtime lease and Run fence differ")
            effect_run = connection.execute(
                "SELECT run_id FROM execution_effects WHERE effect_id = ?",
                (effect_id.value,),
            ).fetchone()
            if effect_run is None or str(effect_run["run_id"]) != execution_lease.run_id:
                raise UnitOfWorkConflict("effect handoff lease belongs to another Run")
            _fault(fault, "effect_handoff.before_write")
            changed = connection.execute(
                """
                UPDATE execution_effects
                SET state = 'handed_off', handoff_receipt_ref = ?,
                    handed_off_at = ?, handoff_attempt = handoff_attempt + 1,
                    version = version + 1
                WHERE effect_id = ? AND state = 'prepared' AND version = ?
                  AND fence_epoch = ?
                """,
                (
                    handoff_receipt_ref,
                    now,
                    effect_id.value,
                    expected_version,
                    run_fence.epoch,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("effect handoff CAS failed")
            _fault(fault, "effect_handoff.after_write")
            self._audit_operation_head(connection, "effect", effect_id.value, now)
        _fault(fault, "effect_handoff.after_commit")
        record = self.read_effect(effect_id)
        assert record is not None
        return record

    def settle_effect(
        self,
        effect_id: EffectId,
        *,
        expected_version: int,
        expected_fence_epoch: int,
        result: ToolResult,
        evidence_ref: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        evidence_ref = _required(evidence_ref, "evidence_ref")
        now = _time(now)
        existing = self.read_effect(effect_id)
        if existing is None:
            raise UnitOfWorkNotFound(effect_id.value)
        if result.call_id != existing.call_id:
            raise UnitOfWorkConflict("effect result call_id mismatch")
        state = EffectState(result.outcome.value)
        result_json = None if state is EffectState.UNKNOWN else _tool_result_json(result)
        with self.database.transaction() as connection:
            _fault(fault, "effect_settle.before_write")
            changed = connection.execute(
                """
                UPDATE execution_effects
                SET state = ?, result_json = ?, evidence_ref = ?, settled_at = ?,
                    version = version + 1
                WHERE effect_id = ? AND state IN ('handed_off', 'unknown')
                  AND version = ? AND fence_epoch = ?
                """,
                (
                    state.value,
                    result_json,
                    evidence_ref,
                    now,
                    effect_id.value,
                    expected_version,
                    expected_fence_epoch,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("effect settlement CAS failed")
            _fault(fault, "effect_settle.after_write")
            self._audit_operation_head(connection, "effect", effect_id.value, now)
        _fault(fault, "effect_settle.after_commit")
        record = self.read_effect(effect_id)
        assert record is not None
        return record

    def mark_effect_unknown(
        self,
        effect_id: EffectId,
        *,
        expected_version: int,
        expected_fence_epoch: int,
        evidence_ref: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        evidence_ref = _required(evidence_ref, "evidence_ref")
        now = _time(now)
        with self.database.transaction() as connection:
            _fault(fault, "effect_unknown.before_write")
            changed = connection.execute(
                """
                UPDATE execution_effects
                SET state = 'unknown', evidence_ref = ?, settled_at = ?,
                    result_json = NULL, version = version + 1
                WHERE effect_id = ? AND state = 'handed_off' AND version = ?
                  AND fence_epoch = ?
                """,
                (
                    evidence_ref,
                    now,
                    effect_id.value,
                    expected_version,
                    expected_fence_epoch,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("effect unknown CAS failed")
            _fault(fault, "effect_unknown.after_write")
            self._audit_operation_head(connection, "effect", effect_id.value, now)
        _fault(fault, "effect_unknown.after_commit")
        record = self.read_effect(effect_id)
        assert record is not None
        return record

    def record_tool_reconciliation(
        self,
        record: EffectRecord,
        *,
        outcome: ResolutionOutcome,
        result: ToolResult | None,
        evidence_ref: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        outcome = ResolutionOutcome(outcome)
        evidence_ref = _required(evidence_ref, "evidence_ref")
        now = _time(now)
        if record.state not in {EffectState.HANDED_OFF, EffectState.UNKNOWN}:
            raise UnitOfWorkConflict("Tool reconciliation requires uncertain ledger")
        if record.handoff_attempt < 1:
            raise UnitOfWorkConflict("Tool reconciliation requires handoff attempt")
        if outcome is ResolutionOutcome.COMPLETED:
            if result is None:
                raise ValueError("completed Tool resolution requires result")
            if result.outcome.value == EffectState.UNKNOWN.value:
                raise ValueError("completed Tool resolution cannot remain unknown")
            if result.call_id != record.call_id:
                result = ToolResult(
                    record.call_id,
                    result.outcome,
                    cast(FrozenJsonValue, thaw_json(result.value)),
                    result.error_code,
                    result.public_message,
                    result.retryable,
                )
            payload: dict[str, JsonValue] = {
                "outcome": outcome.value,
                "evidence_ref": evidence_ref,
                "request_hash": record.request_hash,
                "result": json.loads(_tool_result_json(result)),
            }
        else:
            if result is not None:
                raise ValueError("not-started Tool resolution cannot carry result")
            payload = {
                "outcome": outcome.value,
                "evidence_ref": evidence_ref,
                "request_hash": record.request_hash,
                "result": None,
            }
        outcome_hash = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        resolution_id = f"resolution:{recovery_identity(RecoveryKind.TOOL, record.effect_id.value, record.handoff_attempt)}"  # noqa: E501
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_effects WHERE effect_id=?",
                (record.effect_id.value,),
            ).fetchone()
            if row is None:
                raise UnitOfWorkNotFound(record.effect_id.value)
            current = _effect_record(row)
            if (
                current.run_id != record.run_id
                or current.call_id != record.call_id
                or current.request_hash != record.request_hash
                or current.handoff_attempt != record.handoff_attempt
            ):
                raise UnitOfWorkConflict("Tool reconciliation identity conflict")
            existing = connection.execute(
                """
                SELECT * FROM reconciliation_resolutions
                WHERE kind='tool' AND ledger_identity=? AND handoff_attempt=?
                """,
                (record.effect_id.value, record.handoff_attempt),
            ).fetchone()
            if existing is not None:
                stored = _reconciliation_resolution(existing)
                if stored.outcome_hash != outcome_hash:
                    raise UnitOfWorkConflict("Tool resolution outcome conflict")
                return current
            if current.version != record.version or current.state not in {
                EffectState.HANDED_OFF,
                EffectState.UNKNOWN,
            }:
                raise UnitOfWorkConflict("Tool reconciliation CAS failed")
            _fault(fault, "tool_reconciliation.resolution.before_write")
            connection.execute(
                """
                INSERT INTO reconciliation_resolutions(
                    resolution_id,kind,ledger_identity,handoff_attempt,outcome,
                    outcome_hash,evidence_ref,payload_json,created_at
                ) VALUES (?, 'tool', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolution_id,
                    record.effect_id.value,
                    record.handoff_attempt,
                    outcome.value,
                    outcome_hash,
                    evidence_ref,
                    canonical_json(payload),
                    now,
                ),
            )
            _fault(fault, "tool_reconciliation.resolution.after_write")
            if outcome is ResolutionOutcome.COMPLETED:
                assert result is not None
                changed = connection.execute(
                    """
                    UPDATE execution_effects SET state=?, result_json=?, evidence_ref=?,
                        settled_at=?, version=version+1
                    WHERE effect_id=? AND state IN ('handed_off','unknown')
                      AND version=? AND request_hash=? AND handoff_attempt=?
                    """,
                    (
                        result.outcome.value,
                        _tool_result_json(result),
                        evidence_ref,
                        now,
                        record.effect_id.value,
                        record.version,
                        record.request_hash,
                        record.handoff_attempt,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("Tool completed recovery CAS failed")
                _fault(fault, "tool_reconciliation.ledger.after_write")
                self._audit_operation_head(connection, "effect", record.effect_id.value, now)
            connection.execute(
                """
                UPDATE run_wait_blockers SET resolution_id=?, resolved_at=?,
                    version=version+1
                WHERE kind='tool' AND ledger_identity=? AND handoff_attempt=?
                  AND resolution_id IS NULL
                """,
                (resolution_id, now, record.effect_id.value, record.handoff_attempt),
            )
            _fault(fault, "tool_reconciliation.blocker.after_write")
        _fault(fault, "tool_reconciliation.after_commit")
        result_record = self.read_effect(record.effect_id)
        assert result_record is not None
        return result_record

    def reauthorize_effect_not_started(
        self,
        record: EffectRecord,
        *,
        authorization_receipt_ref: str,
        resolution: ReconciliationResolution,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        authorization_receipt_ref = _required(
            authorization_receipt_ref, "authorization_receipt_ref"
        )
        now = _time(now)
        if (
            resolution.kind is not RecoveryKind.TOOL
            or resolution.ledger_identity != record.effect_id.value
            or resolution.handoff_attempt != record.handoff_attempt
            or resolution.outcome is not ResolutionOutcome.CONFIRMED_NOT_STARTED
        ):
            raise UnitOfWorkConflict("Tool reauthorization resolution mismatch")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            row = connection.execute(
                "SELECT outcome_hash,evidence_ref FROM reconciliation_resolutions WHERE"
                " resolution_id=?",
                (resolution.resolution_id,),
            ).fetchone()
            if (
                row is None
                or str(row["outcome_hash"]) != resolution.outcome_hash
                or str(row["evidence_ref"]) != resolution.evidence_ref
            ):
                raise UnitOfWorkConflict("Tool reauthorization evidence mismatch")
            _fault(fault, "effect_reauthorize.before_write")
            changed = connection.execute(
                """
                UPDATE execution_effects
                SET state='prepared', authorization_receipt_ref=?, fence_epoch=?,
                    handoff_receipt_ref=NULL, handed_off_at=NULL, settled_at=NULL,
                    evidence_ref=?, rehandoff_count=rehandoff_count+1,
                    version=version+1
                WHERE effect_id=? AND state='unknown' AND version=?
                  AND request_hash=? AND handoff_attempt=? AND rehandoff_count=0
                """,
                (
                    authorization_receipt_ref,
                    run_fence.epoch,
                    resolution.evidence_ref,
                    record.effect_id.value,
                    record.version,
                    record.request_hash,
                    record.handoff_attempt,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("Tool reauthorization CAS failed")
            _fault(fault, "effect_reauthorize.after_write")
            self._audit_operation_head(connection, "effect", record.effect_id.value, now)
        _fault(fault, "effect_reauthorize.after_commit")
        refreshed = self.read_effect(record.effect_id)
        assert refreshed is not None
        return refreshed

    def refresh_prepared_effect_authority(
        self,
        record: EffectRecord,
        *,
        authorization_receipt_ref: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> EffectRecord:
        authorization_receipt_ref = _required(
            authorization_receipt_ref, "authorization_receipt_ref"
        )
        now = _time(now)
        if record.state is not EffectState.PREPARED:
            raise UnitOfWorkConflict("Tool authority refresh requires PREPARED")
        if record.handoff_attempt == 0 and record.rehandoff_count != 0:
            raise UnitOfWorkConflict("initial PREPARED has invalid retry count")
        if record.handoff_attempt > 0 and record.rehandoff_count != 1:
            raise UnitOfWorkConflict("retry PREPARED lacks one reauthorization")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            if record.handoff_attempt > 0:
                resolution = connection.execute(
                    """
                    SELECT evidence_ref FROM reconciliation_resolutions
                    WHERE kind='tool' AND ledger_identity=? AND handoff_attempt=?
                      AND outcome='confirmed_not_started'
                    """,
                    (record.effect_id.value, record.handoff_attempt),
                ).fetchone()
                if resolution is None or str(resolution["evidence_ref"]) != record.evidence_ref:
                    raise UnitOfWorkConflict("Tool refresh resolution mismatch")
            if record.run_id.value != execution_lease.run_id or record.run_id != run_fence.run_id:
                raise UnitOfWorkConflict("Tool refresh authority belongs to another Run")
            _fault(fault, "effect_refresh.before_write")
            changed = connection.execute(
                """
                UPDATE execution_effects
                SET fence_epoch=?, authorization_receipt_ref=?, version=version+1
                WHERE effect_id=? AND state='prepared' AND version=?
                  AND request_hash=? AND handoff_attempt=? AND rehandoff_count=?
                  AND COALESCE(evidence_ref, '')=COALESCE(?, '')
                """,
                (
                    run_fence.epoch,
                    authorization_receipt_ref,
                    record.effect_id.value,
                    record.version,
                    record.request_hash,
                    record.handoff_attempt,
                    record.rehandoff_count,
                    record.evidence_ref,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("Tool authority refresh CAS failed")
            _fault(fault, "effect_refresh.after_write")
            self._audit_operation_head(connection, "effect", record.effect_id.value, now)
        _fault(fault, "effect_refresh.after_commit")
        refreshed = self.read_effect(record.effect_id)
        assert refreshed is not None
        return refreshed
