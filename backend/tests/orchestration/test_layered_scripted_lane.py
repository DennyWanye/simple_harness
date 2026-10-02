# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""产品同形的脚本化端到端：Host 默认部署（分层 + 执行图 + 保证通道）上一个任务整圈跑完。

这是 RP-E3 实施记录 §5 第 3 条欠下的"分层默认路径的脚本化端到端覆盖"，也是删旧平面模式
方案第 1 步的 Host 部分：系统这一侧没有替身，只有模型回复是脚本。
"""

from __future__ import annotations

import asyncio

import pytest

from ._layered_lane import (
    REVIEWER,
    LayeredScriptedProvider,
    layered_service,
    notes_mission,
    planner_reply,
    quick_runtime,
    retry_same_method,
    review_input,
    review_reply,
    reviewer_reply,
    run_until_settled,
)
from agent_orchestrator.orchestrator.plan_commits import HIERARCHICAL_SEMANTICS, semantics_of
from agent_orchestrator.storage.assurance_store import AssuranceStore


@pytest.fixture(autouse=True)
def _quick_runtime(monkeypatch):
    quick_runtime(monkeypatch)


def _types(service, mission_id: str) -> list[str]:
    return [event.type for event in service._orchestrator.store.list_events(mission_id)]


@pytest.mark.asyncio
async def test_a_mission_on_the_default_deployment_completes_on_scripted_replies(orchestration_root, principal):
    provider = LayeredScriptedProvider()
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        assert service.status()["available"] and service.status()["assurance_available"], service.status()
        created = service.create_mission(notes_mission())
        mission = await run_until_settled(service, created["mission_id"])
        store = service._orchestrator.store
        assert mission.status.value == "COMPLETED", (mission.status.value, mission.final_report)

        # 与产品同形：分层、保证通道、执行图三样都在。
        assert semantics_of(mission) == HIERARCHICAL_SEMANTICS
        assert AssuranceStore(store).lane(mission.id) == "ASSURANCE_1_1"
        types = _types(service, mission.id)
        assert "TaskGraphContractEnabled" in types
        # 系统这一侧都是 Host 自己做的：确认完成要求、提交计划、认证收尾。
        for kind in ("OperationCompletionSpecApproved", "PlanningMethodProposed", "PlanningMethodReviewed",
                     "PlanRevisionCommitted", "AcceptanceCommitted", "AssuranceMissionFinalized",
                     "MissionCompleted"):
            assert kind in types, kind
        assert types.count("PlanRevisionCommitted") == 1

        # 模型被问到的顺序：提做法 → 审做法 → 采用 → 执行者（写文件、交结果）→ 步骤审查 → 最终审查。
        assert provider.asked == ["planner", REVIEWER, "planner", "worker", "worker", REVIEWER, REVIEWER]

        # 那一步真的做完了，产物是它写的文件。
        leaf = next(task for task in store.list_tasks(mission.id) if task.status.value == "COMPLETED")
        assert leaf.accepted_result_id is not None
        assert [store.get_artifact(item).path for item in leaf.accepted_artifacts] == ["NOTES.md"]
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_a_step_review_that_sends_the_work_back_is_redone_and_then_accepted(orchestration_root, principal):
    """步骤审查第一次判返工：这一步重做一次，第二次通过，任务照常完成。"""

    seen = {"content_reviews": 0}

    def reviewer(request):
        package = review_input(request)
        purpose = str((package or {}).get("package", {}).get("purpose", ""))
        if package is not None and purpose == "TASK_CONTENT":
            seen["content_reviews"] += 1
            if seen["content_reviews"] == 1:
                return review_reply(package, verdict="REWORK", grade="FAIL", reason="要点写得太笼统，请写具体。")
        return reviewer_reply(request)

    def planner(request):
        # 返工后系统把失败如实交给规划器，由它决定怎么修；这里答"原样再做一次"。
        return retry_same_method(request) or planner_reply(request)

    provider = LayeredScriptedProvider(reviewer=reviewer, planner=planner)
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission(notes_mission("layered-rework-1"))
        mission = await run_until_settled(service, created["mission_id"])
        types = _types(service, mission.id)
        assert seen["content_reviews"] >= 1, provider.asked
        assert mission.status.value == "COMPLETED", (mission.status.value, mission.final_report, types[-15:])
        assert types.count("VerificationFailed") == 1
        assert types.count("PlanningRetryAuthorized") == 1
        assert provider.asked.count("worker") == 4  # 两次尝试，各写一次文件、交一次结果
        assert provider.asked.count("planner") == 3  # 提做法、采用、决定原样重试
    finally:
        await asyncio.wait_for(service.close(), 30)
