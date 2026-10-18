from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import pytest
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.companion.runtime import (
    CompanionJobResult,
    CompanionRuntime,
    CompanionRuntimePolicy,
    ForegroundActivityGate,
)
from deskpet.companion.store import CompanionStore

from tests.companion.test_runtime_scheduler import (
    ExecutionState,
    MutableClock,
    wait_until,
)
from tests.host_source import call_source, host_source_text


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
    identity.bind(FrozenOwnerIdentity(owner, "companion:shutdown:1", binding_epoch=1))
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
        if task is not asyncio.current_task() and task.get_name().startswith("companion-")
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
    source = host_source_text()
    runtime_close = source.index("await _companion_runtime_service.close(timeout=5.0)")
    sdk_close = source.index("await host_state.sdk_runtime_stack.close()")
    # 执行账本最后关：伴侣运行时与 SDK 运行栈都还可能写账本。
    ledger_close = source.index("await asyncio.wait_for(_execution_uow.close(), timeout=5.0)")
    assert runtime_close < sdk_close < ledger_close
    coordinator_block = call_source(source, "ProfileBindingCoordinator(")
    assert re.search(r"runtime=(st\.)?companion_runtime,", coordinator_block)
    assert "runtime_start_enabled=True" in coordinator_block
    assert "asyncio.create_task(_reflection_loop())" not in source
