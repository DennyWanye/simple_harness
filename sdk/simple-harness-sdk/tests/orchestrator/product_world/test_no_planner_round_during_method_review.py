# SPDX-License-Identifier: Apache-2.0
"""做法送审、结论还没记进系统时，不开下一轮规划（联测真机）。

真机上的时序：规划器提了新做法；审阅员那一轮结束了，但结论要到后面某一轮才入库；这中间因为还有
别的待处理请求（改要求），系统又开了一轮规划——规划器看到的只能是"审阅中"，采用不了、也没别的
可做，只好选等待，等待又被退回，白丢一轮，最后规划次数用尽、任务失败。

这里把"结论入库"扣住，期间留下一件待规划器处理的事，多转几轮：规划器不该再被问到；放开
之后任务照常完成。

**改坏检验**（PLN-01）：有做法在审时照样开规划轮 → 规划器在扣住期间又被问到 → 变红。"""
from __future__ import annotations

import asyncio
import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_no_planner_round_opens_while_a_proposed_method_is_out_for_review(tmp_path, monkeypatch):
    import agent_orchestrator.orchestrator.method_plan_reviews as method_reviews

    advance, held = method_reviews.advance, {"on": True}
    monkeypatch.setattr(method_reviews, "advance",
                        lambda orch, mission: False if held["on"] else advance(orch, mission))
    async def case():
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "gate-review",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            for _ in range(4):
                await world.drain(timeout=10)
            assert method_reviews.awaiting(world.store, mission_id)  # 做法提了，结论没入库
            asked = provider.asked.count("planner")
            assert asked == 1
            # 留下一件"待规划器处理"的事（真机上是改要求带来的修复请求；这里直接记一条取证回执，
            # 它与修复请求走同一个"叫醒规划器"的入口）
            from agent_orchestrator.orchestrator.hierarchical_dispatch import append_hierarchical_event

            with world.store.transaction():
                append_hierarchical_event(world.store, "PlanningEvidenceRecorded", mission_id, key="gate-test",
                                          payload={"decision_id": "gate-test"})
            for _ in range(6):
                await world.drain(timeout=10)
            assert provider.asked.count("planner") == asked  # 扣住期间没有再开规划轮
            held["on"] = False
            mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)

    asyncio.run(case())
