from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from deskpet.agent.team.team_store import TeamChildRunCommand, TeamStore
from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    ChildCommandIntent,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    fingerprint_json,
    team_idempotency_key,
)
from deskpet.harness.adapters.team import TeamChildRunReconciler
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_REF = fingerprint_json({"tools": ["read"]})


async def _execution(path: Path):
    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()
    parent = (
        await uow.create(
            RunCreate(
                run_id="team-parent",
                idempotency_key="root:team-parent",
                context=RunContext(
                    session_id="team-session",
                    root_run_id="team-parent",
                    parent_run_id=None,
                    request_id="team-request",
                    turn_id="team-turn",
                    venue="text",
                    workspace={"root": "F:/workspace"},
                    capability_hash=CAPABILITY_REF,
                    provider_plan={"model": "fixture"},
                    trace_id="team-trace",
                    principal_id="team-principal",
                    auth_epoch=3,
                ),
                payload_fingerprint=fingerprint_json({"team": "fault-team"}),
                capability_fingerprint=CAPABILITY_REF,
                driver_kind="react",
                profile_key="react.default",
                persistence_level=PersistenceLevel.DURABLE,
            )
        )
    ).record
    return uow, parent


def _intent_factory(parent, *, marker: str = "frozen-v1"):
    def build(operation_id, task) -> ChildCommandIntent:
        child_request = {
            "task": task.description,
            "driver_kind": "react",
            "profile_marker": marker,
        }
        child_run_id = f"team-child-{task.task_id}-{task.claim_epoch}"
        child_spec = RunCreate(
            run_id=child_run_id,
            idempotency_key=operation_id,
            context=RunContext(
                session_id=parent.context.session_id,
                root_run_id=parent.context.root_run_id,
                parent_run_id=parent.run_id,
                request_id=f"{parent.context.request_id}:{task.task_id}",
                turn_id=parent.context.turn_id,
                venue=parent.context.venue,
                workspace={"root": "F:/workspace"},
                capability_hash=CAPABILITY_REF,
                provider_plan={"model": marker},
                trace_id=f"team-trace:{task.task_id}:{task.claim_epoch}",
                principal_id=parent.context.principal_id,
                auth_epoch=parent.context.auth_epoch,
            ),
            payload_fingerprint=fingerprint_json(child_request),
            capability_fingerprint=CAPABILITY_REF,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
        )
        return ChildCommandIntent(
            operation_id=operation_id,
            parent_run_id=parent.run_id,
            command_id=operation_id,
            child_spec=child_spec,
            child_request=child_request,
            capability_subset=("read",),
            attachment_policy=AttachmentPolicy.DETACHED,
            capability_snapshot_ref=CAPABILITY_REF,
        )

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hook", ("team_saga_after_task_claim", "team_saga_after_command_enqueue")
)
async def test_claim_and_frozen_outbox_are_one_transaction(tmp_path, hook) -> None:
    uow, parent = await _execution(tmp_path / "execution.db")
    base = tmp_path / "teams"
    healthy = TeamStore(base)
    task_id = await healthy.create_task("fault-team", "frozen task")

    def fail(point: str) -> None:
        if point == hook:
            raise RuntimeError(f"crash:{point}")

    crashing = TeamStore(base, fault_injector=fail)
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.claim_task_with_child_command(
            "fault-team", "worker", intent_factory=_intent_factory(parent)
        )
    task = await healthy.get_task("fault-team", task_id)
    assert task is not None and task.status == "pending" and task.claim_epoch == 0
    assert await healthy.pending_child_commands("fault-team") == ()

    claimed = await TeamStore(base).claim_task_with_child_command(
        "fault-team", "worker", intent_factory=_intent_factory(parent)
    )
    assert claimed is not None
    task, command = claimed
    assert command.operation_id == team_idempotency_key(
        "fault-team", task.task_id, task.claim_epoch
    )
    assert command.operation_id == f"team:fault-team:{task.task_id}:1"
    frozen = ChildCommandIntent.from_dict(json.loads(command.intent_payload_json or ""))
    assert frozen.child_request["profile_marker"] == "frozen-v1"
    assert task.claimed_pending_run == frozen.child_run_id
    assert await TeamStore(base).update_task(
        "fault-team", task.task_id, "done", "message is not a terminal"
    ) is False
    assert await uow.get_child_command(command.operation_id) is None


@pytest.mark.asyncio
async def test_generic_commit_then_domain_ack_crash_replays_one_frozen_intent(tmp_path) -> None:
    uow, parent = await _execution(tmp_path / "execution.db")
    base = tmp_path / "teams"
    store = TeamStore(base)
    await store.create_task("fault-team", "delegate exactly once")
    claimed = await store.claim_task_with_child_command(
        "fault-team", "worker", intent_factory=_intent_factory(parent)
    )
    assert claimed is not None
    operation_id = claimed[1].operation_id

    def fail(point: str) -> None:
        if point == "team_saga_before_domain_ack":
            raise RuntimeError("crash:team_saga_before_domain_ack")

    failed = TeamChildRunReconciler(TeamStore(base, fault_injector=fail), uow)
    await failed.reconcile_commands_once("fault-team")
    assert any("team_saga_before_domain_ack" in item for item in failed.last_errors)
    first = await uow.get_child_command(operation_id)
    assert first is not None
    assert len(await store.pending_child_commands("fault-team")) == 1
    changed_intent = _intent_factory(parent, marker="changed-after-restart")(
        operation_id, claimed[0]
    )
    assert changed_intent.intent_fingerprint != first.intent.intent_fingerprint

    restarted = TeamChildRunReconciler(TeamStore(base), uow)
    await restarted.reconcile_commands_once("fault-team")
    assert restarted.last_errors == ()
    assert await store.pending_child_commands("fault-team") == ()
    replay = await uow.get_child_command(operation_id)
    assert replay is not None
    assert replay.intent.intent_fingerprint == first.intent.intent_fingerprint
    assert replay.intent.child_request["profile_marker"] == "frozen-v1"
    assert replay.intent.child_spec.context.provider_plan["model"] == "frozen-v1"
    assert replay.intent.child_spec.context.auth_epoch == 3
    async with aiosqlite.connect(tmp_path / "execution.db") as db:
        count = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_child_commands WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
    assert count == (1,)


@pytest.mark.asyncio
async def test_terminal_inbox_crash_replays_exact_text_to_team_task(tmp_path) -> None:
    uow, parent = await _execution(tmp_path / "execution.db")
    base = tmp_path / "teams"
    store = TeamStore(base)
    task_id = await store.create_task("fault-team", "finish through child")
    claimed = await store.claim_task_with_child_command(
        "fault-team", "worker", intent_factory=_intent_factory(parent)
    )
    assert claimed is not None
    operation_id = claimed[1].operation_id
    reconciler = TeamChildRunReconciler(store, uow)
    await reconciler.reconcile_commands_once("fault-team")
    leased = await uow.lease_child_commands(
        owner="team-scheduler", limit=1, lease_seconds=30
    )
    assert len(leased) == 1
    scheduled = await uow.schedule_child_command(
        operation_id,
        lease_owner="team-scheduler",
        lease_epoch=leased[0].schedule_lease_epoch,
    )
    await uow.acknowledge_child_command(
        operation_id,
        lease_owner="team-scheduler",
        lease_epoch=scheduled.schedule_lease_epoch,
    )
    context = scheduled.intent.child_spec.context
    actor = ActorContext(
        context.principal_id,
        context.session_id,
        context.auth_epoch,
        root_run_id=context.root_run_id,
    )
    child = await uow.query(RunRef(scheduled.child_run_id, context.session_id), actor)
    await uow.finalize_child_and_enqueue_parent_signal(
        operation_id,
        expected_version=child.version,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="team-terminal",
            kind="final",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            payload={"text": "exact team result", "content": "legacy wrong value"},
        ),
        value={"text": "exact team result"},
    )

    def fail(point: str) -> None:
        if point == "team_saga_before_terminal_update":
            raise RuntimeError("crash:team_saga_before_terminal_update")

    failed = TeamChildRunReconciler(TeamStore(base, fault_injector=fail), uow)
    await failed.reconcile_terminals_once("fault-team")
    assert any("team_saga_before_terminal_update" in item for item in failed.last_errors)
    task = await store.get_task("fault-team", task_id)
    assert task is not None and task.status == "claimed" and task.result is None

    restarted = TeamChildRunReconciler(TeamStore(base), uow)
    await restarted.reconcile_terminals_once("fault-team")
    task = await store.get_task("fault-team", task_id)
    assert restarted.last_errors == ()
    assert task is not None and task.status == "done"
    assert task.result == "exact team result"
    assert task.claimed_pending_run is None
    await restarted.reconcile_terminals_once("fault-team")
    async with aiosqlite.connect(base / "fault-team.db") as db:
        count = await (
            await db.execute("SELECT COUNT(*) FROM team_childrun_inbox")
        ).fetchone()
    assert count == (1,)


def test_reconciler_fails_closed_for_legacy_nullable_outbox() -> None:
    legacy = TeamChildRunCommand(
        "team:t:task:1", "t", "task", 1, "child", "pending", 0, 1.0
    )
    with pytest.raises(ValueError, match="no frozen intent"):
        TeamChildRunReconciler._intent(legacy)


@pytest.mark.asyncio
async def test_migrated_legacy_nullable_outbox_is_not_reconstructed(tmp_path) -> None:
    base = tmp_path / "teams"
    base.mkdir()
    path = base / "legacy.db"
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE team_childrun_commands(
            operation_id TEXT PRIMARY KEY,team_id TEXT NOT NULL,task_id TEXT NOT NULL,
            claim_epoch INTEGER NOT NULL,child_run_id TEXT NOT NULL,status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,acked_at REAL
            )"""
        )
        await db.commit()
    store = TeamStore(base)
    task_id = await store.create_task("legacy", "old command")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE tasks SET status='claimed',claimed_by='worker',claim_epoch=1,
            claimed_pending_run='legacy-child' WHERE task_id=?""",
            (task_id,),
        )
        await db.execute(
            """INSERT INTO team_childrun_commands(
            operation_id,team_id,task_id,claim_epoch,child_run_id,status,created_at
            ) VALUES(?,?,?,?,?,'pending',1.0)""",
            (f"team:legacy:{task_id}:1", "legacy", task_id, 1, "legacy-child"),
        )
        await db.commit()
    commands = await store.pending_child_commands("legacy")
    assert len(commands) == 1 and commands[0].intent_payload_json is None
    with pytest.raises(ValueError, match="no frozen intent"):
        TeamChildRunReconciler._intent(commands[0])


def test_team_operation_id_has_no_private_hash_implementation() -> None:
    source = Path("backend/deskpet/agent/team/team_store.py").read_text("utf-8")
    assert "_team_operation_id" not in source
    assert "hashlib.sha256" not in source
