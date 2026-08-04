from __future__ import annotations

from dataclasses import replace

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    ActorContext,
    AttemptFailureSet,
    AttemptRecord,
    ExternalWaitKind,
    FailureBackfillState,
    FailureErrorClass,
    FailureSourceKind,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    PlanVersionRecord,
    ProfileLaunchTicket,
    ProfileLaunchTicketState,
    ProviderActionAdmissionState,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderResumeState,
    ProviderTurnFence,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    TaskExternalWait,
    TaskFailureReport,
    TaskGoalRecord,
    fingerprint_json,
)
from deskpet.workflows.store import schema as workflow_schema
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["spawn_profile", "shell"]})


def _spec(run_id: str = "root-run") -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session:request:{run_id}",
        context=RunContext(
            session_id="session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request:{run_id}",
            turn_id=f"turn:{run_id}",
            venue="text",
            workspace={"root": "F:/workspace"},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id=f"trace:{run_id}",
            principal_id="principal",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"run_id": run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
    )


async def _seed_attempt(
    path,
    *,
    fault_hook: str | None = None,
) -> tuple[
    SqliteExecutionUnitOfWork,
    ProviderActionBatch,
    AttemptRecord,
    tuple[ProviderActionCall, ...],
]:
    def inject(point: str) -> None:
        if point == fault_hook:
            raise RuntimeError(f"crash:{point}")

    uow = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=inject
    )
    await uow.create(_spec())
    await uow.create_task_goal(
        TaskGoalRecord(
            goal_id="goal-1",
            root_run_id="root-run",
            task_scope_id="scope-1",
            objective_ref="objective:build-game",
        ),
        PlanVersionRecord(root_run_id="root-run", plan_version=1),
    )
    await uow.open_provider_turn_fence(
        ProviderTurnFence(
            provider_turn_id="turn-1",
            root_run_id="root-run",
            idempotency_key="provider-turn-idem-1",
            request_hash=fingerprint_json({"messages": ["build"]}),
        )
    )
    calls = (
        ProviderActionCall(
            call_record_id="call-record-0",
            root_run_id="root-run",
            provider_batch_id="batch-1",
            call_order=0,
            provider_call_id="spawn-call-0",
            raw_tool_name="spawn_profile",
            raw_arguments_ref="blob:spawn-args",
            raw_arguments_hash=fingerprint_json({"profile": "agent.general"}),
        ),
        ProviderActionCall(
            call_record_id="call-record-1",
            root_run_id="root-run",
            provider_batch_id="batch-1",
            call_order=1,
            provider_call_id="shell-call-1",
            raw_tool_name="shell",
            raw_arguments_ref="blob:shell-args",
            raw_arguments_hash=fingerprint_json({"command": "godot --editor"}),
        ),
    )
    batch = ProviderActionBatch(
        provider_batch_id="batch-1",
        root_run_id="root-run",
        provider_turn_id="turn-1",
        canonical_assistant_batch_ref="blob:assistant-batch",
        batch_fingerprint=fingerprint_json(
            {"calls": [call.provider_call_id for call in calls]}
        ),
        pending_call_count=len(calls),
    )
    attempt = AttemptRecord(
        attempt_id="attempt-1",
        root_run_id="root-run",
        run_id="root-run",
        provider_turn_id="turn-1",
        provider_batch_id="batch-1",
        plan_version=1,
        strategy_fingerprint=fingerprint_json({"strategy": "initial"}),
        planned_call_refs=tuple(call.call_record_id for call in calls),
    )
    return uow, batch, attempt, calls


def _ticket(**overrides) -> ProfileLaunchTicket:
    values = {
        "ticket_ref": "ticket-1",
        "parent_run_id": "root-run",
        "root_run_id": "root-run",
        "task_scope_id": "scope-1",
        "attempt_id": "attempt-1",
        "provider_turn_id": "turn-1",
        "profile_key": "agent.general",
        "driver_kind": "react",
        "profile_catalog_generation": 7,
        "capability_snapshot_ref": "snapshot:7",
        "task_grant_ref": "grant:task-1",
        "spawn_call_id": "spawn-call-0",
        "request_fingerprint": fingerprint_json(
            {"profile": "agent.general", "task": "build tower defense"}
        ),
    }
    values.update(overrides)
    return ProfileLaunchTicket(**values)


@pytest.mark.asyncio
async def test_v9_migrates_to_attempt_and_launch_ticket_schema(tmp_path):
    path = tmp_path / "workflow.db"
    original = workflow_schema.WORKFLOW_SCHEMA_VERSION
    workflow_schema.WORKFLOW_SCHEMA_VERSION = 9
    try:
        await workflow_schema.initialize_workflow_db(path)
    finally:
        workflow_schema.WORKFLOW_SCHEMA_VERSION = original

    await workflow_schema.initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        tables = {
            str(row[0])
            for row in await (
                await db.execute(
                    """SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE 'execution_%'"""
                )
            ).fetchall()
        }
        migration = await (
            await db.execute(
                "SELECT COUNT(*) FROM workflow_schema_migrations WHERE version=10"
            )
        ).fetchone()

    assert version == (workflow_schema.WORKFLOW_SCHEMA_VERSION,)
    assert migration == (1,)
    assert {
        "execution_task_goals",
        "execution_plan_versions",
        "execution_provider_action_calls",
        "execution_attempt_records",
        "execution_task_failure_reports",
        "execution_attempt_failure_sets",
        "execution_task_external_waits",
        "execution_profile_launch_tickets",
    } <= tables


@pytest.mark.asyncio
async def test_provider_batch_crash_rolls_back_and_restart_replays_once(tmp_path):
    path = tmp_path / "workflow.db"
    crashing, batch, attempt, calls = await _seed_attempt(
        path, fault_hook="provider_batch_after_calls"
    )
    with pytest.raises(RuntimeError, match="provider_batch_after_calls"):
        await crashing.accept_provider_batch_and_create_attempt(
            batch, attempt, calls
        )

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    assert await restarted.get_attempt(attempt.attempt_id) is None
    assert await restarted.list_provider_action_calls(batch.provider_batch_id) == ()

    admitted = await restarted.accept_provider_batch_and_create_attempt(
        batch, attempt, calls
    )
    replay = await restarted.accept_provider_batch_and_create_attempt(
        batch, attempt, calls
    )

    assert admitted.idempotent is False
    assert replay.idempotent is True
    assert replay.attempt.attempt_id == attempt.attempt_id
    assert tuple(call.provider_call_id for call in replay.calls) == (
        "spawn-call-0",
        "shell-call-1",
    )


@pytest.mark.asyncio
async def test_success_settlement_atomically_closes_calls_batch_and_attempt(
    tmp_path,
):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    for index, call in enumerate(calls):
        await uow.mark_provider_action_prepared(
            call.call_record_id,
            expected_version=0,
            parsed_arguments_hash=fingerprint_json({"index": index}),
            prepared_call_ref=f"prepared:{index}",
            command_boundary_ref="command-boundary:success",
        )
    outcome_refs = {
        call.call_record_id: f"provider-outcome:{index}"
        for index, call in enumerate(calls)
    }

    settled = await uow.settle_provider_action_success(
        attempt.attempt_id, outcome_refs
    )
    replayed = await uow.settle_provider_action_success(
        attempt.attempt_id, outcome_refs
    )
    settled_calls = await uow.list_provider_action_calls(
        batch.provider_batch_id
    )
    async with aiosqlite.connect(path) as db:
        settled_batch = await (
            await db.execute(
                """SELECT status,pending_call_count,settled_at
                FROM execution_provider_action_batches
                WHERE provider_batch_id=?""",
                (batch.provider_batch_id,),
            )
        ).fetchone()

    assert settled.status.value == "succeeded"
    assert replayed.status.value == "succeeded"
    assert settled_batch is not None
    assert settled_batch[0] == "settled"
    assert settled_batch[1] == 0
    assert settled_batch[2] is not None
    assert all(
        call.admission_state.value == "settled"
        and call.terminal_outcome_ref == outcome_refs[call.call_record_id]
        for call in settled_calls
    )


@pytest.mark.asyncio
async def test_success_settlement_crash_rolls_back_the_whole_ledger(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(
        path, fault_hook="attempt_success_after_calls"
    )
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    outcome_refs = {
        call.call_record_id: f"provider-outcome:{index}"
        for index, call in enumerate(calls)
    }

    with pytest.raises(RuntimeError, match="attempt_success_after_calls"):
        await uow.settle_provider_action_success(
            attempt.attempt_id, outcome_refs
        )

    restarted = SqliteExecutionUnitOfWork(path)
    live_attempt = await restarted.get_attempt(attempt.attempt_id)
    live_calls = await restarted.list_provider_action_calls(
        batch.provider_batch_id
    )
    async with aiosqlite.connect(path) as db:
        live_batch = await (
            await db.execute(
                """SELECT status,pending_call_count,settled_at
                FROM execution_provider_action_batches
                WHERE provider_batch_id=?""",
                (batch.provider_batch_id,),
            )
        ).fetchone()
    assert live_attempt is not None
    assert live_attempt.status.value == "running"
    assert live_batch == ("admitted", 2, None)
    assert all(
        call.admission_state.value == "admitted"
        and call.terminal_outcome_ref is None
        for call in live_calls
    )


@pytest.mark.asyncio
async def test_external_wait_resumes_same_attempt_without_failure_fact(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    prepared = await uow.mark_provider_action_prepared(
        calls[0].call_record_id,
        expected_version=0,
        parsed_arguments_hash=fingerprint_json({"profile": "agent.general"}),
        prepared_call_ref="prepared:spawn-0",
        command_boundary_ref="command-boundary:spawn-0",
    )
    wait = TaskExternalWait(
        wait_ref="wait-1",
        root_run_id="root-run",
        attempt_id="attempt-1",
        call_record_id=prepared.call_record_id,
        provider_call_id=prepared.provider_call_id,
        command_boundary_ref=prepared.command_boundary_ref,
        wait_kind=ExternalWaitKind.CREDENTIAL,
        required_action_ref="action:sign-in",
        resume_admission_state=ProviderActionAdmissionState.PREPARED,
        evidence_refs=("evidence:login-required",),
    )

    staged = await uow.stage_external_wait(wait)
    paused = await uow.get_attempt(attempt.attempt_id)
    assert staged.state.value == "open"
    assert paused is not None
    assert paused.status.value == "waiting_external"
    assert paused.plan_version == 1
    assert paused.budget_eligible is False
    assert await uow.list_task_failure_reports(attempt.attempt_id) == ()

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 102.0)
    resumed = await restarted.resume_external_wait(
        wait.wait_ref, "response:credential-ready", expected_version=0
    )
    live_attempt = await restarted.get_attempt(attempt.attempt_id)
    live_calls = await restarted.list_provider_action_calls(
        batch.provider_batch_id
    )

    assert resumed.state.value == "satisfied"
    assert live_attempt is not None
    assert live_attempt.attempt_id == attempt.attempt_id
    assert live_attempt.plan_version == attempt.plan_version
    assert live_attempt.status.value == "running"
    assert live_calls[0].admission_state.value == "prepared"
    assert await restarted.list_task_failure_reports(attempt.attempt_id) == ()


@pytest.mark.asyncio
async def test_run_cancel_terminalizes_open_external_wait_scope(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    prepared = await uow.mark_provider_action_prepared(
        calls[0].call_record_id,
        expected_version=0,
        parsed_arguments_hash=fingerprint_json({"profile": "agent.general"}),
        prepared_call_ref="prepared:spawn-0",
        command_boundary_ref="command-boundary:spawn-0",
    )
    wait = TaskExternalWait(
        wait_ref="wait-cancel",
        root_run_id="root-run",
        attempt_id=attempt.attempt_id,
        call_record_id=prepared.call_record_id,
        provider_call_id=prepared.provider_call_id,
        command_boundary_ref=prepared.command_boundary_ref,
        wait_kind=ExternalWaitKind.UAC,
        required_action_ref="action:uac",
        resume_admission_state=ProviderActionAdmissionState.PREPARED,
    )
    await uow.stage_external_wait(wait)
    actor = ActorContext(
        principal_id="principal",
        session_id="session",
        auth_epoch=1,
        root_run_id="root-run",
    )
    ref = RunRef("root-run", "session")
    current = await uow.query(ref, actor)
    cancelling = await uow.commit_run_outcome(
        ref.run_id,
        expected_version=current.version,
        cancel_reason="user_stop",
        event=RunEventCandidate(
            event_key="cancel:external-wait",
            kind="cancel_requested",
            status=OutcomeStatus.CANCEL_REQUESTED,
            driver_kind="react",
            payload={"reason": "user_stop"},
        ),
    )

    decisions = await uow.cancel_open_decisions(
        ref, actor, expected_run_version=cancelling.version
    )

    assert decisions == ()
    cancelled_wait = await uow.get_external_wait(wait.wait_ref)
    cancelled_attempt = await uow.get_attempt(attempt.attempt_id)
    cancelled_calls = await uow.list_provider_action_calls(
        batch.provider_batch_id
    )
    cancelled_goal = await uow.get_task_goal("root-run")
    assert cancelled_wait is not None
    assert cancelled_wait.state.value == "cancelled"
    assert cancelled_attempt is not None
    assert cancelled_attempt.status.value == "cancelled"
    assert cancelled_attempt.budget_eligible is False
    assert cancelled_goal is not None
    assert cancelled_goal.status.value == "cancelled"
    assert all(
        call.admission_state.value == "settled"
        for call in cancelled_calls
    )
    assert all(
        str(call.terminal_outcome_ref).startswith("cancelled:wait-cancel:")
        for call in cancelled_calls
    )
    assert await uow.list_task_failure_reports(attempt.attempt_id) == ()


@pytest.mark.asyncio
async def test_failure_set_persists_all_ordered_reports_across_restart(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    reports = tuple(
        TaskFailureReport(
            report_ref=f"report-{index}",
            root_run_id="root-run",
            run_id="root-run",
            task_scope_id="scope-1",
            attempt_id="attempt-1",
            plan_version=1,
            call_record_id=call.call_record_id,
            source_kind=(
                FailureSourceKind.CHILD_LAUNCH
                if index == 0
                else FailureSourceKind.TOOL_EXECUTOR
            ),
            source_identity=f"source-{index}",
            provider_call_id=call.provider_call_id,
            failed_step=f"step-{index}",
            error_class=FailureErrorClass.ENVIRONMENT,
            error_code=f"error-{index}",
            error_fingerprint=fingerprint_json({"error": index}),
            action_fingerprint=fingerprint_json({"action": index}),
            evidence_refs=(f"evidence-{index}",),
            prior_strategy_fingerprints=(attempt.strategy_fingerprint,),
        )
        for index, call in enumerate(calls)
    )
    failure_set = AttemptFailureSet(
        failure_set_id="failure-set-1",
        root_run_id="root-run",
        failed_attempt_id="attempt-1",
        report_refs=tuple(report.report_ref for report in reports),
        primary_report_ref=reports[0].report_ref,
        backfill_state=FailureBackfillState.READY,
        provider_resume_state=ProviderResumeState.PENDING,
    )

    stored = await uow.stage_attempt_failure_set(failure_set, reports)
    restarted = SqliteExecutionUnitOfWork(path)
    recovered_set = await restarted.get_attempt_failure_set(
        failure_set.failure_set_id
    )
    recovered_reports = await restarted.list_task_failure_reports(
        attempt.attempt_id
    )
    failed_attempt = await restarted.get_attempt(attempt.attempt_id)

    assert stored.report_refs == ("report-0", "report-1")
    assert recovered_set == stored
    assert tuple(report.report_ref for report in recovered_reports) == (
        "report-0",
        "report-1",
    )
    assert failed_attempt is not None
    assert failed_attempt.status.value == "failed"


@pytest.mark.asyncio
async def test_same_failure_source_can_be_recorded_in_later_attempt(tmp_path):
    path = tmp_path / "workflow.db"
    uow, first_batch, first_attempt, first_calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(
        first_batch, first_attempt, first_calls
    )
    shared_source = "capability:photo_renamer:photo_rename"
    first_report = TaskFailureReport(
        report_ref="report-attempt-1",
        root_run_id="root-run",
        run_id="root-run",
        task_scope_id="scope-1",
        attempt_id=first_attempt.attempt_id,
        plan_version=1,
        call_record_id=first_calls[0].call_record_id,
        source_kind=FailureSourceKind.TOOL_EXECUTOR,
        source_identity=shared_source,
        provider_call_id=first_calls[0].provider_call_id,
        failed_step="photo_rename",
        error_class=FailureErrorClass.CAPABILITY,
        error_code="worker_failed",
        error_fingerprint=fingerprint_json({"error": "worker_failed"}),
        action_fingerprint=fingerprint_json({"action": "rename"}),
    )
    first_failure_set = AttemptFailureSet(
        failure_set_id="failure-set-attempt-1",
        root_run_id="root-run",
        failed_attempt_id=first_attempt.attempt_id,
        report_refs=(first_report.report_ref,),
        primary_report_ref=first_report.report_ref,
        backfill_state=FailureBackfillState.READY,
        provider_resume_state=ProviderResumeState.PENDING,
    )
    await uow.stage_attempt_failure_set(first_failure_set, (first_report,))

    await uow.append_plan_version(
        PlanVersionRecord(
            root_run_id="root-run",
            plan_version=2,
            trigger_failure_set_id=first_failure_set.failure_set_id,
        )
    )
    await uow.open_provider_turn_fence(
        ProviderTurnFence(
            provider_turn_id="turn-2",
            root_run_id="root-run",
            idempotency_key="provider-turn-idem-2",
            request_hash=fingerprint_json({"messages": ["retry"]}),
        )
    )
    second_call = ProviderActionCall(
        call_record_id="call-record-2",
        root_run_id="root-run",
        provider_batch_id="batch-2",
        call_order=0,
        provider_call_id="photo-rename-call-2",
        raw_tool_name="photo_rename",
        raw_arguments_ref="blob:photo-rename-args-2",
        raw_arguments_hash=fingerprint_json({"path": "F:/photos/a.jpg"}),
    )
    second_batch = ProviderActionBatch(
        provider_batch_id="batch-2",
        root_run_id="root-run",
        provider_turn_id="turn-2",
        canonical_assistant_batch_ref="blob:assistant-batch-2",
        batch_fingerprint=fingerprint_json({"calls": ["photo-rename-call-2"]}),
        pending_call_count=1,
    )
    second_attempt = AttemptRecord(
        attempt_id="attempt-2",
        root_run_id="root-run",
        run_id="root-run",
        provider_turn_id="turn-2",
        provider_batch_id="batch-2",
        plan_version=2,
        trigger_failure_set_id=first_failure_set.failure_set_id,
        supersedes_attempt_id=first_attempt.attempt_id,
        strategy_fingerprint=fingerprint_json({"strategy": "repair-retry"}),
        planned_call_refs=(second_call.call_record_id,),
    )
    await uow.accept_provider_batch_and_create_attempt(
        second_batch, second_attempt, (second_call,)
    )
    second_report = replace(
        first_report,
        report_ref="report-attempt-2",
        attempt_id=second_attempt.attempt_id,
        plan_version=2,
        call_record_id=second_call.call_record_id,
        provider_call_id=second_call.provider_call_id,
    )
    second_failure_set = AttemptFailureSet(
        failure_set_id="failure-set-attempt-2",
        root_run_id="root-run",
        failed_attempt_id=second_attempt.attempt_id,
        report_refs=(second_report.report_ref,),
        primary_report_ref=second_report.report_ref,
        backfill_state=FailureBackfillState.READY,
        provider_resume_state=ProviderResumeState.PENDING,
    )

    await uow.stage_attempt_failure_set(second_failure_set, (second_report,))

    restarted = SqliteExecutionUnitOfWork(path)
    reports = (
        *await restarted.list_task_failure_reports(first_attempt.attempt_id),
        *await restarted.list_task_failure_reports(second_attempt.attempt_id),
    )
    assert [report.source_identity for report in reports] == [
        shared_source,
        shared_source,
    ]
    assert [report.attempt_id for report in reports] == [
        "attempt-1",
        "attempt-2",
    ]


@pytest.mark.asyncio
async def test_profile_ticket_unique_one_shot_and_crash_recovery(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    ticket = _ticket()

    def issue_crash(point: str) -> None:
        if point == "profile_ticket_issue_before_commit":
            raise RuntimeError("issue crash")

    crashing_issue = SqliteExecutionUnitOfWork(
        path, fault_injector=issue_crash
    )
    with pytest.raises(RuntimeError, match="issue crash"):
        await crashing_issue.issue_profile_launch_ticket(ticket)

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 103.0)
    assert await restarted.get_profile_launch_ticket(ticket.ticket_ref) is None
    issued = await restarted.issue_profile_launch_ticket(ticket)
    assert issued.state is ProfileLaunchTicketState.ISSUED
    assert await restarted.issue_profile_launch_ticket(ticket) == issued

    conflicting = replace(
        ticket,
        ticket_ref="ticket-conflict",
        profile_key="agent.coder",
        request_fingerprint=fingerprint_json(
            {"profile": "agent.coder", "task": "build tower defense"}
        ),
    )
    with pytest.raises(IdempotencyConflict, match="spawn call"):
        await restarted.issue_profile_launch_ticket(conflicting)

    def consume_crash(point: str) -> None:
        if point == "profile_ticket_consume_after_cas":
            raise RuntimeError("consume crash")

    crashing_consume = SqliteExecutionUnitOfWork(
        path, clock=lambda: 104.0, fault_injector=consume_crash
    )
    with pytest.raises(RuntimeError, match="consume crash"):
        await crashing_consume.consume_profile_launch_ticket(
            ticket.ticket_ref,
            request_fingerprint=ticket.request_fingerprint,
            child_command_id="child-command-1",
            child_run_id="child-run-1",
            expected_version=0,
        )

    recovered_uow = SqliteExecutionUnitOfWork(path, clock=lambda: 105.0)
    recoverable = await recovered_uow.recover_profile_launch_tickets()
    still_issued = await recovered_uow.get_profile_launch_ticket(
        ticket.ticket_ref
    )
    assert tuple(item.ticket_ref for item in recoverable) == (ticket.ticket_ref,)
    assert still_issued is not None
    assert still_issued.state is ProfileLaunchTicketState.ISSUED

    consumed = await recovered_uow.consume_profile_launch_ticket(
        ticket.ticket_ref,
        request_fingerprint=ticket.request_fingerprint,
        child_command_id="child-command-1",
        child_run_id="child-run-1",
        expected_version=0,
    )
    replay = await SqliteExecutionUnitOfWork(path).consume_profile_launch_ticket(
        ticket.ticket_ref,
        request_fingerprint=ticket.request_fingerprint,
        child_command_id="child-command-1",
        child_run_id="child-run-1",
        expected_version=0,
    )

    assert consumed.idempotent is False
    assert replay.idempotent is True
    assert replay.ticket.child_run_id == "child-run-1"
    with pytest.raises(IdempotencyConflict, match="fingerprint"):
        await recovered_uow.consume_profile_launch_ticket(
            ticket.ticket_ref,
            request_fingerprint=fingerprint_json({"different": True}),
            child_command_id="child-command-1",
            child_run_id="child-run-1",
            expected_version=0,
        )


@pytest.mark.asyncio
async def test_personal_selection_second_spawn_has_explicit_error_code(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    selection_id = "personal-selection-1"
    selection_fingerprint = fingerprint_json(
        {"selection_id": selection_id, "graph": "daily-three"}
    )
    first = _ticket(
        profile_key="workflow.personal_v1",
        personal_selection_id=selection_id,
        personal_selection_fingerprint=selection_fingerprint,
    )
    await uow.issue_profile_launch_ticket(first)

    second = _ticket(
        ticket_ref="ticket-personal-second",
        profile_key="workflow.personal_v1",
        spawn_call_id="shell-call-1",
        personal_selection_id=selection_id,
        personal_selection_fingerprint=selection_fingerprint,
        request_fingerprint=fingerprint_json(
            {"profile": "workflow.personal_v1", "call": "shell-call-1"}
        ),
    )
    with pytest.raises(IdempotencyConflict) as captured:
        await uow.issue_profile_launch_ticket(second)

    assert captured.value.code == "selection_already_consumed"
    assert await uow.get_profile_launch_ticket(second.ticket_ref) is None


@pytest.mark.asyncio
async def test_profile_ticket_recovery_cancels_only_cancelled_goal(tmp_path):
    path = tmp_path / "workflow.db"
    uow, batch, attempt, calls = await _seed_attempt(path)
    await uow.accept_provider_batch_and_create_attempt(batch, attempt, calls)
    ticket = await uow.issue_profile_launch_ticket(_ticket())
    goal = await uow.get_task_goal("root-run")
    assert goal is not None

    await uow.cancel_task_goal("root-run", expected_version=goal.version)
    assert await uow.recover_profile_launch_tickets() == ()
    cancelled = await SqliteExecutionUnitOfWork(path).get_profile_launch_ticket(
        ticket.ticket_ref
    )

    assert cancelled is not None
    assert cancelled.state is ProfileLaunchTicketState.CANCELLED
    assert cancelled.cancelled_at is not None
