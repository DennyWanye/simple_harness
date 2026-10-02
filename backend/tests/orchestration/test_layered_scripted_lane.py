# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""产品同形的脚本化端到端：Host 默认部署（分层 + 执行图 + 保证通道）上一个任务整圈跑完。

这是 RP-E3 实施记录 §5 第 3 条欠下的"分层默认路径的脚本化端到端覆盖"，也是删旧平面模式
方案第 1 步的 Host 部分：系统这一侧没有替身，只有模型回复是脚本。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ._layered_lane import (
    REVIEWER,
    LayeredScriptedProvider,
    broken_result,
    layered_service,
    notes_mission,
    planner_reply,
    quick_runtime,
    retry_same_method,
    review_input,
    review_reply,
    reviewer_reply,
    run_until_settled,
    worker_reply,
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

        # 争议从结论本身读出，最终报告和详情都带着它（这一局没有相反结论，为空）。
        assert mission.final_report["disputes"] == []
        assert "conflicts" not in mission.final_report
        assert service.mission_detail(mission.id)["disputes"] == []
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


@pytest.mark.asyncio
async def test_a_malformed_result_is_redone_in_place_without_charging_the_step(orchestration_root, principal):
    """执行者第一次交的结果格式不对（不是它做错了事）：系统自己批准原地重做，不问规划器，
    这一次不算在这一步的次数里；第二次交对了，任务完成。"""

    results = {"n": 0}

    def worker(request):
        reply = worker_reply(request)
        if isinstance(reply, tuple):
            return reply
        results["n"] += 1
        return broken_result(request) if results["n"] == 1 else reply

    provider = LayeredScriptedProvider(worker=worker)
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission(notes_mission("layered-format-1"))
        mission = await run_until_settled(service, created["mission_id"])
        types = _types(service, mission.id)
        assert mission.status.value == "COMPLETED", (mission.status.value, types[-15:])
        assert types.count("ResultRejected") == 1
        assert types.count("AttemptChargeReleased") == 1  # 次数退回
        assert types.count("PlanningRetryAuthorized") == 1
        assert provider.asked.count("planner") == 2  # 只有提做法和采用；重做不是规划器决定的
        leaf = next(task for task in service._orchestrator.store.list_tasks(mission.id)
                    if task.status.value == "COMPLETED")
        assert leaf.attempt_count == 1
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_results_that_are_never_well_formed_stop_the_mission_at_the_cap(orchestration_root, principal):
    """每次交的结果格式都不对：原地重做有上限，到了上限任务明确停下，不会无限重做。"""

    from agent_orchestrator.orchestrator.failure_classes import NON_MODEL_FAILURE_CAP

    provider = LayeredScriptedProvider(worker=broken_result)
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission(notes_mission("layered-format-cap"))
        mission = await run_until_settled(service, created["mission_id"])
        types = _types(service, mission.id)
        assert mission.status.value == "FAILED", (mission.status.value, types[-15:])
        assert types.count("ResultRejected") == NON_MODEL_FAILURE_CAP
        assert "MissionCompleted" not in types
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_a_mission_cancelled_during_a_model_call_ends_cancelled(orchestration_root, principal):
    """执行者的调用还在进行时取消任务：任务以"已取消"结束，不再派发新的工作。"""

    provider = LayeredScriptedProvider()
    provider.held.add("worker")
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission(notes_mission("layered-cancel-1"))
        mission_id = created["mission_id"]
        for _ in range(8):
            await service.drain(timeout=3)
            if provider.entered.is_set():
                break
        assert provider.entered.is_set(), provider.asked
        service.cancel_mission(mission_id)
        provider.release.set()
        mission = await run_until_settled(service, mission_id)
        types = _types(service, mission_id)
        assert mission.status.value == "CANCELLED", (mission.status.value, types[-15:])
        assert "MissionCompleted" not in types
        asked_after = len(provider.asked)
        await service.drain(timeout=5)
        assert len(provider.asked) == asked_after  # 取消之后没有新的模型调用
    finally:
        provider.release.set()
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_a_restart_during_a_model_call_resumes_and_completes(orchestration_root, principal, monkeypatch):
    """执行者的调用进行到一半时服务重启：新服务接着同一个库把任务做完。

    被打断的那次调用结果不明。系统等够时限（产品里 30 秒，这里调成 3 秒）后判它丢失、
    原地重做一次——不用人接管，也不重做计划。"""

    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "MAX_SERVICE_BLOCKER_SECONDS", 3.0)
    first = LayeredScriptedProvider()
    first.held.add("worker")
    service = layered_service(orchestration_root, principal, first, lease_seconds=4.0)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission(notes_mission("layered-restart-1"))
        mission_id = created["mission_id"]
        for _ in range(8):
            await service.drain(timeout=3)
            if first.entered.is_set():
                break
        assert first.entered.is_set(), first.asked
    finally:
        await asyncio.wait_for(service.close(), 30)
        first.release.set()

    second = LayeredScriptedProvider()
    reopened = layered_service(orchestration_root, principal, second, lease_seconds=4.0)
    await asyncio.wait_for(reopened.start(), 30)
    try:
        mission = await run_until_settled(reopened, mission_id, rounds=20)
        types = _types(reopened, mission_id)
        assert mission.status.value == "COMPLETED", (mission.status.value, types[-20:])
        assert types.count("PlanRevisionCommitted") == 1  # 计划没有重做
        assert types.count("AttemptLost") == 1  # 被打断的那次调用判为丢失
        assert types.count("AttemptChargeReleased") == 1  # 不是模型做错，这一次不算次数
        assert "planner" not in second.asked  # 原地重做是系统批准的，没有问规划器
        assert second.asked.count("worker") == 2  # 新服务把那一步重新做了一遍
        attempts = [a for task in reopened._orchestrator.store.list_tasks(mission_id)
                    for a in reopened._orchestrator.store.list_attempts(task.id)]
        assert [a.status.value for a in attempts] == ["LOST", "COMPLETED"]
    finally:
        await asyncio.wait_for(reopened.close(), 30)


@pytest.mark.asyncio
async def test_a_general_mission_with_attached_sources_gives_them_to_the_worker(orchestration_root, principal):
    """通用任务附资料：资料在建任务的同一个事务里登记，执行者的工作区里读得到，任务照常完成。"""

    from agent_orchestrator.testing.fixtures import package_of

    packages: list[dict] = []

    def worker(request):
        packages.append(package_of(request))
        return worker_reply(request)

    provider = LayeredScriptedProvider(worker=worker)
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        created = service.create_mission_with_sources({
            "mission": notes_mission("layered-sources-1"),
            "sources": [{"path": "sources/policy.md", "content": "# 值班规则\n\n每天九点交接。\n",
                         "kind": "text/markdown"}],
        })
        mission = await run_until_settled(service, created["mission_id"])
        types = _types(service, mission.id)
        assert mission.status.value == "COMPLETED", (mission.status.value, types[-15:])
        assert types.count("SourceRegistered") == 1
        first = packages[0]
        assert "sources/policy.md" in first["tools_and_permissions"]["workspace_files"]
        assert "sources" in json.dumps(first.get("source_roots"), ensure_ascii=False)
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_a_backend_killed_inside_a_model_call_is_resumed_by_the_next_one(
    orchestration_root, principal, tmp_path, monkeypatch
):
    """后端在执行者的模型调用进行中被强杀（没有任何收尾），重启后任务接着做完。

    强杀打断一次调用有两种落点，真机 2026-10-02 两种都碰到了：
    * 调用已经交给模型、结果不明（本场景走的是这一种）：系统等够时限后判那次调用丢失，
      不算次数，原地重做。
    * 调用还没交出去、新进程接着跑那一轮（mission-baddf1eb2442858e）：准入因为执行权还记
      在旧进程名下而拒绝它，修复前主循环把这种拒绝当成不可重试、任务直接失败。这一种由
      SDK 的 ``full_target/test_lease_lost_is_redone.py`` 保护（SDK opt.127）——模拟修复前
      的行为时本场景照样通过，所以它保护不到那一种。"""

    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "MAX_SERVICE_BLOCKER_SECONDS", 3.0)
    marker = tmp_path / "worker.marker"
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.orchestration._layered_child", str(orchestration_root), str(marker)],
        cwd=Path(__file__).resolve().parents[2], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 90
        while not marker.exists():
            assert child.poll() is None, child.stderr.read().decode(errors="replace")[-2000:]
            assert time.monotonic() < deadline, "子进程没有走到执行者的模型调用"
            await asyncio.sleep(0.1)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()

    provider = LayeredScriptedProvider()
    service = layered_service(orchestration_root, principal, provider, lease_seconds=4.0)
    await asyncio.wait_for(service.start(), 30)
    try:
        mission_id = service.list_missions()[0]["id"]
        mission = await run_until_settled(service, mission_id, rounds=20)
        types = _types(service, mission_id)
        assert mission.status.value == "COMPLETED", (mission.status.value, mission.final_report, types[-20:])
        assert types.count("PlanRevisionCommitted") == 1  # 计划没有重做
        assert "planner" not in provider.asked  # 重做是系统批准的
        store = service._orchestrator.store
        attempts = [a for task in store.list_tasks(mission_id) for a in store.list_attempts(task.id)]
        assert len(attempts) == 2 and attempts[-1].status.value == "COMPLETED"
        assert types.count("AttemptChargeReleased") == 1  # 被打断的那次不算次数
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_creating_the_same_mission_twice_is_one_mission(orchestration_root, principal):
    """同一个幂等键重复创建：第二次拿回同一个任务，不会建出第二个，也不会重跑。"""

    provider = LayeredScriptedProvider()
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        first = service.create_mission(notes_mission("layered-same-key"))
        again = service.create_mission(notes_mission("layered-same-key"))
        assert first["created"] is True and again["created"] is False
        assert again["mission_id"] == first["mission_id"]
        mission = await run_until_settled(service, first["mission_id"])
        assert mission.status.value == "COMPLETED"
        calls = len(provider.asked)
        after = service.create_mission(notes_mission("layered-same-key"))  # 完成之后再来一次
        assert after["mission_id"] == first["mission_id"] and after["created"] is False
        await service.drain(timeout=5)
        assert len(provider.asked) == calls
        assert len(service.list_missions()) == 1
    finally:
        await asyncio.wait_for(service.close(), 30)


@pytest.mark.asyncio
async def test_two_missions_run_side_by_side_and_both_complete(orchestration_root, principal):
    """两个任务同时在库里：各走各的计划，都完成，互不占用对方的步骤。"""

    provider = LayeredScriptedProvider()
    service = layered_service(orchestration_root, principal, provider)
    await asyncio.wait_for(service.start(), 30)
    try:
        one = service.create_mission(notes_mission("layered-pair-1"))["mission_id"]
        two = service.create_mission(notes_mission("layered-pair-2", goal="写一份 TODO.md，列出三件事",
                                                   success_criteria=["file:TODO.md"]))["mission_id"]
        assert one != two
        store = service._orchestrator.store
        for _ in range(16):
            await service.drain(timeout=30)
            if all(store.get_mission(m).status.value == "COMPLETED" for m in (one, two)):
                break
        for mission_id, path in ((one, "NOTES.md"), (two, "TODO.md")):
            mission = store.get_mission(mission_id)
            assert mission.status.value == "COMPLETED", (mission_id, mission.status.value, _types(service, mission_id)[-12:])
            assert _types(service, mission_id).count("PlanRevisionCommitted") == 1
            leaf = next(task for task in store.list_tasks(mission_id) if task.status.value == "COMPLETED")
            assert [store.get_artifact(item).path for item in leaf.accepted_artifacts] == [path]
    finally:
        await asyncio.wait_for(service.close(), 30)
