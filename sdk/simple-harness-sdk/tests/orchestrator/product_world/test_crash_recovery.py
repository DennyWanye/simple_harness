# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐 F2：崩溃切点清单里归联测执行的各点，逐点"走到 → 崩 → 同一数据目录重开 → 恢复正确"。

``test_fault_points.py`` 只证明注入点在真实路径上；这里验恢复（切点清单 K03、K06 目标结论那一半、
K08、K09、K13）。重开用同一个数据目录、一个新的产品同形世界——与真重启走同一条恢复路径。
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import REVIEWER, LayeredScriptedProvider
from test_operation import PUBLISH, TARGET, _confirm_completion


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


async def _until_crash(world: Any, fired: str) -> None:
    for _ in range(20):
        try:
            await world.drain(timeout=10)
        except InjectedCrash:
            break
        if fired in world.store.fired:
            break
    assert fired in world.store.fired


async def _until_done(world: Any, mission_id: str, rounds: int = 30) -> str:
    status = ""
    for _ in range(rounds):
        await world.drain(timeout=20)
        status = str(world.store.get_mission(mission_id).status.value)
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
    return status


def _count(world: Any, sql: str, *args: Any) -> int:
    return int(world.store.connection.execute(sql, args).fetchone()[0])


NOTE = {"goal": "写一份笔记", "success_criteria": ["file:notes/a.md"]}


@pytest.mark.parametrize("point", ["after_agent_created", "after_submit"])
def test_work_the_executor_already_received_is_found_again_not_created_anew(tmp_path, point):
    """K03：执行侧已收到工作、编排还没记回执时进程没了。重开后按原身份找回原来那次执行：
    这一步始终只有一次尝试、一条派发意图，任务照常完成。"""

    async def case():
        root = tmp_path / "root"
        async with product_world(root, LayeredScriptedProvider()) as world:
            world.loop.arm_fault(point, kind="attempt")
            mission_id = world.create({**NOTE, "idempotency_key": "crash-" + point})["mission_id"]
            await _until_crash(world, f"{point}:attempt")
            intents = [tuple(r) for r in world.store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt'", (mission_id,))]
            assert len(intents) == 1
        async with product_world(root, LayeredScriptedProvider()) as world:
            assert await _until_done(world, mission_id) == "COMPLETED"
            assert [tuple(r) for r in world.store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt'",
                (mission_id,))] == intents
            assert _count(world, "SELECT COUNT(*) FROM attempts WHERE mission_id=?", mission_id) == 1

    asyncio.run(case())


def test_a_saved_goal_review_is_taken_up_again_without_asking_the_reviewer_twice(tmp_path):
    """K06 目标结论那一半：目标的审阅已存、结论还没提交时进程没了。重开后接着用存下的那次审阅，
    审阅员被问到的次数与不崩时一样多，任务照常完成。"""

    async def case():
        calm = LayeredScriptedProvider()
        async with product_world(tmp_path / "calm", calm) as world:
            mission_id = world.create({**NOTE, "idempotency_key": "calm-k06"})["mission_id"]
            assert await _until_done(world, mission_id) == "COMPLETED"
        root = tmp_path / "root"
        first, second = LayeredScriptedProvider(), LayeredScriptedProvider()
        async with product_world(root, first) as world:
            world.loop.arm_fault("before_goal_resolution", kind="goal")
            mission_id = world.create({**NOTE, "idempotency_key": "crash-k06"})["mission_id"]
            await _until_crash(world, "before_goal_resolution:goal")
        async with product_world(root, second) as world:
            assert await _until_done(world, mission_id) == "COMPLETED"
        assert (first.asked + second.asked).count(REVIEWER) == calm.asked.count(REVIEWER)

    asyncio.run(case())


#: 发布服务真被调用的次数（跨崩前、重开后两个世界累计）
CALLS = {"publish": 0}


def _publishing(tmp_path: Path, provider: LayeredScriptedProvider):  # type: ignore[no-untyped-def]
    published = tmp_path / "published"
    published.mkdir(exist_ok=True)
    connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
    execute = connector.execute

    def counted(*args: Any, **kwargs: Any) -> Any:
        CALLS["publish"] += 1
        return execute(*args, **kwargs)

    connector.execute = counted  # type: ignore[method-assign]
    policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
    return product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                         deployment_policy=policy)


async def _crash_while_publishing(tmp_path: Path, point: str) -> str:
    async with _publishing(tmp_path, LayeredScriptedProvider()) as world:
        mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                   "success_criteria": ["file:" + TARGET, PUBLISH],
                                   "idempotency_key": "crash-" + point})["mission_id"]
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
        await _until_crash(world, f"{point}:action")
    return mission_id


def _after_the_lease(world: Any) -> None:
    """交接的租约（两倍调用超时再加五秒）没到期时，对账不碰它——那次调用可能还在路上。
    进程没了之后租约总会到期；这里把库的时钟拨过去，不真等。"""
    world.store._clock = lambda: time.time() + 600


def _published(tmp_path: Path) -> list[str]:
    return sorted(str(p.relative_to(tmp_path / "published")) for p in (tmp_path / "published").rglob("*")
                  if p.is_file())


@pytest.mark.parametrize("point", ["after_handoff_before_call", "after_external_effect"])
def test_a_publish_cut_off_midway_is_settled_under_its_own_identity(tmp_path, point):
    """K08 交接已记、外部调用前；K09 外部已生效、本地没记下。重开后按原来那个动作的身份去查：
    动作始终只有一条，文件只发布一份，任务照常完成。外部调用前就没了的那次，查到确实没发出去，
    用原来那个动作再交一次；外部已生效的那次，查到原来那份，不再发。"""

    async def case():
        CALLS["publish"] = 0
        mission_id = await _crash_while_publishing(tmp_path, point)
        # 调用前就没了的那次一次没调；外部已生效的那次调过一次
        assert CALLS["publish"] == (0 if point == "after_handoff_before_call" else 1)
        async with _publishing(tmp_path, LayeredScriptedProvider()) as world:
            before = [str(a["action_key"]) for a in world.store.list_actions(mission_id)]
            assert len(before) == 1
            _after_the_lease(world)
            assert await _until_done(world, mission_id) == "COMPLETED"
            actions = world.store.list_actions(mission_id)
            assert [str(a["action_key"]) for a in actions] == before
            assert str(actions[0]["state"]) == "SUCCEEDED"
        assert len(_published(tmp_path)) == 1
        assert CALLS["publish"] == 1  # 前后合计只真调了一次：没发出去的补发一次，已生效的不再发

    asyncio.run(case())


def test_a_fault_while_reconciling_one_action_is_that_missions_round_fault(tmp_path, monkeypatch):
    """K13：对账某个动作时出了库错误。只记成那个任务这一轮的故障，主循环不抛；错误过去后照常对上。"""
    from agent_orchestrator.runtime.actions import ActionExecutor as ActionService

    broken = {"on": True, "hits": 0}
    original = ActionService.reconcile_one

    async def reconcile_one(self, key, **kwargs):  # type: ignore[no-untyped-def]
        if broken["on"]:
            broken["hits"] += 1
            raise sqlite3.OperationalError("disk I/O error")
        return await original(self, key, **kwargs)

    async def case():
        mission_id = await _crash_while_publishing(tmp_path, "after_external_effect")
        monkeypatch.setattr(ActionService, "reconcile_one", reconcile_one)
        async with _publishing(tmp_path, LayeredScriptedProvider()) as world:
            _after_the_lease(world)
            await world.drain(timeout=20)  # run() must not raise
            assert broken["hits"] >= 1
            faults = [e.payload for e in world.store.list_events(mission_id) if e.type == "MissionRoundFault"]
            assert any(str(fault["where"]).startswith("reconcile:") for fault in faults), faults
            broken["on"] = False
            assert await _until_done(world, mission_id) == "COMPLETED"
        assert len(_published(tmp_path)) == 1

    asyncio.run(case())
