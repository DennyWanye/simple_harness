# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 W(HM-TO-A6 第 9 次, 2026-09-09): 物理请求的实测输入闸门。

事故: 窗口钉 32000(effective_input_budget 26752), 同一条 Run 的第 14 次
provider 调用被计费 **76 708** 个 input token —— 整个窗口的 2.40 倍 —— 而
Host 那一轮的 ``planned_input_tokens`` 是 25 139, 一次
``sdk_context_budget_exceeded`` 都没触发, 请求就那么发出去了。

本文件钉四件事:

1. **缺口是结构性的**: ``_wire_messages`` 补回的 assistant ``tool_calls.
   arguments`` 是在 fingerprint 与预算**之后**才拼进 wire 的, 所以装配期
   估算逐 token 看不见它(用真的 ``_wire_messages`` + 真的
   ``ToolCallArgumentsMemo`` 演一遍, 不用手搭的字典)。
2. **实测下界能看见它**: 用同一条 Run 上一次真实 usage 推出的 carry, 事故
   那条 Run 在 ordinal 4 就会被拦下。
3. **这条线必须真的是下界**: carry 只加**回灌的 reasoning**(正文与
   ``tool_calls.arguments`` 下一轮本来就在 payload 里, 再加一次就是重复计价),
   并且在装配期裁史(payload 变小)时按收缩比打折 —— 否则一份过期的 carry 会把
   一个真的装得下的请求**终局性**地打死。
4. **拟合倍率救不了这件事**: 同型号同中转站同 ordinal 上, 「要判出真实超预算
   的请求」与「别误伤真实装得下的请求」对 ratio 的要求互相矛盾 —— 所以这次
   不动 ``llm.model_info`` 的三元组, 改用实测。

夹具口径(2026-09-09 只读评审后重建): ``hm_to_a6_flash_pool_samples.json``
只落**原始观测**——本轮 payload 的字符类计数、provider 计费三元、以及同 Run
上一轮的 ``(wire, input, output, reasoning)``。carry 与 floor **不在夹具里**:
下面每一条群体判据都是把 ``previous_turn`` 灌进真的
:class:`ObservedInputCarryLedger`、再用真的 :func:`check_wire_input_budget`
复算出来的, 免得断言退化成「夹具自证夹具」。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from simple_harness.contracts import CallId
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.sdk_adapters.context_partitions import (
    WIRE_TOOL_SPEC_OVERHEAD_TOKENS,
    text_tokens,
)
from deskpet.sdk_adapters.tool_call_arguments import ToolCallArgumentsMemo
from deskpet.sdk_adapters.wire_input_budget import (
    ObservedInputCarryLedger,
    ObservedProviderTurn,
    WireInputBudgetExceeded,
    check_wire_input_budget,
    provider_turn_ordinal,
    run_key,
    wire_message_tokens,
    wire_request_tokens,
)

POOL_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hm_to_a6_flash_pool_samples.json"
WINDOW = 32000
EFFECTIVE = 26752
MODEL = "deepseek-v4-flash"


@pytest.fixture(scope="module")
def pool() -> dict:
    return json.loads(POOL_FIXTURE.read_text(encoding="utf-8"))


def _text(cjk_chars: int, other_chars: int) -> str:
    return "汉" * int(cjk_chars) + "x" * int(other_chars)


def _payload(messages: list[dict], *, model: str = MODEL) -> dict:
    return {"model": model, "messages": messages}


def _sample_payload(sample: dict) -> tuple[dict, list[dict]]:
    """把一条样本的字符类计数还原成一条**真的** payload + tools 规格。

    还原后 ``wire_request_tokens`` 必须逐 token 等于夹具记的
    ``wire_input_tokens``(见 ``test_the_replayed_payload_reproduces...``) ——
    这条 round-trip 是下面所有群体判据的地基: 判据走的是真代码, 输入是真观测。
    """

    messages = [
        {"role": m["role"], "content": _text(m["cjk_chars"], m["other_chars"])}
        for m in sample["messages"]
    ]
    specs = []
    for tool in sample["tools"]:
        name = str(tool["name"] or "")
        # tool_schema_tokens 渲染成 ``name\ndescription\ncanonical_json(schema)``:
        # 空 schema 是 "{}", 两个换行 + 两个花括号 = 4 个非 CJK 字符。
        filler = int(tool["other_chars"]) - len(name) - 4
        assert filler >= 0, sample["id"]
        specs.append(
            {
                "name": name,
                "description": _text(tool["cjk_chars"], filler),
                "input_schema": {},
            }
        )
    return _payload(messages), specs


def _ledger_with_previous_turn(sample: dict, request_id: str) -> ObservedInputCarryLedger:
    """把样本记的「上一轮真实观测」按生产路径灌进账本。

    走的是 ``record_wire`` + ``observe_usage`` 这对真方法(而不是直接塞
    dataclass), 所以配对语义、(Run, 型号) 键、reasoning 缺失时的退化
    都在判据的覆盖面里。
    """

    ledger = ObservedInputCarryLedger()
    previous = sample["previous_turn"]
    if previous is None:
        return ledger
    ordinal = int(sample["provider_turn_ordinal"])
    previous_id = f"{request_id.rsplit(':', 1)[0]}:{max(0, ordinal - 1)}"
    ledger.record_wire(previous_id, int(previous["wire_tokens"]), model=MODEL)
    ledger.observe_usage(
        previous_id,
        input_tokens=int(previous["input_tokens"]),
        output_tokens=int(previous["output_tokens"]),
        reasoning_tokens=previous["reasoning_tokens"],
    )
    return ledger


def _replay(sample: dict) -> dict:
    """真代码复算这条样本: 返回闸门的 facts, 外加 fired。"""

    request_id = f"product-sdk-{sample['id']}:provider-turn:{sample['provider_turn_ordinal']}"
    ledger = _ledger_with_previous_turn(sample, request_id)
    payload, specs = _sample_payload(sample)
    try:
        facts = check_wire_input_budget(
            request_id=request_id,
            payload=payload,
            tool_specs=specs,
            window_tokens=WINDOW,
            ledger=ledger,
        )
    except WireInputBudgetExceeded as exc:
        return dict(exc.diagnostics, fired=True)
    return dict(facts, fired=False)


@pytest.fixture(scope="module")
def replayed(pool: dict) -> list[tuple[dict, dict]]:
    return [(sample, _replay(sample)) for sample in pool["samples"]]


def _tagged(pool: dict, tag: str) -> list[dict]:
    return [s for s in pool["samples"] if tag in s.get("selected_for", ())]


def _current_estimate(sample: dict, pool: dict) -> int:
    cal = pool["current_calibration"]
    ordinal = int(sample["provider_turn_ordinal"])
    ratio = max(1.0, min(cal["base_ratio"] + cal["per_turn_ratio"] * ordinal, cal["max_ratio"]))
    messages = sum(
        math.ceil(text_tokens(_text(m["cjk_chars"], m["other_chars"])) * ratio)
        for m in sample["messages"]
    )
    tools = sum(
        text_tokens(_text(t["cjk_chars"], t["other_chars"])) + WIRE_TOOL_SPEC_OVERHEAD_TOKENS
        for t in sample["tools"]
    )
    return messages + math.ceil(tools * ratio)


# ── 1. 缺口是结构性的 ────────────────────────────────────────────────────────


def test_restored_tool_call_arguments_are_invisible_to_a_message_text_estimate() -> None:
    """装配期按 ``request.messages`` 估算; wire 上多出来的是补回的 tool_calls。

    事故那条 Run 的 ``task_scope_update`` arguments 是几 KB 的 CJK 文本,
    被 ``_wire_messages`` 在**每一条**后续请求里重新补回 assistant 消息上 ——
    到 ordinal 14 时累计 23 758 token, 而 ``request_json`` 里一个字都没有。
    这里用**真的** ``_wire_messages`` 与**真的** ``ToolCallArgumentsMemo`` 演,
    不是手搭一个「像 wire 的字典」。
    """

    from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider

    memo = ToolCallArgumentsMemo()
    assert memo.record("call_abc", "task_scope_update", {"summary": "任务范围更新" * 400})
    durable = (
        # durable Context 里的 provider assistant: metadata 必为空(SDK 契约),
        # 所以文本估算能看到的只有 content="" 这一个空串。
        Message(role=MessageRole.ASSISTANT, content=""),
        Message(
            role=MessageRole.TOOL,
            content="{}",
            name="task_scope_update",
            call_id=CallId("call_abc"),
        ),
    )
    seen_by_the_planner = wire_message_tokens(
        [_ProductOpenAICompatibleProvider._message_payload(m) for m in durable]
    )
    on_the_wire = _ProductOpenAICompatibleProvider._wire_messages(
        durable, arguments_memo=memo
    )
    really_sent = wire_message_tokens(on_the_wire)

    assert on_the_wire[0]["tool_calls"][0]["function"]["arguments"] != "{}"
    assert seen_by_the_planner < 20
    assert really_sent > seen_by_the_planner + 2000


def test_run_key_and_ordinal_come_from_the_request_id() -> None:
    assert run_key("product-sdk-abc:provider-turn:7") == "product-sdk-abc"
    assert provider_turn_ordinal("product-sdk-abc:provider-turn:7") == 7
    # 取不到序号时退化成 0 而不是抛: 闸门永远不该是发送路径上的新故障源。
    assert provider_turn_ordinal("product-sdk-abc") == 0
    # 不带 Run 前缀的 id(探针的 hash-only、conformance 的 req-1、裸的
    # provider-turn:N)**没有** Run 归属: 按整串退化会把两条 Run 的观测混在
    # 一起, 用另一条 Run 的 carry 判这一条。空串 = 没有观测。
    assert run_key("product-sdk-abc") == ""
    assert run_key("hash-only") == ""
    assert run_key("") == ""
    assert run_key(":provider-turn:3") == ""


def test_a_request_id_without_a_run_prefix_never_gets_a_carry() -> None:
    ledger = ObservedInputCarryLedger()
    ledger.record_wire("provider-turn:1", 10, model=MODEL)
    assert (
        ledger.observe_usage("provider-turn:1", input_tokens=90_000, output_tokens=1)
        is None
    )
    assert ledger.carry_tokens("provider-turn:2", model=MODEL) == 0


# ── 2. 实测下界 ─────────────────────────────────────────────────────────────


def test_without_an_observation_the_gate_is_exactly_the_wire_estimate() -> None:
    """冷启动/首轮: 没有观测就只看 wire —— 与本模块出现之前逐 token 相同。"""

    ledger = ObservedInputCarryLedger()
    facts = check_wire_input_budget(
        request_id="product-sdk-r:provider-turn:1",
        payload=_payload([{"role": "user", "content": "x" * 4000}]),
        window_tokens=WINDOW,
        ledger=ledger,
    )
    assert facts["observed_carry_tokens"] == 0
    assert facts["measured_input_floor"] == facts["wire_input_tokens"] == 1000
    assert facts["effective_input_budget"] == EFFECTIVE


def test_the_measured_carry_fails_the_request_closed_before_it_is_sent() -> None:
    """红线: 上一轮真实 usage 说隐藏质量已经越过预算, 这一轮就不能再发。"""

    ledger = ObservedInputCarryLedger()
    ledger.record_wire("product-sdk-r:provider-turn:5", 12_000, model=MODEL)
    ledger.observe_usage(
        "product-sdk-r:provider-turn:5",
        input_tokens=39_682,
        output_tokens=4_869,
        reasoning_tokens=4_869,
    )
    # carry = (39682 - 12000) + 4869 = 32551 —— 单这一项就越过 26752。
    assert ledger.carry_tokens("product-sdk-r:provider-turn:6", model=MODEL) == 32_551
    with pytest.raises(WireInputBudgetExceeded) as raised:
        check_wire_input_budget(
            request_id="product-sdk-r:provider-turn:6",
            payload=_payload([{"role": "user", "content": "x" * 48_000}]),
            window_tokens=WINDOW,
            ledger=ledger,
        )
    assert str(raised.value) == "sdk_provider_wire_input_budget_exceeded"
    # ``code`` 是 dispatch 结算 provider_invocations.error_code 用的那一个,
    # 覆盖了基类的 provider_request_rejected —— 这次拦截在库里认得出来。
    assert raised.value.code == "sdk_provider_wire_input_budget_exceeded"
    assert raised.value.error_code == "sdk_provider_wire_input_budget_exceeded"
    diagnostics = raised.value.diagnostics
    assert diagnostics["measured_input_floor"] > diagnostics["effective_input_budget"]
    assert diagnostics["observed_input_tokens"] == 39_682
    assert diagnostics["observed_hidden_tokens"] == 27_682
    assert diagnostics["provider_turn_ordinal"] == 6


def test_the_carry_charges_the_relayed_reasoning_not_the_whole_output() -> None:
    """MUST-FIX 2: ``measured_input_floor`` 必须真的是**下界**。

    上一轮的 assistant 正文与 ``tool_calls.arguments`` 这一轮已经在 payload
    里(``wire_request_tokens`` 量的就是补回 arguments 之后的 payload), 只有
    回灌的 ``reasoning_content`` 是量不到的。把整个 ``output_tokens`` 加上去
    等于把正文与入参各算两遍 —— 真机 239 组里有 28 组因此让 "floor" 超过了
    真实计费(最多 +1769), 那就不是下界而是又一个估算。
    """

    turn = ObservedProviderTurn(
        wire_tokens=10_000, input_tokens=21_000, output_tokens=6_000, reasoning_tokens=800
    )
    assert turn.hidden_tokens == 11_000
    assert turn.new_mass_tokens == 800
    assert turn.carry_tokens == 11_800
    # 中转站不报 reasoning_tokens 时才退回 output(保守方向, 宁可多算)。
    silent = ObservedProviderTurn(
        wire_tokens=10_000, input_tokens=21_000, output_tokens=6_000, reasoning_tokens=None
    )
    assert silent.new_mass_tokens == 6_000
    assert silent.carry_tokens == 17_000


def test_a_history_trim_discounts_the_carry_instead_of_killing_the_request() -> None:
    """MUST-FIX 1: 装配期裁完史之后, 账本里的 carry 已经过期。

    真机样本 run9/22ea642e ordinal 3→4: payload 从 14 996 wire 被裁到 11 579,
    provider 这一轮实际只计费 15 543。不打折的 carry 会把 floor 抬到 16 032 ——
    **高过真实计费**, 预算只要落在两者之间, 这道**终局**闸门就会打死一个真的
    装得下的请求。隐藏质量是挂在被裁掉的那几轮 assistant 消息上的, 它们走了,
    它也走了; payload 自己的收缩比是这道闸门唯一看得见的裁史信号。
    """

    previous = ObservedProviderTurn(
        wire_tokens=14_996, input_tokens=19_261, output_tokens=188, reasoning_tokens=83
    )
    undiscounted = previous.carry_tokens
    assert undiscounted == (19_261 - 14_996) + 83
    discounted = previous.carry_for_wire(11_579)
    assert discounted == undiscounted * 11_579 // 14_996 < undiscounted
    # 真实计费 15 543: 不打折的 floor 反超它, 打折后的 floor 仍在它之下。
    assert 11_579 + undiscounted > 15_543 > 11_579 + discounted
    # payload 没变小(或变大)时一分不打折 —— 这不是给 carry 打的通用折扣。
    assert previous.carry_for_wire(14_996) == undiscounted
    assert previous.carry_for_wire(30_000) == undiscounted


def test_the_trim_discount_is_visible_in_the_facts() -> None:
    ledger = ObservedInputCarryLedger()
    ledger.record_wire("product-sdk-t:provider-turn:1", 12_000, model=MODEL)
    ledger.observe_usage(
        "product-sdk-t:provider-turn:1",
        input_tokens=18_000,
        output_tokens=900,
        reasoning_tokens=900,
    )
    facts = check_wire_input_budget(
        request_id="product-sdk-t:provider-turn:2",
        # 4000 个 ASCII 字符 = 1000 token, 只有上一轮 wire 的 1/12。
        payload=_payload([{"role": "user", "content": "x" * 4_000}]),
        window_tokens=WINDOW,
        ledger=ledger,
    )
    assert facts["observed_carry_before_trim"] == 6_900
    assert facts["observed_wire_tokens"] == 12_000
    assert facts["observed_carry_tokens"] == 6_900 * 1_000 // 12_000
    assert facts["measured_input_floor"] == 1_000 + facts["observed_carry_tokens"]


def test_usage_without_a_paired_wire_measurement_is_not_recorded() -> None:
    """没有配对的 wire 就不记观测: hidden 会变成整条 prompt, 下一轮直接误伤。"""

    ledger = ObservedInputCarryLedger()
    assert (
        ledger.observe_usage(
            "product-sdk-r:provider-turn:1", input_tokens=30_000, output_tokens=500
        )
        is None
    )
    assert ledger.carry_tokens("product-sdk-r:provider-turn:2", model=MODEL) == 0


def test_the_observation_is_keyed_on_the_run_and_the_model() -> None:
    """同一条 Run 中途换绑型号: 旧型号的 carry 不判新型号的请求。

    tokenizer 与 reasoning 行为都随型号变, 拿 A 的隐藏质量判 B 是无根据的。
    """

    ledger = ObservedInputCarryLedger()
    ledger.record_wire("product-sdk-r:provider-turn:1", 10_000, model="deepseek-v4-flash")
    ledger.observe_usage(
        "product-sdk-r:provider-turn:1",
        input_tokens=30_000,
        output_tokens=500,
        reasoning_tokens=400,
    )
    assert ledger.carry_tokens("product-sdk-r:provider-turn:2", model="deepseek-v4-flash") == 20_400
    assert ledger.carry_tokens("product-sdk-r:provider-turn:2", model="deepseek-v4-pro") == 0
    # 闸门自己从 payload["model"] 取型号, 所以换型号的那一轮只看 wire。
    facts = check_wire_input_budget(
        request_id="product-sdk-r:provider-turn:2",
        payload=_payload([{"role": "user", "content": "x" * 400}], model="deepseek-v4-pro"),
        window_tokens=WINDOW,
        ledger=ledger,
    )
    assert facts["observed_carry_tokens"] == 0


def test_an_unknown_window_never_blocks_a_request() -> None:
    """窗口取不到就不判 —— 宁可不拦, 也不要凭猜的窗口拦。"""

    ledger = ObservedInputCarryLedger()
    ledger.record_wire("product-sdk-r:provider-turn:1", 10, model=MODEL)
    ledger.observe_usage(
        "product-sdk-r:provider-turn:1", input_tokens=900_000, output_tokens=1
    )
    assert (
        check_wire_input_budget(
            request_id="product-sdk-r:provider-turn:2",
            payload=_payload([{"role": "user", "content": "x"}]),
            window_tokens=None,
            ledger=ledger,
        )
        == {}
    )


def test_the_request_metadata_window_wins_over_the_adapter_default() -> None:
    """请求自称绑在哪个窗口上, 就按哪个窗口判 —— 那是它被装配时用的窗口。"""

    ledger = ObservedInputCarryLedger()
    facts = check_wire_input_budget(
        request_id="product-sdk-r:provider-turn:1",
        payload=_payload([{"role": "user", "content": "x" * 4_000}]),
        window_tokens=1_000_000,
        request_metadata={"budget": {"context_window": WINDOW}},
        ledger=ledger,
    )
    assert facts["window_tokens"] == WINDOW
    assert facts["effective_input_budget"] == EFFECTIVE
    scalar = check_wire_input_budget(
        request_id="product-sdk-r2:provider-turn:1",
        payload=_payload([{"role": "user", "content": "x" * 4_000}]),
        window_tokens=1_000_000,
        request_metadata={"context_window": WINDOW},
        ledger=ledger,
    )
    assert scalar["window_tokens"] == WINDOW


def test_the_ledger_is_bounded_and_run_scoped() -> None:
    ledger = ObservedInputCarryLedger(max_runs=2)
    for index in range(4):
        request_id = f"product-sdk-{index}:provider-turn:1"
        ledger.record_wire(request_id, 100, model=MODEL)
        ledger.observe_usage(
            request_id, input_tokens=500, output_tokens=50, reasoning_tokens=50
        )
    assert ledger.carry_tokens("product-sdk-0:provider-turn:2", model=MODEL) == 0
    assert ledger.carry_tokens("product-sdk-3:provider-turn:2", model=MODEL) == 450


def test_tool_specs_are_charged_on_top_of_the_messages() -> None:
    specs = [{"name": "t", "description": "d" * 400, "input_schema": {}}]
    payload = _payload([{"role": "user", "content": "x" * 40}])
    assert wire_request_tokens(payload, tool_specs=specs) > wire_request_tokens(payload)


def test_a_blocked_request_leaves_no_dangling_wire_record() -> None:
    """闸门拦下的请求不会有 usage 回来 —— pending 必须当场收回。"""

    ledger = ObservedInputCarryLedger()
    ledger.record_wire("product-sdk-r:provider-turn:1", 12_000, model=MODEL)
    ledger.observe_usage(
        "product-sdk-r:provider-turn:1",
        input_tokens=39_682,
        output_tokens=4_869,
        reasoning_tokens=4_869,
    )
    with pytest.raises(WireInputBudgetExceeded):
        check_wire_input_budget(
            request_id="product-sdk-r:provider-turn:2",
            payload=_payload([{"role": "user", "content": "x" * 48_000}]),
            window_tokens=WINDOW,
            ledger=ledger,
        )
    # 那条 wire 没有留下: 一个迟到的 usage 也配不上它。
    assert (
        ledger.observe_usage(
            "product-sdk-r:provider-turn:2", input_tokens=1, output_tokens=1
        )
        is None
    )
    # 上一轮的观测不受影响, 闸门下一次仍然按同一条线判。
    assert ledger.carry_tokens("product-sdk-r:provider-turn:3", model=MODEL) == 32_551


# ── 3. 真机证据: 把 239 组的代表样本用真代码重放一遍 ─────────────────────────


def test_the_replayed_payload_reproduces_the_recorded_wire(
    replayed: list[tuple[dict, dict]]
) -> None:
    """地基: 由字符类计数还原出的 payload, 其 wire 与夹具记的逐 token 相等。

    下面每一条群体判据都建立在这条 round-trip 上 —— 判据走真代码, 输入是真观测。
    """

    for sample, facts in replayed:
        assert facts["wire_input_tokens"] == sample["wire_input_tokens"], sample["id"]


def test_the_measured_floor_never_blocks_a_request_that_really_fitted(
    replayed: list[tuple[dict, dict]]
) -> None:
    """0 误伤: 真实计费在预算内的请求, 一条都不能被这道终局闸门打死。"""

    fitting = [
        (sample, facts)
        for sample, facts in replayed
        if sample["provider_input_tokens"] <= EFFECTIVE
    ]
    assert fitting, "夹具必须留有真实装得下的样本"
    assert [sample["id"] for sample, facts in fitting if facts["fired"]] == []


def test_every_request_that_overflowed_the_physical_window_is_caught(
    replayed: list[tuple[dict, dict]]
) -> None:
    """真正危险的那一类 —— 计费超过整个 32000 窗口 —— 一条都不漏。"""

    over_window = [
        (sample, facts) for sample, facts in replayed if sample["provider_input_tokens"] > WINDOW
    ]
    assert len(over_window) >= 10
    assert [sample["id"] for sample, facts in over_window if not facts["fired"]] == []


def test_the_documented_misses_are_all_inside_the_physical_window(
    pool: dict, replayed: list[tuple[dict, dict]]
) -> None:
    """漏判的那几条: 都只超 effective、仍在 32000 窗口内, 且确实没触发。

    它们是「该 Run 第一次超预算」或「上一轮之后刚裁过史」——反馈机制结构上
    够不着, 由装配期的倍率与有序降级去接。
    """

    misses = _tagged(pool, "over_budget_the_measured_floor_cannot_see")
    assert misses
    replayed_by_id = {sample["id"]: facts for sample, facts in replayed}
    for sample in misses:
        assert EFFECTIVE < sample["provider_input_tokens"] < WINDOW, sample["id"]
        assert not replayed_by_id[sample["id"]]["fired"], sample["id"]


def test_the_incident_run_is_stopped_at_ordinal_four_by_the_measured_floor(
    pool: dict
) -> None:
    """76 708 那条 Run: 实测下界在 ordinal 4 就已经越过预算。

    同一条请求的装配期估算说「装得下」—— 这就是第 9 次静默超窗的全部机理。
    """

    incident = [s for s in pool["samples"] if s["id"] == "run9-ded6c3dc-t4"]
    assert incident, "夹具必须保留事故 Run 的 ordinal 4"
    sample = incident[0]
    facts = _replay(sample)
    assert facts["fired"], "事故那条 Run 必须在 ordinal 4 被拦下"
    assert sample["provider_input_tokens"] > EFFECTIVE
    assert _current_estimate(sample, pool) <= EFFECTIVE


def test_the_discount_only_ever_lowers_the_floor(
    pool: dict, replayed: list[tuple[dict, dict]]
) -> None:
    """裁史打折只会把 floor 往下压, 不会把它抬上去。

    对每一条「上一轮之后 payload 变小」的样本, 复算出来的 carry 必须严格
    小于不打折的 carry, 而 floor 仍然不小于本轮 wire 本身。
    """

    trimmed = _tagged(pool, "history_trim_makes_the_undiscounted_carry_stale")
    assert trimmed
    replayed_by_id = {sample["id"]: facts for sample, facts in replayed}
    for sample in trimmed:
        facts = replayed_by_id[sample["id"]]
        assert facts["observed_carry_tokens"] <= facts["observed_carry_before_trim"], sample["id"]
        assert facts["measured_input_floor"] >= facts["wire_input_tokens"], sample["id"]
    # 至少有一条是真的被打了折的(不是全体相等的空判据)。
    assert any(
        replayed_by_id[s["id"]]["observed_carry_tokens"]
        < replayed_by_id[s["id"]]["observed_carry_before_trim"]
        for s in trimmed
    )


def test_the_floor_stays_below_the_real_billing_except_on_the_recorded_outliers(
    pool: dict, replayed: list[tuple[dict, dict]]
) -> None:
    """"下界" 的字面意思: floor 反超真实计费的样本只能是夹具点名的那几条。

    评审前的口径(carry 加整个 output)在 239 组里有 28 组反超; 改成只加
    reasoning、并在裁史时打折之后剩 6 组, 其中 3 组是 ordinal 1(carry=0,
    超的是文本估算本身, 与本模块无关)。
    """

    recorded = {s["id"] for s in _tagged(pool, "floor_above_the_real_billing")}
    actual = {
        sample["id"]
        for sample, facts in replayed
        if facts["measured_input_floor"] > sample["provider_input_tokens"]
    }
    assert actual == recorded
    with_a_carry = {
        sample["id"]
        for sample, facts in replayed
        if sample["id"] in actual and facts["observed_carry_tokens"] > 0
    }
    # 一半以上的反超与 carry 无关: 那是首轮, 文本估算自己就比真实计费高。
    assert len(with_a_carry) < len(actual)


def test_no_scalar_ratio_can_satisfy_this_evidence(pool: dict) -> None:
    """为什么这次**不**重拟三元组: 同一个 ordinal 上的两个要求互相矛盾。

    直接从样本算: 对每个 ordinal, 「把真实超预算的请求判出来」所需的最低倍率
    ``lo`` 与「不误伤真实装得下的请求」允许的最高倍率 ``hi`` —— 有的 ordinal
    上 ``lo > hi``, 无解。隐藏质量是可加的、逐 Run 的, 不是成比例的。
    """

    lo: dict[int, float] = {}
    hi: dict[int, float] = {}
    for sample in pool["samples"]:
        ordinal = int(sample["provider_turn_ordinal"])
        # est(r) ≈ (messages + tools) × r, 所以「刚好越过预算」的倍率是
        # EFFECTIVE / wire —— 与 fixture 里 239 组算法同一个式子。
        needed = EFFECTIVE / sample["wire_input_tokens"]
        if sample["provider_input_tokens"] > EFFECTIVE:
            lo[ordinal] = max(lo.get(ordinal, 0.0), needed)
        else:
            hi[ordinal] = min(hi.get(ordinal, 99.0), needed)
    contradictory = {o for o in lo.keys() & hi.keys() if lo[o] > hi[o]}
    assert {2, 3, 4, 6, 7} <= contradictory
    assert round(lo[2], 3) >= 1.83 and round(hi[2], 3) <= 1.69
    # 与夹具记的 239 组全量结论同号(样本只是全量的下界视角)。
    recorded = pool["ratio_model_conflict_by_ordinal"]
    for ordinal in contradictory:
        bounds = recorded[str(ordinal)]
        assert (
            bounds["min_ratio_to_flag_an_over_budget_request"]
            > bounds["max_ratio_that_spares_a_fitting_request"]
        )


def test_the_current_calibration_hides_the_over_budget_requests(pool: dict) -> None:
    """事故的量级: 旧口径把真实超预算的请求估成「装得下」。

    逐条按 ``llm.model_info`` 现行三元组复算, 不读夹具里的汇总数。
    """

    over_budget = [s for s in pool["samples"] if s["provider_input_tokens"] > EFFECTIVE]
    assert len(over_budget) >= 20
    hidden = [s for s in over_budget if _current_estimate(s, pool) <= EFFECTIVE]
    assert len(hidden) == len(over_budget)
    worst = min(_current_estimate(s, pool) / s["provider_input_tokens"] for s in pool["samples"])
    assert worst < 0.34


def test_the_calibration_triple_is_unchanged_by_this_incident(pool: dict) -> None:
    """三元组保持 Incident O 的取值 —— 调大它会重新打死装得下的 Run。"""

    from deskpet.sdk_adapters.context_partitions import calibration_for_model

    flash = calibration_for_model("deepseek-v4-flash")
    current = pool["current_calibration"]
    assert flash.base_ratio == current["base_ratio"]
    assert flash.per_turn_ratio == current["per_turn_ratio"]
    assert flash.max_ratio == current["max_ratio"]


def test_the_fixture_carries_only_raw_observations(pool: dict) -> None:
    """夹具只落字符类计数与 usage 三元, 不含任何 payload, 也不含派生量。"""

    assert pool["samples"], "夹具必须留有样本"
    for sample in pool["samples"]:
        assert "measured_input_floor" not in sample, sample["id"]
        assert "measured_carry_tokens" not in sample, sample["id"]
        for message in sample["messages"]:
            assert set(message) <= {"cjk_chars", "name", "other_chars", "role"}
        for tool in sample["tools"]:
            assert set(tool) <= {"cjk_chars", "name", "other_chars"}
        previous = sample["previous_turn"]
        if previous is not None:
            assert set(previous) == {
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "wire_tokens",
            }


def test_the_recorded_population_is_internally_consistent(pool: dict) -> None:
    """239 组全量的取值(memo 引的那些数)自洽: 触发 = 真超 + 误伤, 触发 + 漏判 = 真超预算。"""

    population = pool["population"]
    assert population["pairs"] == sum(population["pairs_by_evidence"].values())
    for block in ("measured_floor", "measured_floor_before_the_review"):
        stats = population[block]
        assert stats["fires"] == stats["fires_on_over_budget"] + stats["fires_on_fitting_requests"]
        assert (
            stats["fires_on_over_budget"] + stats["misses_over_budget"]
            == population["requests_over_effective_budget"]
        )
        assert stats["misses_over_window"] == 0
        assert stats["fires_on_fitting_requests"] == 0
    # 评审的两条修法各自的代价与收益, 都在这两块数字里。
    before = population["measured_floor_before_the_review"]
    after = population["measured_floor"]
    assert after["floor_above_real_billing"] < before["floor_above_real_billing"]
    assert after["misses_over_budget"] > before["misses_over_budget"]


# ── 4. 接线: 闸门真的挡在物理传输之前 ───────────────────────────────────────


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


def _pin_window_to_the_incident(tmp_path, monkeypatch) -> None:
    """事故当场就是这么钉的: 全局 model_overrides.toml 把窗口压到 32000。"""

    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    (tmp_path / "model_overrides.toml").write_text(
        '[models."deepseek-v4-flash"]\ncontext_window = 32000\n', encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_the_gate_stops_the_request_before_any_byte_reaches_the_relay(
    tmp_path, monkeypatch
) -> None:
    """第二轮的实测下界越界 → 抛 ProviderRequestRejectedError, transport 不动。"""

    import httpx
    from simple_harness import RequestId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import (
        CancelToken,
        ProviderRequest,
        ProviderRequestRejectedError,
    )

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window_to_the_incident(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
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
                    "prompt_tokens": 39_682,
                    "completion_tokens": 4_869,
                    "total_tokens": 44_551,
                    "completion_tokens_details": {"reasoning_tokens": 4_800},
                },
            },
        )

    ledger = ObservedInputCarryLedger()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(
            _Registry(),
            provider_id="relay",
            client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            observed_input_carry=ledger,
        )
        assert adapter._wire_input_budget_window == 32000

        first = ProviderRequest(
            RequestId("product-sdk-w:provider-turn:1"),
            (Message(MessageRole.USER, "x" * 4000),),
        )
        response = await adapter.invoke(first, cancel=CancelToken())
        # 第一轮照发, 并且真实 usage 被记成下一轮的实测底线 —— 隐藏质量
        # (39682 − 1000 wire) 加上**回灌的 reasoning**, 不是整个 output。
        assert len(sent) == 1
        assert response.usage.input_tokens == 39_682
        carry = ledger.carry_tokens("product-sdk-w:provider-turn:2", model="deepseek-v4-flash")
        assert carry == (39_682 - 1_000) + 4_800

        second = ProviderRequest(
            RequestId("product-sdk-w:provider-turn:2"),
            # 与上一轮同样大的 payload: 没有裁史, carry 一分不打折。
            (Message(MessageRole.USER, "x" * 4000),),
        )
        with pytest.raises(ProviderRequestRejectedError) as raised:
            await adapter.invoke(second, cancel=CancelToken())
    assert isinstance(raised.value, WireInputBudgetExceeded)
    assert raised.value.code == "sdk_provider_wire_input_budget_exceeded"
    # 关键: 第二次没有任何字节离开 Host。
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_run_with_no_hidden_mass_is_never_blocked(tmp_path, monkeypatch) -> None:
    """反面: 上一轮的真实 prompt 与 Host 量到的 wire 一致时, 闸门从不介入。"""

    import httpx
    from simple_harness import RequestId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import CancelToken, ProviderRequest

    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    _pin_window_to_the_incident(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
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
                "usage": {"prompt_tokens": 1_010, "completion_tokens": 20,
                          "total_tokens": 1_030},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(
            _Registry(),
            provider_id="relay",
            client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"),
            observed_input_carry=ObservedInputCarryLedger(),
        )
        for ordinal in (1, 2, 3):
            await adapter.invoke(
                ProviderRequest(
                    RequestId(f"product-sdk-quiet:provider-turn:{ordinal}"),
                    (Message(MessageRole.USER, "x" * 4000),),
                ),
                cancel=CancelToken(),
            )
    assert len(sent) == 3
