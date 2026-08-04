from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.errors import WorkflowErrorCode, WorkflowNodeError
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner, manifest_hash
from deskpet.workflows.lease import LeaseManager
from deskpet.workflows.store import (
    LegacyCheckpointStore as FencedAsyncSqliteSaver,
    StaleRunFence,
    WorkflowRunStore,
    empty_legacy_checkpoint as empty_checkpoint,
)


def _manifest(*, implementation_hash: str = "implementation-v1") -> WorkflowManifest:
    return WorkflowManifest(
        workflow_name="fake-workflow",
        workflow_version="1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=32,
        max_supersteps=16,
        definition_hash="definition",
        state_hash="state",
        prompt_hash="prompt",
        tool_hash="tool",
        policy_hash="policy",
        callable_source_hash="callable",
        dependency_lock_hash="lock",
        implementation_bundle_hash=implementation_hash,
    )


@dataclass
class FakeWorkflow:
    manifest: WorkflowManifest


class FakeExecutable:
    def __init__(
        self,
        manifest: WorkflowManifest,
        *,
        saver=None,
        failure: BaseException | None = None,
        output: object | None = None,
    ) -> None:
        self.manifest = manifest
        self.saver = saver
        self.failure = failure
        self.output = output
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    async def ainvoke(self, state, context, **kwargs):
        self.calls.append(("run", state, kwargs))
        if self.failure is not None:
            raise self.failure
        if self.saver is not None:
            config = {
                "configurable": {
                    **kwargs["configurable"],
                    "thread_id": kwargs["thread_id"],
                    "checkpoint_ns": kwargs["checkpoint_ns"],
                    "deskpet_run_id": kwargs["run_id"],
                }
            }
            await self.saver.aput(
                config,
                empty_checkpoint(),
                {"source": "input", "step": 0, "parents": {}},
                {},
            )
        return self.output if self.output is not None else {"done": True}

    async def resume(self, responses, context, **kwargs):
        self.calls.append(("resume", dict(responses), kwargs))
        return {"resumed": True}


async def _runner(tmp_path, executable: FakeExecutable | None = None, **runner_kwargs):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path, clock=runner_kwargs.pop("clock", __import__("time").time))
    saver = FencedAsyncSqliteSaver(path)
    manifest = _manifest()
    fake = executable or FakeExecutable(manifest)
    registry = WorkflowRegistry()
    registry.register(FakeWorkflow(manifest), executable=fake)
    runner = WorkflowRunner(store, saver, registry, **runner_kwargs)
    run_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name=manifest.workflow_name,
        workflow_version=manifest.workflow_version,
        capability_snapshot={"tools": ["read"]},
    )
    return runner, store, saver, registry, fake, run_id


@pytest.mark.asyncio
async def test_runner_start_run_propagates_fence_and_completes(tmp_path):
    runner, store, _, _, fake, run_id = await _runner(tmp_path)

    result = await runner.run(run_id, {"schema_version": 1}, WorkflowContext())

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.output == {"done": True}
    row = await store.get_run(run_id)
    assert row is not None and row["status"] == "completed"
    _, _, kwargs = fake.calls[0]
    assert kwargs["configurable"] == {
        "deskpet_lease_owner": runner.owner,
        "deskpet_lease_epoch": 1,
        "deskpet_run_version": 1,
    }


@pytest.mark.asyncio
async def test_runner_maps_safe_domain_error_to_failed_run(tmp_path):
    manifest = _manifest()
    executable = FakeExecutable(
        manifest,
        output={
            "values": {
                "terminal_status": "error",
                "terminal_error": {
                    "code": "text_overflow",
                    "user_message": "页面文字过多，无法完整排版。",
                    "recovery_action": "精简本页文字后重试。",
                    "debug": "must-not-leak",
                },
            }
        },
    )
    runner, store, _, _, _, run_id = await _runner(tmp_path, executable)

    result = await runner.run(run_id, {"schema_version": 1}, WorkflowContext())
    row = await store.get_run(run_id)

    assert result.status is WorkflowRunStatus.FAILED
    assert result.error == {
        "code": "text_overflow",
        "message": "页面文字过多，无法完整排版。",
    }
    assert result.recovery_action == "精简本页文字后重试。"
    assert row["status"] == WorkflowRunStatus.FAILED.value
    assert "must-not-leak" not in str(row)


@pytest.mark.asyncio
async def test_runner_maps_domain_cancel_to_cancelled_run(tmp_path):
    manifest = _manifest()
    executable = FakeExecutable(
        manifest,
        output={"values": {"terminal_status": "cancelled"}},
    )
    runner, _, _, _, _, run_id = await _runner(tmp_path, executable)

    result = await runner.run(run_id, {"schema_version": 1}, WorkflowContext())

    assert result.status is WorkflowRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_runner_unknown_domain_status_fails_closed(tmp_path):
    manifest = _manifest()
    executable = FakeExecutable(
        manifest,
        output={"values": {"terminal_status": "mystery", "terminal_error": "raw"}},
    )
    runner, _, _, _, _, run_id = await _runner(tmp_path, executable)

    result = await runner.run(run_id, {"schema_version": 1}, WorkflowContext())

    assert result.status is WorkflowRunStatus.FAILED
    assert result.error == {
        "code": "invalid_domain_terminal_status",
        "message": "任务返回了无效的终态。",
    }
    assert "raw" not in str(result.error)


@pytest.mark.asyncio
async def test_runner_persists_checkpoint_and_returns_strict_history(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    fake = FakeExecutable(_manifest(), saver=saver)
    registry = WorkflowRegistry()
    registry.register(FakeWorkflow(fake.manifest), executable=fake)
    runner = WorkflowRunner(store, saver, registry, owner="history-runner")
    run_id = await runner.start(
        session_id="s",
        request_id="r",
        turn_id="t",
        workflow_name="fake-workflow",
        workflow_version="1",
        capability_snapshot={},
    )

    await runner.run(run_id, {"schema_version": 1})
    history = await runner.get_state_history(run_id)

    assert len(history) == 1
    assert history[0]["state"]["id"]
    assert history[0]["metadata"]["source"] == "input"


@pytest.mark.asyncio
async def test_resume_promotes_waiting_then_uses_same_fenced_identity(tmp_path):
    runner, store, _, _, fake, run_id = await _runner(tmp_path)
    db = await store._connect()
    try:
        await db.execute("UPDATE workflow_runs SET status='waiting' WHERE run_id=?", (run_id,))
        await db.commit()
    finally:
        await db.close()

    result = await runner.resume(run_id, {"interrupt-1": {"approved": True}})

    assert result.status is WorkflowRunStatus.COMPLETED
    kind, payload, kwargs = fake.calls[0]
    assert kind == "resume"
    assert payload == {"interrupt-1": {"approved": True}}
    assert kwargs["configurable"]["deskpet_run_version"] == 2


@pytest.mark.asyncio
async def test_typed_retryable_failure_releases_lease(tmp_path):
    manifest = _manifest()
    fake = FakeExecutable(
        manifest,
        failure=WorkflowNodeError(
            code=WorkflowErrorCode.RETRYABLE_PROVIDER,
            message_ref="provider:temporary",
        ),
    )
    runner, store, _, _, _, run_id = await _runner(tmp_path, fake)

    result = await runner.run(run_id, {})

    assert result.status is WorkflowRunStatus.RETRYABLE
    assert result.error is not None and result.error["code"] == "retryable_provider"
    row = await store.get_run(run_id)
    assert row is not None
    assert row["lease_owner"] is None
    assert row["recovery_action"] == "retry"


@pytest.mark.asyncio
async def test_graph_recursion_error_maps_to_typed_blocked_recovery(tmp_path):
    class GraphRecursionError(RuntimeError):
        pass

    fake = FakeExecutable(_manifest(), failure=GraphRecursionError("limit"))
    runner, _, _, _, _, run_id = await _runner(tmp_path, fake)

    result = await runner.run(run_id, {})

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert result.error["reason"] == "recursion_limit_before_budget_exit"
    assert result.recovery_action == "inspect_budget_or_fork"


@pytest.mark.asyncio
async def test_registry_refuses_silent_manifest_or_implementation_drift(tmp_path):
    runner, store, _, registry, _, run_id = await _runner(tmp_path)
    row = await store.get_run(run_id)
    assert row is not None
    assert row["manifest_hash"] == manifest_hash(registry.get("fake-workflow", "1").manifest)

    replacement = FakeExecutable(_manifest(implementation_hash="changed"))
    registry.register(FakeWorkflow(replacement.manifest), executable=replacement, replace=True)
    result = await runner.run(run_id, {})

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.recovery_action == "restore_graph_version_or_fork"


@pytest.mark.asyncio
async def test_two_runners_cannot_claim_same_live_lease(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    manifest = _manifest()

    class BlockingExecutable(FakeExecutable):
        async def ainvoke(self, state, context, **kwargs):
            started.set()
            await release.wait()
            return {"done": True}

    fake = BlockingExecutable(manifest)
    runner_a, store, saver, registry, _, run_id = await _runner(
        tmp_path,
        fake,
        owner="runner-a",
        heartbeat_interval=1,
        lease_ttl=10,
    )
    runner_b = WorkflowRunner(
        store,
        saver,
        registry,
        owner="runner-b",
        heartbeat_interval=1,
        lease_ttl=10,
    )
    running = asyncio.create_task(runner_a.run(run_id, {}))
    await started.wait()

    with pytest.raises(StaleRunFence):
        await runner_b.run(run_id, {})

    release.set()
    assert (await running).status is WorkflowRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_invalidates_saver_write_between_node_return_and_commit(tmp_path):
    ready = asyncio.Event()
    commit = asyncio.Event()
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    manifest = _manifest()

    class LateWriter(FakeExecutable):
        async def ainvoke(self, state, context, **kwargs):
            ready.set()
            await commit.wait()
            config = {
                "configurable": {
                    **kwargs["configurable"],
                    "thread_id": kwargs["thread_id"],
                    "checkpoint_ns": kwargs["checkpoint_ns"],
                    "deskpet_run_id": kwargs["run_id"],
                }
            }
            await saver.aput(
                config,
                empty_checkpoint(),
                {"source": "loop", "step": 1, "parents": {}},
                {},
            )
            return {"too_late": True}

    registry = WorkflowRegistry()
    registry.register(FakeWorkflow(manifest), executable=LateWriter(manifest))
    runner = WorkflowRunner(store, saver, registry, owner="runner")
    run_id = await runner.start(
        session_id="s",
        request_id="r",
        turn_id="t",
        workflow_name="fake-workflow",
        workflow_version="1",
        capability_snapshot={},
    )
    task = asyncio.create_task(runner.run(run_id, {}))
    await ready.wait()

    cancelled = await runner.request_cancel(run_id)
    commit.set()
    result = await task

    assert cancelled["status"] == "cancelled"
    assert result.status is WorkflowRunStatus.CANCELLED
    assert await runner.get_state_history(run_id) == []


@pytest.mark.asyncio
async def test_heartbeat_renews_real_sqlite_lease_with_15s_90s_defaults(tmp_path, monkeypatch):
    now = [100.0]
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path, clock=lambda: now[0])
    run_id, _ = await store.create_run(
        request_key="heartbeat",
        session_id="s",
        request_id="r",
        turn_id="t",
        workflow_name="fake-workflow",
        workflow_version="1",
        manifest_hash="m",
        implementation_hash="i",
        capability_hash="c",
        capability_snapshot={},
        state_schema_version=1,
    )
    heartbeat_seen = asyncio.Event()
    original_heartbeat = store.heartbeat

    async def observed_heartbeat(fence, *, ttl_seconds):
        now[0] = 115.0
        result = await original_heartbeat(fence, ttl_seconds=ttl_seconds)
        heartbeat_seen.set()
        return result

    monkeypatch.setattr(store, "heartbeat", observed_heartbeat)

    async def immediate_sleep(_seconds):
        await asyncio.sleep(0)

    manager = LeaseManager(store, owner="owner", sleep=immediate_sleep)
    lease = await manager.claim(run_id)

    async def work():
        await heartbeat_seen.wait()
        return "ok"

    assert await manager.run_with_heartbeat(lease, work()) == "ok"
    row = await store.get_run(run_id)
    assert row is not None
    assert row["heartbeat_at"] == 115.0
    assert row["lease_expires_at"] == 205.0
