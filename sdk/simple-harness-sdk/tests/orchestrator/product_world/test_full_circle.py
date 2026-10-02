# SPDX-License-Identifier: Apache-2.0
"""产品同形测试世界的代表用例一：整圈（HTN 补齐阶段 A′ 第 2 步）。

建任务（执行图当场绑定）→ 第一份规划包 → 自动授权 → 规划器提新做法 → 做法独立审阅 → 采用 →
执行 → 叶子验收 → 根终审 → 完成。系统一侧全是产品那一份部署组装，只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.orchestrator.plan_commits import HIERARCHICAL_SEMANTICS, semantics_of
from agent_orchestrator.storage.assurance_store import AssuranceStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_a_user_mission_completes_on_the_product_deployment(tmp_path):
    async def case():
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider) as world:
            created = world.create({"goal": "写一份 NOTES.md，列出三个要点", "success_criteria": ["file:NOTES.md"],
                                    "idempotency_key": "full-circle-1"})
            mission_id = created["mission_id"]
            assert world.store.connection.execute(
                "SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?", (mission_id,)).fetchone()[0] == 1
            mission = await world.run_until_settled(mission_id)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            assert semantics_of(mission) == HIERARCHICAL_SEMANTICS
            assert AssuranceStore(world.store).lane(mission_id) == "ASSURANCE_1_1"
            types = [event.type for event in world.store.list_events(mission_id)]
            assert "TaskGraphRevisionRecorded" in types and "TaskGraphDispatchBound" in types
            assert set(provider.asked) >= {"planner", "worker", "unknown"}

    asyncio.run(case())
