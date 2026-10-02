"""2026-10-03 性能：执行图通知每处理一条消息，要核对两次"计划来源还是不是当前的"。这一核对只比本地
计划来源，此前却顺带把整个任务的原始运行历史（每个子任务的执行记录、用量、副作用）全部重读重验一遍，
任务越长越慢。运行历史只在真正要用它做决定的地方读。

**改坏检验**：核对改回读完整执行来源 → 变红。
"""

import asyncio

from production_fixture import enabled_world
from agent_orchestrator.orchestrator.taskgraph_plan_sources import TaskGraphPlanSourceReader


def test_the_currentness_check_reads_no_runtime_history(tmp_path, monkeypatch):
    calls = []
    original = TaskGraphPlanSourceReader.read_execution

    def counting(self, mission_id, **kwargs):
        calls.append(mission_id)
        return original(self, mission_id, **kwargs)

    monkeypatch.setattr(TaskGraphPlanSourceReader, "read_execution", counting)

    async def case():
        async with enabled_world(tmp_path, key="tg-currentness-local", hold_worker=True) as world:
            await world.commit_seed()
            calls.clear()
            world.loop._taskgraph_notifications.validate_current(world.mission.id)
            assert calls == []

    asyncio.run(case())
