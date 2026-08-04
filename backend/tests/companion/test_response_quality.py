from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.companion.response_quality import (
    ModelResponseCompletenessGate,
)
from llm.types import ChatResponse, ChatUsage


class _Tools:
    def schemas(self, enabled_toolsets=None):
        return []


class _LLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_with_fallback(
        self,
        messages,
        tools=None,
        model=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "kwargs": kwargs,
            }
        )
        return self.responses.pop(0)


def _response(content: str, *, input_tokens: int = 2, output_tokens: int = 1):
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def test_response_quality_gate_repairs_semantic_omission() -> None:
    gate = ModelResponseCompletenessGate()

    decision = gate.resolve(
        original_response="周三发布，小赵。",
        review_response=json.dumps(
            {
                "complete": False,
                "missing_items": ["小孙今晚完成验收清单"],
                "revised_response": (
                    "周三发布；负责人小赵；"
                    "小孙今晚完成验收清单。"
                ),
            },
            ensure_ascii=False,
        ),
    )

    assert decision.checked is True
    assert decision.revised is True
    assert decision.missing_items == ("小孙今晚完成验收清单",)
    assert decision.text.endswith("小孙今晚完成验收清单。")


def test_response_quality_gate_preserves_original_when_complete() -> None:
    gate = ModelResponseCompletenessGate()

    decision = gate.resolve(
        original_response="周三发布；负责人小赵；小孙今晚完成验收清单。",
        review_response=(
            "```json\n"
            '{"complete":true,"missing_items":[],"revised_response":'
            '"不应采用这个改写"}\n'
            "```"
        ),
    )

    assert decision.checked is True
    assert decision.revised is False
    assert decision.text == "周三发布；负责人小赵；小孙今晚完成验收清单。"


def test_response_quality_gate_fails_open_on_invalid_model_output() -> None:
    gate = ModelResponseCompletenessGate()

    decision = gate.resolve(
        original_response="保留原回答",
        review_response="not-json",
    )

    assert decision.checked is False
    assert decision.revised is False
    assert decision.text == "保留原回答"


def test_turn_payload_freezes_quality_gate_only_for_brief_preference() -> None:
    resolution = SimpleNamespace(
        snapshot_hash="snapshot-brief",
        items=(
            SimpleNamespace(
                preference_key="response.detail",
                value="brief",
            ),
        ),
    )
    routed = SimpleNamespace(
        pre_loop=None,
        problem_type=None,
        prepared=SimpleNamespace(preference_resolution=resolution),
    )
    config = SimpleNamespace(features=None, raw={})

    payload = ProductTurnPreparer.prepare_workflow_request_payload(
        routed,
        TurnInput(
            text="请概括项目记录",
            session_id="session-1",
            request_id="request-1",
            turn_id="turn-1",
            venue="text",
            context_usage_basis_sample_id="sample-before",
        ),
        config,
    )

    assert payload["loop"]["response_quality"] == {
        "schema_version": 1,
        "mode": "semantic_completeness",
        "preference_key": "response.detail",
        "preference_value": "brief",
        "snapshot_hash": "snapshot-brief",
    }
    assert payload["context_usage_basis_sample_id"] == "sample-before"


@pytest.mark.asyncio
async def test_agent_loop_uses_model_review_and_accounts_for_usage() -> None:
    llm = _LLM(
        [
            _response("周三发布，小赵。", input_tokens=10, output_tokens=2),
            _response(
                json.dumps(
                    {
                        "complete": False,
                        "missing_items": ["小孙今晚完成验收清单"],
                        "revised_response": (
                            "周三发布；负责人小赵；"
                            "小孙今晚完成验收清单。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                input_tokens=6,
                output_tokens=4,
            ),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_Tools(),
        response_quality_gate=ModelResponseCompletenessGate(),
    )

    events = [
        event
        async for event in loop.run(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "请概括：版本周三发布，负责人小赵，"
                        "行动项小孙今晚完成验收清单。"
                    ),
                }
            ],
            loop_user_request=(
                "请概括：版本周三发布，负责人小赵，"
                "行动项小孙今晚完成验收清单。"
            ),
        )
    ]

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == "周三发布；负责人小赵；小孙今晚完成验收清单。"
    assert final.total_input_tokens == 16
    assert final.total_output_tokens == 6
    assert len(llm.calls) == 2
    assert llm.calls[1]["tools"] is None
    assert (
        llm.calls[1]["kwargs"]["response_format"]["json_schema"]["name"]
        == "companion_response_completeness_v1"
    )


@pytest.mark.asyncio
async def test_agent_loop_without_quality_gate_keeps_single_provider_call() -> None:
    llm = _LLM([_response("简短回答")])
    loop = AgentLoop(llm_registry=llm, tool_registry=_Tools())

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "你好"}],
            loop_user_request="你好",
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == "简短回答"
    assert len(llm.calls) == 1
