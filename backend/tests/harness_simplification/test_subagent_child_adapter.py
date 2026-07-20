"""R4 test-only Subagent composition over the generic ChildRun boundary."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution import AuthorizationError, RunRef, RunStatus
from deskpet.harness.child_runs import ChildRunCoordinator, ChildRunScheduler
from deskpet.harness.drivers.react import ReActDriver, ReactFinal
from deskpet.harness.kernel import HostContext, KernelChildLauncher, RegisteredDriver, RunKernel, RunRequest
from deskpet.harness.router import RegisteredRouter, RouteProfile
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.code_tools.spawn_subagents_tool import (
    build_spawn_subagents_tools,
    build_subagent_batch_delegate,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


SUBAGENTS = {
    "subagents": [
        {"task_id": "research", "prompt": "inspect", "tools": ["read"]},
        {"task_id": "edit", "prompt": "patch", "tools": ["read", "write"]},
    ]
}


class _Classifier:
    def classify(self, request):
        return SimpleNamespace(profile_key="react.default", reason="test", confidence=1.0)


class _Policy:
    def prepared_execution_policy(self, call):
        return False, False


class _Inner:
    async def start(self, request):
        if False:
            yield ReactFinal("")

    async def resume(self, boundary, response):
        if False:
            yield ReactFinal("")

    async def cancel(self, run_id, reason):
        return None

    async def close(self):
        return None


class _BatchCollaborator(_Inner):
    """Test composition: child batch items are lexically owned parallel spans."""

    def __init__(self, command=None) -> None:
        self.command = command
        self.starts: list[str] = []
        self.resume_inputs: list[dict] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.release = asyncio.Event()

    async def _run_span(self, task, request):
        assert request.canonical_messages[0]["content"]
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        if self.concurrent == len(request.request_payload["subagent_runs"]):
            self.release.set()
        await self.release.wait()
        self.concurrent -= 1
        return f"done:{task['task_id']}"

    async def start(self, request):
        tasks = request.request_payload.get("subagent_runs")
        if tasks is None:
            assert self.command is not None
            if callable(self.command):
                built = self.command(request)
                assert built == self.command(request)
                self.command = built
            yield self.command
            return
        self.starts.append(request.request_payload["text"])
        values = await asyncio.gather(*(self._run_span(task, request) for task in tasks))
        results = {
            task["run_id"]: {"status": "completed", "value": value}
            for task, value in zip(tasks, values)
        }
        yield ReactFinal(json.dumps({"results": results}, sort_keys=True))

    async def resume(self, boundary, response):
        self.resume_inputs.append(dict(response))
        if False:
            yield ReactFinal("")


def _host(session_id="session-a"):
    return HostContext(
        session_id=session_id,
        principal_id=f"principal-{session_id}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset({"read", "write"}),
        provider_plan=("primary",),
        trace_id=f"trace-{session_id}",
    )


def _tool_context(request):
    context = request.run_context
    return ToolExecutionContext(
        scope_id=f"scope-{request.run_id}",
        session_id=context.session_id,
        request_id=context.request_id,
        root_run_id=context.root_run_id,
        capability_hash=context.capability_hash,
        run_id=request.run_id,
    )


def _command(request):
    return build_subagent_batch_delegate(
        request,
        SUBAGENTS,
        _tool_context(request),
        frozenset({"read", "write"}),
    )


def _kernel(uow, coordinator, collaborator):
    driver = ReActDriver(collaborator, uow, _Policy())
    return RunKernel(
        uow=uow,
        router=RegisteredRouter(
            _Classifier(),
            [RouteProfile("react.default", "react")],
        ),
        drivers=[RegisteredDriver("react", driver)],
        child_runs=coordinator,
        child_signal_heartbeat_interval=0.01,
    )


@pytest.mark.asyncio
async def test_batch_child_restart_runs_parallel_and_reaches_parent_inbox(tmp_path) -> None:
    path = tmp_path / "execution.db"
    first_uow = SqliteExecutionUnitOfWork(path)
    await first_uow.activate_empty_runtime()
    first_coordinator = ChildRunCoordinator(first_uow)
    first_collaborator = _BatchCollaborator(_command)
    first_kernel = _kernel(first_uow, first_coordinator, first_collaborator)

    handle = await first_kernel.start(
        RunRequest("delegate", "request-a", "turn-a"),
        _host(),
    )
    await first_kernel._active[handle.ref.run_id].task
    actor = _host().actor(root_run_id=handle.root_run_id)
    parent = await first_uow.query(handle.ref, actor)
    command = first_collaborator.command
    assert command is not None

    fresh_uow = SqliteExecutionUnitOfWork(path)
    await fresh_uow.initialize()
    fresh_parent = await fresh_uow.query(handle.ref, actor)
    fresh_coordinator = ChildRunCoordinator(fresh_uow)
    duplicate_a = await fresh_coordinator.submit(fresh_parent, command)
    duplicate_b = await fresh_coordinator.submit(fresh_parent, command)
    assert duplicate_a == duplicate_b

    fresh_collaborator = _BatchCollaborator()
    fresh_kernel = _kernel(fresh_uow, fresh_coordinator, fresh_collaborator)
    scheduler = ChildRunScheduler(
        fresh_coordinator,
        KernelChildLauncher(fresh_kernel),
        owner="restarted-subagent-scheduler",
    )
    await scheduler.reconcile_commands_once()
    child_ref = RunRef(duplicate_a.child_run_id, parent.context.session_id)
    for _ in range(100):
        child = await fresh_uow.query(child_ref, actor)
        if child.status is RunStatus.COMPLETED:
            break
        await asyncio.sleep(0.01)
    assert child.status is RunStatus.COMPLETED
    assert fresh_collaborator.max_concurrent == 2
    assert fresh_collaborator.starts[0]

    await scheduler.reconcile_signals_once()
    continuation = await fresh_uow.load_continuation(parent.run_id)
    messages = [
        json.loads(item["content"])
        for item in continuation.payload["canonical_messages"]
        if item["role"] == "system"
    ]
    assert [item["signal"] for item in messages[-2:]] == [
        "child_accepted",
        "child_terminal",
    ]
    assert continuation.payload["completion_state"]["detached_children"] == {}

    terminal = await fresh_uow.get_event(child.terminal_event_id)
    keyed = json.loads(terminal.candidate.payload["text"])["results"]
    logical_ids = [item["run_id"] for item in command.child_request["subagent_runs"]]
    assert list(keyed) == sorted(logical_ids)
    assert {item["value"] for item in keyed.values()} == {"done:research", "done:edit"}

    foreign = _host("session-b").actor(root_run_id=handle.root_run_id)
    with pytest.raises(AuthorizationError):
        await fresh_uow.list_children(handle.ref, foreign)
    with pytest.raises(AuthorizationError):
        await fresh_kernel.cancel(child_ref, foreign, "cross-session")


@pytest.mark.asyncio
async def test_public_hooks_bypass_legacy_registry_queue() -> None:
    calls = []

    async def spawn_hook(args, task_id, *, execution_context):
        calls.append(("spawn", args, task_id, execution_context))
        return json.dumps({"ok": True, "run_ids": ["logical-a"]})

    async def await_hook(args, task_id, *, execution_context):
        calls.append(("await", args, task_id, execution_context))
        return json.dumps({"ok": True, "results": []})

    class _Forbidden:
        def __getattr__(self, name):
            raise AssertionError(f"legacy owner touched: {name}")

    (spawn, _), (await_runs, _) = build_spawn_subagents_tools(
        llm_shim=SimpleNamespace(),
        parent_tool_registry=SimpleNamespace(),
        parent_session_id_resolver=lambda: "unused",
        scheduler=_Forbidden(),
        registry=_Forbidden(),
        spawn_delegate=spawn_hook,
        await_delegate=await_hook,
    )
    context = SimpleNamespace(session_id="session-a")
    assert json.loads(await spawn(SUBAGENTS, "tool-a", execution_context=context))["ok"]
    assert json.loads(await await_runs({}, "tool-b", execution_context=context))["ok"]
    assert [item[0] for item in calls] == ["spawn", "await"]


def test_batch_builder_has_no_process_local_owner_and_production_stays_legacy() -> None:
    source = inspect.getsource(build_subagent_batch_delegate)
    assert "create_task" not in source
    assert "SubagentRegistry" not in source
    assert "completion_queue" not in source
    main = (Path(__file__).resolve().parents[3] / "backend" / "main.py").read_text(encoding="utf-8")
    assert "spawn_delegate=" not in main
    assert "build_spawn_subagents_tools" in main
