# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""2026-09-28 用户决定：只有模型自己做错才扣任务次数。

真机第四、五局：12 次尝试里大半耗在格式、服务出错、执行卡住上，任务因"次数用完"失败。
格式、服务、打断三类失败退还次数（账本与 task.attempt_count 一起退），同一步合计上限 6 次。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "full_target"))

from leaf_world import loop_config, loop_leaf  # noqa: E402

from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    NonModelFailuresExhausted,
    mission_account,
    task_account,
)
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.failure_classes import (  # noqa: E402
    FORMAT,
    INFRA,
    INTERRUPTED,
    INTERRUPTED_REVIEW,
    MODEL,
    NON_MODEL_FAILURE_CAP,
    classify_failure,
)
from agent_orchestrator.testing.fixtures import RoleScriptedProvider  # noqa: E402


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


def _counts(leaf):
    with leaf.store.transaction():
        return (leaf.service.ledger.account(task_account(leaf.task_id)).attempts_created,
                leaf.service.ledger.account(mission_account(leaf.mission.id)).attempts_created,
                leaf.task.attempt_count)


def _in_a_loop(tmp_path, case):
    """真实主循环里一个分层任务的一个步骤。失败后的"再试一次"按生产顺序先拿"原样重试"
    的规划决定——次数是在这条路径上扣的。"""

    async def run():
        async with Orchestrator(loop_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            await case(await loop_leaf(loop, tmp_path, key="mission-charge"))

    asyncio.run(run())


def test_a_service_error_gives_the_attempt_back_and_a_model_mistake_does_not(tmp_path):
    async def case(leaf):
        service = leaf.service
        first = leaf.running()
        assert _counts(leaf) == (1, 1, 1)
        service.reject_result(first.id, turn_id=leaf.turn_of(first), reason="turn_failed", detail={
            "error": {"error_code": "provider_protocol_error", "source_kind": "tool_parse"},
            "error_kind": "provider_error"})
        assert _counts(leaf) == (0, 0, 0)  # 账本与任务次数一起退
        assert leaf.store.count_events(leaf.mission.id, "AttemptChargeReleased") == 1
        # 按尝试幂等：同一尝试再退一次不会多退
        with leaf.store.transaction():
            assert service._release_attempt_charge(leaf.store.get_attempt(first.id)) is False
        assert _counts(leaf) == (0, 0, 0)
        assert leaf.store.count_events(leaf.mission.id, "AttemptChargeReleased") == 1

        await leaf.authorize_retry(first)
        second = leaf.running(retry_of=first.id)
        service.reject_result(second.id, turn_id=leaf.turn_of(second), reason="turn_failed", detail={
            "error": {"error_code": "react_max_turns_exceeded", "source_kind": "termination"},
            "error_kind": "other"})
        assert _counts(leaf) == (1, 1, 1)  # 模型原地打转：扣
        assert second.ordinal == 2  # 次数退了，尝试编号照常递增，不会撞号

        await leaf.authorize_retry(second)
        third = leaf.running(retry_of=second.id)
        service.mark_attempt_timed_out(third.id, reason="executor_stalled", detail={"stalled_seconds": 900})
        assert _counts(leaf) == (1, 1, 1)

    _in_a_loop(tmp_path, case)


def test_a_step_stops_after_six_failures_that_were_not_the_models_fault(tmp_path):
    async def case(leaf):
        previous = None
        for _ in range(NON_MODEL_FAILURE_CAP):
            attempt = leaf.running(retry_of=None if previous is None else previous.id)
            leaf.service.reject_result(attempt.id, turn_id=leaf.turn_of(attempt), reason="envelope_invalid",
                                       detail={"error": "block_missing"})
            await leaf.authorize_retry(attempt)
            previous = attempt
        assert _counts(leaf) == (0, 0, 0)
        with pytest.raises(NonModelFailuresExhausted) as raised:
            leaf.running(retry_of=previous.id)
        assert raised.value.failure_count == NON_MODEL_FAILURE_CAP
        assert len(leaf.store.list_attempts(leaf.task_id)) == NON_MODEL_FAILURE_CAP  # 什么都没写

    _in_a_loop(tmp_path, case)
