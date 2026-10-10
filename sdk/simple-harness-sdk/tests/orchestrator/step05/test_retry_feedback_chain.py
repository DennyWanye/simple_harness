# SPDX-License-Identifier: Apache-2.0
"""2026-09-26 真机文档任务：中间夹一次模型接口失败时，前面那次内容拒收的原因不能丢。"""
from types import SimpleNamespace

from agent_orchestrator.orchestrator.event_handler import retry_feedback

REJECTED = {"reason": "result_evidence_kind_not_allowed", "error": "evidence of kind 'pytest' is not allowed"}
# 产品里协议错误的 error_kind 由 classify_turn_error 定为 provider_error（服务侧），夹具照产品写
TURN = {"reason": "turn_failed", "error_kind": "provider_error", "error": {"error_code": "provider_protocol_error"}}


def _attempt(n, failure, status="RETRY_WAIT"):
    return SimpleNamespace(id=f"a{n}", status=status, failure=failure)


def test_a_turn_failure_does_not_hide_the_earlier_content_rejection():
    attempts = [_attempt(1, REJECTED), _attempt(2, TURN)]
    feedback, verifier = retry_feedback(attempts, attempts[1])
    assert len(feedback) == 2 and feedback[0].startswith("上一轮有一次模型调用没有拿到回复")  # 2026-10-10 只写事实
    assert "pytest" in feedback[1] and verifier == []


def test_a_content_failure_stops_the_walk_back():
    older = {"reason": "verification_failed", "failures": [{"layer": "rule_check", "summary": "old"}]}
    attempts = [_attempt(1, older), _attempt(2, REJECTED)]
    feedback, verifier = retry_feedback(attempts, attempts[1])
    assert feedback == [f"result_evidence_kind_not_allowed: {REJECTED['error']}"] and verifier == []


def test_verifier_details_still_travel_and_no_previous_means_no_feedback():
    failed = {"reason": "inconclusive", "failures": [{"layer": "rule_check", "summary": "s", "detail": {}}]}
    attempts = [_attempt(1, failed), _attempt(2, TURN), _attempt(3, TURN)]
    feedback, verifier = retry_feedback(attempts, attempts[2])
    assert feedback[-1] == "rule_check: s" and len(verifier) == 1 and len(feedback) == 3
    assert retry_feedback(attempts, None) == ([], [])


def test_a_provider_failure_turn_is_told_as_one_fact_not_a_raw_dict():
    """2026-10-10（第三轮复查）：5xx 之后给下一轮的反馈原来是一段原始字典。只写一句事实。

    **Mutation**: drop the plain ``turn_failed`` branch in ``retry_feedback`` → red."""
    failed = _attempt(1, {"reason": "turn_failed", "error_kind": "provider_unavailable",
                          "error": {"error_code": "provider_server_error", "source_kind": "tool_parse"}})
    feedback, _ = retry_feedback([failed], failed)
    assert feedback == ["上一轮有一次模型调用没有拿到回复（服务侧错误，代码 provider_server_error）；这一轮从头做。"]
    assert "{" not in feedback[0]


def test_a_turn_the_model_itself_failed_is_not_called_a_server_error():
    """独立核验 M2（2026-10-10）：用完回合上限是模型自己的事，不能说成"服务侧错误"。

    **Mutation**: call every ``turn_failed`` a server error → red."""
    failed = _attempt(1, {"reason": "turn_failed", "error_kind": "react",
                          "error": {"error_code": "react_max_turns_exceeded", "message": "12 turns used"}})
    feedback, _ = retry_feedback([failed], failed)
    assert feedback == ["上一轮有一次模型调用失败（代码 react_max_turns_exceeded：12 turns used）；这一轮从头做。"]
    assert "服务侧" not in feedback[0]


def test_a_turn_past_its_time_limit_is_told_as_that_fact():
    """2026-10-10 parse 重跑：单轮时限到了被终止，下一轮收到的是"模型调用失败（代码 react_wall_clock_exceeded）"。

    **Mutation**: drop the wall-clock branch → red."""
    timed = _attempt(1, {"reason": "turn_failed", "error_kind": "other",
                         "error": {"error_code": "react_wall_clock_exceeded", "source_kind": "termination"}})
    feedback, _ = retry_feedback([timed], timed)
    assert feedback == ["上一次尝试超过了单轮时限，被系统终止；这一轮从头做。"]


def test_a_stalled_attempt_is_told_as_one_fact_not_a_raw_dict():
    """2026-10-10（parse/docopt 重跑）：被当成卡死终止的尝试，下一轮收到的是 ``executor_stalled: ``。

    **Mutation**: drop the ``executor_stalled`` branch in ``retry_feedback`` → red."""
    stalled = _attempt(1, {"reason": "executor_stalled", "stalled_seconds": 180.899, "progress_marker": 13})
    feedback, _ = retry_feedback([stalled], stalled)
    assert feedback == ["上一次尝试 181 秒没有进展，被系统终止；这一轮从头做。"]


def test_an_output_exhausted_turn_is_told_as_one_fact_not_a_raw_dict():
    """2026-10-09：重做那一轮原来收到一段原始字典；现在收到一句事实，怎么办由模型判断。

    **Mutation**: drop the ``output_exhausted`` branch in ``retry_feedback`` → red."""
    exhausted = {"reason": "turn_failed", "error": {
        "error_code": "provider_empty_response", "source_kind": "tool_parse",
        "detail": {"finish_reason": "length", "usage": {"output_tokens": 32768, "reasoning_tokens": 32768}}}}
    attempts = [_attempt(1, exhausted)]
    feedback, verifier = retry_feedback(attempts, attempts[0])
    assert feedback == ["上一轮有一次模型调用因输出上限（32768 个 token，其中思考 32768 个）停止，没有给出最终结果。"]
    assert verifier == []
    # 别的回合失败照旧
    assert retry_feedback([_attempt(1, TURN)], _attempt(1, TURN))[0][0].startswith("上一轮有一次模型调用没有拿到回复")
