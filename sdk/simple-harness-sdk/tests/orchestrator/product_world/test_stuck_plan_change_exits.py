# SPDX-License-Identifier: Apache-2.0
"""改计划卡在半路时的两个出口（2026-10-03 HTN 补齐阶段 B 第 2 条）。

卡住的形状（故障注入：新计划提交那一步一直出错）：旧尝试已经停了、计划结构没变，收敛已就绪，
但推进它的通知连败 5 次被挡住。只读视图如实给出收敛作业和被挡通知（消息号、行版本、类型、
错误码、失败次数）；出口是人的两个动作，都走 SDK 操作员服务（模型调不到）：

* **重新发送被挡的通知**：故障排除后，通知重回待发，新计划照常提交，任务完成；
* **放弃这次改计划**：旧计划恢复执行；规划器下一次请求里看得到"用户放弃了哪个决定、理由原文"，
  由它决定换个改法还是怎样。

**改坏检验**：规划包不再带 ``abandoned_plan_changes`` → 第二条变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.storage.store import StoreError
from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import TENANT, product_world
from test_repair_replace_method import CRITERIA, _Provider

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
REASON = "先按原计划做完"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class _Recording(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.abandoned_seen: list[list[dict[str, Any]]] = []

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "planner":
            self.abandoned_seen.append(list(package_of(request).get("abandoned_plan_changes") or ()))
        return await super().invoke(request, cancel=cancel)


@pytest.mark.parametrize("exit_", ["retry", "abandon"])
def test_a_plan_change_stuck_behind_a_blocked_notification_has_two_exits(tmp_path, monkeypatch, exit_):
    import agent_orchestrator.orchestrator.taskgraph_resume as resume

    real_resume = resume.resume_converged_plan
    broken = {"on": True}

    async def resume_or_fail(*args: Any, **kwargs: Any) -> Any:
        if broken["on"]:
            raise StoreError("TASKGRAPH_INJECTED_RESUME_FAILURE")
        return await real_resume(*args, **kwargs)

    monkeypatch.setattr(resume, "resume_converged_plan", resume_or_fail)

    async def case():
        provider = _Recording()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "整理事实并写笔记", "idempotency_key": "stuck-" + exit_,
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            store = world.loop.store
            principal = world.control._principal
            reads = world.loop.taskgraph_read_api(tenant_id=TENANT, principal=principal)
            operator = world.loop.taskgraph_operator_api(tenant_id=TENANT, principal=principal)
            stop = asyncio.Event()

            async def drive() -> None:  # the held step keeps run() from going idle
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            def cancelled_by_convergence() -> bool:
                return any(event.type == "AttemptCancelled"
                           and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")
                           for event in store.list_events(mission_id))

            runner = asyncio.create_task(drive())
            try:
                view = None
                for _ in range(900):
                    if not provider.late.is_set() and cancelled_by_convergence():
                        provider.late.set()
                    view = reads.convergence(mission_id)
                    if view["blocked_notifications"]:
                        break
                    await asyncio.sleep(0.1)
                assert view is not None and view["blocked_notifications"], "the notification never blocked"
                [job] = [item for item in view["jobs"] if item["state"] not in {"APPLIED", "ABANDONED"}]
                assert job["state"] == "READY"
                # each step of the job sent its own CONVERGE notification; all of them failed 5 times
                for blocked in view["blocked_notifications"]:
                    assert blocked["kind"] == "CONVERGE" and blocked["subject_key"] == job["job_id"]
                    assert blocked["attempts"] >= 5 and blocked["error_code"] == "FOLLOWUP_HANDLER_FAILED"

                broken["on"] = False
                if exit_ == "retry":
                    for number, blocked in enumerate(view["blocked_notifications"]):
                        operator.retry_notification(mission_id, blocked["message_id"],
                                                    expected_version=blocked["row_version"],
                                                    command_id=f"click-retry-{number}", reason="故障已排除")
                else:
                    operator.abandon_convergence(mission_id, job["job_id"], expected_version=job["row_version"],
                                                 command_id="click-abandon-1", reason=REASON)
                # 双击、或看着没刷新的旧画面再点一次：带的是点击前的版本号——按名拒收，库里不多写。
                with pytest.raises(StoreError) as refused:
                    if exit_ == "retry":
                        stale = view["blocked_notifications"][0]
                        operator.retry_notification(mission_id, stale["message_id"],
                                                    expected_version=stale["row_version"],
                                                    command_id="click-again", reason="又点了一次")
                    else:
                        operator.abandon_convergence(mission_id, job["job_id"], expected_version=job["row_version"],
                                                     command_id="click-again", reason="又点了一次")
                assert str(refused.value) in {"TASKGRAPH_FOLLOWUP_REPAIR_CONFLICT", "TASKGRAPH_CONVERGENCE_TERMINAL",
                                              "TASKGRAPH_CONVERGENCE_CAS_CONFLICT"}
                assert store.get_receipt("click-again") is None
                assert not [event for event in store.list_events(mission_id) if event.trace_id == "click-again"]
                world.loop._wake.set() if hasattr(world.loop, "_wake") else None
                for _ in range(900):
                    if str(store.get_mission(mission_id).status.value) in TERMINAL:
                        break
                    await asyncio.sleep(0.1)
            finally:
                stop.set()
                provider.late.set()
                await asyncio.wait_for(runner, 30)
            kinds = [event.type for event in store.list_events(mission_id)]
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED", kinds[-20:]
            final = reads.convergence(mission_id)
            assert not final["blocked_notifications"]
            if exit_ == "abandon":
                assert "TaskGraphConvergenceAbandoned" in kinds
                told = [rows for rows in provider.abandoned_seen if rows]
                assert told, "the planner was never told about the abandoned change"
                [row] = told[0]
                assert row["reason"] == REASON and row["decision_id"] == job["decision_id"]
                assert row["decision_type"] == "REPAIR"

    asyncio.run(case())
