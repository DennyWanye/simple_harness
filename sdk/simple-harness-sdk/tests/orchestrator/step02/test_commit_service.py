# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 2 · slice A3/A4: the Commit Service is the single writer — attempts are retried
only on a decision, a step stops when its attempts run out, a rejected result leaves the
formal Task unchanged, cancel works from VERIFYING, and the Mission-wide open-Attempt
bound holds.

HTN 补齐阶段 A′：迁到产品同形部署（:func:`product_world`）。尝试只由真实派发建，失败由外界造
（审阅员判不通过、执行者交不出结果信封），重试由规划器"原样重试"或系统原地批准，取消走门面。

删去（分诊表 step02 行）：
* 建任务幂等与冲突 → Host ``test_layered_scripted_lane.py::test_creating_the_same_mission_twice_is_one_mission``；
  SDK 侧另有 ``p34/test_mission_runtime_profile.py``、``full_target/test_planning_protocol_switch.py`` 的重放用例。
* 预留 / 派发身份 / 结果验收 → ``product_world/test_full_circle.py``（整圈）。
* r1 过期属主不能提交裁决：**无覆盖（偏离）**。Host 的重启两条（调用中重启、强杀续跑）只断言新属主续上，
  没有断言"旧属主仍活着时写不进裁决"；产品同形世界的属主名固定、两个进程同库同时活着的场景本轮没有
  搭法（要改 ``testing/product_world``）。产品检查在 ``CommitService.accept_result/fail_result`` 的
  ``owner`` 参数与 ``event_handler`` 收审阅结论处"verdict dropped"分支。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.contracts import AttemptStatus, MissionStatus, MissionStopReason, TaskStatus
from agent_orchestrator.orchestrator.commit_service import OPEN_ATTEMPT_STATES
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _retrying_planner(request: Any) -> Any:
    return retry_same_method(request) or planner_reply(request)


def _always_rejected(request: Any) -> Any:
    data = review_input(request)
    if data is None:
        return None
    if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
        return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：1 failed")
    return review_reply(data)


def _create(world, key: str, *, max_attempts: int = 12) -> str:
    return world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"], "idempotency_key": key,
                         "budget": {"max_tokens": 8_000_000, "max_attempts": max_attempts}})["mission_id"]


def _leaf_attempts(store, mission_id):
    [task] = [task for task in store.list_tasks(mission_id) if store.list_attempts(task.id)]
    return store.get_task(task.id), store.list_attempts(task.id)


def test_failed_verification_retry_and_stop_paths(tmp_path):
    """审阅判不通过：这次尝试进 RETRY_WAIT；下一次尝试只在规划器的"原样重试"决定提交之后才建，
    带 ``retry_of`` 与审查意见；次数（2）用完，步骤失败，任务以 max_attempts_reached 失败一次。"""

    async def case():
        provider = LayeredScriptedProvider(planner=_retrying_planner, reviewer=_always_rejected)
        async with product_world(tmp_path / "root", provider, max_concurrency=1) as world:
            mission_id = _create(world, "mission-1", max_attempts=2)
            mission = await world.run_until_settled(mission_id, rounds=30)
            store = world.store
            task, attempts = _leaf_attempts(store, mission_id)
            events = list(store.list_events(mission_id))

            assert mission.status is MissionStatus.FAILED
            assert mission.stop_reason == MissionStopReason.MAX_ATTEMPTS_REACHED.value
            assert store.count_events(mission_id, "MissionFailed") == 1
            assert task.status is TaskStatus.FAILED and task.attempt_count == 2  # 模型做错的两次都计数
            first, second = attempts
            assert first.status is AttemptStatus.RETRY_WAIT
            assert first.failure["reason"] == "verification_failed"
            assert second.retry_of == first.id and second.ordinal == 2
            assert second.feedback and "1 failed" in second.feedback[0]
            # 第二次尝试建在规划器"原样重试"的决定提交之后（分层任务没有这个决定就建不出下一次）
            created = [event.seq for event in events if event.type == "AttemptCreated"]
            addressed = [event.seq for event in events if event.type == "PlanningRepairAddressed"]
            assert addressed and addressed[0] < created[1]
            assert provider.asked.count("planner") == 4  # 提做法、采用、两次修复轮

    asyncio.run(case())


class _MalformedThenHeldReview(LayeredScriptedProvider):
    """执行者第一次交不出结果信封；之后的内容审阅被扣住（结果停在 VERIFYING）。"""

    def __init__(self) -> None:
        super().__init__()
        self.worker_calls = 0
        self.review_held = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        role = role_of(request)
        if role == "worker":
            self.worker_calls += 1
            if self.worker_calls == 1:
                self.asked.append(role)
                self.scripts["worker"] = ["做完了。"]  # no <result_envelope> block
                return await RoleScriptedProvider.invoke(self, request, cancel=cancel)
        data = review_input(request) if role == "unknown" else None
        if data is not None and str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            self.review_held.set()
            await asyncio.Event().wait()  # a review that never answers while the case runs
        return await super().invoke(request, cancel=cancel)


def test_reject_result_and_cancel_from_verifying(tmp_path):
    """交不出结果信封：结果被拒（envelope_invalid），尝试进 RETRY_WAIT，正式步骤不动；
    第二次尝试的结果在核验中（VERIFYING）时取消任务：任务、步骤、尝试都 CANCELLED（D15'）。"""

    async def case():
        provider = _MalformedThenHeldReview()
        async with product_world(tmp_path / "root", provider, max_concurrency=1) as world:
            store = world.store
            mission_id = _create(world, "mission-cancel")

            async def drive() -> None:
                while True:
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.02)

            runner = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(provider.review_held.wait(), 30)
                task, attempts = _leaf_attempts(store, mission_id)
                first, second = attempts
                assert first.status is AttemptStatus.RETRY_WAIT
                assert first.failure["reason"] == "envelope_invalid"
                assert store.count_events(mission_id, "ResultRejected") == 1
                assert second.retry_of == first.id
                assert second.status is AttemptStatus.VERIFYING
                assert task.status is TaskStatus.VERIFYING
                world.control.cancel(mission_id)
            finally:
                runner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await runner
            assert store.get_mission(mission_id).status is MissionStatus.CANCELLED
            task, attempts = _leaf_attempts(store, mission_id)
            assert task.status is TaskStatus.CANCELLED
            assert attempts[-1].status is AttemptStatus.CANCELLED

    asyncio.run(case())


def _parallel_planner(request: Any) -> Any:
    """根目标提一个两步并行的做法（两份文件各一步）；其余照普通脚本。"""

    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if not contexts or (package.get("method_selection") or [{}])[0].get("applicable"):
        return planner_reply(request)
    request_body = contexts[0]["request"]
    operator = next(item for item in request_body["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request_body["criterion_evidence"]]
    identity = request_body["new_method_identity"]

    def step(local_id: str) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": operator["task_type_ref"], "form": "primitive",
                "arguments": {}, "required_capabilities": list(operator["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    method = {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request_body["goal_type_ref"],
        "parameter_schema_ref": request_body["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request_body["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [step("a"), step("b")], "ordering": [], "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "a", "child_criterion_id": first,
                 "evidence_requirement": "a 写出第一份文件"},
                {"parent_criterion_id": second, "child_step": "b", "child_criterion_id": second,
                 "evidence_requirement": "b 写出第二份文件"},
            ],
            "outputs": {}, "finalizer_step": "b", "independent_review_required": True,
        },
        "basis_refs": [],
    }
    return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                    {"method_proposal": {"method": method, "rationale": "两份文件分开写。"}}, "两步并行。")


def test_r1_mission_wide_concurrency_is_enforced(tmp_path):
    """P1-11 / D3-4：任务级同时在开的尝试数不超过 ``max_concurrency``。两个互不依赖的步骤、上限 1：
    任何时刻至多一个尝试在开，两步依次做完。

    偏离：原用例直接调 ``create_attempt(max_open_attempts=…)`` 钉提交层里的那道检查；产品上派发前
    调度已按同一上限挑步骤，这里钉的是结果（上限在主循环里成立），不再单测提交层那一行。"""

    async def case():
        peaks: list[int] = []

        class Watching(LayeredScriptedProvider):
            async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
                if role_of(request) == "worker":
                    peaks.append(sum(1 for task in world.store.list_tasks(mission_id)
                                     for attempt in world.store.list_attempts(task.id)
                                     if attempt.status in OPEN_ATTEMPT_STATES))
                    await asyncio.sleep(0.05)  # a slow model: the other step had time to be dispatched
                return await super().invoke(request, cancel=cancel)

        provider = Watching(planner=_parallel_planner)
        async with product_world(tmp_path / "root", provider, max_concurrency=1) as world:
            mission_id = world.create({"goal": "写两份文件", "success_criteria": ["file:a.md", "file:b.md"],
                                       "idempotency_key": "r1-conc"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert mission.status is MissionStatus.COMPLETED, mission.stop_reason
            leaves = [task for task in world.store.list_tasks(mission_id) if world.store.list_attempts(task.id)]
            assert len(leaves) == 2
            assert peaks and max(peaks) == 1

    asyncio.run(case())
