# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""后台任务结束通知进主对话（HTN 补齐阶段 B 第 1 条，2026-10-03）。

保证通道在任务结束时通知 Host（至少一次）；Host 记下通知直到人点"已收到"：
* 任务完成后，通知出现在待确认列表里，主 Agent 下一轮的上下文文字里也有它；
* "已收到"走控制通道，重复点不改第一次的时间；没有的通知按名拒绝；
* 重启后已收到的不再出现；Host 记录丢了，也能从 SDK 的持久回执补回来。

**改坏检验**：启动时不补回（删 ``backfill`` 调用）→ 第三段变红。
"""
from __future__ import annotations

import asyncio

import pytest
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.notices import NOTICES_FILE

from ._layered_lane import LayeredScriptedProvider, layered_service, notes_mission, quick_runtime, run_until_settled


async def _pending_after_drain(service, mission_id: str, *, rounds: int = 40) -> list[dict]:
    for _ in range(rounds):
        pending = [row for row in service.pending_notices() if row["mission_id"] == mission_id]
        if pending:
            return pending
        await service.drain(timeout=2)
        await asyncio.sleep(0.05)
    return []


@pytest.mark.asyncio
async def test_a_finished_mission_reaches_the_main_conversation_until_the_person_acknowledges_it(
    orchestration_root, principal, monkeypatch
):
    quick_runtime(monkeypatch)
    service = layered_service(orchestration_root, principal, LayeredScriptedProvider())
    await service.start()
    try:
        mission_id = service.create_mission(notes_mission("notice-1"))["mission_id"]
        assert (await run_until_settled(service, mission_id)).status.value == "COMPLETED"
        [notice] = await _pending_after_drain(service, mission_id)
        assert notice["status"] == "COMPLETED" and notice["status_zh"] == "已完成"
        assert notice["acked_at"] is None
        assert f"mission_id={mission_id} status=COMPLETED" in service.notice_context_text()

        listed = await handle(service, "mission_notices", {})
        assert [row["notice_id"] for row in listed["payload"]["data"]] == [notice["notice_id"]]
        missing = await handle(service, "mission_notice_ack", {"notice_id": "no-such-notice"})
        assert missing["payload"]["ok"] is False and missing["payload"]["error_code"] == "notice_not_found"
        first = await handle(service, "mission_notice_ack", {"notice_id": notice["notice_id"]})
        assert first["payload"]["ok"] is True, first
        again = await handle(service, "mission_notice_ack", {"notice_id": notice["notice_id"]})
        assert again["payload"]["data"]["acked_at"] == first["payload"]["data"]["acked_at"]
        assert service.pending_notices() == []
        assert service.notice_context_text() == ""
    finally:
        await service.close()

    # 重启：已收到的不再出现
    service = layered_service(orchestration_root, principal, LayeredScriptedProvider())
    await service.start()
    try:
        assert service.pending_notices() == []
    finally:
        await service.close()

    # Host 记录丢了：从 SDK 的持久回执补回（没收到过，所以重新待确认）；人点"全部已收到"一次清空
    (orchestration_root / NOTICES_FILE).unlink()
    service = layered_service(orchestration_root, principal, LayeredScriptedProvider())
    await service.start()
    try:
        assert [row["mission_id"] for row in service.pending_notices()] == [mission_id]
        everything = await handle(service, "mission_notice_ack", {"all": True})
        assert everything["payload"]["data"] == {"acked": 1}
        assert service.pending_notices() == []
    finally:
        await service.close()
