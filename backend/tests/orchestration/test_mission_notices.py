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


def _host_with(final_report: dict, events: list[tuple[str, str, dict]], notices: list[str]):
    """Host 的通知读法本身：任务记录与事件表是 SDK 的事实，这里只摆出读得到的那几条。"""
    import json
    import sqlite3
    from types import SimpleNamespace

    from deskpet.orchestration.service import OrchestrationService

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE events (event_id TEXT, type TEXT, payload_json TEXT)")
    connection.executemany("INSERT INTO events VALUES (?,?,?)",
                           [(eid, kind, json.dumps(payload)) for eid, kind, payload in events])
    mission = SimpleNamespace(status=SimpleNamespace(value="CANCELLED"), goal="写周报并发布",
                              stop_reason="CANCELLED", final_report=final_report)
    host = SimpleNamespace(
        _notices=SimpleNamespace(pending=lambda: [
            {"notice_id": nid, "mission_id": "m1", "state_version": 3, "notified_at": float(n), "acked_at": None}
            for n, nid in enumerate(notices)]),
        _orchestrator=SimpleNamespace(store=SimpleNamespace(get_mission=lambda _id: mission, connection=connection)))
    host.pending_notices = lambda *_: OrchestrationService.pending_notices(host)
    return host, OrchestrationService


def test_a_stopped_mission_with_unsettled_actions_and_their_later_result_are_both_said():
    """一致性补改 H-2：取消时还有结果不明的对外操作，通知写"还有 N 个结果不明，系统会继续核对"；
    之后核对出结果，那条通知说的是"核对结果：已生效 / 未生效"。主 Agent 上下文同样写明。

    **改坏检验**：通知不读核对结果事件（删掉 ``action_settled`` 那一段）→ 变红。
    """
    from agent_orchestrator.orchestrator.event_handler import ACTION_SETTLED_AFTER_STOP

    report = {"unresolved_actions": [{"action_key": "a:v1", "operation": "publish", "target": "reports/weekly.md",
                                      "state": "UNKNOWN"}]}
    host, service_class = _host_with(report, [
        ("e-cancel", "MissionCancelled", {}),
        ("e-settled", ACTION_SETTLED_AFTER_STOP, {"operation": "publish", "target": "reports/weekly.md",
                                                  "state": "SUCCEEDED", "applied": True}),
    ], ["e-cancel", "e-settled"])
    stopped, settled = host.pending_notices()
    assert stopped["unresolved_actions"] == 1 and "action_settled" not in stopped
    assert settled["action_settled"] == {"operation": "publish", "target": "reports/weekly.md", "applied": True}
    text = service_class.notice_context_text(host)
    assert "unresolved_actions=1 (outcome unknown; the system keeps checking)" in text
    assert "action_checked=publish reports/weekly.md result=applied" in text
