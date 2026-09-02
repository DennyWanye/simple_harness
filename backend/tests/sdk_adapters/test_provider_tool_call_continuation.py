# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5A-UI-F2: OpenAI 兼容线格式要求 tool 结果前的 assistant 携带 tool_calls。

2026-09-02 真实桌面 UI 实测（真实 provider + 真实工具调用 + HTTP 传输）抓到：
SDK 0.7.1 的冻结契约禁止 provider assistant 消息把私有 metadata 写进 durable
Context（``provider_invocations.py``: "stored public provider message metadata
must be empty"），所以 Host 靠 ``metadata[provider_tool_calls]`` 跨轮携带
tool_calls 的机制在真实 continuation 上必然失效——第二轮请求变成"tool 消息前
的 assistant 没有 tool_calls"，被 provider 以 400 拒绝，整个 Run 失败。

线格式实测（本机 provider）：
  assistant(tool_calls) + tool(tool_call_id)  -> 200
  assistant(无 tool_calls) + tool             -> 400 invalid_request_error
  assistant(tool_calls) + tool(无 tool_call_id)-> 400 invalid_request_error

因此 Host 的请求组装必须从消息序列自身补齐合法 tool_calls。
"""

from __future__ import annotations

from simple_harness.contracts import CallId
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider


def _wire_messages(messages):
    """线上真正发出的 messages 数组。"""

    return [_ProductOpenAICompatibleProvider._message_payload(m) for m in messages]


def test_durable_assistant_without_metadata_still_carries_tool_calls() -> None:
    """durable 往返后 metadata 为空的 assistant，线上仍须带 tool_calls。"""

    durable = (
        Message(role=MessageRole.USER, content="继续青海湖观测报告那个任务"),
        # durable Context 里的 provider assistant：metadata 必为空（SDK 契约）
        Message(role=MessageRole.ASSISTANT, content=""),
        Message(
            role=MessageRole.TOOL,
            content='{"candidates":[]}',
            name="task_scope_search",
            call_id=CallId("call_abc"),
        ),
    )

    wire = _ProductOpenAICompatibleProvider._wire_messages(durable)

    assistant = wire[1]
    tool = wire[2]
    calls = assistant.get("tool_calls")
    assert calls, "tool 结果前的 assistant 必须带 tool_calls，否则 provider 400"
    assert calls[0]["id"] == "call_abc"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "task_scope_search"
    # arguments 必须是合法 JSON 字符串（缺失入参时退化为空对象，形状仍合法）
    assert isinstance(calls[0]["function"]["arguments"], str)
    assert tool["tool_call_id"] == "call_abc"


def test_assistant_keeps_live_metadata_tool_calls_with_arguments() -> None:
    """同进程内（metadata 仍在）必须保留模型原始入参，不被兜底覆盖。"""

    live = (
        Message(role=MessageRole.USER, content="继续"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            metadata={
                "provider_tool_calls": [
                    {
                        "id": "call_abc",
                        "name": "task_scope_search",
                        "arguments": {"query": "青海湖"},
                    }
                ]
            },
        ),
        Message(
            role=MessageRole.TOOL,
            content='{"candidates":[]}',
            name="task_scope_search",
            call_id=CallId("call_abc"),
        ),
    )

    wire = _ProductOpenAICompatibleProvider._wire_messages(live)
    calls = wire[1]["tool_calls"]
    # 既有序列化用 ensure_ascii（非 ASCII 转义），断言按实际线格式。
    import json

    assert json.loads(calls[0]["function"]["arguments"]) == {"query": "青海湖"}


def test_assistant_without_following_tool_result_stays_plain() -> None:
    """普通终答 assistant 不得被凭空加上 tool_calls。"""

    plain = (
        Message(role=MessageRole.USER, content="你好"),
        Message(role=MessageRole.ASSISTANT, content="你好，有什么可以帮你的？"),
    )
    wire = _ProductOpenAICompatibleProvider._wire_messages(plain)
    assert "tool_calls" not in wire[1]


def test_multiple_parallel_tool_results_map_to_one_assistant() -> None:
    """一轮多工具调用：所有 call_id 必须归到同一条 assistant 的 tool_calls。"""

    parallel = (
        Message(role=MessageRole.USER, content="查两件事"),
        Message(role=MessageRole.ASSISTANT, content=""),
        Message(
            role=MessageRole.TOOL,
            content="{}",
            name="task_scope_search",
            call_id=CallId("call_1"),
        ),
        Message(
            role=MessageRole.TOOL,
            content="{}",
            name="context_route",
            call_id=CallId("call_2"),
        ),
    )
    wire = _ProductOpenAICompatibleProvider._wire_messages(parallel)
    ids = [c["id"] for c in wire[1]["tool_calls"]]
    assert ids == ["call_1", "call_2"]
