# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐 F1-2：崩溃切点清单里新补的三个注入点，各装上一次、走到、确实触发。

只证明注入点在真实路径上（产品同形世界），不验恢复——恢复按切点清单在联测（F2）里逐点执行。

**改坏检验**：删掉注入调用 → 装上了也不触发 → 变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from test_operation import PUBLISH, TARGET, _confirm_completion


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


async def _drive(world: Any, fired: str) -> None:
    """Run until the point fires; the crash leaves the main loop the way a dying process would."""
    for _ in range(20):
        try:
            await world.drain(timeout=10)
        except InjectedCrash:
            break
        if fired in world.store.fired:
            break


def test_goal_conclusion_point_fires(tmp_path):
    """K06 目标结论那一半：审阅已存、目标结论还没提交。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            world.loop.arm_fault("before_goal_resolution", kind="goal")
            mission_id = world.create({"goal": "写一份笔记", "idempotency_key": "fault-k06",
                                       "success_criteria": ["file:notes/a.md"]})["mission_id"]
            await _drive(world, "before_goal_resolution:goal")
            assert "before_goal_resolution:goal" in world.store.fired

    asyncio.run(case())


@pytest.mark.parametrize("point", ["after_handoff_before_call", "after_external_effect"])
def test_operation_points_fire(tmp_path, point):
    """K08 交接已记、外部调用前；K09 外部调用已返回、结果还没记下。"""

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(),
                                 connectors={"file_publish": connector}, deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "fault-" + point})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            approvals: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            world.loop.arm_fault(point, kind="action")
            world.control.decide(approvals[0]["request_id"], "approve")
            await _drive(world, f"{point}:action")
            assert f"{point}:action" in world.store.fired
            if point == "after_external_effect":
                assert any(published.rglob("*.md"))  # 外部真的生效了，本地还没记下

    asyncio.run(case())
