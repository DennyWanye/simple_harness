from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from deskpet.agent.goal_store import SessionGoalStore
from deskpet.execution.contracts import (
    ActorContext, RunRef, RunStatus, WorkflowRunSeed, fingerprint_json,
)
from deskpet.harness.bootstrap import build_harness_runtime
from deskpet.harness.contracts import HostContext, RegisteredDriver
from deskpet.harness.ports import ChildTerminalSignal, DriverTerminalCandidate
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.projector import (
    ExecutionDeliveryDispatcher, GoalTerminalProjection, SinkRegistration,
)
from deskpet.harness.router import ClassifiedRoute
from deskpet.memory.session_db import SessionDB
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class _Classifier:
    def __init__(self, profile: str) -> None:
        self.profile = profile

    def classify(self, request):
        return ClassifiedRoute(self.profile, "fixture", 1.0)


class _Resolver:
    def resolve_host(self, transport):
        session_id = str(transport["session_id"])
        return HostContext(
            session_id=session_id, principal_id=f"principal-{session_id}",
            auth_epoch=1, capability_hash=fingerprint_json({}),
            available_capabilities=frozenset(), provider_plan=("primary",),
            trace_id="trace-goal",
        )

    def resolve_actor(self, transport, *, root_run_id):
        session_id = str(transport["session_id"])
        return ActorContext(f"principal-{session_id}", session_id, 1, root_run_id)


class _TerminalDriver:
    def __init__(self, uow=None, *, race: bool = False) -> None:
        self.uow = uow
        self.release = asyncio.Event() if race else None

    async def start(self, request):
        if self.uow is not None:
            await self.uow.start_workflow(
                request.run_spec,
                WorkflowRunSeed(
                    request_key=request.run_spec.idempotency_key,
                    workflow_name="fixture", workflow_version="v1",
                    manifest_hash="manifest", implementation_hash="implementation",
                    capability_hash=request.run_spec.capability_fingerprint,
                    capability_snapshot={}, state_schema_version=1,
                    trace_id="trace-goal", thread_id=request.run_id,
                ),
                association_event=request.association_event,
            )
        if self.release is not None:
            await self.release.wait()
        yield DriverTerminalCandidate(request.run_id, "completed", "done")

    async def signal(self, signal):
        yield DriverTerminalCandidate(signal.run_id, "completed", "child done")

    async def cancel(self, run_id, reason):
        yield DriverTerminalCandidate(run_id, "cancelled", error=reason)

    async def recover(self, run_id, recovery_lease):
        if False:
            yield DriverTerminalCandidate(run_id, "completed", "")

    async def close(self):
        return None

    @property
    def profile_keys(self):
        return frozenset({"workflow.default"})


async def _runtime(tmp_path, kind: str, *, race: bool = False):
    uow = SqliteExecutionUnitOfWork(tmp_path / f"{kind}.db")
    await uow.activate_runtime()
    goals = SessionGoalStore()
    state = SessionDB(tmp_path / f"{kind}-state.db")
    goals.bind_persistence(state)
    goal = goals.set("session-1", f"{kind} goal")
    await goals.persist(goal)
    driver = _TerminalDriver(uow if kind == "workflow" else None, race=race)
    runtime = await build_harness_runtime(
        uow=uow, classifier=_Classifier(f"{kind}.default"),
        profiles=ProfileRegistry((ProfileSpec(
            f"{kind}.default", kind, kind,
            **({
                "workflow_key": "fixture.v1", "workflow_name": "fixture",
                "workflow_version": "v1", "state_factory": dict,
                "context_factory": dict,
            } if kind == "workflow" else {}),
        ),)),
        drivers=(RegisteredDriver(
            kind, driver, durable_from_start=kind == "workflow",
            atomic_start=kind == "workflow",
        ),),
        resolver=_Resolver(), goal_store=goals, owner_generation=1,
    )
    return runtime, uow, goals, goal, driver


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("react", "workflow"))
async def test_kernel_root_terminal_projects_exact_associated_goal(tmp_path, kind) -> None:
    runtime, uow, goals, goal, _ = await _runtime(tmp_path, kind)
    await runtime.supervisor.close()
    handle = await runtime.run_client.start(
        {"text": "finish", "request_id": f"request-{kind}", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    assert [event async for event in handle.events][-1].kind == "run.final"
    events = await uow.list_events(handle.run_id)
    assert [event.candidate.payload["target_id"] for event in events
            if event.kind == "goal_associated"] == [goal.goal_id]
    await runtime.supervisor.run_once()
    assert goals.get("session-1").status == "done"
    await runtime.supervisor.run_once()
    await runtime.close()


@pytest.mark.asyncio
async def test_child_and_root_terminal_race_keeps_one_goal_delivery(tmp_path) -> None:
    runtime, uow, goals, _, driver = await _runtime(tmp_path, "react", race=True)
    await runtime.supervisor.close()
    handle = await runtime.run_client.start(
        {"text": "race", "request_id": "request-race", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    ref = RunRef(handle.run_id, "session-1")
    actor = _Resolver().resolve_actor({"session_id": "session-1"}, root_run_id=handle.run_id)
    signal = asyncio.create_task(runtime.kernel.signal(
        ref, actor,
        ChildTerminalSignal(handle.run_id, "delegate-1", "child-1", "completed", "ok"),
    ))
    driver.release.set()
    await signal
    await asyncio.sleep(0.02)
    record = await uow.query(ref, actor)
    events = await uow.list_events(handle.run_id)
    assert record.status is RunStatus.COMPLETED
    assert len([event for event in events if event.kind == "run.final"]) == 1
    deliveries = await uow.list_event_deliveries(record.terminal_event_id)
    assert len(deliveries) == 1
    await runtime.supervisor.run_once()
    assert goals.get("session-1").status == "done"
    await runtime.close()


@pytest.mark.asyncio
async def test_goal_sink_projects_frozen_target_after_current_goal_changes(tmp_path) -> None:
    runtime, uow, goals, _, _ = await _runtime(tmp_path, "react")
    await runtime.supervisor.close()
    handle = await runtime.run_client.start(
        {"text": "finish", "request_id": "request-mismatch", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    _ = [event async for event in handle.events]
    replacement = goals.set("session-1", "replacement")
    await goals.persist(replacement)
    await runtime.supervisor.run_once()
    rows = await uow.list_event_deliveries((await uow.query(
        RunRef(handle.run_id, "session-1"), _Resolver().resolve_actor(
            {"session_id": "session-1"}, root_run_id=handle.run_id,
        ),
    )).terminal_event_id)
    assert rows[0].status.value == "delivered"
    async with aiosqlite.connect(tmp_path / "react-state.db") as db:
        statuses = await (await db.execute(
            "SELECT goal_id,status FROM session_goals ORDER BY goal_id"
        )).fetchall()
    assert sorted(status for _goal_id, status in statuses) == ["active", "done"]
    assert goals.get("session-1").goal_id == replacement.goal_id
    assert goals.get("session-1").status == "active"
    await runtime.close()


@pytest.mark.asyncio
async def test_goal_storage_failure_retries_after_dispatcher_restart(
    tmp_path, monkeypatch,
) -> None:
    runtime, uow, goals, goal, _ = await _runtime(tmp_path, "react")
    await runtime.supervisor.close()
    handle = await runtime.run_client.start(
        {"text": "finish", "request_id": "request-retry", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    _ = [event async for event in handle.events]
    state = goals._session_db
    original = state.project_goal_terminal

    async def fail_projection(goal_id, status):
        raise OSError("disk-full")

    monkeypatch.setattr(state, "project_goal_terminal", fail_projection)
    await runtime.supervisor.run_once()
    record = await uow.query(
        RunRef(handle.run_id, "session-1"),
        _Resolver().resolve_actor({"session_id": "session-1"}, root_run_id=handle.run_id),
    )
    rows = await uow.list_event_deliveries(record.terminal_event_id)
    assert rows[0].status.value == "failed"
    assert "disk-full" in (rows[0].last_error or "")
    assert goals.get("session-1").status == "active"
    await runtime.close()

    monkeypatch.setattr(state, "project_goal_terminal", original)
    assert rows[0].next_attempt_at is not None
    restarted_uow = SqliteExecutionUnitOfWork(
        tmp_path / "react.db", clock=lambda: rows[0].next_attempt_at + 1.0,
    )
    await restarted_uow.initialize()
    projection = GoalTerminalProjection(goals)
    restarted = ExecutionDeliveryDispatcher(
        restarted_uow,
        (SinkRegistration("goal_projection", "session-goals", projection),),
        owner_generation=1,
    )
    assert await restarted.run_once() is True
    assert goals.get("session-1").goal_id == goal.goal_id
    assert goals.get("session-1").status == "done"
    final = await restarted_uow.list_event_deliveries(record.terminal_event_id)
    assert final[0].status.value == "delivered"


@pytest.mark.asyncio
async def test_create_and_goal_association_are_one_restart_safe_commit(tmp_path) -> None:
    path = tmp_path / "atomic.db"

    def crash(point: str) -> None:
        if point == "create_after_run":
            raise RuntimeError("crash:create_after_run")

    uow = SqliteExecutionUnitOfWork(path, fault_injector=crash)
    await uow.activate_runtime()
    goals = SessionGoalStore()
    goal = goals.set("session-1", "atomic goal")
    runtime = await build_harness_runtime(
        uow=uow, classifier=_Classifier("react.default"),
        profiles=ProfileRegistry((ProfileSpec("react.default", "react", "react"),)),
        drivers=(RegisteredDriver("react", _TerminalDriver()),), resolver=_Resolver(),
        goal_store=goals, owner_generation=1,
    )
    with pytest.raises(RuntimeError, match="create_after_run"):
        await runtime.run_client.start(
            {"text": "finish", "request_id": "request-atomic", "turn_id": "turn-1"},
            {"session_id": "session-1", "venue": "text"},
        )
    assert await uow.list_recoverable() == ()

    restarted = SqliteExecutionUnitOfWork(path)
    resumed = await build_harness_runtime(
        uow=restarted, classifier=_Classifier("react.default"),
        profiles=ProfileRegistry((ProfileSpec("react.default", "react", "react"),)),
        drivers=(RegisteredDriver("react", _TerminalDriver()),), resolver=_Resolver(),
        goal_store=goals, owner_generation=1,
    )
    handle = await resumed.run_client.start(
        {"text": "finish", "request_id": "request-atomic", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    _ = [event async for event in handle.events]
    assert [event.candidate.payload["target_id"] for event in
            await restarted.list_events(handle.run_id)
            if event.kind == "goal_associated"] == [goal.goal_id]
    await resumed.close()
