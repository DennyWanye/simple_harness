"""2026-10-10 parse 重跑：执行者做满单轮调用次数上限被终止，规划器收到的修复请求只有错误码。
两条上限（输出上限、单轮调用次数）的事实都如实写进请求；拆不拆步由规划器判断。"""

from agent_orchestrator.orchestrator.planning_repair_requests import attempt_failure_facts


def test_the_two_executor_caps_are_told_to_the_planner_as_facts():
    capped = {"reason": "turn_failed", "error_kind": "other",
              "error": {"error_code": "react_max_turns_exceeded", "source_kind": "termination"}}
    assert attempt_failure_facts(capped) == {
        "failure_class": "MODEL",
        "failure_fact": "上一次尝试达到单轮模型调用次数上限被终止，没有交出结果。",
    }
    tools = {"reason": "turn_failed", "error": {"error_code": "react_max_tool_calls_exceeded", "source_kind": "termination"}}
    assert attempt_failure_facts(tools)["failure_fact"] == "上一次尝试达到单轮工具调用次数上限被终止，没有交出结果。"
    exhausted = {"reason": "turn_failed", "error": {
        "error_code": "provider_empty_response", "source_kind": "tool_parse",
        "detail": {"finish_reason": "length", "usage": {"output_tokens": 32768, "reasoning_tokens": 32768}}}}
    assert attempt_failure_facts(exhausted)["failure_fact"].startswith("上一轮有一次模型调用因输出上限")
    # other failures: only whose fault
    assert attempt_failure_facts({"reason": "turn_failed", "error_kind": "provider_error",
                                  "error": {"error_code": "provider_server_error"}}) == {"failure_class": "INFRA"}
    assert attempt_failure_facts(None) == {}
