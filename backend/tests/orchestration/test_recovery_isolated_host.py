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


@pytest.mark.asyncio
async def test_a_write_to_an_isolated_mission_is_refused_with_the_sdks_code_and_cancel_still_works(
    orchestration_root, principal
):
    """N3-27：SDK 门面对被隔离任务的推进类写入报 ``MISSION_RECOVERY_ISOLATED``。Host 把码原样透给界面
    （IPC 回执的 ``error_code``）与主对话（``mission_amend`` 的拒绝码），给人看的话换成同一句
    ``RECOVERY_ISOLATED_NOTE``；取消照常。没被隔离的任务同一个请求不报这个码。

    这里直接把任务放进 SDK 第 3 步写的那张隔离名单（``Orchestrator.recovery_isolated`` 只读它）。"""
    from deskpet.orchestration.chat_tool import MissionStartRefused, amend_mission

    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(missions=2),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        bad = service.create_mission(notes_request("iso-write-bad"))["mission_id"]
        good = service.create_mission(notes_request("iso-write-good"))["mission_id"]
        service._orchestrator._recovery_isolated[bad] = {"tables": ["missions"], "silent_changes": []}

        approve = {"command_id": "c-iso", "proposal": {}, "approval_source": "HUMAN",
                   "expected_requirements_ref": {"id": "r", "revision": 1, "content_hash": "0" * 64}}
        refused = await handle(service, "mission_operation_completion_approve",
                               {"request_id": "a1", "mission_id": bad, **approve})
        assert refused["payload"]["ok"] is False
        assert refused["payload"]["error_code"] == "MISSION_RECOVERY_ISOLATED"
        assert refused["payload"]["error"] == RECOVERY_ISOLATED_NOTE
        other = await handle(service, "mission_operation_completion_approve",
                             {"request_id": "a2", "mission_id": good, **approve})
        assert other["payload"]["error_code"] != "MISSION_RECOVERY_ISOLATED"

        revision = int(service.mission_detail(bad)["operation_workspace"]["requirements_ref"]["revision"])
        with pytest.raises(MissionStartRefused) as caught:
            amend_mission(lambda: service, {"mission_id": bad, "expected_revision": revision, "reason": "加一条",
                                            "changes": [{"op": "add", "statement": "file:MORE.md"}]},
                          run_id="run-iso", call_id="call-iso")
        assert caught.value.code == "MISSION_RECOVERY_ISOLATED" and str(caught.value) == RECOVERY_ISOLATED_NOTE

        cancelled = await handle(service, "mission_cancel", {"request_id": "c1", "mission_id": bad})
        assert cancelled["payload"]["ok"] is True and cancelled["payload"]["data"]["status"] == "CANCELLED"
    finally:
        await service.close()
