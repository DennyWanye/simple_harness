# SPDX-License-Identifier: Apache-2.0
"""修复时规划器拿得到"换一个新做法"的材料（2026-10-03，迁移裁决 A1）。

规划器提示词说"失败后重试还是换做法，都由你决定"；桌面产品没有种子做法库，换做法就得自己
先提一个新做法。此前写新做法的材料只发给还没有做法的目标，被修复请求指向的已细化目标拿不到，
这个判断名义上交给了规划器、实际交不过去。这里：一步的内容审阅被打回，修复轮的规划包里，
这个目标有写新做法的材料。

**改坏检验**：把材料的发放条件改回"只给没有做法的目标"→ 变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_a_repair_round_carries_method_material_for_the_goal_under_repair(tmp_path):
    repair_packages: list[dict[str, Any]] = []

    def planner(request: Any):
        package = package_of(request)
        if package.get("repair_requests"):
            repair_packages.append(package)
            return retry_same_method(request)
        return planner_reply(request)

    def reviewer(request: Any):
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：内容不满足要求。")
        return review_reply(data)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            created = world.create({"goal": "写一份 NOTES.md", "idempotency_key": "repair-material",
                                    "success_criteria": ["file:NOTES.md"]})
            for _ in range(20):
                await world.drain(timeout=20)
                if repair_packages:
                    break
            assert repair_packages, "no repair round was asked"
            package = repair_packages[0]
            repaired = [goal for goal in package["views"]["goals"] if goal.get("under_repair")]
            assert repaired, package["views"]["goals"]
            offered = {item["subject_key"] for item in package.get("method_proposal_contexts") or ()}
            assert {goal["subject_key"] for goal in repaired} <= offered
            assert created["mission_id"]

    asyncio.run(case())
