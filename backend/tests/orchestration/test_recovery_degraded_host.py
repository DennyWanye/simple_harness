# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""第 2～4 批补齐评估处置（H01 的 Host 半边）：SDK 重启恢复协议降级（DEGRADED_RECOVERY）时，编排服务
不当"可用"——状态 ``degraded_recovery``、原因写明失败的那一步、``status().recovery`` 带 SDK 的只读诊断；
新任务与改要求被拒（具名码），只读动词仍开。

降级本身由 SDK 判（``recovery_coordinator``），SDK 侧用例在 ``tests/orchestrator/full_target/test_recovery_*``。
这里把 SDK 的 ``recovery_status`` 钉成降级，只测 Host 怎么接。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request

DEGRADED = {"state": "DEGRADED_RECOVERY", "side_effects_disabled": True, "recovery_id": "rec-1",
            "latest": {"recovery_id": "rec-1", "state": "DEGRADED_RECOVERY", "failed_step": "orphan_reclaim",
                       "steps": [{"step_no": 7, "step": "orphan_reclaim", "status": "FAILED"}]}}


@pytest.mark.asyncio
async def test_a_degraded_recovery_turns_the_service_read_only_and_names_the_failed_step(
    orchestration_root, principal, monkeypatch
):
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        before = service.status()
        # 装的轮子若还没有 ``recovery_status``（钉版前）读不到为 None；钉版后是 READY 一类，总之不是降级
        assert before["state"] == "available" and (before["recovery"] or {}).get("state") != "DEGRADED_RECOVERY"
        monkeypatch.setattr(Orchestrator, "recovery_status", lambda self: dict(DEGRADED), raising=False)
        assert await service.drain(timeout=10) is True

        status = service.status()
        assert status["available"] is False and status["state"] == "degraded_recovery"
        assert "orphan_reclaim" in (status["reason"] or "")
        assert status["recovery"] == DEGRADED

        # 新工作不收：建任务、改要求都是具名拒绝；只读动词照答
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(notes_request("deg-1"))
        assert refused.value.code == "orchestration_degraded_recovery"
        listed = await handle(service, "mission_list", {"request_id": "l1"})
        assert listed["payload"]["ok"] is True, listed
        shown = await handle(service, "orchestration_status", {"request_id": "s1"})
        assert shown["payload"]["data"]["state"] == "degraded_recovery"
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("deg-2")})
        assert created["payload"]["ok"] is False and created["payload"]["error_code"] == "orchestration_unavailable"
        cancelled = await handle(service, "mission_cancel", {"request_id": "x1", "mission_id": "mission-x"})
        assert cancelled["payload"]["ok"] is False and cancelled["payload"]["error_code"] == "orchestration_unavailable"

        # 主对话的另两个写入口（换资料、退役方法库条目）同样具名拒绝
        with pytest.raises(OrchestrationRequestError) as refused:
            service.source_command("supersede", {"mission_id": "mission-x", "path": "a.md"})
        assert refused.value.code == "orchestration_degraded_recovery"
        with pytest.raises(OrchestrationRequestError) as refused:
            service.retire_library_entry({"entry_id": "e-1", "reason": "过时"})
        assert refused.value.code == "orchestration_degraded_recovery"

        # 非披露诊断在降级恢复里照开（DEGRADED_RECOVERY_READS 含它），状态里的保证通道根也照读
        diagnostic = await handle(service, "mission_assurance_root_diagnostic", {"request_id": "d1"})
        assert diagnostic["payload"]["ok"] is True, diagnostic
        assert diagnostic["payload"]["data"]["host_state"] == "degraded_recovery"
        assert status["assurance_root"] is not None
    finally:
        await service.close()
