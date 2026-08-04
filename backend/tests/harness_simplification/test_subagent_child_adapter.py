"""R4 test-only Subagent composition over the generic ChildRun boundary."""

from __future__ import annotations

import asyncio
import ast
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import AuthorizationError, RunRef, RunStatus
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.reconciler import HarnessReconciler
from deskpet.harness.contracts import driver_catalog
from deskpet.harness.drivers.react import ReActDriver, ReactFinal
from deskpet.harness.adapters.subagent_registry import build_product_delegate
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel, RunRequest
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import RegisteredRouter
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.code_tools.spawn_subagents_tool import (
    build_subagent_batch_delegate,
    product_delegation_tool_catalog,
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


def _profiles(profile_key: str, driver_kind: str) -> ProfileRegistry:
    return ProfileRegistry((ProfileSpec(profile_key, profile_key, driver_kind),))


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
            _profiles("react.default", "react"),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
        child_runs=coordinator,
        child_signal_heartbeat_interval=0.01,
    )


@pytest.mark.asyncio
async def test_batch_child_restart_runs_parallel_and_reaches_parent_inbox(tmp_path) -> None:
    path = tmp_path / "execution.db"
    first_uow = SqliteExecutionUnitOfWork(path)
    await first_uow.activate_runtime()
    first_coordinator = ChildRunCoordinator(first_uow)
    first_collaborator = _BatchCollaborator(_command)
    first_kernel = _kernel(first_uow, first_coordinator, first_collaborator)

    handle = await first_kernel.start(
        RunRequest("delegate", "request-a", "turn-a"),
        _host(),
    )
    active = first_kernel._live.get(handle.ref.run_id)
    assert active is not None
    await active.task
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
    scheduler = HarnessReconciler(
        fresh_uow, fresh_kernel,
        coordinator=fresh_coordinator,
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

    for _ in range(2):
        await scheduler.reconcile_signals_once()
        parent_active = fresh_kernel._live.get(parent.run_id)
        assert parent_active is not None and parent_active.task is not None
        await parent_active.task
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
        await fresh_uow.list_child_links(handle.ref, foreign)
    with pytest.raises(AuthorizationError):
        await fresh_kernel.cancel(child_ref, foreign, "cross-session")


def test_batch_builder_has_no_process_local_owner_and_production_uses_typed_delegate() -> None:
    source = inspect.getsource(build_subagent_batch_delegate)
    assert "create_task" not in source
    assert "SubagentRegistry" not in source
    assert "completion_queue" not in source
    request = SimpleNamespace(
        run_id="run-a",
        capability_snapshot={"tools": ["read", "write", "agent_parallel"]},
    )
    call = SimpleNamespace(
        id="call-a",
        name="agent_parallel",
        arguments=SUBAGENTS,
    )
    command = build_product_delegate(request, call)
    assert command.kind == "delegate_run"
    assert command.join_policy.value == "join_before_final"
    assert "agent_parallel" not in command.capability_subset
    assert command == build_product_delegate(request, call)
    main = (Path(__file__).resolve().parents[3] / "backend" / "main.py").read_text(encoding="utf-8")
    assert "build_product_harness_composition" in main


def test_product_delegation_catalog_preserves_public_schemas() -> None:
    catalog = product_delegation_tool_catalog()
    assert set(catalog) == {
        "agent",
        "agent_parallel",
        "spawn_team",
        "spawn_subagents",
    }
    assert {name: schema["name"] for name, (_, schema) in catalog.items()} == {
        "agent": "agent",
        "agent_parallel": "agent_parallel",
        "spawn_team": "spawn_team",
        "spawn_subagents": "spawn_subagents",
    }


def test_production_has_one_canonical_agent_loop_constructor_and_no_legacy_builders() -> None:
    backend = Path(__file__).resolve().parents[3] / "backend"
    production_sources = []
    for current, directories, filenames in os.walk(backend):
        directories[:] = [
            name
            for name in directories
            if not name.startswith(".")
            and name
            not in {
                "assets",
                "bin",
                "build",
                "data",
                "dist",
                "dist-msi",
                "dist-portable",
                "models",
                "node_modules",
                "scripts",
                "temp",
                "tests",
                "userdata",
                "__pycache__",
            }
        ]
        production_sources.extend(
            Path(current) / filename
            for filename in filenames
            if filename.endswith(".py")
        )
    production_sources.sort()

    constructors: list[tuple[str, str]] = []
    legacy_calls: list[tuple[str, str]] = []
    legacy_builders = {
        "build_agent_tool",
        "build_agent_parallel_tool",
        "build_spawn_team_tool",
        "build_spawn_subagents_tools",
    }
    for path in production_sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                called = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
            else:
                continue
            relative = path.relative_to(backend).as_posix()
            if called in {"AgentLoop", "_AgentLoop"}:
                constructors.append((relative, called))
            if called in legacy_builders:
                legacy_calls.append((relative, called))

    assert constructors == [("main.py", "_AgentLoop")]
    assert legacy_calls == []
