# SPDX-License-Identifier: Apache-2.0
"""开发库里残留的旧任务（建于"建任务即绑定执行图"之前）不许拖垮启动和主循环（2026-10-03，A′ 发版核验）。

核验员在真实开发库副本上复现：13 个没绑执行图的旧任务各有没结清的预留，启动时的补记账扫描碰到它们，
``TASKGRAPH_NOT_BOUND`` 冲出 ``Orchestrator.__aenter__``，整个编排服务起不来。

旧库用改库字节造（分诊裁决①b1）：上一个进程里建好的任务，在库里删掉它的执行图绑定行，并把它那次
在途派发改成"已结束"（预留仍未结清，即真实开发库里的形状），再冷重开。
重开后：启动扫描照常完成；这个旧任务被主循环按名停掉（``unsupported_unbound_mission``），同库新建的
任务照常完成。开发期不做旧数据兼容：旧任务不迁移、不服务，只是不许连累别人。

**改坏检验**（TG3-02）：补记账扫描不再捕获"没绑定"（TaskGraph 补全第二批起判断只留 ``require_bound``，
不该报错的调用方捕获带类型的 ``NotBoundError``）→ 老任务的预留每轮报一轮故障、不再由关口按名停 → 变红。
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class _HeldWorker(LayeredScriptedProvider):
    """第一个进程里执行者的调用停在半路：进程退出时它的预留还没结清。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker":
            self.entered.set()
            await asyncio.Event().wait()
        return await super().invoke(request, cancel=cancel)


def _as_old_library(path: Any, mission_id: str) -> None:
    """Rewrite this Mission's rows into the shape the release review found in the real
    development library: no TaskGraph binding, and an ended dispatch whose reservation is
    still held (older flows left such holds; the current product does not produce them)."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=OFF")
    for trigger in [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN "
            "('taskgraph_policy_bindings','dispatch_intents')")]:
        db.execute(f"DROP TRIGGER {trigger}")
    db.execute("DELETE FROM taskgraph_policy_bindings WHERE mission_id=?", (mission_id,))
    db.execute("UPDATE dispatch_intents SET state='FAILED' WHERE mission_id=? AND state='SUBMITTED'", (mission_id,))
    db.commit()
    db.close()


@pytest.mark.replay_audit_exempt("用例直接改库造一个旧任务")
def test_an_unbound_legacy_mission_neither_blocks_startup_nor_the_loop(tmp_path):
    root = tmp_path / "root"

    async def case() -> None:
        first = _HeldWorker()
        async with product_world(root, first) as world:
            old = world.create({"goal": "写一份 NOTES.md", "idempotency_key": "legacy",
                                "success_criteria": ["file:NOTES.md"]})["mission_id"]
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            await asyncio.wait_for(first.entered.wait(), 60)
            stop.set()
            runner.cancel()
            path = world.loop.store.path
        _as_old_library(path, old)
        open_holds = sqlite3.connect(path).execute(
            "SELECT COUNT(*) FROM dispatch_intents i JOIN budget_reservations r ON r.subject_id=i.subject_id "
            "WHERE i.mission_id=? AND r.state='RESERVED' AND i.state IN ('SETTLED','FAILED')",
            (old,)).fetchone()[0]
        assert open_holds >= 1

        async with product_world(root, LayeredScriptedProvider()) as world:  # startup scan ran
            new = world.create({"goal": "再写一份 NOTES.md", "idempotency_key": "fresh",
                                "success_criteria": ["file:NOTES.md"]})["mission_id"]
            mission = await world.run_until_settled(new, rounds=20)
            assert str(mission.status.value) == "COMPLETED"
            assert str(world.loop.store.get_mission(old).status.value) == "FAILED"
            assert any(event.type == "MissionFailed" and "unsupported_unbound_mission" in str(event.payload)
                       for event in world.loop.store.list_events(old))

    asyncio.run(case())
