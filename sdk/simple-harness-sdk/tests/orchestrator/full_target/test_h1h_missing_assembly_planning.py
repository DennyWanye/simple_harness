"""Native-discovered isolation: absent HTN assembly must never send planning.

HTN 补齐阶段 A′：任务经产品同形部署建出，第一轮规划真跑起来（规划器调用被扣住，留下一条在途的
规划派发），然后停机；再由一个**没接上部署安装**的进程（执行池在、分层装配不在）打开同一个库。
守的性质：这个进程不新开规划轮、不派发上一个进程留下的在途规划、不调提供方，记一条
``HierarchicalAssemblyMissing``，任务停在原状态等装配回来，不判失败。

原用例手工 ``create_service_intent`` 造"上一个进程留下的规划派发"（裁决①b2 不允许）；现在由真实的
上一个进程留下。
"""
from __future__ import annotations

import asyncio

import pytest
from h1i_seed import run_until
from product_assembly import unstarted

from agent_orchestrator.contracts import ContractError, MissionStatus
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

OPEN = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")


class NeverProvider:
    calls = 0

    async def invoke(self, *args, **kwargs):
        NeverProvider.calls += 1
        raise AssertionError("a process without the HTN assembly must not call a provider")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_missing_assembly_blocks_initial_retry_and_recovered_dispatch(tmp_path):
    root = tmp_path / "root"

    async def first_process() -> tuple[str, str, MissionStatus]:
        provider = LayeredScriptedProvider()
        provider.held.add("planner")
        try:
            async with product_world(root, provider) as world:
                mission_id = world.create({"goal": "确认完成要求", "success_criteria": ["file:NOTES.md"],
                                           "idempotency_key": "missing-assembly"})["mission_id"]
                await run_until(world, provider.entered.is_set)
                [intent] = [item for item in world.store.list_intents(*OPEN)
                            if item.mission_id == mission_id and item.kind == "plan"]
                return mission_id, intent.intent_id, world.store.get_mission(mission_id).status
        finally:
            provider.release.set()

    mission_id, intent_id, status = asyncio.run(first_process())

    async def without_assembly() -> None:
        async with unstarted(root, NeverProvider(), installed=False).orchestrator as loop:
            intent = loop.store.get_intent(intent_id)
            assert await loop._dispatch(intent) is False  # the left-over planning dispatch is held
            assert loop.store.get_intent(intent_id) == intent
            assert await loop._try_planner_intent(mission_id, ordinal=2) is False
            with pytest.raises(ContractError, match="hierarchical_assembly_missing"):
                loop._create_planner_intent_now(mission_id, ordinal=2)
            await loop._cycle()
            await loop._cycle()
            assert NeverProvider.calls == 0
            events = loop.store.list_events(mission_id)
            assert any(event.type == "HierarchicalAssemblyMissing" for event in events)
            assert not [event for event in events if event.type in {"PlanRevisionCommitted", "MissionFailed"}]
            assert loop.store.get_mission(mission_id).status is status
            assert loop.store.get_intent(intent_id).state == intent.state  # 两轮之后在途派发仍没被动

    asyncio.run(without_assembly())
