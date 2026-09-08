# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeepSeek 工具入参多余 `}` 缺陷：Host 侧规范化 + 无载荷解析诊断。

2026-09-08 原生 HM-TO-A6 实跑（`deepseek-v4-pro` 官方 API）：记忆分析车道
6/10 次 Provider 调用以 `ProviderProtocolError` 失败（HTTP 200、
`tool_call_count=1`、`retryable=False`），用户事实因此没有落成记忆头。
用该轮 durable `analysis-attempt-input-*` 信封原样重放同一请求 39 次，12 次复现，
两种形态，本质同一个缺陷——模型多吐了一个 `}`：

* `trailing_delimiter`（11/12）：完整合法 JSON 对象后面多一个 `}`；
* `early_object_close`（1/12）：根对象提前一个 `}` 收尾，随后继续
  `, "closure_reason": ...}`。

`finish_reason=tool_calls`、`completion_tokens` 约 540–1250（预算 6144），
因此是序列化缺陷而非截断。

Host 只删除这一个多余的 `}`，且只在两个确定位置尝试；修复后必须整串解析成一个
JSON 对象。截断 JSON、尾随正文、尾随第二个独立值一律保持 fail-closed，并由新的
诊断字段说明是哪一步检查失败（只含形状、有界枚举与整数长度/偏移，不含任何载荷）。
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from simple_harness.contracts.identity import RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import (
    CancelToken,
    ProviderProtocolError,
    ProviderRequest,
    Secret,
)

from deskpet.sdk_adapters.provider import (
    ProductProviderAdapter,
    _ProductOpenAICompatibleProvider,
    _parse_failure_diagnostic,
    _repaired_tool_arguments,
    _ToolArgumentsProtocolError,
)
from tests.sdk_adapters.test_product_host_ports import Registry

# 实测捕获的 arguments（A6 第 2 轮证据），末尾多一个 `}`。
_QUOTE = "我的校对结果一律存到「外接硬盘 / 校对归档」这个目录"
_GOOD_ARGUMENTS = json.dumps(
    {
        "outcome": "mutate",
        "operations": [
            {
                "operation_id": "op_1",
                "memory_type": "semantic",
                "evidence_item_id": "99f9068e-d084-4e6d-8f18-cfe6052dab3c",
                "exact_quote": _QUOTE,
                "reason_code": "explicit_user_statement",
                "semantic": {
                    "subject_entity": "user:self",
                    "predicate": "proofreading_results_storage_directory",
                    "object_value": "外接硬盘 / 校对归档",
                },
            }
        ],
    },
    ensure_ascii=False,
)
_DEFECTIVE_ARGUMENTS = _GOOD_ARGUMENTS + "}"
# 实测捕获的第二形态：根对象提前一个 `}` 收尾，随后继续 closure_reason。
_CLOSURE = "记录用户关于校对结果存储目录的稳定偏好。"
_EARLY_CLOSE_ARGUMENTS = (
    _GOOD_ARGUMENTS + ', "closure_reason": ' + json.dumps(_CLOSURE, ensure_ascii=False) + "}"
)


def _provider() -> _ProductOpenAICompatibleProvider:
    return _ProductOpenAICompatibleProvider(
        httpx.AsyncClient(),
        "https://api.deepseek.com/v1",
        "deepseek-v4-pro",
        Secret("test-secret-value-not-real"),
    )


def _request() -> ProviderRequest:
    return ProviderRequest(
        RequestId("post-turn-analysis-test-1"),
        (Message(role=MessageRole.USER, content="[analysis evidence]"),),
    )


def _payload(arguments: object, **overrides: object) -> dict:
    payload = {
        "id": "c93b95d0-b1f5-432c-9451-fab662de0db7",
        "model": "deepseek-v4-pro",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "思考过程",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_00_lORSPnbgWEFwmddaDYnB1584",
                            "type": "function",
                            "function": {
                                "name": "memory_analysis_proposal",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 3876, "completion_tokens": 771, "total_tokens": 4647},
    }
    payload.update(overrides)
    return payload


def _parse(payload: dict):
    return _provider()._parse_response(_request(), payload, httpx.Response(200, json=payload))


# --- 修复：只修实测到的那一种畸形 -------------------------------------------------


def test_trailing_brace_arguments_are_repaired_and_proposal_survives() -> None:
    """实测缺陷响应必须解析成功，且入参与模型意图逐字一致。"""

    parsed = _parse(_payload(_DEFECTIVE_ARGUMENTS))

    assert len(parsed.tool_calls) == 1
    call = parsed.tool_calls[0]
    assert call.name == "memory_analysis_proposal"
    assert call.arguments["outcome"] == "mutate"
    assert call.arguments["operations"][0]["exact_quote"] == _QUOTE
    # 修复不得改动同一响应的其它字段
    assert parsed.finish_reason == "tool_calls"
    assert parsed.message.metadata["provider_reasoning_content"] == "思考过程"


@pytest.mark.parametrize("suffix", ["}", "}}", "]", "\n}", " } "])
def test_only_duplicate_closing_delimiters_are_stripped(suffix: str) -> None:
    assert _repaired_tool_arguments(_GOOD_ARGUMENTS + suffix) == (
        _GOOD_ARGUMENTS,
        "trailing_delimiter",
    )


def test_early_object_close_is_spliced_back_into_one_object() -> None:
    """第二实测形态：根对象提前收尾，删掉那个 `}` 后整串必须解析成一个对象。"""

    repaired = _repaired_tool_arguments(_EARLY_CLOSE_ARGUMENTS)
    assert repaired is not None
    text, reason = repaired
    assert reason == "early_object_close"
    assert len(text) == len(_EARLY_CLOSE_ARGUMENTS) - 1
    value = json.loads(text)
    assert value["outcome"] == "mutate"
    assert value["closure_reason"] == _CLOSURE
    assert value["operations"][0]["exact_quote"] == _QUOTE

    parsed = _parse(_payload(_EARLY_CLOSE_ARGUMENTS))
    assert parsed.tool_calls[0].arguments["closure_reason"] == _CLOSURE


@pytest.mark.parametrize(
    "tail",
    [
        ', "outcome": "no_mutation"}',  # 重复键会覆盖前缀 → 提案被反转
        ', "operations": []}',  # 重复键会清空操作 → 事实静默丢失
    ],
)
def test_duplicate_key_splice_is_refused(tail: str) -> None:
    """JSON 后键覆盖前键：拼接不得把「响亮的失败」变成「错误的成功」。"""

    raw = _GOOD_ARGUMENTS + tail
    # 无守卫时的朴素拼接确实合法，但语义已被后键改写——正是必须拒绝的原因。
    naive = json.loads(_GOOD_ARGUMENTS[:-1] + tail)
    assert (naive["outcome"], naive["operations"]) != (
        "mutate",
        json.loads(_GOOD_ARGUMENTS)["operations"],
    )
    assert _repaired_tool_arguments(raw) is None
    with pytest.raises(ProviderProtocolError):
        _parse(_payload(raw))


def test_repair_never_mutates_the_callers_payload() -> None:
    """修复必须是写时复制：调用方持有的对象一字不动。"""

    payload = _payload(_DEFECTIVE_ARGUMENTS)
    normalized = _ProductOpenAICompatibleProvider._normalized_tool_arguments(payload, "ref")

    assert normalized is not payload
    assert (
        payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        == _DEFECTIVE_ARGUMENTS
    )
    assert (
        normalized["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        == _GOOD_ARGUMENTS
    )


def test_repair_log_is_payload_free(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        _parse(_payload(_DEFECTIVE_ARGUMENTS))

    line = next(
        record.getMessage()
        for record in caplog.records
        if "product_provider_tool_arguments_repaired" in record.getMessage()
    )
    assert "call_index=0" in line
    assert f"arguments_length={len(_DEFECTIVE_ARGUMENTS)}" in line
    assert f"repaired_length={len(_GOOD_ARGUMENTS)}" in line
    assert "reason=trailing_delimiter" in line
    for secret in (_QUOTE, "外接硬盘", "proofreading", "call_00_lORSPnbgWEFwmddaDYnB1584"):
        assert secret not in line


def test_wellformed_response_is_passed_through_unchanged() -> None:
    """合法响应必须原对象透传：不复制、不重写、行为零变化。"""

    payload = _payload(_GOOD_ARGUMENTS)
    normalized = _ProductOpenAICompatibleProvider._normalized_tool_arguments(payload, "ref")

    assert normalized is payload
    assert _parse(payload).tool_calls[0].arguments["outcome"] == "mutate"


def test_wellformed_arguments_are_never_reparsed_as_repairable() -> None:
    assert _repaired_tool_arguments(_GOOD_ARGUMENTS) is None
    assert _repaired_tool_arguments(_GOOD_ARGUMENTS + "  \n") is None


@pytest.mark.parametrize(
    "arguments",
    [
        _GOOD_ARGUMENTS[:200],  # 截断（真正的输出预算不足）
        _GOOD_ARGUMENTS + '{"outcome": "no_mutation"}',  # 第二个独立对象：语义有歧义
        _GOOD_ARGUMENTS + " 抱歉，我再补充一句",  # 尾随正文
        _GOOD_ARGUMENTS + ', "closure_reason": "少了收尾',  # 拼接后仍不合法
        '["outcome"]',  # 顶层不是对象
        "",
    ],
)
def test_other_malformations_still_fail_closed(arguments: str) -> None:
    assert _repaired_tool_arguments(arguments) is None
    with pytest.raises(ProviderProtocolError):
        _parse(_payload(arguments))


# --- 诊断：说清是哪一步检查失败，且不含任何载荷 -------------------------------------


def test_parse_failure_log_names_the_failing_check_without_payload(caplog) -> None:
    # 尾部截断：整份证据正文、谓词与引文都还在载荷里，隐私断言才有意义。
    truncated = _GOOD_ARGUMENTS[:-30]
    assert _QUOTE in truncated and "proofreading" in truncated and "外接硬盘" in truncated
    payload = _payload(truncated)
    payload["choices"][0]["finish_reason"] = "length"

    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        with pytest.raises(ProviderProtocolError):
            _parse(payload)

    line = next(
        record.getMessage()
        for record in caplog.records
        if "product_provider_response_parse_failed" in record.getMessage()
    )
    diagnostic = json.loads(line.partition("diagnostic=")[2])
    assert diagnostic["check"] == "tool_call_arguments_not_json"
    assert diagnostic["arguments_length"] == len(truncated)
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["completion_tokens"] == 771
    assert diagnostic["json_error_position"] >= 0
    assert diagnostic["json_error_kind"] and diagnostic["json_error_kind"].islower()
    # 隐私：整行日志不得出现证据正文、入参正文或工具调用 id
    assert _QUOTE not in line
    assert "外接硬盘" not in line
    assert "call_00_lORSPnbgWEFwmddaDYnB1584" not in line
    assert "proofreading" not in line


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"choices": "nope"}, "choices_not_list_or_empty"),
        (_payload(_GOOD_ARGUMENTS, choices=[{"index": 0, "message": 7}]), "message_not_mapping"),
        (_payload({"outcome": "mutate"}), "unclassified"),
        (_payload(123), "tool_call_arguments_not_object"),
    ],
)
def test_diagnostic_classifies_each_check(payload: dict, expected: str) -> None:
    assert _parse_failure_diagnostic(payload)["check"] == expected


def _mutated(**changes) -> dict:
    payload = _payload(_GOOD_ARGUMENTS)
    call = payload["choices"][0]["message"]["tool_calls"][0]
    for key, value in changes.items():
        if key == "call_id":
            call["id"] = value
        elif key == "arguments":
            call["function"]["arguments"] = value
        elif key == "usage":
            payload["usage"] = value
        else:
            payload[key] = value
    return payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # CallId 只接受 1-255 个可打印 ASCII（contracts/identity.py）
        (_mutated(call_id="调用-1"), "tool_call_id_not_identifier"),
        (_mutated(call_id="c" * 256), "tool_call_id_not_identifier"),
        # json.loads 接受 NaN，SDK 的 JSON 契约不接受
        (_mutated(arguments='{"outcome": "mutate", "score": NaN}'),
         "tool_call_arguments_json_value_invalid"),
        # ProviderUsage 还会拒绝负数与「总数小于输入+输出」
        (_mutated(usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 5}),
         "usage_values_rejected"),
        (_mutated(usage={"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 1}),
         "usage_values_rejected"),
    ],
)
def test_diagnostic_mirrors_the_sdk_constructor_checks(payload: dict, expected: str) -> None:
    """这些检查藏在 SDK 的构造函数里；漏掉就只会报 unclassified。"""

    with pytest.raises(ProviderProtocolError):
        _parse(payload)
    assert _parse_failure_diagnostic(payload)["check"] == expected


def test_non_string_id_is_not_a_failure_when_the_header_supplies_one() -> None:
    """SDK 只有在没有 x-request-id 头时才回落到 payload['id']。"""

    payload = _mutated(id=12345)
    assert _parse_failure_diagnostic(payload)["check"] == "id_not_string"
    assert (
        _parse_failure_diagnostic(payload, provider_request_id_header=True)["check"]
        == "unclassified"
    )


def test_diagnostic_reports_non_string_content() -> None:
    payload = _payload(_GOOD_ARGUMENTS)
    payload["choices"][0]["message"]["content"] = ["blocked"]

    assert _parse_failure_diagnostic(payload)["check"] == "content_not_string"
    with pytest.raises(ProviderProtocolError):
        _parse(payload)


def test_diagnostic_never_raises_on_hostile_shapes() -> None:
    for payload in (None, [], {"choices": [None]}, {"choices": [{"message": {"tool_calls": [1]}}]}):
        assert isinstance(json.dumps(_parse_failure_diagnostic(payload)), str)


def test_deeply_nested_arguments_still_fail_with_the_sdk_error(caplog) -> None:
    """规范化/诊断本身出错时，绝不能取代或吞掉 SDK 的原始 Provider 错误。

    深嵌套入参会让 ``json.loads`` 抛 ``RecursionError``——不是 ``ValueError``，
    因此既不被修复路径捕获、也不被 SDK 的 ``_parse_tool_calls`` 捕获。
    """

    deep = "[" * 20000 + "]" * 20000
    payload = _payload('{"outcome": "mutate", "operations": ' + deep + "}")

    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        with pytest.raises(RecursionError):
            _parse(payload)

    messages = [record.getMessage() for record in caplog.records]
    assert any("product_provider_response_parse_failed" in message for message in messages)
    assert any(
        "product_provider_tool_arguments_normalization_unavailable" in message
        for message in messages
    )


# --- 重采样：不可修复的畸形工具调用不再一次就判死整个 Run ---------------------------
#
# 2026-09-08 HM-TO-A6 事件 D（同一次原生实跑，Run `product-sdk-26c66feb…` 第 10 轮）：
# `product_provider_response_parse_failed … diagnostic={"arguments_length":42,
# "check":"tool_call_arguments_not_json","json_error_kind":"expecting_delimiter",
# "json_error_position":10,"finish_reason":"tool_calls"}` → SDK
# `sdk_run_driver_failed ProviderProtocolError` → `run.fail`。
# `_repaired_tool_arguments` 只救得了多吐 `}` 那一种；救不回来的那一种是采样偶发，
# 却因为 `ProviderProtocolError` 属于 SDK 的 *definite* 失败而直接判 Run 死。
# 修法：Host 在 `ProductProviderAdapter`（SDK coordinator 之前的最后一帧）对且仅对
# `check=tool_call_arguments_not_json` 就地重采一次同一请求；第二次仍失败原样上抛。


_UNREPAIRABLE_ARGUMENTS = '{"a": 12345 "b": "c", "ddd": "eeeeeeeeee"}'  # 42 字节，与实测同形


def _good_analysis_payload(request_id: str = "resample-2") -> dict:
    payload = _payload(_GOOD_ARGUMENTS)
    payload["id"] = request_id
    return payload


def test_unrepairable_arguments_raise_the_resampleable_protocol_error() -> None:
    """诊断命中 tool_call_arguments_not_json 时改抛可重采子类，公开分类学一字不变。"""

    assert _repaired_tool_arguments(_UNREPAIRABLE_ARGUMENTS) is None
    with pytest.raises(_ToolArgumentsProtocolError) as raised:
        _parse(_payload(_UNREPAIRABLE_ARGUMENTS))
    assert isinstance(raised.value, ProviderProtocolError)
    assert raised.value.error_code == "provider_protocol_error"
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        _payload(_GOOD_ARGUMENTS, usage={"prompt_tokens": "x", "completion_tokens": 1, "total_tokens": 2}),
        _payload({"outcome": "mutate"}, model=7),
    ],
)
def test_other_protocol_failures_are_not_resampleable(payload: dict) -> None:
    """只有那一种畸形可重采：其它 200 响应缺陷仍是一次性的确定失败。"""

    with pytest.raises(ProviderProtocolError) as raised:
        _parse(payload)
    assert not isinstance(raised.value, _ToolArgumentsProtocolError)


def _adapter(transport, client_holder: list) -> ProductProviderAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    client_holder.append(client)
    return ProductProviderAdapter(
        Registry("resample-secret-never-logged"),
        provider_id="relay",
        client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"),
    )


async def _invoke(transport, *, cancel: CancelToken | None = None):
    holder: list = []
    adapter = _adapter(transport, holder)
    try:
        return await adapter.invoke(_request(), cancel=cancel or CancelToken())
    finally:
        await holder[0].aclose()


@pytest.mark.asyncio
async def test_one_malformed_tool_call_is_resampled_once_and_the_run_survives(caplog) -> None:
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = _payload(_UNREPAIRABLE_ARGUMENTS) if len(sent) == 1 else _good_analysis_payload()
        return httpx.Response(200, json=body)

    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        response = await _invoke(transport)

    # 第二次采样的结果被正常返回，模型意图逐字保留。
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].arguments["operations"][0]["exact_quote"] == _QUOTE
    # 恰好两次物理请求，且是**同一个**请求（同 request_id、同请求体）。
    assert len(sent) == 2
    assert sent[0].content == sent[1].content
    assert json.loads(sent[0].content)["messages"] == json.loads(sent[1].content)["messages"]

    lines = [r.getMessage() for r in caplog.records]
    resampled = [line for line in lines if "product_provider_protocol_resampled" in line]
    assert len(resampled) == 1
    assert "attempt=1" in resampled[0] and "max_attempts=2" in resampled[0]
    assert "check=tool_call_arguments_not_json" in resampled[0]
    # 审计行不得带任何载荷 / 密钥。
    for secret in (_QUOTE, "外接硬盘", "resample-secret-never-logged", _UNREPAIRABLE_ARGUMENTS):
        assert secret not in resampled[0]
    # 未被判定为整 Run 失败。
    assert not [line for line in lines if "product_provider_attempt_failed" in line]


@pytest.mark.asyncio
async def test_second_malformed_sample_still_fails_the_run_and_never_sends_a_third(caplog) -> None:
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_payload(_UNREPAIRABLE_ARGUMENTS))

    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        with pytest.raises(ProviderProtocolError) as raised:
            await _invoke(transport)

    assert raised.value.error_code == "provider_protocol_error"
    assert raised.value.retryable is False
    assert len(sent) == 2  # 一次重采，绝不第三次
    lines = [r.getMessage() for r in caplog.records]
    assert len([line for line in lines if "product_provider_protocol_resampled" in line]) == 1
    failed = [line for line in lines if "product_provider_attempt_failed" in line]
    assert len(failed) == 1 and "stage=response_protocol" in failed[0]


@pytest.mark.asyncio
async def test_wellformed_response_is_never_resampled(caplog) -> None:
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_good_analysis_payload())

    with caplog.at_level(logging.WARNING, logger="deskpet.sdk_adapters.provider"):
        response = await _invoke(transport)

    assert len(sent) == 1
    assert response.tool_calls[0].arguments["outcome"] == "mutate"
    assert not [r.getMessage() for r in caplog.records if "protocol_resampled" in r.getMessage()]


@pytest.mark.asyncio
async def test_repairable_defect_is_repaired_in_place_without_a_second_call() -> None:
    """多吐 `}` 那一种仍由确定性修复就地救回：不多花一次采样。"""

    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_payload(_DEFECTIVE_ARGUMENTS))

    response = await _invoke(transport)
    assert len(sent) == 1
    assert response.tool_calls[0].arguments["operations"][0]["exact_quote"] == _QUOTE


@pytest.mark.asyncio
async def test_other_protocol_failure_is_not_resampled_end_to_end() -> None:
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_payload(_GOOD_ARGUMENTS, model=7))

    with pytest.raises(ProviderProtocolError):
        await _invoke(transport)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_cancelled_turn_is_not_resampled() -> None:
    """用户已停止：畸形样本不再补采，取消语义优先。"""

    sent: list[httpx.Request] = []
    cancel = CancelToken()

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        cancel.cancel()
        return httpx.Response(200, json=_payload(_UNREPAIRABLE_ARGUMENTS))

    with pytest.raises(ProviderProtocolError):
        await _invoke(transport, cancel=cancel)
    assert len(sent) == 1
