from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.companion.runtime import (
    CompanionJobResult,
    CompanionRuntime,
    CompanionRuntimePolicy,
    ForegroundActivityGate,
)
from deskpet.companion.store import CompanionStore
from backend.tests.companion.test_runtime_scheduler import (
    ExecutionState,
    MutableClock,
    wait_until,
)


@pytest.mark.asyncio
async def test_close_timeout_cancels_children_and_leaves_no_companion_tasks(
    tmp_path,
) -> None:
    clock = MutableClock(datetime(2026, 7, 25, 4, 0, tzinfo=UTC))
    store = CompanionStore(tmp_path / "companion.db", clock=clock.store_now)
    owner = store.create_profile(
        profile_id="shutdown",
        generation=1,
        identity_namespace_hash="identity",
    )
    identity = IdentityReadyGate()
    identity.bind(
        FrozenOwnerIdentity(owner, "companion:shutdown:1", binding_epoch=1)
    )
    store.enqueue_job(
        owner,
        job_id="blocked",
        kind="reflection",
        dedupe_key="blocked",
        payload={"requires_idle": False},
    )
    entered = asyncio.Event()

    async def handler(_owner, _claim):
        entered.set()
        await asyncio.Event().wait()
        return CompanionJobResult()

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=ForegroundActivityGate(ExecutionState(), clock=clock),
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            idle_window_seconds=0,
            shutdown_timeout_seconds=0.001,
        ),
        authority_active=lambda: True,
    )
    await runtime.start(owner)
    await wait_until(entered.is_set)
    await runtime.close(timeout=0)
    assert runtime.diagnostics()["scheduler_count"] == 0
    assert runtime.diagnostics()["child_count"] == 0
    assert store.get_job(owner, job_id="blocked")["status"] == "queued"
    assert not [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith("companion-")
    ]


@pytest.mark.asyncio
async def test_identity_unready_and_kill_switch_do_not_start_scheduler(
    tmp_path,
) -> None:
    clock = MutableClock(datetime(2026, 7, 25, 4, 0, tzinfo=UTC))
    store = CompanionStore(tmp_path / "companion.db", clock=clock.store_now)
    owner = store.create_profile(
        profile_id="inactive",
        generation=1,
        identity_namespace_hash="identity",
    )
    identity = IdentityReadyGate()

    async def handler(_owner, _claim):
        return CompanionJobResult()

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=ForegroundActivityGate(ExecutionState(), clock=clock),
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(paused=True),
        authority_active=lambda: True,
    )
    assert await runtime.start(owner) is False
    assert runtime.diagnostics()["scheduler_count"] == 0


def test_main_shutdown_order_closes_active_runtime_then_execution_uow() -> None:
    source = (Path(__file__).parents[2] / "main.py").read_text(encoding="utf-8")
    runtime_close = source.index(
        "await _companion_runtime_service.close(timeout=5.0)"
    )
    harness_close = source.index("await _harness_runtime.close(timeout=5.0)")
    launcher_close = source.index("await _workflow_launcher.shutdown()")
    uow_close = source.index(
        "await asyncio.wait_for(_execution_uow.close(), timeout=5.0)"
    )
    assert runtime_close < harness_close < launcher_close < uow_close
    coordinator_block = source[
        source.index("profile_coordinator = ProfileBindingCoordinator(") :
        source.index("_companion_runtime = companion_runtime")
    ]
    assert "runtime=companion_runtime" in coordinator_block
    assert "runtime_start_enabled=True" in coordinator_block
    assert "_legacy_reflection_tasks.add(_reflection_task)" in source
    assert "_reflection_task.add_done_callback(_legacy_reflection_tasks.discard)" in source
    assert "asyncio.create_task(_reflection_loop())" not in source
