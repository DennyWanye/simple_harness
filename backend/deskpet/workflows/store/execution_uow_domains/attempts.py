"""Provider turns, attempts, failure sets and external waits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from deskpet.execution.contracts import (
    AttemptFailureSet,
    AttemptRecord,
    AttemptStatus,
    ExternalWaitState,
    FailureBackfillState,
    IdempotencyConflict,
    ProviderActionAdmissionState,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderBatchAdmissionResult,
    ProviderBatchStatus,
    ProviderResumeState,
    ProviderTurnFence,
    ProviderTurnState,
    TaskExternalWait,
    TaskFailureReport,
    TaskGoalStatus,
    TerminalConflict,
    VersionConflict,
    canonical_json,
)

from .core import ExecutionUowCore


class ProviderAttemptMixin(ExecutionUowCore):
    """Provider turns, attempts, failure sets and external waits."""

    __slots__ = ()

    async def open_provider_turn_fence(self, fence: ProviderTurnFence) -> ProviderTurnFence:
        """Persist the provider response idempotency fence before accepting calls."""

        if (
            fence.state is not ProviderTurnState.PENDING
            or fence.version != 0
            or fence.accepted_batch_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_provider_turn", "new provider turns must start pending"
            )
        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=?
                       OR (root_run_id=? AND idempotency_key=?)""",
                    (
                        fence.provider_turn_id,
                        fence.root_run_id,
                        fence.idempotency_key,
                    ),
                )
            ).fetchone()
            if existing is not None:
                record = self._row_to_provider_turn(existing)
                if (
                    record.provider_turn_id,
                    record.root_run_id,
                    record.idempotency_key,
                    record.request_hash,
                ) != (
                    fence.provider_turn_id,
                    fence.root_run_id,
                    fence.idempotency_key,
                    fence.request_hash,
                ):
                    raise IdempotencyConflict(
                        "provider_turn_conflict",
                        "provider turn fence already names another request",
                    )
                return record
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (fence.root_run_id,),
                )
            ).fetchone()
            if goal is None or str(goal["status"]) != TaskGoalStatus.ACTIVE.value:
                raise IdempotencyConflict(
                    "provider_turn_goal_inactive",
                    "a provider turn requires an active task goal",
                )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_provider_turn_fences(
                provider_turn_id,root_run_id,idempotency_key,request_hash,state,
                accepted_batch_id,fence_version,created_at,updated_at,accepted_at
                ) VALUES(?,?,?,?,'pending',NULL,0,?,?,NULL)""",
                (
                    fence.provider_turn_id,
                    fence.root_run_id,
                    fence.idempotency_key,
                    fence.request_hash,
                    now,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=?""",
                    (fence.provider_turn_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_provider_turn(row)

    async def accept_provider_batch_and_create_attempt(
        self,
        batch: ProviderActionBatch,
        attempt: AttemptRecord,
        calls: Sequence[ProviderActionCall],
    ) -> ProviderBatchAdmissionResult:
        """Atomically accept one provider batch and create exactly one attempt."""

        ordered_calls = tuple(sorted(calls, key=lambda item: item.call_order))
        if (
            batch.status is not ProviderBatchStatus.ADMITTED
            or batch.version != 0
            or attempt.status is not AttemptStatus.RUNNING
            or attempt.version != 0
            or not attempt.budget_eligible
            or not ordered_calls
            or batch.pending_call_count != len(ordered_calls)
            or tuple(item.call_order for item in ordered_calls) != tuple(range(len(ordered_calls)))
            or tuple(attempt.planned_call_refs)
            != tuple(item.call_record_id for item in ordered_calls)
        ):
            raise IdempotencyConflict(
                "invalid_provider_batch",
                "provider batch, attempt and ordered calls do not form one initial admission",
            )
        if (
            batch.root_run_id != attempt.root_run_id
            or batch.provider_turn_id != attempt.provider_turn_id
            or batch.provider_batch_id != attempt.provider_batch_id
            or any(
                call.root_run_id != batch.root_run_id
                or call.provider_batch_id != batch.provider_batch_id
                or call.admission_state
                not in {
                    ProviderActionAdmissionState.ADMITTED,
                    ProviderActionAdmissionState.REJECTED,
                }
                or call.version != 0
                for call in ordered_calls
            )
        ):
            raise IdempotencyConflict(
                "provider_batch_scope_conflict",
                "provider batch members must share root, turn and batch identity",
            )
        async with self._write_transaction() as db:
            existing_batch_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_batches
                    WHERE provider_batch_id=? OR provider_turn_id=?""",
                    (batch.provider_batch_id, batch.provider_turn_id),
                )
            ).fetchone()
            if existing_batch_row is not None:
                existing_batch = self._row_to_provider_batch(existing_batch_row)
                existing_attempt_row = await (
                    await db.execute(
                        """SELECT * FROM execution_attempt_records
                        WHERE provider_batch_id=?""",
                        (existing_batch.provider_batch_id,),
                    )
                ).fetchone()
                existing_call_rows = await (
                    await db.execute(
                        """SELECT * FROM execution_provider_action_calls
                        WHERE provider_batch_id=? ORDER BY call_order""",
                        (existing_batch.provider_batch_id,),
                    )
                ).fetchall()
                if existing_attempt_row is None:
                    raise IdempotencyConflict(
                        "provider_batch_partial",
                        "provider batch exists without its attempt",
                    )
                existing_attempt = self._row_to_attempt(existing_attempt_row)
                existing_calls = tuple(
                    self._row_to_provider_call(row) for row in existing_call_rows
                )
                if (
                    not self._same_batch_intent(existing_batch, batch)
                    or not self._same_attempt_intent(existing_attempt, attempt)
                    or len(existing_calls) != len(ordered_calls)
                    or any(
                        not self._same_call_intent(existing, requested)
                        for existing, requested in zip(existing_calls, ordered_calls)
                    )
                ):
                    raise IdempotencyConflict(
                        "provider_batch_conflict",
                        "provider batch replay differs from the accepted intent",
                    )
                return ProviderBatchAdmissionResult(existing_attempt, existing_calls, True)
            fence = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=? AND root_run_id=?""",
                    (batch.provider_turn_id, batch.root_run_id),
                )
            ).fetchone()
            if fence is None or str(fence["state"]) != ProviderTurnState.PENDING.value:
                raise IdempotencyConflict(
                    "provider_turn_not_pending",
                    "provider batch admission requires its pending turn fence",
                )
            plan = await (
                await db.execute(
                    """SELECT 1 FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (attempt.root_run_id, attempt.plan_version),
                )
            ).fetchone()
            run = await (
                await db.execute(
                    "SELECT root_run_id FROM execution_runs WHERE run_id=?",
                    (attempt.run_id,),
                )
            ).fetchone()
            if plan is None or run is None or str(run["root_run_id"]) != attempt.root_run_id:
                raise IdempotencyConflict(
                    "attempt_scope_conflict",
                    "attempt requires a durable plan and run in the same root",
                )
            if attempt.trigger_failure_set_id is not None:
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (attempt.trigger_failure_set_id, attempt.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "attempt_trigger_missing",
                        "attempt trigger failure set is not durable",
                    )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_provider_action_batches(
                provider_batch_id,root_run_id,provider_turn_id,
                canonical_assistant_batch_ref,batch_fingerprint,pending_call_count,
                failure_set_id,status,batch_version,created_at,updated_at,settled_at
                ) VALUES(?,?,?,?,?,?,NULL,'admitted',0,?,?,NULL)""",
                (
                    batch.provider_batch_id,
                    batch.root_run_id,
                    batch.provider_turn_id,
                    batch.canonical_assistant_batch_ref,
                    batch.batch_fingerprint,
                    batch.pending_call_count,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_attempt_records(
                attempt_id,schema_version,root_run_id,run_id,provider_turn_id,
                provider_batch_id,plan_version,trigger_failure_set_id,
                supersedes_attempt_id,strategy_fingerprint,planned_call_refs_json,
                checkpoint_ref,status,budget_eligible,attempt_version,created_at,
                updated_at,ended_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'running',1,0,?,?,NULL)""",
                (
                    attempt.attempt_id,
                    attempt.schema_version,
                    attempt.root_run_id,
                    attempt.run_id,
                    attempt.provider_turn_id,
                    attempt.provider_batch_id,
                    attempt.plan_version,
                    attempt.trigger_failure_set_id,
                    attempt.supersedes_attempt_id,
                    attempt.strategy_fingerprint,
                    canonical_json(list(attempt.planned_call_refs)),
                    attempt.checkpoint_ref,
                    now,
                    now,
                ),
            )
            self._fault("provider_batch_after_attempt")
            for call in ordered_calls:
                await db.execute(
                    """INSERT INTO execution_provider_action_calls(
                    call_record_id,schema_version,root_run_id,provider_batch_id,
                    call_order,provider_call_id,raw_tool_name,raw_arguments_ref,
                    raw_arguments_hash,parsed_arguments_hash,admission_state,
                    prepared_call_ref,command_boundary_ref,terminal_outcome_ref,
                    call_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                    (
                        call.call_record_id,
                        call.schema_version,
                        call.root_run_id,
                        call.provider_batch_id,
                        call.call_order,
                        call.provider_call_id,
                        call.raw_tool_name,
                        call.raw_arguments_ref,
                        call.raw_arguments_hash,
                        call.parsed_arguments_hash,
                        call.admission_state.value,
                        call.prepared_call_ref,
                        call.command_boundary_ref,
                        call.terminal_outcome_ref,
                        now,
                        now,
                    ),
                )
            self._fault("provider_batch_after_calls")
            cursor = await db.execute(
                """UPDATE execution_provider_turn_fences
                SET state='accepted',accepted_batch_id=?,fence_version=fence_version+1,
                    updated_at=?,accepted_at=?
                WHERE provider_turn_id=? AND root_run_id=? AND state='pending'
                  AND fence_version=?""",
                (
                    batch.provider_batch_id,
                    now,
                    now,
                    batch.provider_turn_id,
                    batch.root_run_id,
                    int(fence["fence_version"]),
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_turn_cas_conflict",
                    "provider turn acceptance compare-and-set lost",
                )
            self._fault("provider_batch_before_commit")
            attempt_row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (attempt.attempt_id,),
                )
            ).fetchone()
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (batch.provider_batch_id,),
                )
            ).fetchall()
            assert attempt_row is not None
            return ProviderBatchAdmissionResult(
                self._row_to_attempt(attempt_row),
                tuple(self._row_to_provider_call(row) for row in call_rows),
                False,
            )

    async def get_attempt(self, attempt_id: str) -> AttemptRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (attempt_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_attempt(row)

    async def list_provider_action_calls(
        self, provider_batch_id: str
    ) -> tuple[ProviderActionCall, ...]:
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (provider_batch_id,),
                )
            ).fetchall()
            return tuple(self._row_to_provider_call(row) for row in rows)

    async def mark_provider_action_prepared(
        self,
        call_record_id: str,
        *,
        expected_version: int,
        parsed_arguments_hash: str,
        prepared_call_ref: str,
        command_boundary_ref: str | None = None,
    ) -> ProviderActionCall:
        """CAS an admitted provider call to its prepared durable boundary."""

        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (call_record_id,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_call_missing", "provider action call does not exist"
                )
            existing = self._row_to_provider_call(row)
            if existing.admission_state in {
                ProviderActionAdmissionState.PREPARED,
                ProviderActionAdmissionState.SETTLED,
            }:
                if (
                    existing.parsed_arguments_hash,
                    existing.prepared_call_ref,
                    existing.command_boundary_ref,
                ) != (
                    parsed_arguments_hash,
                    prepared_call_ref,
                    command_boundary_ref,
                ):
                    raise IdempotencyConflict(
                        "prepared_call_conflict", "prepared call replay differs"
                    )
                return existing
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET parsed_arguments_hash=?,admission_state='prepared',
                    prepared_call_ref=?,command_boundary_ref=?,
                    call_version=call_version+1,updated_at=?
                WHERE call_record_id=? AND admission_state='admitted'
                  AND call_version=?""",
                (
                    parsed_arguments_hash,
                    prepared_call_ref,
                    command_boundary_ref,
                    now,
                    call_record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_call_cas_conflict",
                    "provider call preparation compare-and-set lost",
                )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (call_record_id,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_call(updated)

    async def settle_provider_action_success(
        self,
        attempt_id: str,
        outcome_refs_by_call: Mapping[str, str],
    ) -> AttemptRecord:
        """Atomically settle every call, its batch and the successful attempt."""

        normalized_refs = {
            str(call_record_id).strip(): str(outcome_ref).strip()
            for call_record_id, outcome_ref in outcome_refs_by_call.items()
        }
        if (
            not normalized_refs
            or any(not key or not value for key, value in normalized_refs.items())
            or len(normalized_refs) != len(outcome_refs_by_call)
        ):
            raise IdempotencyConflict(
                "attempt_success_outcomes_invalid",
                "successful attempt settlement requires one outcome per call",
            )
        async with self._write_transaction() as db:
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (attempt_id,),
                )
            ).fetchone()
            if attempt_row is None:
                raise IdempotencyConflict(
                    "attempt_missing",
                    "successful attempt settlement requires an existing attempt",
                )
            attempt = self._row_to_attempt(attempt_row)
            batch_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_batches
                    WHERE provider_batch_id=?""",
                    (attempt.provider_batch_id,),
                )
            ).fetchone()
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (attempt.provider_batch_id,),
                )
            ).fetchall()
            if batch_row is None or not call_rows:
                raise IdempotencyConflict(
                    "attempt_success_scope_missing",
                    "successful attempt lost its provider batch or calls",
                )
            calls = tuple(self._row_to_provider_call(row) for row in call_rows)
            call_ids = tuple(call.call_record_id for call in calls)
            if tuple(attempt.planned_call_refs) != call_ids or set(normalized_refs) != set(
                call_ids
            ):
                raise IdempotencyConflict(
                    "attempt_success_scope_conflict",
                    "successful outcomes must exactly cover the planned calls",
                )
            batch = self._row_to_provider_batch(batch_row)
            if attempt.status is AttemptStatus.SUCCEEDED:
                if (
                    batch.status is not ProviderBatchStatus.SETTLED
                    or batch.pending_call_count != 0
                    or any(
                        call.admission_state is not ProviderActionAdmissionState.SETTLED
                        or call.terminal_outcome_ref != normalized_refs[call.call_record_id]
                        for call in calls
                    )
                ):
                    raise IdempotencyConflict(
                        "attempt_success_partial_settlement",
                        "successful attempt replay found a partially settled ledger",
                    )
                return attempt
            if (
                attempt.status is not AttemptStatus.RUNNING
                or batch.status not in {ProviderBatchStatus.ADMITTED, ProviderBatchStatus.RUNNING}
                or any(
                    call.admission_state
                    not in {
                        ProviderActionAdmissionState.ADMITTED,
                        ProviderActionAdmissionState.PREPARED,
                    }
                    for call in calls
                )
            ):
                raise IdempotencyConflict(
                    "attempt_not_success_settleable",
                    "only one fully completed live attempt can be settled",
                )
            now = float(self._clock())
            for call in calls:
                cursor = await db.execute(
                    """UPDATE execution_provider_action_calls
                    SET admission_state='settled',terminal_outcome_ref=?,
                        call_version=call_version+1,updated_at=?
                    WHERE call_record_id=? AND call_version=?
                      AND admission_state IN ('admitted','prepared')""",
                    (
                        normalized_refs[call.call_record_id],
                        now,
                        call.call_record_id,
                        call.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "attempt_success_call_cas_conflict",
                        "provider call changed during successful settlement",
                    )
            self._fault("attempt_success_after_calls")
            batch_cursor = await db.execute(
                """UPDATE execution_provider_action_batches
                SET pending_call_count=0,status='settled',
                    batch_version=batch_version+1,updated_at=?,settled_at=?
                WHERE provider_batch_id=? AND batch_version=?
                  AND status IN ('admitted','running')""",
                (
                    now,
                    now,
                    attempt.provider_batch_id,
                    batch.version,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='succeeded',attempt_version=attempt_version+1,
                    updated_at=?,ended_at=?
                WHERE attempt_id=? AND attempt_version=? AND status='running'""",
                (now, now, attempt.attempt_id, attempt.version),
            )
            if batch_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "attempt_success_cas_conflict",
                    "batch or attempt changed during successful settlement",
                )
            self._fault("attempt_success_before_commit")
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (attempt.attempt_id,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_attempt(updated)

    async def stage_attempt_failure_set(
        self,
        failure_set: AttemptFailureSet,
        reports: Sequence[TaskFailureReport],
    ) -> AttemptFailureSet:
        """Persist every call failure and fail the attempt in one transaction."""

        report_by_ref = {report.report_ref: report for report in reports}
        if (
            len(report_by_ref) != len(reports)
            or set(report_by_ref) != set(failure_set.report_refs)
            or failure_set.backfill_state is not FailureBackfillState.READY
            or failure_set.provider_resume_state is not ProviderResumeState.PENDING
            or failure_set.version != 0
        ):
            raise IdempotencyConflict(
                "invalid_failure_set",
                "failure set must contain unique reports and begin ready for backfill",
            )
        async with self._write_transaction() as db:
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=? OR failed_attempt_id=?""",
                    (failure_set.failure_set_id, failure_set.failed_attempt_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = await self._failure_set_from_row(db, existing_row)
                if (
                    existing.failure_set_id,
                    existing.root_run_id,
                    existing.failed_attempt_id,
                    tuple(existing.report_refs),
                    existing.primary_report_ref,
                ) != (
                    failure_set.failure_set_id,
                    failure_set.root_run_id,
                    failure_set.failed_attempt_id,
                    tuple(failure_set.report_refs),
                    failure_set.primary_report_ref,
                ):
                    raise IdempotencyConflict("failure_set_conflict", "failure set replay differs")
                return existing
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=? AND root_run_id=?""",
                    (failure_set.failed_attempt_id, failure_set.root_run_id),
                )
            ).fetchone()
            if attempt_row is None or str(attempt_row["status"]) not in {
                AttemptStatus.RUNNING.value,
                AttemptStatus.WAITING_EXTERNAL.value,
            }:
                raise IdempotencyConflict(
                    "attempt_not_failurable",
                    "failure facts require a live attempt in the same root",
                )
            attempt = self._row_to_attempt(attempt_row)
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (attempt.provider_batch_id,),
                )
            ).fetchall()
            call_order = {str(row["call_record_id"]): int(row["call_order"]) for row in call_rows}
            ordered_reports = sorted(
                reports,
                key=lambda report: (
                    call_order.get(report.call_record_id, 2**31),
                    report.source_kind.value,
                    report.report_ref,
                ),
            )
            if (
                not ordered_reports
                or tuple(report.report_ref for report in ordered_reports)
                != tuple(failure_set.report_refs)
                or failure_set.primary_report_ref != ordered_reports[0].report_ref
                or len({call_order.get(report.call_record_id) for report in reports})
                != len(reports)
                or any(
                    report.root_run_id != attempt.root_run_id
                    or report.run_id != attempt.run_id
                    or report.attempt_id != attempt.attempt_id
                    or report.plan_version != attempt.plan_version
                    or report.call_record_id not in call_order
                    for report in reports
                )
            ):
                raise IdempotencyConflict(
                    "failure_set_order_conflict",
                    "failure reports must be complete, call-ordered and share the attempt",
                )
            goal_row = await (
                await db.execute(
                    """SELECT task_scope_id FROM execution_task_goals
                    WHERE root_run_id=?""",
                    (attempt.root_run_id,),
                )
            ).fetchone()
            if goal_row is None or any(
                report.task_scope_id != str(goal_row["task_scope_id"]) for report in reports
            ):
                raise IdempotencyConflict(
                    "failure_scope_conflict",
                    "failure reports must use the task goal scope",
                )
            now = float(self._clock())
            for report in ordered_reports:
                await db.execute(
                    """INSERT INTO execution_task_failure_reports(
                    report_ref,schema_version,root_run_id,run_id,task_scope_id,
                    attempt_id,plan_version,call_record_id,source_kind,
                    source_identity,provider_call_id,child_run_id,inner_failure_ref,
                    failed_call_id,failed_effect_id,failed_step,error_class,
                    error_code,error_fingerprint,action_fingerprint,exit_code,
                    evidence_refs_json,completed_step_refs_json,artifact_refs_json,
                    checkpoint_ref,prior_strategy_fingerprints_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        report.report_ref,
                        report.schema_version,
                        report.root_run_id,
                        report.run_id,
                        report.task_scope_id,
                        report.attempt_id,
                        report.plan_version,
                        report.call_record_id,
                        report.source_kind.value,
                        report.source_identity,
                        report.provider_call_id,
                        report.child_run_id,
                        report.inner_failure_ref,
                        report.failed_call_id,
                        report.failed_effect_id,
                        report.failed_step,
                        report.error_class.value,
                        report.error_code,
                        report.error_fingerprint,
                        report.action_fingerprint,
                        report.exit_code,
                        canonical_json(list(report.evidence_refs)),
                        canonical_json(list(report.completed_step_refs)),
                        canonical_json(list(report.artifact_refs)),
                        report.checkpoint_ref,
                        canonical_json(list(report.prior_strategy_fingerprints)),
                        now,
                    ),
                )
            self._fault("failure_set_after_reports")
            await db.execute(
                """INSERT INTO execution_attempt_failure_sets(
                failure_set_id,schema_version,root_run_id,failed_attempt_id,
                primary_report_ref,backfill_state,provider_resume_state,
                set_version,created_at,updated_at
                ) VALUES(?,?,?,?,?,'ready','pending',0,?,?)""",
                (
                    failure_set.failure_set_id,
                    failure_set.schema_version,
                    failure_set.root_run_id,
                    failure_set.failed_attempt_id,
                    failure_set.primary_report_ref,
                    now,
                    now,
                ),
            )
            for report in ordered_reports:
                await db.execute(
                    """INSERT INTO execution_attempt_failure_set_members(
                    failure_set_id,report_ref,provider_call_order,created_at
                    ) VALUES(?,?,?,?)""",
                    (
                        failure_set.failure_set_id,
                        report.report_ref,
                        call_order[report.call_record_id],
                        now,
                    ),
                )
            cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='failed',attempt_version=attempt_version+1,
                    updated_at=?,ended_at=?
                WHERE attempt_id=? AND attempt_version=?
                  AND status IN ('running','waiting_external')""",
                (now, now, attempt.attempt_id, attempt.version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "attempt_failure_cas_conflict",
                    "attempt failure compare-and-set lost",
                )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='ready_backfill',failure_set_id=?,
                    batch_version=batch_version+1,updated_at=?
                WHERE provider_batch_id=?""",
                (
                    failure_set.failure_set_id,
                    now,
                    attempt.provider_batch_id,
                ),
            )
            self._fault("failure_set_before_commit")
            row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=?""",
                    (failure_set.failure_set_id,),
                )
            ).fetchone()
            assert row is not None
            return await self._failure_set_from_row(db, row)

    async def get_attempt_failure_set(self, failure_set_id: str) -> AttemptFailureSet | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=?""",
                    (failure_set_id,),
                )
            ).fetchone()
            return None if row is None else await self._failure_set_from_row(db, row)

    async def list_task_failure_reports(self, attempt_id: str) -> tuple[TaskFailureReport, ...]:
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT reports.* FROM execution_task_failure_reports AS reports
                    JOIN execution_attempt_failure_set_members AS members
                      ON members.report_ref=reports.report_ref
                    JOIN execution_attempt_failure_sets AS sets
                      ON sets.failure_set_id=members.failure_set_id
                    WHERE sets.failed_attempt_id=?
                    ORDER BY members.provider_call_order,reports.report_ref""",
                    (attempt_id,),
                )
            ).fetchall()
            return tuple(self._row_to_failure_report(row) for row in rows)

    async def stage_external_wait(self, wait: TaskExternalWait) -> TaskExternalWait:
        """Pause the same attempt/call without manufacturing a failure fact."""

        if wait.state is not ExternalWaitState.OPEN or wait.version != 0:
            raise IdempotencyConflict(
                "invalid_external_wait", "a new external wait must start open"
            )
        async with self._write_transaction() as db:
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_external_waits
                    WHERE wait_ref=? OR (root_run_id=? AND call_record_id=?)""",
                    (wait.wait_ref, wait.root_run_id, wait.call_record_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_external_wait(existing_row)
                fields = (
                    "wait_ref",
                    "root_run_id",
                    "attempt_id",
                    "call_record_id",
                    "provider_call_id",
                    "command_boundary_ref",
                    "effect_id",
                    "wait_kind",
                    "required_action_ref",
                    "checkpoint_ref",
                    "resume_admission_state",
                    "evidence_refs",
                )
                if not all(
                    getattr(existing, field_name) == getattr(wait, field_name)
                    for field_name in fields
                ):
                    raise IdempotencyConflict(
                        "external_wait_conflict", "external wait replay differs"
                    )
                return existing
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=? AND root_run_id=?""",
                    (wait.attempt_id, wait.root_run_id),
                )
            ).fetchone()
            call_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=? AND root_run_id=?""",
                    (wait.call_record_id, wait.root_run_id),
                )
            ).fetchone()
            if attempt_row is None or call_row is None:
                raise IdempotencyConflict(
                    "external_wait_scope_missing",
                    "external wait requires an existing attempt and call",
                )
            attempt = self._row_to_attempt(attempt_row)
            call = self._row_to_provider_call(call_row)
            if (
                attempt.status is not AttemptStatus.RUNNING
                or attempt.provider_batch_id != call.provider_batch_id
                or call.provider_call_id != wait.provider_call_id
                or call.admission_state != wait.resume_admission_state
                or call.admission_state
                not in {
                    ProviderActionAdmissionState.ADMITTED,
                    ProviderActionAdmissionState.PREPARED,
                }
            ):
                raise IdempotencyConflict(
                    "external_wait_scope_conflict",
                    "external wait must pause the live call in its current admission state",
                )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_task_external_waits(
                wait_ref,schema_version,root_run_id,attempt_id,call_record_id,
                provider_call_id,command_boundary_ref,effect_id,wait_kind,
                required_action_ref,checkpoint_ref,resume_admission_state,
                evidence_refs_json,state,response_ref,wait_version,created_at,
                updated_at,resolved_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open',NULL,0,?,?,NULL)""",
                (
                    wait.wait_ref,
                    wait.schema_version,
                    wait.root_run_id,
                    wait.attempt_id,
                    wait.call_record_id,
                    wait.provider_call_id,
                    wait.command_boundary_ref,
                    wait.effect_id,
                    wait.wait_kind.value,
                    wait.required_action_ref,
                    wait.checkpoint_ref,
                    wait.resume_admission_state.value,
                    canonical_json(list(wait.evidence_refs)),
                    now,
                    now,
                ),
            )
            call_cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET admission_state='waiting_external',call_version=call_version+1,
                    updated_at=?
                WHERE call_record_id=? AND call_version=?
                  AND admission_state=?""",
                (
                    now,
                    call.call_record_id,
                    call.version,
                    call.admission_state.value,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='waiting_external',budget_eligible=0,
                    attempt_version=attempt_version+1,updated_at=?
                WHERE attempt_id=? AND attempt_version=? AND status='running'""",
                (now, attempt.attempt_id, attempt.version),
            )
            if call_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_cas_conflict",
                    "external wait compare-and-set lost",
                )
            await db.execute(
                """UPDATE execution_task_goals
                SET status='waiting_external',goal_version=goal_version+1,
                    updated_at=?
                WHERE root_run_id=? AND status='active'""",
                (now, wait.root_run_id),
            )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='waiting_external',batch_version=batch_version+1,
                    updated_at=?
                WHERE provider_batch_id=?""",
                (now, attempt.provider_batch_id),
            )
            self._fault("external_wait_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait.wait_ref,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_external_wait(row)

    async def resume_external_wait(
        self,
        wait_ref: str,
        response_ref: str,
        *,
        expected_version: int,
    ) -> TaskExternalWait:
        """Satisfy a wait and resume the exact same attempt, plan and call."""

        if not str(response_ref).strip():
            raise ValueError("response_ref is required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict("external_wait_missing", "external wait does not exist")
            wait = self._row_to_external_wait(row)
            if wait.state is ExternalWaitState.SATISFIED:
                if wait.response_ref != response_ref:
                    raise IdempotencyConflict(
                        "external_wait_response_conflict",
                        "external wait already has another response",
                    )
                return wait
            if wait.state is ExternalWaitState.CANCELLED:
                raise TerminalConflict(
                    "external_wait_cancelled", "cancelled external wait cannot resume"
                )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_external_waits
                SET state='satisfied',response_ref=?,wait_version=wait_version+1,
                    updated_at=?,resolved_at=?
                WHERE wait_ref=? AND state='open' AND wait_version=?""",
                (response_ref, now, now, wait_ref, expected_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_cas_conflict",
                    "external wait response compare-and-set lost",
                )
            call_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (wait.call_record_id,),
                )
            ).fetchone()
            attempt_row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (wait.attempt_id,),
                )
            ).fetchone()
            if call_row is None or attempt_row is None:
                raise IdempotencyConflict(
                    "external_wait_orphaned", "external wait lost its call or attempt"
                )
            call = self._row_to_provider_call(call_row)
            attempt = self._row_to_attempt(attempt_row)
            call_cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET admission_state=?,call_version=call_version+1,updated_at=?
                WHERE call_record_id=? AND admission_state='waiting_external'
                  AND call_version=?""",
                (
                    wait.resume_admission_state.value,
                    now,
                    call.call_record_id,
                    call.version,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='running',budget_eligible=1,
                    attempt_version=attempt_version+1,updated_at=?
                WHERE attempt_id=? AND status='waiting_external'
                  AND attempt_version=?""",
                (now, attempt.attempt_id, attempt.version),
            )
            if call_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_resume_conflict",
                    "external wait resume boundary is stale",
                )
            await db.execute(
                """UPDATE execution_task_goals
                SET status='active',goal_version=goal_version+1,updated_at=?
                WHERE root_run_id=? AND status='waiting_external'""",
                (now, wait.root_run_id),
            )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='running',batch_version=batch_version+1,updated_at=?
                WHERE provider_batch_id=? AND status='waiting_external'""",
                (now, attempt.provider_batch_id),
            )
            self._fault("external_wait_resume_before_commit")
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_external_wait(updated)

    async def get_external_wait(self, wait_ref: str) -> TaskExternalWait | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            return None if row is None else self._row_to_external_wait(row)
