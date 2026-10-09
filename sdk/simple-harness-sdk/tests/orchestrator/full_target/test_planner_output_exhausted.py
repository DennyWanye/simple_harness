# SPDX-License-Identifier: Apache-2.0
"""2026-10-09 编程测评（schedule 局）：规划器一轮输出 32768 个 token 全是思考、没有正文，
按"回合失败"进服务故障宽限、不扣次数、规划器不知道。现在这样的回合按一条空回复评估：
读不懂（没有决定块）、扣答错次数、事实写进决定行给下一问看。

**Mutation**: route every uncommitted turn to ``PlannerTurnFailed`` again → red."""
import asyncio
from types import SimpleNamespace

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from simple_harness.agents import AgentTurnState

_EXHAUSTED = {"error_code": "provider_empty_response", "source_kind": "tool_parse",
              "detail": {"finish_reason": "length", "usage": {"output_tokens": 32768, "reasoning_tokens": 32768}}}


def _reply(error):
    calls = []
    fake = SimpleNamespace(
        _settle_intent=lambda intent, state: calls.append(("settle", state)),
        _settle_service_if_known=lambda subject, mission_id: None,
    )

    async def collect(intent, result, mission, text, new_mode):
        calls.append(("collect", text))

    async def rejected(intent, *, reason, detail):
        calls.append(("rejected", reason, detail.get("turn_failed")))

    fake._collect_plan_decision = collect
    fake._planning_rejected = rejected
    intent = SimpleNamespace(intent_id="i1", subject_id="s", mission_id="m1", config={})
    result = SimpleNamespace(state=AgentTurnState.FAILED, error=error)
    asyncio.run(Orchestrator._collect_plan_hierarchical(fake, intent, result, SimpleNamespace(id="m1"), "", None))
    return calls


def test_an_output_exhausted_planner_turn_is_evaluated_as_an_empty_reply():
    assert _reply(_EXHAUSTED) == [("collect", "")]


def test_other_uncommitted_turns_still_take_the_no_reply_path():
    assert _reply({"error_code": "provider_server_error"}) == [("settle", "FAILED"), ("rejected", "proposal_unreadable", True)]
