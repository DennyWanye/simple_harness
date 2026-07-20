from __future__ import annotations

import asyncio
from dataclasses import replace

import aiosqlite
import pytest

from deskpet.execution import (
    ActorContext,
    AttachmentPolicy,
    AuthorizationError,
    DeliveryPolicy,
    DeliverySpec,
    ExecutionLedger,
    FinalizeRunResult,
    IdempotencyConflict,
    LegacyRunProjection,
    OutcomeStatus,
    ParentCycleError,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunLinkSpec,
    RunRef,
    RunStatus,
    TerminalConflict,
    WorkflowRunSeed,
    delegate_idempotency_key,
    fingerprint_json,
    root_idempotency_key,
    team_idempotency_key,
    workflow_idempotency_key,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork, WorkflowRunStore


CAPABILITY = {"tools": ["read", "write"], "scope": "workspace"}
CAPABILITY_HASH = fingerprint_json(CAPABILITY)


def _spec(
    run_id: str,
    *,
    key: str | None = None,
    persistence: PersistenceLevel = PersistenceLevel.DURABLE,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
    session_id: str = "session-a",
    principal_id: str = "principal-a",
    auth_epoch: int = 3,
    driver_kind: str = "react",
    profile_key: str = "chat",
    payload: object = None,
) -> RunCreate:
    root = root_run_id or run_id
    request_id = f"request-{run_id}"
    turn_id = f"turn-{run_id}"
    context = RunContext(
        session_id=session_id,
        root_run_id=root,
        parent_run_id=parent_run_id,
        request_id=request_id,
        turn_id=turn_id,
        venue="text",
        workspace={"root": "F:/workspace"},
        capability_hash=CAPABILITY_HASH,
        provider_plan={"model": "fixture"},
        trace_id=f"trace-{run_id}",
        principal_id=principal_id,
        auth_epoch=auth_epoch,
    )
    return RunCreate(
        run_id=run_id,
        idempotency_key=key or root_idempotency_key(session_id, request_id, turn_id),
        context=context,
        payload_fingerprint=fingerprint_json(
            {"prompt": "hello"} if payload is None else payload
        ),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind=driver_kind,
        profile_key=profile_key,
        persistence_level=persistence,
    )


def _actor(*, session_id: str = "session-a", principal_id: str = "principal-a"):
    return ActorContext(
        principal_id=principal_id,
        session_id=session_id,
        auth_epoch=3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    (
        root_idempotency_key("session-a", "request", "turn"),
        delegate_idempotency_key("parent", "command", "profile"),
        team_idempotency_key("team", "task", 7),
        workflow_idempotency_key("parent", "node", "slot"),
    ),
)
async def test_all_key_namespaces_converge_under_concurrent_create(tmp_path, key):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    first = _spec("proposed-a", key=key)
    second = _spec("proposed-b", key=key)
    # The first committed run id/trace are authoritative.  A concurrent caller
    # may have proposed different generated values before discovering it.
    second = replace(
        second,
        context=replace(
            second.context,
            request_id=first.context.request_id,
            turn_id=first.context.turn_id,
        ),
    )

    results = await asyncio.gather(uow.create(first), uow.create(second))

    assert sum(result.created for result in results) == 1
    assert len({result.record.run_id for result in results}) == 1
    assert {result.record.run_id for result in results} <= {"proposed-a", "proposed-b"}

    conflict = replace(
        first,
        run_id="proposed-conflict",
        context=replace(
            first.context,
            root_run_id="proposed-conflict",
            trace_id="trace-conflict",
        ),
        payload_fingerprint=fingerprint_json({"prompt": "different"}),
    )
    with pytest.raises(IdempotencyConflict) as error:
        await uow.create(conflict)
    assert error.value.code == "idempotency_conflict"


@pytest.mark.asyncio
async def test_ephemeral_create_does_not_touch_sqlite_and_promotes_same_id(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    ledger = ExecutionLedger(uow, max_active_runs=4)
    ephemeral = _spec("run-active", persistence=PersistenceLevel.EPHEMERAL)

    created = await ledger.create(ephemeral)
    assert created.created is True
    assert created.record.persistence_level is PersistenceLevel.EPHEMERAL
    assert path.exists() is False

    promoted = await ledger.promote(
        replace(ephemeral, persistence_level=PersistenceLevel.DURABLE),
        expected_version=0,
    )
    assert promoted.record.run_id == "run-active"
    assert promoted.record.persistence_level is PersistenceLevel.DURABLE
    assert promoted.record.version == 1
    assert (await ledger.query(RunRef("run-active", "session-a"), _actor())).run_id == (
        "run-active"
    )

    async with aiosqlite.connect(path) as db:
        rows = await (await db.execute("SELECT run_id FROM execution_runs")).fetchall()
    assert rows == [("run-active",)]


@pytest.mark.asyncio
async def test_workflow_start_is_one_transaction_and_reuses_execution_id(tmp_path):
    path = tmp_path / "workflow.db"

    def fail(point: str) -> None:
        if point == "start_workflow_after_execution":
            raise RuntimeError("injected start fault")

    spec = _spec(
        "workflow-run",
        driver_kind="workflow",
        profile_key="deep_research/v7",
    )
    workflow = WorkflowRunSeed(
        request_key="workflow-request-key",
        workflow_name="deep_research",
        workflow_version="v7",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash=CAPABILITY_HASH,
        capability_snapshot=CAPABILITY,
        state_schema_version=7,
        trace_id=spec.context.trace_id,
        thread_id="workflow-thread",
    )
    accepted = RunEventCandidate(
        event_key="accepted",
        kind="run.accepted",
        status=OutcomeStatus.ACCEPTED,
        driver_kind="workflow",
    )
    broken = SqliteExecutionUnitOfWork(path, fault_injector=fail)
    with pytest.raises(RuntimeError, match="injected start fault"):
        await broken.start_workflow(spec, workflow, accepted_event=accepted)

    async with aiosqlite.connect(path) as db:
        execution_count = await (
            await db.execute("SELECT COUNT(*) FROM execution_runs")
        ).fetchone()
        workflow_count = await (
            await db.execute("SELECT COUNT(*) FROM workflow_runs")
        ).fetchone()
    assert execution_count == (0,)
    assert workflow_count == (0,)

    uow = SqliteExecutionUnitOfWork(path)
    result = await uow.start_workflow(spec, workflow, accepted_event=accepted)
    replay = await uow.start_workflow(spec, workflow, accepted_event=accepted)
    assert result.record.run_id == "workflow-run"
    assert result.record.version == 1
    assert replay.created is False
    assert replay.record.run_id == result.record.run_id
    async with aiosqlite.connect(path) as db:
        pair = await (
            await db.execute(
                """SELECT execution_runs.run_id,workflow_runs.run_id
                FROM execution_runs JOIN workflow_runs USING(run_id)"""
            )
        ).fetchone()
        event_count = await (
            await db.execute("SELECT COUNT(*) FROM execution_events")
        ).fetchone()
    assert pair == ("workflow-run", "workflow-run")
    assert event_count == (1,)


@pytest.mark.asyncio
async def test_terminal_race_has_one_winner_and_one_outbox_intent(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.create(_spec("run-terminal"))
    delivery = DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session-a",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )
    completed = RunEventCandidate(
        event_key="terminal-completed",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
    )
    failed = RunEventCandidate(
        event_key="terminal-failed",
        kind="run.final",
        status=OutcomeStatus.FAILED,
        driver_kind="react",
        error={"code": "fixture"},
    )

    results = await asyncio.gather(
        uow.finalize(
            "run-terminal",
            expected_version=0,
            terminal_status=RunStatus.COMPLETED,
            event=completed,
            deliveries=(delivery,),
        ),
        uow.finalize(
            "run-terminal",
            expected_version=0,
            terminal_status=RunStatus.FAILED,
            event=failed,
            deliveries=(delivery,),
        ),
        return_exceptions=True,
    )
    winners = [item for item in results if isinstance(item, FinalizeRunResult)]
    losers = [item for item in results if isinstance(item, BaseException)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], TerminalConflict)

    winner = winners[0]
    replay = await uow.finalize(
        "run-terminal",
        expected_version=0,
        terminal_status=winner.record.status,
        event=winner.event.candidate,
        deliveries=(delivery,),
    )
    assert replay.idempotent is True

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT status,version,durable_seq,terminal_event_id FROM execution_runs"
            )
        ).fetchone()
        events = await (await db.execute("SELECT COUNT(*) FROM execution_events")).fetchone()
        deliveries = await (
            await db.execute("SELECT COUNT(*) FROM execution_deliveries")
        ).fetchone()
    assert row[0] in {"completed", "failed"}
    assert row[1:3] == (1, 1)
    assert row[3]
    assert events == (1,)
    assert deliveries == (1,)


@pytest.mark.asyncio
async def test_terminal_outbox_fault_rolls_back_the_entire_intent(tmp_path):
    path = tmp_path / "workflow.db"
    base = SqliteExecutionUnitOfWork(path)
    await base.create(_spec("run-fault"))

    def fail(point: str) -> None:
        if point == "finalize_after_outbox":
            raise RuntimeError("injected terminal fault")

    broken = SqliteExecutionUnitOfWork(path, fault_injector=fail)
    event = RunEventCandidate(
        event_key="terminal",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
    )
    with pytest.raises(RuntimeError, match="injected terminal fault"):
        await broken.finalize(
            "run-fault",
            expected_version=0,
            terminal_status=RunStatus.COMPLETED,
            event=event,
        )

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT status,version,durable_seq,terminal_event_id FROM execution_runs"
            )
        ).fetchone()
        events = await (await db.execute("SELECT COUNT(*) FROM execution_events")).fetchone()
    assert row == ("created", 0, 0, None)
    assert events == (0,)


@pytest.mark.asyncio
async def test_workflow_cancel_updates_coarse_and_scheduler_rows_once(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    spec = _spec(
        "workflow-cancel",
        driver_kind="workflow",
        profile_key="code_complex/v1",
    )
    workflow = WorkflowRunSeed(
        request_key="workflow-cancel-request",
        workflow_name="code_complex",
        workflow_version="v1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash=CAPABILITY_HASH,
        capability_snapshot=CAPABILITY,
        state_schema_version=1,
        trace_id=spec.context.trace_id,
        thread_id="workflow-cancel-thread",
    )
    await uow.start_workflow(spec, workflow)
    cancel = RunEventCandidate(
        event_key="cancel-requested",
        kind="run.cancel_requested",
        status=OutcomeStatus.CANCEL_REQUESTED,
        driver_kind="workflow",
        payload={"reason": "user"},
    )
    record = await uow.request_cancel(
        spec.run_id,
        expected_version=0,
        reason="user",
        event=cancel,
    )
    replay = await uow.request_cancel(
        spec.run_id,
        expected_version=0,
        reason="user",
        event=cancel,
    )
    assert record.status is RunStatus.CANCEL_REQUESTED
    assert record.version == 1
    assert replay.version == 1

    with pytest.raises(IdempotencyConflict):
        await uow.request_cancel(
            spec.run_id,
            expected_version=1,
            reason="other",
            event=replace(cancel, event_key="different-cancel"),
        )

    async with aiosqlite.connect(path) as db:
        statuses = await (
            await db.execute(
                """SELECT execution_runs.status,workflow_runs.status,
                execution_runs.version,execution_runs.durable_seq
                FROM execution_runs JOIN workflow_runs USING(run_id)"""
            )
        ).fetchone()
        events = await (await db.execute("SELECT COUNT(*) FROM execution_events")).fetchone()
    assert statuses == ("cancel_requested", "cancel_requested", 1, 1)
    assert events == (1,)


@pytest.mark.asyncio
async def test_structural_links_reject_parent_cycles(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.create(_spec("root"))
    await uow.create(_spec("child", parent_run_id="root", root_run_id="root"))
    await uow.create(_spec("grandchild", parent_run_id="child", root_run_id="root"))
    await uow.link(
        RunLinkSpec(
            link_id="root-child",
            root_run_id="root",
            parent_run_id="root",
            child_run_id="child",
            attachment_policy=AttachmentPolicy.ATTACHED,
        ),
        expected_child_version=0,
    )
    await uow.link(
        RunLinkSpec(
            link_id="child-grandchild",
            root_run_id="root",
            parent_run_id="child",
            child_run_id="grandchild",
            attachment_policy=AttachmentPolicy.ATTACHED,
        ),
        expected_child_version=0,
    )

    with pytest.raises(ParentCycleError):
        await uow.link(
            RunLinkSpec(
                link_id="grandchild-root",
                root_run_id="root",
                parent_run_id="grandchild",
                child_run_id="root",
                attachment_policy=AttachmentPolicy.ATTACHED,
            ),
            expected_child_version=0,
        )
    children = await uow.list_children(RunRef("root", "session-a"), _actor())
    assert [child.run_id for child in children] == ["child"]


@pytest.mark.asyncio
async def test_actor_authorization_fails_closed_across_session_and_principal(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await uow.create(_spec("owned"))
    ref = RunRef("owned", "session-a")
    assert (await uow.query(ref, _actor())).run_id == "owned"

    with pytest.raises(AuthorizationError):
        await uow.query(
            RunRef("owned", "session-b"),
            _actor(session_id="session-b"),
        )
    with pytest.raises(AuthorizationError):
        await uow.query(ref, _actor(principal_id="principal-b"))

    internal = ActorContext(
        principal_id="driver",
        session_id="session-a",
        auth_epoch=0,
        root_run_id="owned",
        capability_hash=CAPABILITY_HASH,
        expires_at=101.0,
        internal=True,
    )
    assert (await uow.query(ref, internal)).run_id == "owned"
    with pytest.raises(AuthorizationError):
        await uow.query(ref, replace(internal, expires_at=99.0))


@pytest.mark.asyncio
async def test_grant_schema_accepts_only_exact_allowed_permission_decision(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.create(_spec("permission-run"))
    args_hash = fingerprint_json({"path": "file.txt"})
    scope_hash = fingerprint_json({"root": "F:/workspace"})
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """INSERT INTO execution_decisions(
            decision_id,schema_version,run_id,nonce,kind,status,
            prompt_schema_version,prompt_json,response_schema_version,response_json,
            decision_version,expires_at,created_at,resolved_at
            ) VALUES('plan-decision',1,'permission-run','plan-nonce','plan','allowed',
            1,'{}',1,'{}',1,200.0,100.0,101.0)"""
        )
        await db.execute(
            """INSERT INTO execution_decisions(
            decision_id,schema_version,run_id,nonce,kind,status,
            prompt_schema_version,prompt_json,response_schema_version,response_json,
            call_id,effect_id,tool_name,args_hash,capability_hash,scope_hash,
            decision_version,expires_at,created_at,resolved_at
            ) VALUES('permission-decision',1,'permission-run','permission-nonce',
            'permission','allowed',1,'{}',1,'{}','call','effect','write_file',
            ?,?,?,1,200.0,100.0,101.0)""",
            (args_hash, CAPABILITY_HASH, scope_hash),
        )
        await db.commit()

        with pytest.raises(aiosqlite.IntegrityError, match="invalid_execution_grant"):
            await db.execute(
                """INSERT INTO execution_grants(
                grant_id,schema_version,decision_id,run_id,call_id,effect_id,
                tool_name,args_hash,capability_hash,scope_hash,status,grant_version,
                expires_at,created_at
                ) VALUES('invalid-grant',1,'plan-decision','permission-run','call',
                'effect','write_file',?,?,?,'issued',0,200.0,101.0)""",
                (args_hash, CAPABILITY_HASH, scope_hash),
            )
        await db.rollback()

        await db.execute(
            """INSERT INTO execution_grants(
            grant_id,schema_version,decision_id,run_id,call_id,effect_id,
            tool_name,args_hash,capability_hash,scope_hash,status,grant_version,
            expires_at,created_at
            ) VALUES('valid-grant',1,'permission-decision','permission-run','call',
            'effect','write_file',?,?,?,'issued',0,200.0,101.0)""",
            (args_hash, CAPABILITY_HASH, scope_hash),
        )
        await db.commit()
        grants = await (
            await db.execute("SELECT grant_id FROM execution_grants")
        ).fetchall()
    assert grants == [("valid-grant",)]


@pytest.mark.asyncio
async def test_legacy_workflow_query_is_read_only_and_never_backfills(tmp_path):
    path = tmp_path / "workflow.db"
    legacy = WorkflowRunStore(path)
    run_id, _ = await legacy.create_run(
        request_key="legacy-request",
        session_id="legacy-session",
        request_id="legacy-r",
        turn_id="legacy-t",
        workflow_name="deep_research",
        workflow_version="v7",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="legacy-capability",
        capability_snapshot={},
        state_schema_version=7,
    )
    uow = SqliteExecutionUnitOfWork(path)
    projection = await uow.query(
        RunRef(run_id, "legacy-session"),
        ActorContext(
            principal_id="legacy-principal",
            session_id="legacy-session",
            auth_epoch=0,
        ),
    )
    assert isinstance(projection, LegacyRunProjection)
    assert projection.read_only is True
    async with aiosqlite.connect(path) as db:
        count = await (
            await db.execute("SELECT COUNT(*) FROM execution_runs")
        ).fetchone()
    assert count == (0,)
