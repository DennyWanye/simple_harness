from __future__ import annotations

import pytest

from agent.agent_loop import _inject_loop_user_request, _tool_declares_user_request
from deskpet.tools import research_tools as r
from llm.types import ToolCall


ORIGINAL_REQUEST = "Please deeply research Rust Tokio architecture and competitors"


def test_plan_prompt_includes_authoritative_user_request() -> None:
    prompt = r._PLAN_PROMPT.format(
        topic="CATL drift value",
        user_request=ORIGINAL_REQUEST,
    )

    assert "ORIGINAL USER REQUEST" in prompt
    assert ORIGINAL_REQUEST in prompt
    assert "REFINED TOPIC: CATL drift value" in prompt


@pytest.mark.asyncio
async def test_deepresearch_user_request_none_falls_back_to_topic_without_keyerror(monkeypatch) -> None:
    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: False)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    seen_prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        seen_prompts.append(prompt)
        return '["What is Rust Tokio?"]'

    async def fake_search(query: str, *, max_results: int = 4):
        return []

    report = await r.deepresearch(
        "Rust Tokio",
        llm_call=fake_llm,
        search=fake_search,
        user_request=None,
    )

    assert report.topic == "Rust Tokio"
    assert seen_prompts
    assert "ORIGINAL USER REQUEST" in seen_prompts[0]
    assert "Rust Tokio" in seen_prompts[0]


def test_tool_declares_user_request_detects_schema_property() -> None:
    schemas = [
        {
            "name": "deepresearch",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "user_request": {"type": "string"},
                },
            },
        },
        {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    ]

    assert _tool_declares_user_request("deepresearch", schemas) is True
    assert _tool_declares_user_request("web_search", schemas) is False
    assert _tool_declares_user_request("missing", schemas) is False


def test_dispatch_user_request_injection_unconditionally_overwrites_llm_value() -> None:
    schemas = [
        {
            "name": "deepresearch",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "user_request": {"type": "string"},
                },
            },
        }
    ]
    tc = ToolCall(
        id="call_1",
        name="deepresearch",
        arguments={"topic": "drift value", "user_request": "llm filled wrong"},
    )

    _inject_loop_user_request(
        tc,
        schemas,
        loop_user_request=ORIGINAL_REQUEST,
    )

    assert tc.arguments["user_request"] == ORIGINAL_REQUEST
