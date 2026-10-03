# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""2026-09-28 用户决定：只有模型自己做错才扣任务次数。

真机第四、五局：12 次尝试里大半耗在格式、服务出错、执行卡住上，任务因"次数用完"失败。
格式、服务、打断三类失败退还次数（账本与 task.attempt_count 一起退），同一步合计上限 6 次。

HTN 补齐阶段 A′：两条主循环用例迁到产品同形部署（:func:`product_world`）。失败由外界造：提供方
报服务错误、执行者交不出结果信封（格式）、审阅员判不通过（模型做错）；尝试只由真实派发建，
重做由系统原地批准或由规划器决定"原样重试"，不再手工建尝试、手记拒绝。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.contracts import MissionStatus, MissionStopReason, TaskStatus
from agent_orchestrator.orchestrator.commit_service import mission_account, task_account
from agent_orchestrator.orchestrator.failure_classes import (
    FORMAT,
    INFRA,
    INTERRUPTED,
    INTERRUPTED_REVIEW,
    MODEL,
    NON_MODEL_FAILURE_CAP,
    classify_failure,
)
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)
from simple_harness.providers import ProviderServerError


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_the_table_says_whose_fault_each_real_failure_is():
    turn = lambda **error: {"reason": "turn_failed", **error}  # noqa: E731
    assert classify_failure(turn(error={"error_code": "provider_protocol_error",
                                        "source_kind": "tool_parse"}, error_kind="provider_error")) == INFRA
    assert classify_failure(turn(error={"error_code": "x"}, error_kind="provider_unavailable")) == INFRA
    assert classify_failure(turn(error={"error_code": "react_max_turns_exceeded",
                                        "source_kind": "termination"}, error_kind="other")) == MODEL
    assert classify_failure(turn(error={"error_code": "react_max_tool_calls_exceeded"})) == MODEL
    # 2026-09-29 真机第八局：停机/重启期间单轮时限走完，重启后报墙钟超时——被打断，不是模型做错
    assert classify_failure(turn(error={"error_code": "react_wall_clock_exceeded",
                                        "source_kind": "termination"}, error_kind="other")) == INTERRUPTED
    # 第九局：重启后调用已交出、结果未知，执行层以内部异常结束这一轮
    assert classify_failure(turn(error={"error_code": "base_agent_driver_exception",
                                        "source_kind": "runtime"}, error_kind="other")) == INTERRUPTED
    # 调了不存在的工具、参数不合规定：同样标着 tool_parse，但属于模型的错
    assert classify_failure(turn(error={"error_code": "tool_not_exposed", "source_kind": "tool_parse"})) == MODEL
    assert classify_failure(turn(error={"error_code": "invalid_tool_arguments",
                                        "source_kind": "tool_parse"}, error_kind="other")) == MODEL
    assert classify_failure({"reason": "envelope_invalid", "error": "block_missing"}) == FORMAT
    assert classify_failure({"reason": "executor_stalled", "stalled_seconds": 600}) == INTERRUPTED
    assert classify_failure({"reason": "provider_outcome_unknown"}) == INTERRUPTED
    assert classify_failure({"reason": "runtime_unavailable"}) == INFRA
    interrupted = {"layer": "critic_review", "status": "ERROR",
                   "summary": "critic verdict unusable: " + INTERRUPTED_REVIEW}
    assert classify_failure({"reason": "verification_failed", "failures": [interrupted]}) == INTERRUPTED
    assert classify_failure({"reason": "verification_failed", "failures": [
        interrupted, {"layer": "code_test", "status": "FAIL", "summary": "1 failed"}]}) == MODEL
    assert classify_failure({"reason": "verification_failed", "failures": [
        {"layer": "rule_check", "status": "FAIL", "summary": "x"}]}) == MODEL
    # 2026-10-02 真机：重启后接着跑的那一轮在准入处被拒，因为执行权还记在旧进程名下——被打断
    denied = lambda code: {"reason": "provider_admission_denied", "error": {  # noqa: E731
        "error_code": "provider_admission_denied", "source_kind": "provider_admission",
        "detail": {"schema_version": 1, "reason_code": code}}, "error_kind": "admission_denied"}
    assert classify_failure(denied("lease_lost")) == INTERRUPTED
    assert classify_failure(denied("authority_rejected")) == MODEL  # 别的准入拒绝不变
    # 不在表里的一律按模型做错：宁可多扣，不能漏扣
    assert classify_failure({"reason": "model_echo_mismatch"}) == MODEL
    assert classify_failure({"reason": "something_new"}) == MODEL
    assert classify_failure(None) == MODEL


class _Faults(LayeredScriptedProvider):
    """执行者第 ``n`` 次调用的外界故障：``"infra"`` 提供方报服务错误、``"format"`` 交不出结果信封；
    ``reject`` 里的第几次内容审阅判不通过（模型做错）。规划器在系统来问时答"原样重试"。"""

    def __init__(self, faults: dict[int, str], *, reject: set[int] = frozenset()) -> None:
        self.faults, self.worker_calls, self.reviews = faults, 0, 0

        def reviewer(request: Any) -> Any:
            data = review_input(request)
            if data is None:
                return None
            if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
                self.reviews += 1
                if self.reviews in reject:
                    return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：不满足要求。")
            return review_reply(data)

        super().__init__(planner=lambda request: retry_same_method(request) or planner_reply(request),
                         reviewer=reviewer)

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker":
            self.worker_calls += 1
            fault = self.faults.get(self.worker_calls)
            if fault == "infra":
                self.asked.append("worker")
                raise ProviderServerError()
            if fault == "format":
                self.asked.append("worker")
                self.scripts["worker"] = ["做完了。"]  # no <result_envelope> block
                return await RoleScriptedProvider.invoke(self, request, cancel=cancel)
        return await super().invoke(request, cancel=cancel)


def _run(tmp_path, provider: _Faults, *, max_attempts: int = 12):
    async def case():
        async with product_world(tmp_path / "root", provider, max_concurrency=1) as world:
            created = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                    "idempotency_key": "mission-charge",
                                    "budget": {"max_tokens": 8_000_000, "max_attempts": max_attempts}})
            mission = await world.run_until_settled(created["mission_id"], rounds=40)
            store = world.store
            [task] = [task for task in store.list_tasks(mission.id) if store.list_attempts(task.id)]
            with store.transaction():
                counts = (world.loop.commit.ledger.account(task_account(task.id)).attempts_created,
                          world.loop.commit.ledger.account(mission_account(mission.id)).attempts_created,
                          store.get_task(task.id).attempt_count)
            events = list(store.list_events(mission.id))
            return mission, store.get_task(task.id), store.list_attempts(task.id), counts, events

    return asyncio.run(case())


def test_a_service_error_gives_the_attempt_back_and_a_model_mistake_does_not(tmp_path):
    # 第 1 次：提供方服务错误（INFRA）；第 2 次：交了结果但审阅判不通过（MODEL）；第 3 次：通过。
    provider = _Faults({1: "infra"}, reject={1})
    mission, task, attempts, counts, events = _run(tmp_path, provider)
    assert mission.status is MissionStatus.COMPLETED, mission.stop_reason
    assert [attempt.ordinal for attempt in attempts] == [1, 2, 3]  # 次数退了，编号照常递增、不撞号
    assert [(attempt.failure or {}).get("reason") for attempt in attempts] == [
        "turn_failed", "verification_failed", None]
    released = [event.payload for event in events if event.type == "AttemptChargeReleased"]
    assert [(item["failure_class"], item["reason"]) for item in released] == [("INFRA", "turn_failed")]
    # 账本与任务次数一起退：只有模型做错那次和成功那次算次数
    assert counts == (2, 2, 2) and task.attempt_count == 2
    # 服务出错由系统原地批准重做，不问规划器；模型做错那次才问规划器（"原样重试"）
    assert provider.asked.count("planner") == 3  # 提做法、采用、修复一次


def test_a_step_stops_after_six_failures_that_were_not_the_models_fault(tmp_path):
    provider = _Faults({n: "format" for n in range(1, 20)})
    mission, task, attempts, counts, events = _run(tmp_path, provider)
    assert mission.status is MissionStatus.FAILED
    assert mission.stop_reason == MissionStopReason.RUNTIME_UNAVAILABLE.value
    assert task.status is TaskStatus.FAILED
    assert len(attempts) == NON_MODEL_FAILURE_CAP  # 第七次没有建出来
    assert counts == (0, 0, 0)  # 六次都退了次数
    assert sum(1 for event in events if event.type == "AttemptChargeReleased") == NON_MODEL_FAILURE_CAP
    [failed] = [event.payload for event in events if event.type == "TaskFailed"]
    assert "non_model_failures_exhausted" in str(failed)
    assert provider.asked.count("planner") == 2  # 重做全由系统批准，没有问规划器
