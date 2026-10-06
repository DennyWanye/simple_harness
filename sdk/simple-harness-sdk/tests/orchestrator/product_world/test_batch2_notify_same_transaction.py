# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 I1，A20：通知待办与终态 / 完成事件同事务建（原计划 §7.3）。

此前终态写入只发 ``AssuranceStatusNotificationRequested`` 事件，NOTIFY 待办要等下一轮轮询的游标
摄取才入箱。现在终态写入在同一事务里把 NOTIFY 待办写进 ``assurance_pending_work``；之后游标摄取到
同一事件时按同目标（同 seq、同指纹）合并，不冲突、不重建——事件重放一致。
"""
from __future__ import annotations

import asyncio

from agent_orchestrator.orchestrator.assurance_final_writer import NOTIFICATION_EVENT
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def _notify_rows(world, mission_id):
    return world.store.connection.execute(
        "SELECT work_key,trigger_event_id,target_epoch,state FROM assurance_pending_work "
        "WHERE mission_id=? AND consumer='NOTIFY' ORDER BY work_key", (mission_id,)).fetchall()


def test_a20_the_terminal_write_builds_the_notify_work_in_its_own_transaction(tmp_path):
    async def scenario():
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "g-1"})["mission_id"]
            # 还没有跑过一轮：取消是第一笔终态写入，一个事务
            assert world.control.cancel(mission_id)["changed"] is True
            [request] = [e for e in world.store.list_events(mission_id) if e.type == NOTIFICATION_EVENT]
            final_id = request.payload["final_event_id"]
            rows = _notify_rows(world, mission_id)
            assert [tuple(r) for r in rows] == [
                (f"notify:{mission_id}:{final_id}", request.id, request.seq, "PENDING")], rows
            cursor = world.store.connection.execute(
                "SELECT last_event_seq FROM assurance_event_cursors WHERE mission_id=? AND consumer='NOTIFY'",
                (mission_id,)).fetchone()
            assert cursor is None or cursor[0] < request.seq  # 游标还没摄取到它：待办是终态事务建的
            # 重放：轮询的游标摄取到同一事件 → 同目标合并，仍是这一行；送出一次
            assert await world.drain()
            rows = _notify_rows(world, mission_id)
            assert [tuple(r) for r in rows] == [(f"notify:{mission_id}:{final_id}", request.id, request.seq, "DONE")]
            assert [n["event_id"] for n in world.notices] == [final_id]
            assert world.store.connection.execute(
                "SELECT last_event_seq FROM assurance_event_cursors WHERE mission_id=? AND consumer='NOTIFY'",
                (mission_id,)).fetchone()[0] >= request.seq

    asyncio.run(scenario())
