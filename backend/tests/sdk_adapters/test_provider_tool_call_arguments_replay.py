# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 事件 K：continuation 跨轮重建 assistant.tool_calls 必须带回真实入参。

2026-09-08 真实原生运行（DeepSeek ``deepseek-v4-pro``）实测：durable
``provider_invocations`` 里 ``metadata[provider_tool_calls]`` **一条都不存在**，
所以线上出现的每一条 assistant ``tool_calls`` 都是
``_wire_messages`` 用字面量 ``"{}"`` 重建的。模型随后照抄自己被污染的
transcript——三份证据库合并后，重建数为 0 的请求 0/16 条空参调用，重建数 ≥20 的
请求 22/35 条空参调用（严格单调剂量反应），每条都被
``missing_required_argument`` 拒绝，直到 ``react_max_turns_exceeded``。

本文件锁定修复：Host 自己的 ``ToolCallArgumentsMemo`` 在解析 provider 响应时留存
模型入参，``_wire_messages`` 在后续每一跳把它原样贴回；memo 未命中才退化成
``"{}"``，并且必须记一条无载荷计数日志。
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from simple_harness.contracts import CallId, RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.provider_invocations import (
    provider_response_from_json,
    provider_response_json,
)
from simple_harness.providers import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from simple_harness.providers.base import (
    ProviderContinuationCapability,
    ProviderContinuationMode,
    Secret,
)

from deskpet.sdk_adapters.provider import (
    ProductProviderAdapter,
    _ProductOpenAICompatibleProvider,
    _retain_tool_calls_in_message,
)
from deskpet.sdk_adapters.tool_call_arguments import ToolCallArgumentsMemo

from tests.sdk_adapters.test_product_host_ports import Registry

SEARCH_ARGUMENTS = {"query": "青海湖 观测报告", "limit": 5}
WRITE_ARGUMENTS = {"path": "notes/湖泊.md", "content": "第一行\n第二行"}


def _delegate(memo: ToolCallArgumentsMemo) -> _ProductOpenAICompatibleProvider:
    return _ProductOpenAICompatibleProvider(
        httpx.AsyncClient(),
        "http://127.0.0.1:9/v1",
        "test-model",
        Secret("unused-test-key"),
        tool_call_arguments_memo=memo,
    )


def _issued_response(*calls: tuple[str, str, dict]) -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("provider-turn:1"),
        message=Message(MessageRole.ASSISTANT, ""),
        tool_calls=tuple(
            ProviderToolCall(CallId(call_id), name, arguments)
            for call_id, name, arguments in calls
        ),
        model="test-model",
        finish_reason="tool_calls",
    )


def _durable_assistant(response: ProviderResponse) -> Message:
    """真实走一遍 SDK durable 序列化，metadata 由契约强制清空。"""

    capability = ProviderContinuationCapability(ProviderContinuationMode.REASONING_DISABLED)
    stored = provider_response_json(response, capability=capability)
    restored = provider_response_from_json(stored, expected_capability=capability)
    assert restored.message.metadata == {}, "SDK 契约必须清空 provider assistant metadata"
    return restored.message


def test_durable_round_trip_loses_metadata_and_memo_restores_the_arguments() -> None:
    """事件 K 主控：durable 往返后 metadata 为空，memo 必须补回真实入参。"""

    memo = ToolCallArgumentsMemo()
    issued = _issued_response(("call_abc", "task_scope_search", SEARCH_ARGUMENTS))
    # 生产路径：解析响应时留存（provider.py 的 _attempt 里就是这一句）。
    retained = _retain_tool_calls_in_message(issued, arguments_memo=memo)
    assistant = _durable_assistant(retained)
    assert "provider_tool_calls" not in assistant.metadata

    wire = _ProductOpenAICompatibleProvider._wire_messages(
        (
            Message(MessageRole.USER, "继续青海湖那个任务"),
            assistant,
            Message(
                MessageRole.TOOL,
                '{"candidates":[]}',
                name="task_scope_search",
                call_id=CallId("call_abc"),
            ),
        ),
        arguments_memo=memo,
    )
    call = wire[1]["tool_calls"][0]
    assert call["id"] == "call_abc"
    assert json.loads(call["function"]["arguments"]) == SEARCH_ARGUMENTS


def test_parallel_calls_each_keep_their_own_arguments() -> None:
    """一轮多调用：入参必须按 call_id 各归各位，不得串台。"""

    memo = ToolCallArgumentsMemo()
    issued = _issued_response(
        ("call_1", "task_scope_search", SEARCH_ARGUMENTS),
        ("call_2", "write_file", WRITE_ARGUMENTS),
    )
    _retain_tool_calls_in_message(issued, arguments_memo=memo)
    wire = _ProductOpenAICompatibleProvider._wire_messages(
        (
            Message(MessageRole.USER, "查一件事再写一个文件"),
            Message(MessageRole.ASSISTANT, ""),
            Message(MessageRole.TOOL, "{}", name="task_scope_search", call_id=CallId("call_1")),
            Message(MessageRole.TOOL, "{}", name="write_file", call_id=CallId("call_2")),
        ),
        arguments_memo=memo,
    )
    calls = wire[1]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_1", "call_2"]
    assert json.loads(calls[0]["function"]["arguments"]) == SEARCH_ARGUMENTS
    assert json.loads(calls[1]["function"]["arguments"]) == WRITE_ARGUMENTS


def test_same_call_id_under_another_tool_name_is_not_borrowed() -> None:
    """memo 跨 Run 存活，工具名不符必须退化，不得张冠李戴。"""

    memo = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_x", "task_scope_search", SEARCH_ARGUMENTS)),
        arguments_memo=memo,
    )
    wire = _ProductOpenAICompatibleProvider._wire_messages(
        (
            Message(MessageRole.USER, "继续"),
            Message(MessageRole.ASSISTANT, ""),
            Message(MessageRole.TOOL, "{}", name="write_file", call_id=CallId("call_x")),
        ),
        arguments_memo=memo,
    )
    assert wire[1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_memo_miss_falls_back_to_empty_object_and_counts(caplog) -> None:
    """未命中仍出合法线形状（不杀 Run），但必须记一条无载荷计数。"""

    memo = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_known", "task_scope_search", SEARCH_ARGUMENTS)),
        arguments_memo=memo,
    )
    messages = (
        Message(MessageRole.USER, "继续"),
        Message(MessageRole.ASSISTANT, ""),
        Message(MessageRole.TOOL, "{}", name="task_scope_search", call_id=CallId("call_known")),
        Message(MessageRole.ASSISTANT, ""),
        Message(MessageRole.TOOL, "{}", name="write_file", call_id=CallId("call_cold")),
    )
    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        wire = _ProductOpenAICompatibleProvider._wire_messages(messages, arguments_memo=memo)
    assert json.loads(wire[1]["tool_calls"][0]["function"]["arguments"]) == SEARCH_ARGUMENTS
    assert wire[3]["tool_calls"][0]["function"]["arguments"] == "{}"
    records = [r for r in caplog.records
               if r.getMessage().startswith("product_provider_tool_call_arguments_unavailable")]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "rebuilt=2 restored=1 fallback_empty=1" in message
    # 无载荷：不得泄漏 call id、工具名或任何入参值。
    for secret in ("call_known", "call_cold", "write_file", "task_scope_search", "青海湖"):
        assert secret not in message


def test_full_hit_logs_no_fallback_warning(caplog) -> None:
    memo = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_ok", "task_scope_search", SEARCH_ARGUMENTS)),
        arguments_memo=memo,
    )
    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        _ProductOpenAICompatibleProvider._wire_messages(
            (
                Message(MessageRole.USER, "继续"),
                Message(MessageRole.ASSISTANT, ""),
                Message(MessageRole.TOOL, "{}", name="task_scope_search", call_id=CallId("call_ok")),
            ),
            arguments_memo=memo,
        )
    assert not [r for r in caplog.records
                if "tool_call_arguments_unavailable" in r.getMessage()]


def test_restored_bytes_equal_the_live_metadata_path() -> None:
    """memo 复原与同进程 metadata 路径必须逐字节一致（不改请求指纹口径）。"""

    memo = ToolCallArgumentsMemo()
    retained = _retain_tool_calls_in_message(
        _issued_response(("call_same", "task_scope_search", SEARCH_ARGUMENTS)),
        arguments_memo=memo,
    )
    tool = Message(
        MessageRole.TOOL, "{}", name="task_scope_search", call_id=CallId("call_same")
    )
    live = _ProductOpenAICompatibleProvider._wire_messages(
        (Message(MessageRole.USER, "继续"), retained.message, tool), arguments_memo=memo
    )
    restored = _ProductOpenAICompatibleProvider._wire_messages(
        (Message(MessageRole.USER, "继续"), _durable_assistant(retained), tool),
        arguments_memo=memo,
    )
    assert live[1]["tool_calls"] == restored[1]["tool_calls"]
    assert (
        live[1]["tool_calls"][0]["function"]["arguments"]
        == '{"limit":5,"query":"\\u9752\\u6d77\\u6e56 \\u89c2\\u6d4b\\u62a5\\u544a"}'
    )


def test_live_metadata_serialisation_is_byte_frozen() -> None:
    """把内联 json.dumps 换成共享序列化，一个字节都不许变（黄金串）。

    这是 §byte-stable 的真正承重面：无重建的请求根本走不到这段代码，只有带
    ``tool_calls`` 的消息才走。黄金串按修复前的
    ``json.dumps(obj, sort_keys=True, separators=(",", ":"))``（ensure_ascii 默认
    True）写死，任何一处口径漂移都会在这里断。
    """

    live = Message(
        MessageRole.ASSISTANT,
        "",
        metadata={"provider_tool_calls": [{
            "id": "call_frozen", "name": "write_file",
            "arguments": {"z": 1, "a": "中文", "n": None, "f": 1.5,
                          "nested": {"b": [1, "二"], "a": True}},
        }]},
    )
    payload = _ProductOpenAICompatibleProvider._message_payload(live)
    assert payload["tool_calls"][0]["function"]["arguments"] == (
        '{"a":"\\u4e2d\\u6587","f":1.5,"n":null,'
        '"nested":{"a":true,"b":[1,"\\u4e8c"]},"z":1}'
    )


def test_requests_without_rebuilt_tool_calls_are_byte_stable() -> None:
    """无重建的请求必须与修复前逐字节相同（含空 memo 与满 memo 两种）。"""

    plain = ProviderRequest(
        request_id=RequestId("req-plain"),
        messages=(
            Message(MessageRole.SYSTEM, "system"),
            Message(MessageRole.USER, "你好"),
            Message(MessageRole.ASSISTANT, "你好，有什么可以帮你的？"),
            Message(MessageRole.USER, "再见"),
        ),
        tools=(),
    )
    empty_memo = _delegate(ToolCallArgumentsMemo())._request_payload(plain)
    warm = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_unrelated", "task_scope_search", SEARCH_ARGUMENTS)),
        arguments_memo=warm,
    )
    warm_memo = _delegate(warm)._request_payload(plain)
    assert json.dumps(empty_memo, sort_keys=True) == json.dumps(warm_memo, sort_keys=True)
    assert all("tool_calls" not in message for message in empty_memo["messages"])


def test_public_progress_is_stripped_before_it_is_retained() -> None:
    """留存的是 effect 账本里的同一份入参：Host 内部叙述字段不得回到线上。"""

    from deskpet.sdk_adapters.provider import _extract_public_progress

    memo = ToolCallArgumentsMemo()
    issued = _issued_response(
        (
            "call_narrate",
            "task_scope_search",
            {**SEARCH_ARGUMENTS, "deskpet_public_progress": "我先查一下"},
        )
    )
    _retain_tool_calls_in_message(_extract_public_progress(issued), arguments_memo=memo)
    restored = memo.read("call_narrate", "task_scope_search")
    assert json.loads(restored) == SEARCH_ARGUMENTS


def test_real_http_continuation_hop_sends_the_original_arguments() -> None:
    """端到端：真实 HTTP 传输 + 生产 ProductProviderAdapter，第二跳线体带真实入参。"""

    sent: list[dict] = []

    def physical(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return httpx.Response(200, json={
                "id": "hop-1", "model": "model-a",
                "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "call_hop", "type": "function",
                    # 带上 Host 内部叙述字段：它必须在留存前就被剥掉，
                    # 从而锁定 invoke 里 _extract_public_progress 在
                    # _retain_tool_calls_in_message **之前**这一顺序。
                    "function": {"name": "task_scope_search",
                                 "arguments": json.dumps(
                                     {**SEARCH_ARGUMENTS,
                                      "deskpet_public_progress": "NARRATION_CANARY"})},
                }]}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            })
        return httpx.Response(200, json={
            "id": "hop-2", "model": "model-a",
            "choices": [{"message": {"role": "assistant", "content": "查完了"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    async def case() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
        adapter = ProductProviderAdapter(
            Registry("fixture-secret"), provider_id="relay", client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            tool_call_arguments_memo=ToolCallArgumentsMemo(),
        )
        first = await adapter.invoke(
            ProviderRequest(
                request_id=RequestId("provider-turn:1"),
                messages=(Message(MessageRole.USER, "继续青海湖那个任务"),),
                tools=(),
            ),
            cancel=CancelToken(),
        )
        assert [call.call_id.value for call in first.tool_calls] == ["call_hop"]
        # 第二跳的 Context 是 durable 往返后的样子：assistant metadata 为空。
        await adapter.invoke(
            ProviderRequest(
                request_id=RequestId("provider-turn:2"),
                messages=(
                    Message(MessageRole.USER, "继续青海湖那个任务"),
                    _durable_assistant(first),
                    Message(MessageRole.TOOL, '{"candidates":[]}',
                            name="task_scope_search", call_id=CallId("call_hop")),
                ),
                tools=(),
            ),
            cancel=CancelToken(),
        )
        await client.aclose()

    asyncio.run(case())
    assert len(sent) == 2
    hop = sent[1]["messages"][1]
    assert json.loads(hop["tool_calls"][0]["function"]["arguments"]) == SEARCH_ARGUMENTS
    assert sent[1]["messages"][2]["tool_call_id"] == "call_hop"
    # 叙述字段不得随备忘回到 tool_calls 的 arguments 里。（它出现在 assistant
    # 的 content 上是 _extract_public_progress 的既定行为：把叙述提升为公开正文。）
    assert "NARRATION_CANARY" not in json.dumps(hop["tool_calls"], ensure_ascii=False)
    assert hop["content"] == "NARRATION_CANARY", "叙述必须被提升为公开正文，而不是丢弃"


# --- 原始 call_id 复用（index 型 id 端点）：必须失败关闭 ------------------------


def test_reused_call_id_never_staples_another_turns_arguments(caplog) -> None:
    """审计 F1：``call_0``/``call_1`` 这类每轮重排的 id 不得张冠李戴。

    原始 call_id 只在一个 ``(run, turn, ordinal)`` 内唯一——SDK 自己把这三者一起
    哈希才得到内部 CallId（``react_loop.py::_internal_effect_identity``）。把第 2 轮
    的入参贴到第 1 轮的 assistant 上比 ``"{}"`` 更坏：模型会看到自洽但虚假的
    「调用 read_file {"path":"B.md"} → A.md 的内容」。
    """

    memo = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_0", "read_file", {"path": "A.md"})), arguments_memo=memo
    )
    _retain_tool_calls_in_message(
        _issued_response(("call_0", "read_file", {"path": "B.md"})), arguments_memo=memo
    )
    messages = (
        Message(MessageRole.USER, "读两个文件"),
        Message(MessageRole.ASSISTANT, ""),
        Message(MessageRole.TOOL, "contents of A", name="read_file", call_id=CallId("call_0")),
        Message(MessageRole.ASSISTANT, ""),
        Message(MessageRole.TOOL, "contents of B", name="read_file", call_id=CallId("call_0")),
    )
    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        wire = _ProductOpenAICompatibleProvider._wire_messages(messages, arguments_memo=memo)
    assert wire[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert wire[3]["tool_calls"][0]["function"]["arguments"] == "{}"
    record = next(r.getMessage() for r in caplog.records
                  if "tool_call_arguments_unavailable" in r.getMessage())
    assert "rebuilt=2 restored=0 fallback_empty=2 ambiguous_call_ids=2" in record
    for secret in ("call_0", "read_file", "A.md", "B.md"):
        assert secret not in record


def test_conflicting_record_poisons_the_key_for_later_requests() -> None:
    """毒化跨请求生效：即使某一跳只看到一处出现，也不得给出另一轮的入参。"""

    memo = ToolCallArgumentsMemo()
    assert memo.record("call_0", "read_file", {"path": "A.md"}) is True
    assert memo.record("call_0", "read_file", {"path": "B.md"}) is False
    assert memo.is_poisoned("call_0")
    assert memo.read("call_0", "read_file") is None
    single = (
        Message(MessageRole.USER, "u"),
        Message(MessageRole.ASSISTANT, ""),
        Message(MessageRole.TOOL, "{}", name="read_file", call_id=CallId("call_0")),
    )
    wire = _ProductOpenAICompatibleProvider._wire_messages(single, arguments_memo=memo)
    assert wire[1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_identical_re_record_is_idempotent_and_stays_readable() -> None:
    """协议重采样会对同一轮再记一次：同参数不得被判为冲突。"""

    memo = ToolCallArgumentsMemo()
    assert memo.record("call_r", "read_file", {"path": "A.md"}) is True
    assert memo.record("call_r", "read_file", {"path": "A.md"}) is True
    assert not memo.is_poisoned("call_r")
    assert json.loads(memo.read("call_r", "read_file")) == {"path": "A.md"}
    assert len(memo) == 1


def test_same_call_id_under_another_tool_also_poisons() -> None:
    memo = ToolCallArgumentsMemo()
    memo.record("call_0", "read_file", {"path": "A.md"})
    assert memo.record("call_0", "write_file", {"path": "A.md"}) is False
    assert memo.read("call_0", "read_file") is None
    assert memo.read("call_0", "write_file") is None


def test_unique_ids_still_get_their_arguments_across_turns() -> None:
    """守卫不得误伤正常端点：不同 call_id 的两轮仍各自复原。"""

    memo = ToolCallArgumentsMemo()
    _retain_tool_calls_in_message(
        _issued_response(("call_t1", "read_file", {"path": "A.md"})), arguments_memo=memo
    )
    _retain_tool_calls_in_message(
        _issued_response(("call_t2", "read_file", {"path": "B.md"})), arguments_memo=memo
    )
    wire = _ProductOpenAICompatibleProvider._wire_messages(
        (
            Message(MessageRole.USER, "读两个文件"),
            Message(MessageRole.ASSISTANT, ""),
            Message(MessageRole.TOOL, "A", name="read_file", call_id=CallId("call_t1")),
            Message(MessageRole.ASSISTANT, ""),
            Message(MessageRole.TOOL, "B", name="read_file", call_id=CallId("call_t2")),
        ),
        arguments_memo=memo,
    )
    assert json.loads(wire[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "A.md"}
    assert json.loads(wire[3]["tool_calls"][0]["function"]["arguments"]) == {"path": "B.md"}


# --- ToolCallArgumentsMemo 本体 -------------------------------------------------


def test_rejected_record_never_leaves_a_stale_answer() -> None:
    """非法/超大入参不得让旧值继续可读（失败方向必须是退化，不是旧答案）。"""

    memo = ToolCallArgumentsMemo(entry_max_bytes=64)
    memo.record("call_s", "t", {"v": "ok"})
    assert memo.record("call_s", "t", "not-an-object") is False
    assert memo.read("call_s", "t") is None
    memo2 = ToolCallArgumentsMemo(entry_max_bytes=64)
    memo2.record("call_o", "t", {"v": "ok"})
    assert memo2.record("call_o", "t", {"v": "x" * 4096}) is False
    assert memo2.read("call_o", "t") is None


def test_record_never_raises_into_the_provider_path() -> None:
    """备忘是 advisory：任何异常都必须被吞成 False，不得变成 Run 的未知结局。"""

    class Exploding(dict):
        def items(self):  # json.dumps walks items()
            raise RuntimeError("boom")

    memo = ToolCallArgumentsMemo()
    assert memo.record("call_boom", "t", Exploding(a=1)) is False
    assert memo.read("call_boom", "t") is None




def test_memo_rejects_invalid_bounds() -> None:
    for kwargs in ({"capacity": 0}, {"max_bytes": 0}, {"entry_max_bytes": 0},
                   {"capacity": True}, {"capacity": "4"}):
        with pytest.raises(ValueError):
            ToolCallArgumentsMemo(**kwargs)


def test_memo_evicts_oldest_beyond_capacity() -> None:
    memo = ToolCallArgumentsMemo(capacity=2)
    for index in range(3):
        assert memo.record(f"call_{index}", "t", {"i": index})
    assert len(memo) == 2
    assert memo.read("call_0", "t") is None
    assert json.loads(memo.read("call_2", "t")) == {"i": 2}


def test_memo_read_refreshes_recency() -> None:
    memo = ToolCallArgumentsMemo(capacity=2)
    memo.record("call_a", "t", {"i": 0})
    memo.record("call_b", "t", {"i": 1})
    assert memo.read("call_a", "t") is not None
    memo.record("call_c", "t", {"i": 2})
    assert memo.read("call_a", "t") is not None
    assert memo.read("call_b", "t") is None


def test_memo_honours_the_total_byte_budget() -> None:
    memo = ToolCallArgumentsMemo(capacity=64, max_bytes=256, entry_max_bytes=256)
    for index in range(20):
        memo.record(f"call_{index:03d}", "t", {"v": "x" * 40})
    assert memo.recorded_bytes <= 256
    assert 0 < len(memo) < 20


def test_memo_skips_an_oversized_entry_without_evicting_the_rest() -> None:
    memo = ToolCallArgumentsMemo(entry_max_bytes=64)
    memo.record("call_small", "t", {"v": "ok"})
    assert memo.record("call_big", "t", {"v": "x" * 4096}) is False
    assert memo.read("call_big", "t") is None
    assert memo.read("call_small", "t") is not None


def test_memo_rejects_malformed_input() -> None:
    memo = ToolCallArgumentsMemo()
    assert memo.record("", "t", {}) is False
    assert memo.record("call", "", {}) is False
    assert memo.record("call", "t", "not-an-object") is False
    assert memo.record("call", "t", None) is False
    assert memo.read("", "t") is None
    assert memo.read("call", "") is None


def test_memo_conflicting_write_poisons_instead_of_last_write_wins() -> None:
    """入参不同的二次留存必须毒化，而不是「后写覆盖」——覆盖正是 F1 的成因。"""

    memo = ToolCallArgumentsMemo()
    memo.record("call_r", "t", {"v": 1})
    memo.record("call_r", "t", {"v": 2})
    assert memo.read("call_r", "t") is None
    assert memo.is_poisoned("call_r")
    assert len(memo) == 1
    memo.release_call("call_r")
    assert memo.read("call_r", "t") is None
    assert not memo.is_poisoned("call_r")
    assert len(memo) == 0 and memo.recorded_bytes == 0


def test_memo_clear_resets_the_byte_accounting() -> None:
    memo = ToolCallArgumentsMemo()
    memo.record("call_c1", "t", {"v": "x" * 100})
    memo.clear()
    assert len(memo) == 0 and memo.recorded_bytes == 0
