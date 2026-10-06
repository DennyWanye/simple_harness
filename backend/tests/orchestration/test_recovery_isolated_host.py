# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""第 2 批车道 R：重启恢复第 3 步只隔离"库与自己历史对不上"的任务（SDK ``recovery_status().
isolated_missions``）。Host 要让人看得见：任务列表、任务详情、主对话的任务状态 / 列任务回执都带
``recovery_isolated``（对不上的表名），主对话再带一句给用户的话；没被隔离的任务为 None；任务结束后不再标。

隔离本身由 SDK 判（``recovery_coordinator._step_rebuild``）。Host 装的轮子可能还没有这个字段，这里把
``Orchestrator.recovery_status`` 钉成带 ``isolated_missions`` 的值，只测 Host 怎么接。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.chat_tool import mission_list, mission_status
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import RECOVERY_ISOLATED_NOTE, OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def _pin(monkeypatch, isolated):
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    status = {"state": "READY", "side_effects_disabled": False, "recovery_id": "rec-1", "latest": None,
              "isolated_missions": isolated}
    monkeypatch.setattr(Orchestrator, "recovery_status", lambda self: dict(status), raising=False)


@pytest.mark.asyncio
async def test_an_isolated_mission_is_marked_in_the_list_the_detail_and_the_chat(
    orchestration_root, principal, monkeypatch
):
    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(missions=2),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        bad = service.create_mission(notes_request("iso-bad"))["mission_id"]
        good = service.create_mission(notes_request("iso-good"))["mission_id"]
        _pin(monkeypatch, {bad: {"tables": ["attempts", "tasks"], "silent_changes": [{"table": "tasks"}]}})
        assert service.status()["state"] == "available"  # 只隔离这一个，不让整个部署降级

        rows = {row["id"]: row for row in service.list_missions()}
        assert rows[bad]["recovery_isolated"] == {"tables": ["attempts", "tasks"]}
        assert rows[good]["recovery_isolated"] is None
        listed = await handle(service, "mission_list", {"request_id": "l1"})
        by_id = {row["id"]: row for row in listed["payload"]["data"]["missions"]}
        assert by_id[bad]["recovery_isolated"] == {"tables": ["attempts", "tasks"]}

        assert service.mission_detail(bad)["recovery_isolated"] == {"tables": ["attempts", "tasks"]}
        assert service.mission_detail(good)["recovery_isolated"] is None

        shown = mission_status(lambda: service, {"mission_id": bad})
        assert shown["recovery_isolated"] == {"tables": ["attempts", "tasks"], "note": RECOVERY_ISOLATED_NOTE}
        assert shown["note"] == RECOVERY_ISOLATED_NOTE
        plain = mission_status(lambda: service, {"mission_id": good})
        assert plain["recovery_isolated"] is None and plain["note"] != RECOVERY_ISOLATED_NOTE
        chat_rows = {row["mission_id"]: row for row in mission_list(lambda: service, {})["missions"]}
        assert chat_rows[bad]["recovery_isolated"]["note"] == RECOVERY_ISOLATED_NOTE
        assert chat_rows[good]["recovery_isolated"] is None

        # 取消后任务结束：SDK 名单里还在，也不再标"不再推进；可以取消"
        service.cancel_mission(bad)
        assert {row["id"]: row for row in service.list_missions()}[bad]["recovery_isolated"] is None
        assert service.mission_detail(bad)["recovery_isolated"] is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_an_unreadable_recovery_status_means_nothing_is_isolated(orchestration_root, principal, monkeypatch):
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        mission_id = service.create_mission(notes_request("iso-x"))["mission_id"]

        def broken(self):
            raise RuntimeError("store closed")

        monkeypatch.setattr(Orchestrator, "recovery_status", broken, raising=False)
        assert service.list_missions()[0]["recovery_isolated"] is None
        assert service.mission_detail(mission_id)["recovery_isolated"] is None
    finally:
        await service.close()
