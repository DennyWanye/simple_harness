"""OCC-09：完成范围随第一版计划一起冻结（2026-10-03 迁到产品同形世界，HTN 补齐阶段 A′）。

计划由产品主循环真提交（提做法 → 独立审阅 → 采用，:func:`plan_committed`）。本文件守三件事：

* 范围读侧按"当前的精确来源"复核：计划快照哈希、计划版本、范围本身，任何一样对不上都按名拒绝、
  不写；
* 冻结范围是计划提交事务的一部分：在范围写入前后各有一个产品自带的崩溃点，崩在哪里，计划、提交
  收据、计划成员、范围、事件都一起回滚；进程重启后照常提交第一版计划（恰好一版）；
* 完成映射没确认，主循环根本不开工（产品的开工条件），不会把要求悄悄降成"只有内容"。

原"计划提交冻结出精确的单根范围"由下面第一条的前半段断言承接；"同一计划提交重放不重复写范围"
由 ``taskgraph_exec/test_commit_atomicity.py`` 的重放只读用例覆盖（分诊表：删）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

from agent_orchestrator.contracts.operation_completion import PlanRevisionPinV1
from agent_orchestrator.orchestrator.operation_completion import (
    OperationCompletionError,
    OperationCompletionReader,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import plan_committed, publishing, root_scope  # noqa: E402


def _plan_scope_counts(store, mission_id: str) -> tuple[int, int, int, int, int]:
    """必须与计划提交同一个事务的五组写入。"""

    connection = store.connection
    return (
        int(connection.execute("SELECT count(*) FROM plan_revisions").fetchone()[0]),
        int(connection.execute("SELECT count(*) FROM plan_commit_receipts").fetchone()[0]),
        int(connection.execute("SELECT count(*) FROM plan_memberships").fetchone()[0]),
        int(connection.execute("SELECT count(*) FROM operation_completion_scopes").fetchone()[0]),
        sum(1 for event in store.list_events(mission_id) if event.type == "PlanRevisionCommitted"),
    )


@pytest.mark.parametrize("fault", ("plan_hash", "plan_revision", "missing_scope"))
def test_occ02_scope_reader_rechecks_current_exact_sources(tmp_path, fault: str) -> None:
    async def run() -> None:
        async with plan_committed(tmp_path) as case:
            stored = root_scope(case)
            scope = stored["document"]
            htn = HtnStore(case.store)
            active = htn.active_plan_revision(case.mission_id)
            assert active is not None
            # 冻结出来的范围与计划、确认的映射、任务合同逐项对得上。
            approved = case.store.connection.execute(
                "SELECT spec_hash FROM operation_completion_specs WHERE mission_id=?", (case.mission_id,)).fetchone()
            assert scope.spec_hash == approved["spec_hash"]
            assert (scope.plan_ref.revision, scope.plan_ref.snapshot_hash) == (active.revision, active.snapshot_hash)
            assert scope.required_effect_keys == scope.owned_effect_keys == ("publish-weekly",)
            binding = htn.task_semantics_of(case.mission_id, scope.task_ref.id)
            assert binding is not None
            assert (scope.task_ref.revision, scope.task_ref.content_hash) == (
                binding.contract_revision, binding.contract_hash)
            before = case.store.connection.total_changes
            reader = OperationCompletionReader(case.store)
            assert reader.read_scope(case.mission_id, scope.plan_ref, scope.occurrence_id) == scope

            pin = PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash)
            occurrence_id = scope.occurrence_id
            if fault == "plan_hash":
                pin = dataclasses.replace(pin, snapshot_hash="f" * 64)
            elif fault == "plan_revision":
                pin = dataclasses.replace(pin, revision=active.revision + 1)
            else:
                occurrence_id = "no-such-occurrence"
            with pytest.raises(OperationCompletionError) as refused:
                reader.read_scope(case.mission_id, pin, occurrence_id)
            assert refused.value.code == (
                "OP_COMPLETION_SCOPE_UNRESOLVED" if fault == "missing_scope" else "OP_EFFECT_SCOPE_STALE")
            assert case.store.connection.total_changes == before

    asyncio.run(run())


@pytest.mark.parametrize("fault", ("completion_plan_before_scopes", "completion_plan_after_scope"))
def test_occ09_scope_fault_rolls_back_plan_receipt_membership_scope_and_event(tmp_path, fault: str) -> None:
    """崩溃点在计划提交事务里：崩了什么都不留（崩溃现状是逃出本轮主循环，见迁移裁决 B4）；进程重启后照常提交第一版计划。"""

    state: dict[str, str] = {}

    async def first() -> None:
        async with publishing(tmp_path) as case:
            state["mission_id"] = case.mission_id
            before = _plan_scope_counts(case.store, case.mission_id)
            case.store.arm(f"{fault}:operation_completion")
            with pytest.raises(InjectedCrash, match=fault):
                await case.run_until(lambda: False, timeout=30)
            assert _plan_scope_counts(case.store, case.mission_id) == before
            assert HtnStore(case.store).active_plan_revision(case.mission_id) is None

    async def second() -> None:
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        try:
            async with product_world(tmp_path / "root", provider) as world:
                mission_id = state["mission_id"]

                async def drive() -> None:
                    while True:
                        await world.loop.run()
                        await world.deployment.between_cycles(auto=world.auto)
                        await asyncio.sleep(0.01)

                task = asyncio.create_task(drive())
                try:
                    async with asyncio.timeout(30):
                        while not provider.entered.is_set():
                            if task.done():
                                task.result()
                            await asyncio.sleep(0.01)
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                assert HtnStore(world.store).active_plan_revision(mission_id) is not None
                counts = _plan_scope_counts(world.store, mission_id)
                assert counts[0] == 1 and counts[4] == 1  # 恰好一版计划、一个提交事件
        finally:
            provider.release.set()

    asyncio.run(first())
    asyncio.run(second())


def test_occ09_no_plan_is_published_before_the_completion_mapping_is_confirmed(tmp_path) -> None:
    """要求里有 action: 的任务，确认页没点确认之前主循环不开工：规划器一次都没被问，没有计划、
    没有范围，任务停在 CREATED——要求不会被悄悄降成"只有内容"。"""

    provider = LayeredScriptedProvider()

    async def run() -> None:
        async with publishing(tmp_path, provider=provider, confirm=False) as case:
            for _ in range(3):
                await case.world.drain()
            assert case.status() == "CREATED"
            assert provider.asked == []
            assert HtnStore(case.store).active_plan_revision(case.mission_id) is None
            assert _plan_scope_counts(case.store, case.mission_id) == (0, 0, 0, 0, 0)

    asyncio.run(run())
