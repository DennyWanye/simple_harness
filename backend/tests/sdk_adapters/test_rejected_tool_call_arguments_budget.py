# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 事件 AL：**被拒**的大参数调用不该在历史里一直收费。

2026-09-09 第 13 次整跑 T17（``deepseek-v4-flash``，窗口 32000，thinking 关，
证据：``.local-test-evidence/2026-09-09/native-a6-run13/primary-ui-hwbyjnym``）。
同一轮里模型发了**两次**巨型入参调用，两次都被 Host 当场拒掉：

* ``context_route``（33 775 B）——``invalid_tool_arguments`` / ``outcome=failed``；
* ``task_scope_update``（34 680 B，逐字带着用户 18 KB 目标）——
  ``task_scope_update_refs_outside_scope`` / ``outcome=rejected``，因为它引用的是
  ``context_route`` 回执 id。

两份入参都已经作废，可 ``_wire_messages`` 每一轮照样把它们原样补回 payload
（事件 K 的修复：assistant 的 ``tool_calls.arguments`` 只能由 Host 的 memo 还原）。
于是重发那一轮：

    sdk_provider_wire_input_budget_exceeded floor=27176 effective=26752
    wire=26072 carry=1104   (observed_input=19898 output=6256)

—— 撑爆窗口的不是要发的内容，是历史里两份**死掉**的入参。本文件锁定：被拒调用
的入参在下一次线上请求里压成短存根（结构、``kind``/``operation_id``/
``reason_code`` 全留，长字符串留前 200 字并就地写明要重发），成功调用的入参一个
字节都不动。

数值全部取自那一次真机证据（``provider_invocations``）：消息按字符类等量重建，
上一轮观测 ``wire=18794 / input=19898`` 正好给出 ``carry=1104``。
"""

from __future__ import annotations

import json

import pytest
from simple_harness.contracts import CallId, RequestId
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.sdk_adapters import provider as provider_module
from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider
from deskpet.sdk_adapters.tool_call_arguments import (
    REJECTED_TOOL_CALL_ARGUMENTS_MAX_BYTES,
    REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX,
    ToolCallArgumentsMemo,
    canonical_tool_arguments_json,
    rejected_tool_result_reason,
    stub_rejected_tool_call_arguments,
)
from deskpet.sdk_adapters.wire_input_budget import (
    ObservedInputCarryLedger,
    WireInputBudgetExceeded,
    effective_budget_for_window,
    enforce_wire_input_budget,
)

MODEL = "deepseek-v4-flash"
WINDOW = 32000
RUN = "product-sdk-t17"
PREVIOUS_REQUEST = RequestId(f"{RUN}:provider-turn:3")
FAILING_REQUEST = RequestId(f"{RUN}:provider-turn:4")

#: 上一轮的真实配对：Host 量到 18 794，中转站计费 19 898 → hidden = carry = 1104。
PREVIOUS_WIRE_TOKENS = 18_794
PREVIOUS_INPUT_TOKENS = 19_898
PREVIOUS_OUTPUT_TOKENS = 6_256
T17_CARRY_TOKENS = PREVIOUS_INPUT_TOKENS - PREVIOUS_WIRE_TOKENS

#: 那一次真机请求的 12 份工具 schema 渲染后的字符量（全 ASCII）。
TOOL_SCHEMA_CHARS = 15_748
TOOL_SPEC_COUNT = 12

CALL_SEARCH = "call_00_D8OneUxjBnIS4zdsh2wu3697"
CALL_ROUTE_BAD = "call_01_y93Z5RTg0lWDQCMcMiNL3890"
CALL_ROUTE_OK = "call_00_koT4OkpMK7QLAnNUz7r05314"
CALL_UPDATE = "call_00_kkFW1UmUnu151asOWpjb3058"

ROUTE_RECEIPT_ID = "2cc4a020-cd05-561e-8e15-c87ccae10df2"


def _text(cjk: int, other: int) -> str:
    """字符类等量的填充文本：估算器只看字符类，不看字面。"""

    return "务" * cjk + "a" * other


def _envelope(
    *, outcome: str, error_code: str | None, message_chars: int
) -> str:
    """Host 工具结果信封，与真机上线的那一份同形。"""

    body = {
        "error_code": error_code,
        "outcome": outcome,
        "public_message": None if message_chars <= 0 else "m" * message_chars,
        "value": None,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _succeeded_envelope(cjk: int, other: int) -> str:
    return json.dumps(
        {"error_code": None, "outcome": "succeeded", "public_message": None,
         "value": {"disclosure": _text(cjk, other)}},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )


#: T17 的 ``goal.set`` 载荷：18 KB 中文目标逐字回显（真机 34 680 B canonical）。
GOAL_VALUE = _text(5_485, 1_572)

UPDATE_ARGUMENTS = {
    "base_revision": 1,
    # 模型手里当时**只有**路由回执 id，于是引用了它 —— 这正是被拒的原因。
    "evidence_refs": [ROUTE_RECEIPT_ID],
    "idempotency_key": "task-scope-update:t17:goal-set:attempt-1",
    "operations": [
        {
            "evidence_refs": [ROUTE_RECEIPT_ID],
            "kind": "goal.set",
            "operation_id": "op-t17-goal-set-1",
            "reason_code": "model_closure",
            "value": GOAL_VALUE,
        }
    ],
    "outcome": "mutate",
}
#: 参数非法的那一次 ``context_route``：同一段目标又逐字回显了一遍。
BAD_ROUTE_ARGUMENTS = {
    "goal": _text(5_330, 1_527),
    "route": "continue_active",
    "task_scope_id": None,
}
SEARCH_ARGUMENTS = {"limit": 5, "query": "校对流程 目标条款"}
OK_ROUTE_ARGUMENTS = {"route": "continue_active"}


def _messages() -> tuple[Message, ...]:
    """T17 第二次请求的 13 条消息，逐条按真机的字符类重建。"""

    return (
        Message(MessageRole.SYSTEM, _text(0, 4_696)),
        Message(MessageRole.SYSTEM, _text(0, 432)),
        Message(MessageRole.USER, _text(11, 0)),
        Message(MessageRole.ASSISTANT, _text(131, 4)),
        Message(MessageRole.USER, _text(456, 3_171)),
        # 用户这一轮的 7 KB 消息：107 条「目标条款」。
        Message(MessageRole.USER, _text(5_597, 1_602)),
        Message(MessageRole.ASSISTANT, _text(477, 162)),
        Message(MessageRole.TOOL, _succeeded_envelope(42, 7_800),
                name="task_scope_search", call_id=CallId(CALL_SEARCH)),
        Message(MessageRole.TOOL,
                _envelope(outcome="failed", error_code="invalid_tool_arguments",
                          message_chars=246),
                name="context_route", call_id=CallId(CALL_ROUTE_BAD)),
        Message(MessageRole.ASSISTANT, _text(791, 614)),
        Message(MessageRole.TOOL, _succeeded_envelope(0, 600),
                name="context_route", call_id=CallId(CALL_ROUTE_OK)),
        Message(MessageRole.ASSISTANT, _text(324, 6_000)),
        Message(MessageRole.TOOL,
                _envelope(outcome="rejected",
                          error_code="task_scope_update_refs_outside_scope",
                          message_chars=1_330),
                name="task_scope_update", call_id=CallId(CALL_UPDATE)),
    )


def _memo() -> ToolCallArgumentsMemo:
    memo = ToolCallArgumentsMemo()
    memo.record(CALL_SEARCH, "task_scope_search", SEARCH_ARGUMENTS)
    memo.record(CALL_ROUTE_BAD, "context_route", BAD_ROUTE_ARGUMENTS)
    memo.record(CALL_ROUTE_OK, "context_route", OK_ROUTE_ARGUMENTS)
    memo.record(CALL_UPDATE, "task_scope_update", UPDATE_ARGUMENTS)
    return memo


def _tool_specs() -> list[dict]:
    """渲染后与真机同量的 schema（``tool_schema_tokens`` 只看渲染出的字符）。"""

    per_spec = TOOL_SCHEMA_CHARS // TOOL_SPEC_COUNT - 5
    return [{"name": f"t{index}", "description": "d" * per_spec, "input_schema": {}}
            for index in range(TOOL_SPEC_COUNT)]


def _ledger() -> ObservedInputCarryLedger:
    """上一轮的真实观测：thinking 关 → ``no_reasoning`` 档，carry 只剩 hidden。"""

    book = ObservedInputCarryLedger()
    book.record_wire(PREVIOUS_REQUEST, PREVIOUS_WIRE_TOKENS, model=MODEL,
                     reasoning_disabled=True)
    book.observe_usage(PREVIOUS_REQUEST, input_tokens=PREVIOUS_INPUT_TOKENS,
                       output_tokens=PREVIOUS_OUTPUT_TOKENS, reasoning_tokens=None,
                       reasoning_content_seen=False)
    return book


def _payload(memo: ToolCallArgumentsMemo) -> dict:
    return {
        "model": MODEL,
        "messages": _ProductOpenAICompatibleProvider._wire_messages(
            _messages(), arguments_memo=memo
        ),
    }


def _enforce(payload: dict) -> dict:
    return enforce_wire_input_budget(
        request_id=FAILING_REQUEST,
        payload=payload,
        tool_specs=_tool_specs(),
        window_tokens=WINDOW,
        model_id=MODEL,
        ledger=_ledger(),
        reasoning_preserve="not_required",
    )


def _arguments(payload: dict, call_id: str) -> str:
    for message in payload["messages"]:
        for call in message.get("tool_calls") or ():
            if call["id"] == call_id:
                return call["function"]["arguments"]
    raise AssertionError(f"call {call_id} never reached the wire")


def test_t17_without_the_stub_the_resend_still_dies_over_budget(monkeypatch) -> None:
    """对照组：入参照旧原样补回，第二次请求仍然越界（真机 floor=27176）。"""

    monkeypatch.setattr(provider_module, "rejected_tool_result_reason",
                        lambda _content: None)
    payload = _payload(_memo())
    # 两份死掉的入参都完整地回到了 payload 上。
    assert _arguments(payload, CALL_UPDATE) == canonical_tool_arguments_json(
        UPDATE_ARGUMENTS
    )
    with pytest.raises(WireInputBudgetExceeded) as raised:
        _enforce(payload)
    facts = raised.value.diagnostics
    effective = effective_budget_for_window(WINDOW)
    assert facts["observed_carry_tokens"] == T17_CARRY_TOKENS == 1_104
    assert int(facts["measured_input_floor"]) > effective == 26_752


def test_t17_resend_fits_once_rejected_arguments_are_stubbed() -> None:
    """事件 AL 的验收：同一条序列，第二次请求进得了 26752。"""

    payload = _payload(_memo())
    facts = _enforce(payload)
    effective = effective_budget_for_window(WINDOW)
    assert effective == 26_752
    # payload 缩了，carry 按同一条收缩比打折（不打折就是拿裁史之前的账去判）。
    assert 0 < int(facts["observed_carry_tokens"]) <= T17_CARRY_TOKENS
    assert int(facts["measured_input_floor"]) <= effective
    # 省下来的正是两份作废入参，而不是别的什么被裁掉了。
    assert facts["wire_input_tokens"] < 26_072 - 8_000


def test_the_stub_keeps_the_shape_and_says_how_to_resend() -> None:
    """存根保留结构与 kind/operation_id/reason_code，只裁长字符串。"""

    payload = _payload(_memo())
    stubbed = json.loads(_arguments(payload, CALL_UPDATE))
    assert stubbed["base_revision"] == 1
    assert stubbed["evidence_refs"] == [ROUTE_RECEIPT_ID]
    assert stubbed["outcome"] == "mutate"
    operation = stubbed["operations"][0]
    assert operation["kind"] == "goal.set"
    assert operation["operation_id"] == "op-t17-goal-set-1"
    assert operation["reason_code"] == "model_closure"
    assert operation["value"].startswith(GOAL_VALUE[:200])
    assert operation["value"].endswith(REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX)
    # 严格 schema 的入参对象里不许多出宿主自己的键（事件 K 的模仿通道）。
    assert set(stubbed) == set(UPDATE_ARGUMENTS)
    assert set(operation) == set(UPDATE_ARGUMENTS["operations"][0])


def test_succeeded_and_small_calls_are_never_touched() -> None:
    """成功的调用、以及没到阈值的被拒调用，入参一个字节都不动。"""

    payload = _payload(_memo())
    assert _arguments(payload, CALL_SEARCH) == canonical_tool_arguments_json(
        SEARCH_ARGUMENTS
    )
    assert _arguments(payload, CALL_ROUTE_OK) == canonical_tool_arguments_json(
        OK_ROUTE_ARGUMENTS
    )
    small = canonical_tool_arguments_json({"outcome": "mutate"})
    assert len(small.encode("utf-8")) <= REJECTED_TOOL_CALL_ARGUMENTS_MAX_BYTES
    assert stub_rejected_tool_call_arguments(small) is None


def test_only_a_rejected_or_failed_envelope_counts_as_a_rejection() -> None:
    """判据是 Host 自己的结果信封，读不懂就当没拒（退回事件 AL 之前的行为）。"""

    assert rejected_tool_result_reason(
        _envelope(outcome="rejected", error_code="task_scope_update_refs_outside_scope",
                  message_chars=8)
    ) == "task_scope_update_refs_outside_scope"
    assert rejected_tool_result_reason(
        _envelope(outcome="failed", error_code=None, message_chars=8)
    ) == "failed"
    assert rejected_tool_result_reason(_succeeded_envelope(4, 4)) is None
    assert rejected_tool_result_reason("not json at all") is None
    assert rejected_tool_result_reason(None) is None


def test_the_bad_route_call_is_stubbed_too() -> None:
    """``outcome=failed`` 的巨型入参与 ``rejected`` 同等对待。"""

    payload = _payload(_memo())
    stubbed = json.loads(_arguments(payload, CALL_ROUTE_BAD))
    assert stubbed["route"] == "continue_active"
    assert stubbed["task_scope_id"] is None
    assert stubbed["goal"].endswith(REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX)
