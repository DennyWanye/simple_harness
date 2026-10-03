"""Startup cleanup uses real SQLite pools, with a controlled sweep failure.

No subprocesses or provider calls. These controls do not repeat OS reap acceptance.
"""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from h1i_seed import run_until
from product_assembly import unstarted

from agent_orchestrator.artifacts.workspace import WorkspaceCleanupIncomplete, WorkspaceManager
from agent_orchestrator.governance.budgets import BudgetLedger
from agent_orchestrator.orchestrator import event_handler
from agent_orchestrator.runtime.assembly import AssembledOrchestratorRuntime
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from simple_harness.agents.runtime import AgentRuntime


class NeverProvider:
    async def invoke(self, *args, **kwargs):
        raise AssertionError("startup cleanup must not call a provider")


async def _a_running_attempt(root) -> tuple[str, dict]:
    """产品同形部署上跑到执行者的模型调用进行中（被扣住），然后停机：留下一次在途尝试和它的预留。"""

    provider = LayeredScriptedProvider()
    provider.held.add("worker")
    try:
        async with product_world(root, provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "g-1"})["mission_id"]
            await run_until(world, provider.entered.is_set)
            [attempt] = [attempt for task in world.store.list_tasks(mission_id)
                         for attempt in world.store.list_attempts(task.id)]
            reservation = world.loop.commit.ledger.reservation(attempt.id)
            assert reservation is not None and reservation["state"] == "RESERVED"
            return attempt.id, reservation
    finally:
        provider.release.set()


@pytest.mark.parametrize("close_raises", [False, True])
def test_sweep_failure_closes_all_pools_preserves_the_reservation_and_original_error(
    tmp_path, monkeypatch, close_raises
):
    """重启时清理执行副本失败（磁盘上的副本删不掉）：编排服务带着原错误起不来，运行时从没启动，
    所有执行池与库都关掉；上一次停机时在途尝试的预留原样保留（HTN 补齐阶段 A′：在途尝试由产品
    同形部署真跑出来，不再手工建尝试、手记未知用量）。"""

    root = tmp_path / "root"
    attempt_id, reservation = asyncio.run(_a_running_attempt(root))
    orch = unstarted(root, NeverProvider()).orchestrator
    error = WorkspaceCleanupIncomplete([{"status": "unknown", "identity": "retained"}])
    captured = {}
    original_assemble = event_handler.assemble_orchestrator_runtime

    def assemble(*args, **kwargs):
        captured["assembled"] = original_assemble(*args, **kwargs)
        captured["store"] = orch.store
        return captured["assembled"]

    def fail_sweep(self, **kwargs):
        raise error

    monkeypatch.setattr(event_handler, "assemble_orchestrator_runtime", assemble)
    monkeypatch.setattr(WorkspaceManager, "sweep_exec_copies", fail_sweep)
    enter = AsyncMock(side_effect=AssertionError("runtime started before sweep completed"))
    monkeypatch.setattr(AgentRuntime, "__aenter__", enter)
    original_exit = AssembledOrchestratorRuntime.__aexit__
    exits = []

    async def close(self, *exc_info):
        exits.append(exc_info)
        await original_exit(self, *exc_info)
        if close_raises:
            raise RuntimeError("secondary close failure")

    monkeypatch.setattr(AssembledOrchestratorRuntime, "__aexit__", close)

    async def exercise():
        with pytest.raises(WorkspaceCleanupIncomplete) as raised:
            await asyncio.wait_for(orch.__aenter__(), 5)
        assert raised.value is error
        assert raised.value.reports == ({"status": "unknown", "identity": "retained"},)
        enter.assert_not_awaited()
        await asyncio.wait_for(orch.__aexit__(None, None, None), 5)
        assert len(exits) == 1  # Host's follow-up exit is harmless.
        assert exits[0][1] is error

    asyncio.run(exercise())
    assert orch._store is None and orch._assembled is None
    with pytest.raises(sqlite3.ProgrammingError):
        captured["store"].connection.execute("SELECT 1")
    pools = captured["assembled"].pools
    assert len(pools) == 2  # 部署的两个原生执行池（两档上下文尺寸）
    for pool in pools.values():
        runtime = pool.runtime._assembled
        assert not runtime.database.is_open
        assert not runtime.runtime._leases and not runtime.runtime._fences
    reopened = Store.open(root / "orchestrator.db")
    try:
        assert BudgetLedger(reopened).reservation(attempt_id) == reservation
    finally:
        reopened.close()


def test_exit_closes_store_even_when_pool_close_fails_and_second_exit_is_noop(tmp_path):
    orch = unstarted(tmp_path / "root", NeverProvider()).orchestrator
    store = Store.open(tmp_path / "orchestrator.db")
    error = RuntimeError("pool close failed")
    close = AsyncMock(side_effect=error)
    orch._store = store
    orch._assembled = SimpleNamespace(__aexit__=close)

    async def exercise():
        with pytest.raises(RuntimeError) as raised:
            await asyncio.wait_for(orch.__aexit__(None, None, None), 5)
        assert raised.value is error
        await asyncio.wait_for(orch.__aexit__(None, None, None), 5)

    asyncio.run(exercise())
    close.assert_awaited_once()
    with pytest.raises(sqlite3.ProgrammingError):
        store.connection.execute("SELECT 1")
