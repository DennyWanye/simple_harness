# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""看计划、跳过、现在执行、停止（AgentOS 迁移第 6 块）。

- 看计划：每步有稳定编号（occurrence_id）、步骤号、版本、状态；计划版本。
- 跳过：在跑的步骤被跳过 → 开着的尝试取消、步骤记 CANCELLED（不算成功）；规划器下一轮看到
  human_takeovers 里这条记录。
- 重复点击（同一 command_id）只执行一次；过期的决定（expected_version 不符）被拒。
- 现在执行：三步并列、并发 1 时，被点名的那步排到最前；没就绪的拒绝。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import pytest

from agent_orchestrator.host import HostRequestError, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.testing.scripted_replies import (
    decision,
    one_step_method,
    package_of,
    planner_reply,
    worker_reply,
)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=300))


async def _open(tmp_path, *, planner, worker=worker_reply, **settings):
    provider = ScriptedProvider(planner=planner, worker=worker)
    host = await OrchestrationHost.open(
        tmp_path / "host",
        principal=Principal("u", "用户"),
        provider=provider,
        counter=FixtureWordCounter(),
        settings=HostSettings(**settings),
        auto_mode=True,
        require_acceptance=False,
    )
    return host, provider


def _primitive_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in plan["steps"] if step["form"] == "primitive"]


class _Driver:
    """在后台驱动编排（``run_once`` 会等在途的模型调用，所以不能在前台 await），同时读计划。"""

    def __init__(self, host: Any, mission_id: str) -> None:
        self.host, self.mission_id = host, mission_id
        self._runner: asyncio.Task[Any] | None = None

    def _kick(self) -> None:
        if self._runner is None or self._runner.done():
            if self._runner is not None and not self._runner.cancelled():
                self._runner.result()  # 上一轮的错误要冒出来
            self._runner = asyncio.create_task(self.host.run_once())

    async def until(self, predicate, *, seconds: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        plan = None
        while time.monotonic() < deadline:
            self._kick()
            await asyncio.sleep(0.2)
            plan = self.host.plan(self.mission_id)
            if predicate(plan):
                return plan
        raise AssertionError(f"条件没有在 {seconds} 秒内成立；最后的计划：{plan}")

    async def stop(self) -> None:
        if self._runner is not None and not self._runner.done():
            self._runner.cancel()
            with contextlib.suppress(BaseException):
                await self._runner


def test_skip_a_running_step_cancels_it_and_the_planner_sees_the_takeover(tmp_path):
    seen: list[dict[str, Any]] = []

    def planner(request):
        package = package_of(request)
        seen.append(package)
        if package.get("human_takeovers"):
            subject = package["planning_subjects"][0]["subject_key"]
            return decision(
                subject,
                "REQUEST_HUMAN",
                {"question": "这一步被跳过了，剩下的要求怎么办？", "options": [], "blocking": True},
                "用户跳过了唯一的步骤，要求没满足，问用户。",
            )
        return planner_reply(request)

    async def case():
        host, provider = await _open(tmp_path, planner=planner)
        provider.held.add("worker")
        try:
            mission_id = host.create_mission(
                {
                    "goal": "写一份 NOTES.md 列三条要点。",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "skip-1",
                }
            )["mission_id"]
            driver = _Driver(host, mission_id)
            plan = await driver.until(
                lambda p: any(s["status"] == "ACTIVE" for s in _primitive_steps(p))
            )
            assert plan["plan_revision"] >= 1
            [step] = _primitive_steps(plan)
            assert step["occurrence_id"] and step["version"] >= 1
            # 过期的决定：版本对不上就拒
            with pytest.raises(HostRequestError) as stale:
                host.takeover(
                    step["task_id"], "skip", basis="不要了", expected_version=step["version"] + 7
                )
            assert "stale_decision" in str(stale.value)
            first = host.takeover(
                step["task_id"],
                "skip",
                basis="用户说这一步不要了",
                expected_version=step["version"],
                command_id="click-1",
            )
            again = host.takeover(
                step["task_id"], "skip", basis="用户说这一步不要了", command_id="click-1"
            )
            assert again["override_id"] == first["override_id"]
            # 跳过不算成功：步骤 CANCELLED，开着的尝试取消
            snapshot = host.snapshot(mission_id)["snapshot"]
            [task] = [t for t in snapshot["tasks"] if t["id"] == step["task_id"]]
            assert task["status"] == "CANCELLED"
            assert str(task.get("failure_reason", "")).startswith("skipped_by_human")
            assert all(
                a["status"] in {"CANCELLED", "COMPLETED", "FAILED"}
                for a in snapshot["attempts"]
                if a["task_id"] == step["task_id"]
            )
            overrides = [
                e
                for e in host.events(mission_id, limit=200)["events"]
                if e["type"] == "HumanOverride"
            ]
            assert len(overrides) == 1 and overrides[0]["payload"]["action"] == "takeover_skip"
            provider.release.set()
            # 规划器被重新问到，并且看到了这条接管记录
            await driver.until(lambda p: any(pkg.get("human_takeovers") for pkg in seen))
            takeover = next(pkg for pkg in seen if pkg.get("human_takeovers"))["human_takeovers"]
            assert takeover[-1]["action"] == "takeover_skip"
            assert takeover[-1]["task_id"] == step["task_id"]
            await driver.stop()
        finally:
            await host.close()

    _run(case())


def _three_step_method(context: dict[str, Any]) -> dict[str, Any]:
    """三步并列的做法：每条要求一步，没有先后顺序。"""

    method = one_step_method(context)
    criteria = [item["id"] for item in context["request"]["criterion_evidence"]]
    operator = method["steps"][0]
    steps = []
    links = []
    for index, criterion in enumerate(criteria):
        local = f"step{index + 1}"
        steps.append({**operator, "local_id": local})
        links.append(
            {
                "parent_criterion_id": criterion,
                "child_step": local,
                "child_criterion_id": criterion,
                "evidence_requirement": f"{local} 完成 {criterion} 要求的产出",
            }
        )
    method["steps"] = steps
    method["composition"]["criterion_links"] = links
    method["composition"]["finalizer_step"] = steps[-1]["local_id"]
    return method


def test_run_now_puts_a_ready_step_first_and_refuses_an_unready_one(tmp_path):
    dispatched: list[str] = []

    def planner(request):
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            return decision(
                contexts[0]["subject_key"],
                "PROPOSE_METHOD",
                {
                    "method_proposal": {
                        "method": _three_step_method(contexts[0]),
                        "rationale": "三条要求各一步。",
                    }
                },
                "三步并列。",
            )
        return planner_reply(request)

    def worker(request):
        package = package_of(request)
        outputs = list((package.get("task_contract") or {}).get("outputs") or [])
        if outputs and outputs[0] not in dispatched:
            dispatched.append(outputs[0])
        return worker_reply(request)

    async def case():
        host, provider = await _open(tmp_path, planner=planner, worker=worker, max_concurrency=1)
        provider.held.add("worker")
        try:
            mission_id = host.create_mission(
                {
                    "goal": "写出 A.md、B.md、C.md 三份文件。",
                    "success_criteria": ["file:A.md", "file:B.md", "file:C.md"],
                    "idempotency_key": "run-now-1",
                }
            )["mission_id"]
            driver = _Driver(host, mission_id)
            plan = await driver.until(
                lambda p: (
                    len(_primitive_steps(p)) == 3
                    and any(s["status"] == "ACTIVE" for s in _primitive_steps(p))
                )
            )
            ready = [s for s in _primitive_steps(plan) if s["status"] == "READY"]
            assert len(ready) == 2, plan
            chosen = ready[-1]
            host.takeover(chosen["task_id"], "run_now", basis="用户点了现在执行")
            # 已经在跑的那步不能 run_now
            running = next(s for s in _primitive_steps(plan) if s["status"] == "ACTIVE")
            with pytest.raises(HostRequestError):
                host.takeover(running["task_id"], "run_now", basis="再点一次")
            provider.release.set()
            await driver.stop()
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED", final["snapshot"][
                "mission"
            ]
            chosen_task = next(
                t for t in final["snapshot"]["tasks"] if t["id"] == chosen["task_id"]
            )
            chosen_output = chosen_task["outputs"][0] if chosen_task.get("outputs") else None
            # 第一个是开局就跑的那步；被点名的那步紧随其后，排在另一步前面
            assert len(dispatched) == 3, dispatched
            assert chosen_output is None or dispatched[1] == chosen_output, (
                dispatched,
                chosen_output,
            )
        finally:
            await host.close()

    _run(case())


def test_stop_clicked_twice_returns_the_first_record(tmp_path):
    """stop 第一次就结束整个任务；同一命令号第二次仍原样拿回第一次的记录，不报"任务已结束"。"""

    async def case():
        host, provider = await _open(tmp_path, planner=planner_reply)
        provider.held.add("worker")
        try:
            mission_id = host.create_mission(
                {
                    "goal": "写 NOTES.md。",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "stop-1",
                }
            )["mission_id"]
            driver = _Driver(host, mission_id)
            plan = await driver.until(
                lambda p: any(s["status"] == "ACTIVE" for s in _primitive_steps(p))
            )
            [step] = _primitive_steps(plan)
            first = host.takeover(step["task_id"], "stop", basis="用户点了停止", command_id="c-1")
            again = host.takeover(step["task_id"], "stop", basis="用户点了停止", command_id="c-1")
            assert again["override_id"] == first["override_id"]
            assert host.snapshot(mission_id)["snapshot"]["mission"]["status"] != "ACTIVE"
            provider.release.set()
            await driver.stop()
        finally:
            await host.close()

    _run(case())
