# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 Y(HM-TO-A6 第 10a 次, 2026-09-09): Run 内 reasoning 回传的质量。

事故: 窗口钉 32000(effective 26752)、Host 量到的 wire 只有 **10 731**,
provider 上一轮却计了 **24 818** —— 差出来的 14 740 是同一条 Run 里 15 条
assistant 的 ``reasoning_content``, 由 Host 自己(``_message_payload``)逐字
回灌到 wire payload 上。事件 W 的闸门把它当成「结构上看不见的隐藏质量」用
carry 去猜, 于是第 16 次调用被终局拦下, 而分页/裁史(F-E2/F-E3)碰不到这块。

真机探针(2026-09-09, ``deepseek-v4-flash``, 见 DECISION-Y 备忘录 §1)钉死:
回传按普通输入文本计费(+772.4 token/块 vs 同样文本挂 ``content`` 的 +780.0),
省略它**不是**协议错误(整条 5 步工具循环丢光回传仍 HTTP 200 且答案正确)。

本文件钉三件事:

1. **精确记账**: 回传的 ``reasoning_content`` 直接进 wire, 不再走 carry;
   ``hidden`` 塌回真正看不见的那点残差, 回执里出现 ``reasoning_relay_tokens``。
2. **有界回传**: 越界时按契约由老到新丢回传, 一旦装得下立刻停手 ——
   事故那一轮因此**发得出去**, 而不是终局失败。
3. **终局行为不变**: 回传全丢完仍越界(受保护的正文本身就超了)时, 还是
   ``sdk_provider_wire_input_budget_exceeded``, 一个字节都不发。
"""

from __future__ import annotations

import pytest

from deskpet.sdk_adapters.context_partitions import NON_CJK_CHARS_PER_TOKEN
from deskpet.sdk_adapters.wire_input_budget import (
    ObservedInputCarryLedger,
    WireInputBudgetExceeded,
    check_wire_input_budget,
    enforce_wire_input_budget,
    message_reasoning_tokens,
    reasoning_relay_order,
    wire_message_tokens,
    wire_reasoning_tokens,
    wire_request_tokens,
)

WINDOW = 32_000
EFFECTIVE = 26_752
MODEL = "deepseek-v4-flash"
RUN = "product-sdk-y"

# 事故那条 Run 的真实观测(evidence: .local-test-evidence/2026-09-09/
# native-a6-run10a/primary-ui-gd0661ss, execution-v6.sqlite3 provider_invocations)。
INCIDENT_TEXT_TOKENS = 10_731  # 第 16 次调用 Host 量到的非 reasoning 文本
PREVIOUS_TEXT_TOKENS = 10_078  # 第 15 次调用同口径
PREVIOUS_INPUT_TOKENS = 24_818
PREVIOUS_OUTPUT_TOKENS = 2_592
PREVIOUS_REASONING_TOKENS = 2_403
# 前 14 条 assistant 的 reasoning 累计(usage 逐轮相加, 见备忘录 §2 的表)。
PREVIOUS_RELAY_TOKENS = 12_877


def _tokens(count: int) -> str:
    """恰好 ``count`` 个 token 的非 CJK 文本(``text_tokens`` 口径)。"""

    return "x" * (int(count) * NON_CJK_CHARS_PER_TOKEN)


def _incident_messages(reasoning_each: int = 1_000) -> list[dict]:
    """事故形态: 一条 user, 之后 15 组 (assistant tool-call, tool result)。

    15 条 assistant 每条背 ``reasoning_each`` 个 token 的回传 —— 与真机同量级
    (那条 Run 累计 15 280, 单轮 26…4 761)。非 reasoning 的文本总量钉在
    ``INCIDENT_TEXT_TOKENS``, 与事故日志的 ``wire=10731`` 逐 token 相同。
    """

    turns = 15
    per_message = INCIDENT_TEXT_TOKENS // (2 * turns + 2)
    remainder = INCIDENT_TEXT_TOKENS - per_message * (2 * turns + 2)
    messages: list[dict] = [
        {"role": "system", "content": _tokens(per_message + remainder)},
        {"role": "user", "content": _tokens(per_message)},
    ]
    for index in range(turns):
        messages.append(
            {
                "role": "assistant",
                "content": _tokens(per_message),
                "reasoning_content": _tokens(reasoning_each),
            }
        )
        messages.append({"role": "tool", "content": _tokens(per_message)})
    return messages


def _payload(messages: list[dict]) -> dict:
    return {"model": MODEL, "messages": messages}


def _ledger_at_the_incident(*, relay_tokens: int = PREVIOUS_RELAY_TOKENS) -> ObservedInputCarryLedger:
    """把第 15 次调用的真实观测按生产路径灌进账本。

    ``record_wire`` 记的 wire 是**含回传**的口径(修复后的生产口径), 所以
    ``hidden`` 只剩 24 818 − (10 078 + 12 877) = 1 863 的残差。
    """

    ledger = ObservedInputCarryLedger()
    previous_id = f"{RUN}:provider-turn:15"
    ledger.record_wire(
        previous_id,
        PREVIOUS_TEXT_TOKENS + relay_tokens,
        model=MODEL,
        reasoning_relay_tokens=relay_tokens,
    )
    ledger.observe_usage(
        previous_id,
        input_tokens=PREVIOUS_INPUT_TOKENS,
        output_tokens=PREVIOUS_OUTPUT_TOKENS,
        reasoning_tokens=PREVIOUS_REASONING_TOKENS,
    )
    return ledger


# ── 1. 精确记账 ──────────────────────────────────────────────────────────────


def test_the_relayed_reasoning_is_counted_as_wire_not_guessed_as_hidden() -> None:
    """回传的 ``reasoning_content`` 是文本, 就按文本算 —— 它一直在 Host 手里。"""

    plain = [{"role": "assistant", "content": _tokens(100)}]
    relayed = [
        {
            "role": "assistant",
            "content": _tokens(100),
            "reasoning_content": _tokens(900),
        }
    ]
    assert wire_message_tokens(plain) == 100
    assert wire_message_tokens(relayed) == 1_000
    assert wire_reasoning_tokens(relayed) == 900
    assert message_reasoning_tokens(relayed[0]) == 900
    # 非字符串 / 空串 / 非 assistant 一律 0, 不猜。
    assert message_reasoning_tokens({"reasoning_content": ""}) == 0
    assert message_reasoning_tokens({"reasoning_content": {"text": "x"}}) == 0


def test_the_wire_estimate_matches_a_synthetic_billed_number() -> None:
    """精确记账判据: wire 估算 ≈ 计费, 残差不再是 reasoning 的量级。

    合成计费 = 中转站把 payload 的每一段文本(含回传)按 1 token / 4 字符
    计价, 再加每条消息的定长信封。修复前 ``reasoning_content`` 完全不进
    ``wire_input_tokens``, 残差 = 整块回传(事故当场 14 740);修复后残差只
    剩信封, 占计费不到 3%。
    """

    messages = _incident_messages()
    payload = _payload(messages)
    facts = check_wire_input_budget(
        request_id=f"{RUN}:provider-turn:1",
        payload=payload,
        window_tokens=WINDOW,
        ledger=ObservedInputCarryLedger(),
    )
    envelope_per_message = 4
    billed = sum(
        len(str(m.get("content") or "")) // NON_CJK_CHARS_PER_TOKEN
        + len(str(m.get("reasoning_content") or "")) // NON_CJK_CHARS_PER_TOKEN
        + envelope_per_message
        for m in messages
    )
    relay = facts["reasoning_relay_tokens"]
    assert relay == 15 * 1_000
    assert facts["wire_input_tokens"] == INCIDENT_TEXT_TOKENS + relay
    residual = billed - facts["wire_input_tokens"]
    assert 0 <= residual < 0.03 * billed, (billed, facts["wire_input_tokens"])
    # 修复前那条线看不见的正是 relay 这一整块。
    assert relay > 10 * residual


def test_the_hidden_carry_collapses_to_the_residual_once_the_relay_is_measured() -> None:
    """同一组真实数字: hidden 从 14 740 塌到 1 863, 因为 12 877 进了 wire。"""

    ledger = _ledger_at_the_incident()
    observed = ledger.last(f"{RUN}:provider-turn:16", model=MODEL)
    assert observed is not None
    assert observed.reasoning_relay_tokens == PREVIOUS_RELAY_TOKENS
    assert observed.hidden_tokens == PREVIOUS_INPUT_TOKENS - (
        PREVIOUS_TEXT_TOKENS + PREVIOUS_RELAY_TOKENS
    ) == 1_863
    # 事件 W 的口径(回传当隐藏质量)会把同一件事记成 14 740。
    assert PREVIOUS_INPUT_TOKENS - PREVIOUS_TEXT_TOKENS == 14_740


# ── 2. 有界回传 ──────────────────────────────────────────────────────────────


def test_the_incident_shaped_turn_is_still_blocked_without_a_bounded_relay() -> None:
    """红: 不裁回传时, 事故那一轮的实测下界仍然是 27 874 > 26 752。"""

    ledger = _ledger_at_the_incident()
    with pytest.raises(WireInputBudgetExceeded) as raised:
        check_wire_input_budget(
            request_id=f"{RUN}:provider-turn:16",
            payload=_payload(_incident_messages()),
            window_tokens=WINDOW,
            ledger=ledger,
        )
    facts = raised.value.diagnostics
    assert facts["reasoning_relay_tokens"] == 15_000
    assert facts["observed_hidden_tokens"] == 1_863
    # 精确记账**不改结论, 只改归属**: floor 逐 token 等于事故日志那一行的
    # 27 874, 但这 27 874 里 15 000 是量到的回传, 只有 1 863 + 280 是猜的。
    # (280 = 上一轮 usage 报的 2 403 减去本轮 wire 上多出来的 2 123 —— 合成
    # 夹具每条回传取整 1 000, 与真机 12 877/2 403 的分布差这一点, 而这条线
    # 会把差额老老实实补回 carry, 所以总数不变。)
    assert facts["wire_input_tokens"] == INCIDENT_TEXT_TOKENS + 15_000
    assert facts["observed_carry_tokens"] == 1_863 + 280
    assert facts["measured_input_floor"] == 27_874
    assert facts["measured_input_floor"] > facts["effective_input_budget"]


def test_the_incident_shaped_turn_fits_once_the_relay_is_bounded() -> None:
    """绿: 由老到新丢回传, 丢到刚好装得下就停 —— 这一轮发得出去。"""

    ledger = _ledger_at_the_incident()
    messages = _incident_messages()
    facts = enforce_wire_input_budget(
        request_id=f"{RUN}:provider-turn:16",
        payload=_payload(messages),
        window_tokens=WINDOW,
        ledger=ledger,
        reasoning_preserve="tool_loop",
    )
    assert facts["measured_input_floor"] <= facts["effective_input_budget"] == EFFECTIVE
    # 要补的缺口是 27 874 − 26 752 = 1 122, 每条回传 1 000 token → 丢 2 条。
    assert facts["reasoning_relay_dropped_messages"] == 2
    assert facts["reasoning_relay_dropped"] == 2_000
    assert facts["reasoning_relay_tokens"] == 13_000
    # 丢的是**最老**的两条, 最近一轮的思考原封不动。
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert [bool(m.get("reasoning_content")) for m in assistants] == [False, False] + [
        True
    ] * 13
    # 停手就停手: 装得下之后一条都不多丢。
    assert facts["measured_input_floor"] > EFFECTIVE - 1_000


def test_a_turn_that_already_fits_keeps_every_in_loop_relay() -> None:
    """预算够用时不动循环内的回传 —— 裁剪是压力响应, 不是常态。"""

    messages = _incident_messages(reasoning_each=100)
    ledger = ObservedInputCarryLedger()
    previous_id = f"{RUN}:provider-turn:15"
    # 上一轮: wire(含 1 400 回传) 11 478, 计费 11 700 —— 残差 222, 没有隐藏质量。
    ledger.record_wire(previous_id, 11_478, model=MODEL, reasoning_relay_tokens=1_400)
    ledger.observe_usage(
        previous_id, input_tokens=11_700, output_tokens=140, reasoning_tokens=100
    )
    facts = enforce_wire_input_budget(
        request_id=f"{RUN}:provider-turn:16",
        payload=_payload(messages),
        window_tokens=WINDOW,
        ledger=ledger,
        reasoning_preserve="tool_loop",
    )
    assert facts["measured_input_floor"] <= EFFECTIVE
    assert facts["reasoning_relay_dropped"] == 0
    assert facts["reasoning_relay_tokens"] == 1_500
    assert all(m.get("reasoning_content") for m in messages if m["role"] == "assistant")


def test_relay_outside_the_current_tool_loop_is_dropped_unconditionally() -> None:
    """契约只覆盖当前这一轮工具循环 —— 更早的回传本来就不该在 prompt 里。"""

    messages = [
        {"role": "user", "content": _tokens(10)},
        {"role": "assistant", "content": _tokens(10), "reasoning_content": _tokens(500)},
        {"role": "user", "content": _tokens(10)},
        {"role": "assistant", "content": _tokens(10), "reasoning_content": _tokens(700)},
        {"role": "tool", "content": _tokens(10)},
    ]
    free, pressured = reasoning_relay_order(messages, preserve="tool_loop")
    assert free == (1,)
    assert pressured == (3,)
    facts = enforce_wire_input_budget(
        request_id=f"{RUN}:provider-turn:2",
        payload=_payload(messages),
        window_tokens=WINDOW,
        ledger=ObservedInputCarryLedger(),
    )
    assert facts["reasoning_relay_dropped"] == 500
    assert facts["reasoning_relay_contract_dropped_messages"] == 1
    assert facts["reasoning_relay_tokens"] == 700
    assert "reasoning_content" not in messages[1]
    assert messages[3]["reasoning_content"]


def test_an_all_turns_model_never_drops_a_relay_it_does_not_have_to() -> None:
    """``preserve_reasoning="all_turns"``(kimi 系): 没有无条件可丢的。"""

    messages = [
        {"role": "user", "content": _tokens(10)},
        {"role": "assistant", "content": _tokens(10), "reasoning_content": _tokens(500)},
        {"role": "user", "content": _tokens(10)},
        {"role": "assistant", "content": _tokens(10), "reasoning_content": _tokens(700)},
    ]
    assert reasoning_relay_order(messages, preserve="all_turns") == ((), (1, 3))
    assert reasoning_relay_order(messages, preserve="not_required") == ((1, 3), ())
    facts = enforce_wire_input_budget(
        request_id=f"{RUN}:provider-turn:2",
        payload=_payload(messages),
        window_tokens=WINDOW,
        ledger=ObservedInputCarryLedger(),
        reasoning_preserve="all_turns",
    )
    assert facts["reasoning_relay_dropped"] == 0
    assert facts["reasoning_relay_tokens"] == 1_200


# ── 3. 终局行为不变 ──────────────────────────────────────────────────────────


def test_protected_mass_over_budget_still_fails_closed_with_the_stable_code() -> None:
    """回传全丢完仍越界 = 超的是受保护的正文, 终局失败的语义一个字没变。"""

    messages = [
        {"role": "user", "content": _tokens(30_000)},
        {"role": "assistant", "content": _tokens(10), "reasoning_content": _tokens(500)},
    ]
    with pytest.raises(WireInputBudgetExceeded) as raised:
        enforce_wire_input_budget(
            request_id=f"{RUN}:provider-turn:2",
            payload=_payload(messages),
            window_tokens=WINDOW,
            ledger=ObservedInputCarryLedger(),
        )
    assert raised.value.code == "sdk_provider_wire_input_budget_exceeded"
    facts = raised.value.diagnostics
    # 该丢的都丢了才认输: 回执上留得下证据。
    assert facts["reasoning_relay_dropped"] == 500
    assert facts["reasoning_relay_tokens"] == 0


def test_a_payload_without_any_relay_is_byte_identical_to_event_w() -> None:
    """没有回传的 payload(夹具回放 / 非 thinking 端点)走的还是事件 W 那条线。"""

    messages = [{"role": "user", "content": _tokens(1_000)}]
    payload = _payload(messages)
    ledger = ObservedInputCarryLedger()
    ledger.record_wire(f"{RUN}:provider-turn:1", 1_000, model=MODEL)
    ledger.observe_usage(
        f"{RUN}:provider-turn:1",
        input_tokens=5_000,
        output_tokens=900,
        reasoning_tokens=800,
    )
    facts = enforce_wire_input_budget(
        request_id=f"{RUN}:provider-turn:2",
        payload=payload,
        window_tokens=WINDOW,
        ledger=ledger,
    )
    assert facts["wire_input_tokens"] == wire_request_tokens(payload) == 1_000
    assert facts["reasoning_relay_tokens"] == 0
    assert facts["reasoning_relay_dropped"] == 0
    # carry = hidden(4000) + 回灌的 reasoning(800), 与事件 W 逐 token 相同。
    assert facts["observed_carry_before_trim"] == 4_800
    assert facts["measured_input_floor"] == 5_800


# ── 4. 接线: 真的发出去的那份 payload ────────────────────────────────────────


class _Entry:
    id = "relay"
    base_url = "https://relay.invalid/v1"
    model = "deepseek-v4-flash"
    models = ("deepseek-v4-flash",)
    incarnation_id = "incarnation-1"
    config_revision = 3
    enabled = True


class _Registry:
    def __init__(self) -> None:
        self.entry = _Entry()

    def get_entry(self, provider_id: str):
        return self.entry if provider_id == self.entry.id else None

    def resolve_api_key(self, provider_id: str) -> str:
        return "secret"


def _pin_window(tmp_path, monkeypatch, *, extra: str = "") -> None:
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "model_overrides.toml").write_text(
        '[models."deepseek-v4-flash"]\ncontext_window = 32000\n' + extra,
        encoding="utf-8",
    )


def _ok_response(prompt_tokens: int, reasoning: int = 0):
    import httpx

    return httpx.Response(
        200,
        json={
            "id": "x",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": reasoning + 20,
                "total_tokens": prompt_tokens + reasoning + 20,
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        },
    )


@pytest.mark.asyncio
async def test_the_relay_that_leaves_the_host_is_the_one_that_was_measured(
    tmp_path, monkeypatch
) -> None:
    """端到端: 元数据里的 ``provider_reasoning_content`` 回灌 → 计量 → 按契约裁。

    这一条走的是真的 ``_request_payload``: 断言的是**真正发到 transport 上的
    那个 JSON**, 所以「量的与发的是同一份」不是注释里的承诺而是判据。
    """

    import json as _json

    import httpx
    from simple_harness import RequestId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import CancelToken, ProviderRequest

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window(tmp_path, monkeypatch)
    sent: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(_json.loads(request.content.decode()))
        return _ok_response(20_000, reasoning=2_000)

    def _assistant(text: str, reasoning: str) -> Message:
        return Message(
            MessageRole.ASSISTANT,
            text,
            metadata={"provider_reasoning_content": reasoning},
        )

    messages = [Message(MessageRole.USER, _tokens(200))]
    for _ in range(15):
        messages.append(_assistant(_tokens(200), _tokens(1_000)))

    ledger = ObservedInputCarryLedger()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(
            _Registry(),
            provider_id="relay",
            client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            observed_input_carry=ledger,
        )
        assert adapter._reasoning_preserve == "tool_loop"
        await adapter.invoke(
            ProviderRequest(
                RequestId(f"{RUN}e2e:provider-turn:1"), tuple(messages)
            ),
            cancel=CancelToken(),
        )

    wire = sent[0]["messages"]
    relayed = [m for m in wire if m.get("reasoning_content")]
    # 契约要的是当前工具循环的回传, 预算够就一条不丢。
    assert len(relayed) == 15
    assert sum(len(m["reasoning_content"]) for m in relayed) == 15 * 1_000 * 4
    # 账本记下的 wire **含**这块回传, 所以下一轮的 hidden 是真的残差:
    # 计费 20 000 − wire(≈18 200) ≈ 1 800, 而不是把 15 000 也算成隐藏质量。
    observed = ledger.last(f"{RUN}e2e:provider-turn:2", model=MODEL)
    assert observed is not None
    assert observed.reasoning_relay_tokens == 15_000
    assert observed.wire_tokens == 15_000 + 15 * 200 + 200
    assert observed.hidden_tokens == 20_000 - observed.wire_tokens
    assert observed.hidden_tokens < 15_000


@pytest.mark.asyncio
async def test_the_gate_now_trims_the_relay_instead_of_killing_the_turn(
    tmp_path, monkeypatch
) -> None:
    """事故形态的第二轮: 事件 W 会终局失败, 现在按契约丢回传后发得出去。"""

    import json as _json

    import httpx
    from simple_harness import RequestId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import CancelToken, ProviderRequest

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window(tmp_path, monkeypatch)
    sent: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(_json.loads(request.content.decode()))
        # 第一轮真实计费远高于 Host 量到的 wire: 残差进 carry。
        return _ok_response(21_500, reasoning=2_000)

    def _turn(count: int) -> tuple[Message, ...]:
        rows = [Message(MessageRole.USER, _tokens(200))]
        for _ in range(count):
            rows.append(
                Message(
                    MessageRole.ASSISTANT,
                    _tokens(200),
                    metadata={"provider_reasoning_content": _tokens(1_000)},
                )
            )
        return tuple(rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(
            _Registry(),
            provider_id="relay",
            client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            observed_input_carry=ObservedInputCarryLedger(),
        )
        await adapter.invoke(
            ProviderRequest(RequestId(f"{RUN}trim:provider-turn:1"), _turn(15)),
            cancel=CancelToken(),
        )
        # 第二轮多六步工具循环: 事件 W 的口径下 floor 必然越界。
        await adapter.invoke(
            ProviderRequest(RequestId(f"{RUN}trim:provider-turn:2"), _turn(21)),
            cancel=CancelToken(),
        )

    # 两轮都发出去了 —— 关键: 第二轮没有变成 sdk_provider_wire_input_budget_exceeded。
    assert len(sent) == 2
    kept = sum(1 for m in sent[1]["messages"] if m.get("reasoning_content"))
    # wire 25 400 + carry 3 300 = 28 700 > 26 752, 缺 1 948 → 丢最老的 2 条。
    assert kept == 19
    # 丢的是最老的那几条, 最近一轮的思考一定还在。
    assert sent[1]["messages"][-1].get("reasoning_content")
    assert not sent[1]["messages"][1].get("reasoning_content")


@pytest.mark.asyncio
async def test_a_pre_loop_relay_never_reaches_the_relay_endpoint(
    tmp_path, monkeypatch
) -> None:
    """上一轮 user 之前的 assistant 回传, 契约上不需要 → 不发。"""

    import json as _json

    import httpx
    from simple_harness import RequestId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import CancelToken, ProviderRequest

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window(tmp_path, monkeypatch)
    sent: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(_json.loads(request.content.decode()))
        return _ok_response(1_200)

    messages = (
        Message(MessageRole.USER, "first question"),
        Message(
            MessageRole.ASSISTANT,
            "first answer",
            metadata={"provider_reasoning_content": _tokens(400)},
        ),
        Message(MessageRole.USER, "second question"),
        Message(
            MessageRole.ASSISTANT,
            "tool step",
            metadata={"provider_reasoning_content": _tokens(300)},
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(
            _Registry(),
            provider_id="relay",
            client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            observed_input_carry=ObservedInputCarryLedger(),
        )
        await adapter.invoke(
            ProviderRequest(RequestId(f"{RUN}pre:provider-turn:1"), messages),
            cancel=CancelToken(),
        )
    wire = sent[0]["messages"]
    assert [bool(m.get("reasoning_content")) for m in wire] == [
        False,
        False,
        False,
        True,
    ]


# ── 5. per-model thinking 开关 ──────────────────────────────────────────────


def test_the_thinking_control_is_off_by_default(tmp_path, monkeypatch) -> None:
    """没配就什么都不写 —— 型号用它自己的默认, 行为与事件 Y 之前相同。"""

    import httpx

    from deskpet.sdk_adapters.provider import ProductProviderAdapter
    from llm.model_info import resolve_reasoning_mode

    _pin_window(tmp_path, monkeypatch)
    assert resolve_reasoning_mode("deepseek-v4-flash") == "default"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _ok_response(10)))
    adapter = ProductProviderAdapter(
        _Registry(),
        provider_id="relay",
        client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"),
        observed_input_carry=ObservedInputCarryLedger(),
    )
    assert adapter.reasoning_wire == {}


def test_model_overrides_can_pin_the_thinking_control(tmp_path, monkeypatch) -> None:
    """``model_overrides.toml`` 的 ``reasoning_mode = "fast"`` → 请求里带
    ``thinking={"type":"disabled"}``(真机探针实测该端点支持, 见备忘录 §1)。"""

    import httpx

    from deskpet.sdk_adapters.provider import ProductProviderAdapter
    from llm.model_info import resolve_reasoning_mode

    _pin_window(tmp_path, monkeypatch, extra='reasoning_mode = "fast"\n')
    assert resolve_reasoning_mode("deepseek-v4-flash") == "fast"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _ok_response(10)))
    adapter = ProductProviderAdapter(
        _Registry(),
        provider_id="relay",
        client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"),
        observed_input_carry=ObservedInputCarryLedger(),
    )
    assert adapter.reasoning_wire == {"thinking": {"type": "disabled"}}


def test_the_session_model_params_always_beat_the_file(tmp_path, monkeypatch) -> None:
    """用户在「模型与参数」面板选的东西永远赢 override。"""

    import httpx

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window(tmp_path, monkeypatch, extra='reasoning_mode = "fast"\n')
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _ok_response(10)))
    adapter = ProductProviderAdapter(
        _Registry(),
        provider_id="relay",
        client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"),
        model_params={"reasoning_mode": "thinking"},
        observed_input_carry=ObservedInputCarryLedger(),
    )
    assert adapter.reasoning_wire == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def test_an_illegal_reasoning_mode_degrades_to_default(tmp_path, monkeypatch) -> None:
    """手写错的 TOML 不该让 provider 起不来。"""

    from llm.model_info import resolve_reasoning_mode

    _pin_window(tmp_path, monkeypatch, extra='reasoning_mode = "turbo"\n')
    assert resolve_reasoning_mode("deepseek-v4-flash") == "default"
