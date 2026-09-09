# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Last-mile, *measured* input-budget fence for the physical provider request.

事件 W(HM-TO-A6 第 9 次,2026-09-09)。装配期的预算闸门只能按 Host 自己
写得出的文本估 token,而真正上线的 prompt 里有两块它结构上看不见的质量:

1. **中转站回灌的 ``reasoning_content``** —— 上一轮 assistant 的思考文本被加回
   下一轮 prompt。Host 从来没有拿到过那段文本。
2. **Host 自己在 wire 期补回的 assistant ``tool_calls.arguments``** ——
   :meth:`deskpet.sdk_adapters.provider._ProductOpenAICompatibleProvider._wire_messages`
   在 **fingerprint 与预算之后** 才把它拼进 payload(事件 K 的 memo 修复),
   所以它既不在 ``request.messages`` 里、也不在落库的 ``request_json`` 里。

两块都只能靠 per-model 校准倍率(``llm.model_info`` 的 ratio 三元组)去 *猜*。
第 9 次证明猜错的代价是静默超窗:窗口钉 32000、effective 26752,Host 逐轮
planned 都停在 26k 以下,provider 却一路计到 **76 708**(窗口的 2.40 倍),
14 次调用里 **一次** ``sdk_context_budget_exceeded`` 都没触发。

这个模块提供的是**不靠猜**的那一条线:同一条 Run 上一次真实的
``usage.input_tokens`` 已经在 Host 手里,它减去当时 Host 能量到的 wire,
就是那一轮隐藏质量的**实测值**:

    carry(n)  = max(0, input_tokens(n-1) - wire_tokens(n-1)) + reasoning_tokens(n-1)
    carry_eff = carry(n) × min(1, wire_tokens(n) ÷ wire_tokens(n-1))
    floor(n)  = wire_tokens(n) + carry_eff

两处口径都必须**逐字**这么写,否则这条线不是下界:

*为什么第二项是 reasoning 而不是 output*:上一轮的 assistant 正文与
``tool_calls.arguments`` 这一轮**已经在 wire_tokens(n) 里**——这个函数量的就是
补回 arguments 之后的 payload 本身。只有回灌的 ``reasoning_content`` 是计费了
却谁也量不到的那一块。把整个 ``output_tokens`` 加上去等于把正文与入参各算两遍:
239 组真机配对里有 **28 组**因此让 "floor" 超过了真实计费(最多 +1769),
它就不再是下界而是又一个估算。``usage`` 没给 ``reasoning_tokens`` 时(不是所有
中转站都给)才退回 ``output_tokens`` —— 那是保守方向,宁可多算。

*为什么要乘 payload 的收缩比*:隐藏质量是挂在**产生它的那几轮 assistant 消息**上
的;装配期一旦裁史或强制分页,那几轮连同它们的 reasoning 一起离开了 payload,
而账本记的 carry 还停在裁史之前。``wire_tokens(n) < wire_tokens(n-1)`` 是这道
闸门唯一能看见的裁史信号(裁史事实落在**回执**里,不在 ``ProviderRequest``
上——239 组真机配对的 ``request.metadata`` 全是 ``{}``),所以按 payload 自己的
收缩比例把 carry 打折。不打折就会用一份过期的 carry 去打死一个真的装得下的
请求,而这道闸门是**终局**的:它一响这次尝试就结束了。

``floor`` 越过 ``effective_input_budget`` 就 **fail closed**,错误码
``sdk_provider_wire_input_budget_exceeded``。在 239 组真机配对上(run9 134 +
run8 87 + run6 18,``request_json`` 逐条重放,见 memo §3.1)这条线触发 **36 次**、
**36/36** 落在真实超预算的请求上、**0/197** 误伤真实装得下的请求,真超**整个
窗口**的 **24/24** 全部拦下;真实超预算而漏判 6 条,全部仍在 32000 物理窗口内。
"floor 反而高过真实计费" 的组数由 28 降到 **6**,其中 3 条是 ordinal 1
(carry=0,超的是文本估算本身,与本模块无关),carry 惹的只剩 3 条。

与装配期闸门的分工:装配期 ``ContextBudgetExceeded`` 之前有有序降级(强制
分页 → 裁史),能救一条 Run;这里是最后一道、只在 **物理发出之前** 拦截,
拦到就是这次尝试失败。所以它的阈值必须是**实测下界**而不是保守估算——宁可
让 ratio 先把 Run 降级掉,也不要在这里替 ratio 背误判。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from simple_harness.providers.errors import ProviderRequestRejectedError

_LOG = logging.getLogger(__name__)

#: 一个进程内最多记住多少条 Run 的观测。Run 结束时没有可靠的统一钩子
#: (前台/后台/工作流各有各的收尾),所以用有界 FIFO 而不是显式生命周期:
#: 记漏了退化成「没有观测」= 现状,记多了只是占几百字节。
_MAX_TRACKED_RUNS = 256

_PROVIDER_TURN_SEPARATOR = ":provider-turn:"


class WireInputBudgetExceeded(ProviderRequestRejectedError):
    """本次物理请求的**实测**输入下界越过了 effective_input_budget。

    继承 :class:`ProviderRequestRejectedError`,与 ``ProspectiveRequestGuard``
    同一类:pre-delegate 边界上的**确定性**请求失败,没有任何字节离开 Host。
    """

    #: 自己的稳定错误码。``ProviderError.__init__`` 把 **类属性** ``error_code``
    #: 写进实例 slot ``code``,而 ``dispatch`` 正是按 ``str(exc.code)`` 结算
    #: ``provider_invocations.error_code`` 的 —— 覆盖它, 这次拦截在库里就与
    #: ``provider_request_rejected`` 的其它成因分得开(a6_verify 按它取证)。
    #: 覆盖 ``code`` 本身没有用: 那是实例 slot, 会被 ``__init__`` 盖掉。
    error_code = "sdk_provider_wire_input_budget_exceeded"

    def __init__(self, **diagnostics: int) -> None:
        super().__init__(public_message=WireInputBudgetExceeded.error_code)
        self.diagnostics: dict[str, int] = {k: int(v) for k, v in diagnostics.items()}

    def __str__(self) -> str:
        return WireInputBudgetExceeded.error_code


@dataclass(frozen=True, slots=True)
class ObservedProviderTurn:
    """某一轮真实发生过的 (Host 量得到的 wire, provider 计费) 配对。"""

    wire_tokens: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None

    @property
    def hidden_tokens(self) -> int:
        """provider 计费里 Host 结构上看不见的那部分(实测,非估算)。"""

        return max(0, int(self.input_tokens) - int(self.wire_tokens))

    @property
    def new_mass_tokens(self) -> int:
        """这一轮的产出里,下一轮 payload **量不到**的那部分。

        正文与 ``tool_calls.arguments`` 下一轮都会原样回到 payload 里,而
        :func:`wire_request_tokens` 量的就是那个 payload —— 再加一次就是重复
        计价。真正量不到的只有回灌的 ``reasoning_content``。中转站不报
        ``reasoning_tokens`` 时退回整个 ``output_tokens``(保守方向)。
        """

        if self.reasoning_tokens is None:
            return max(0, int(self.output_tokens))
        return max(0, int(self.reasoning_tokens))

    @property
    def carry_tokens(self) -> int:
        """下一轮 prompt 至少会带着的隐藏质量(未按裁史打折)。"""

        return self.hidden_tokens + self.new_mass_tokens

    def carry_for_wire(self, wire_tokens: int) -> int:
        """按本轮 payload 相对上一轮的收缩比打折后的 carry。

        payload 变小 = 装配期裁掉了历史(或把结果换成了页引用),那几轮
        assistant 消息背着的隐藏质量也随之离开了 prompt。这是这道闸门唯一
        能看见的裁史信号,见模块 docstring。
        """

        carry = self.carry_tokens
        previous = max(0, int(self.wire_tokens))
        current = max(0, int(wire_tokens))
        if previous <= 0 or current >= previous:
            return carry
        return carry * current // previous


def run_key(request_id: object) -> str:
    """``product-sdk-<run>:provider-turn:7`` → ``product-sdk-<run>``。

    **没有 ``:provider-turn:`` 就返回空串**,而不是退化成整串。整串退化在这里
    是错的:那些 id(``provider-turn:N``、探针的 ``hash-only``、conformance 的
    ``req-1``)不带 Run 前缀,按整串记会把**不同 Run 的观测**混进同一个 key,
    carry 就来自另一条 Run。空串 = 没有观测 = 只看 wire,与本模块出现之前同。
    """

    text = str(getattr(request_id, "value", request_id) or "")
    head, separator, _ = text.partition(_PROVIDER_TURN_SEPARATOR)
    return head if separator else ""


def provider_turn_ordinal(request_id: object) -> int:
    text = str(getattr(request_id, "value", request_id) or "")
    _, sep, tail = text.partition(_PROVIDER_TURN_SEPARATOR)
    if not sep:
        return 0
    try:
        return max(0, int(tail))
    except (TypeError, ValueError):
        return 0


class ObservedInputCarryLedger:
    """进程内、按 Run 记住上一轮真实 usage 的有界账本。

    只在**同一个进程**里有效:冷启动后第一轮没有观测,闸门退化成「只看
    wire」——与本模块出现之前的行为逐 token 相同,不会多打死任何 Run。
    """

    def __init__(self, *, max_runs: int = _MAX_TRACKED_RUNS) -> None:
        self._max_runs = max(1, int(max_runs))
        self._lock = threading.Lock()
        self._turns: dict[tuple[str, str], ObservedProviderTurn] = {}
        self._pending: dict[str, tuple[int, str]] = {}

    def record_wire(self, request_id: object, wire_tokens: int, *, model: object = "") -> None:
        """记下「这一次物理请求 Host 量到的 wire」,等 usage 回来配对。

        单独一步而不是等 usage 一起写,是因为这两个数字**只有在这里**同时
        成立:wire 只有装配 payload 的那一刻知道,usage 只有响应回来才知道,
        而 hidden = usage.input - wire 必须是**同一次请求**的差值。

        ``model`` 一起记下来:观测按 ``(Run, 型号)`` 存,同一条 Run 中途换了
        绑定型号(不同 tokenizer、不同 reasoning 行为)时不会拿旧型号的 carry
        判新型号的请求。
        """

        key = str(getattr(request_id, "value", request_id) or "")
        if not key:
            return
        with self._lock:
            self._pending[key] = (max(0, int(wire_tokens)), str(model or ""))
            while len(self._pending) > self._max_runs:
                self._pending.pop(next(iter(self._pending)))

    def observe_usage(
        self,
        request_id: object,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int | None = None,
    ) -> ObservedProviderTurn | None:
        """把真实 usage 与同一次请求的 wire 配对,成为本 Run 的最新观测。

        没有配对的 wire(没走过 :meth:`record_wire`)就**不记**:hidden 会
        变成整条 prompt,下一轮直接误伤。宁可没有观测。
        """

        key = str(getattr(request_id, "value", request_id) or "")
        run = run_key(request_id)
        if not key or not run:
            return None
        # 取 pending 与写 turns 必须在同一个临界区里: 中间放开锁, 两次并发的
        # observe_usage 就可能让**旧**的一轮后写、覆盖掉新的观测。
        with self._lock:
            recorded = self._pending.pop(key, None)
            if recorded is None:
                return None
            wire, model = recorded
            turn = ObservedProviderTurn(
                wire_tokens=wire,
                input_tokens=max(0, int(input_tokens)),
                output_tokens=max(0, int(output_tokens)),
                reasoning_tokens=(
                    None if reasoning_tokens is None else max(0, int(reasoning_tokens))
                ),
            )
            slot = (run, model)
            self._turns.pop(slot, None)
            self._turns[slot] = turn
            while len(self._turns) > self._max_runs:
                self._turns.pop(next(iter(self._turns)))
        return turn

    def last(self, request_id: object, *, model: object = "") -> ObservedProviderTurn | None:
        with self._lock:
            return self._turns.get((run_key(request_id), str(model or "")))

    def carry_tokens(self, request_id: object, *, model: object = "") -> int:
        turn = self.last(request_id, model=model)
        return 0 if turn is None else turn.carry_tokens

    def discard_wire(self, request_id: object) -> None:
        """撤回一条还没配对的 wire 记录(请求最终没有发出)。"""

        key = str(getattr(request_id, "value", request_id) or "")
        with self._lock:
            self._pending.pop(key, None)


_DEFAULT_LEDGER = ObservedInputCarryLedger()


def default_observed_input_carry_ledger() -> ObservedInputCarryLedger:
    """生产里没有注入点,这条进程级单例就是账本本身。"""

    return _DEFAULT_LEDGER


def _text_tokens(value: object) -> int:
    from deskpet.sdk_adapters.context_partitions import text_tokens

    return text_tokens(value)


def wire_message_tokens(messages: Iterable[Mapping[str, object]]) -> int:
    """已装配好的 wire ``messages`` 数组的 token 量。

    与装配期估算的口径**故意保持一致**(同一个 ``text_tokens``),差别只有
    一处、也正是这个模块存在的理由:这里的 ``tool_calls`` 是
    ``_wire_messages`` 补回来的**真实 arguments**,装配期看不到它。
    """

    total = 0
    for message in messages or ():
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += _text_tokens(content)
        elif content is not None:
            total += _text_tokens(_render(content))
        for call in message.get("tool_calls") or ():
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            total += _text_tokens(function.get("name") or "")
            total += _text_tokens(function.get("arguments") or "")
            # ``{"id":…,"type":"function","function":{…}}`` 的定长信封,与
            # context_partitions.WIRE_TOOL_SPEC_OVERHEAD_TOKENS 同一档。
            # **这 8 个是偏低的**: 光 ``{"id":"call_0_<32 hex>","type":"function",
            # "function":{"name":"","arguments":""}}`` 就 ~20 个 token。保持 8
            # 是刻意的 —— 这条线要的是**下界**, 信封宁可少算; 真正的量级(逐轮
            # 几 K 的 arguments)在上面两行里, 不在信封里。
    return total


def _render(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def wire_request_tokens(
    payload: Mapping[str, object],
    *,
    tool_specs: Sequence[object] = (),
) -> int:
    """整条物理请求的 wire token 量:messages(含补回的 tool_calls)+ tools。"""

    from deskpet.sdk_adapters.context_partitions import tool_schema_tokens

    messages = payload.get("messages") if isinstance(payload, Mapping) else ()
    total = wire_message_tokens(messages or ())
    return total + tool_schema_tokens(tool_specs or ())


def effective_budget_for_window(window_tokens: object) -> int:
    from deskpet.sdk_adapters.context_partitions import (
        effective_input_budget,
        window_tokens_for,
    )

    return effective_input_budget(window_tokens_for(window_tokens, None))


def window_tokens_from_metadata(metadata: Mapping[str, object] | None) -> int | None:
    """请求**自己**带的窗口(``budget.context_window`` / ``context_window``)。

    与 ``context_authority._resolve_window_tokens`` 同一批 key。一条请求要是
    自称绑在某个窗口上,那就是它被装配时用的那个窗口,比适配器构造时解析出来
    的型号默认值更近。第 9 次的 239 组真机配对里 ``ProviderRequest.metadata``
    **全是空的**(SDK 目前不往这里写),所以这条路径今天只在测试与未来的写入
    方生效——但它必须排在型号默认值前面,否则等写入方出现的那天,闸门会拿一个
    与装配期不同的窗口去判。
    """

    if not isinstance(metadata, Mapping):
        return None
    budget = metadata.get("budget")
    if isinstance(budget, Mapping) and budget.get("context_window"):
        with_budget = budget["context_window"]
        try:
            return int(with_budget)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    scalar = metadata.get("context_window")
    if scalar:
        try:
            return int(scalar)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return None


def resolve_window_tokens(model_id: object) -> int | None:
    """型号绑定的上下文窗口,含用户 ``model_overrides.toml`` 的全局层。

    解析失败一律返回 ``None``:窗口未知时这道闸门**不判**,把决定权留给
    装配期(它有自己的兜底档位)。宁可不拦,也不要凭猜的窗口拦。
    """

    name = str(model_id or "").strip().lower()
    if "/" in name:
        name = name.split("/", 1)[1]
    if not name:
        return None
    try:
        from llm.model_info import BUILTIN, resolve

        if name not in BUILTIN:
            return None
        return int(resolve(name).context_window)
    except Exception:  # noqa: BLE001 — 元数据永远不该打断发送路径
        return None


def check_wire_input_budget(
    *,
    request_id: object,
    payload: Mapping[str, object],
    tool_specs: Sequence[object] = (),
    window_tokens: object = None,
    request_metadata: Mapping[str, object] | None = None,
    model_id: object = None,
    ledger: ObservedInputCarryLedger | None = None,
) -> dict[str, int]:
    """物理发出之前的最后一道闸门。返回本次的实测账,越界则抛。

    窗口取不到 → 直接放行(见 :func:`resolve_window_tokens`)。没有同 (Run, 型号)
    的观测 → ``carry=0``,退化成「只看 wire」。观测存在时,carry 按本轮 payload
    相对上一轮的收缩比打折(见 :meth:`ObservedProviderTurn.carry_for_wire`)。
    """

    window = window_tokens_from_metadata(request_metadata) or window_tokens
    if not window:
        return {}
    effective = effective_budget_for_window(window)
    wire = wire_request_tokens(payload, tool_specs=tool_specs)
    model = model_id if model_id is not None else payload.get("model")
    model = str(model or "")
    book = ledger if ledger is not None else default_observed_input_carry_ledger()
    observed = book.last(request_id, model=model)
    carry = 0 if observed is None else observed.carry_for_wire(wire)
    floor = wire + carry
    facts = {
        "wire_input_tokens": wire,
        "observed_carry_tokens": carry,
        "measured_input_floor": floor,
        "effective_input_budget": effective,
        "window_tokens": int(window),
        "provider_turn_ordinal": provider_turn_ordinal(request_id),
        "observed_input_tokens": 0 if observed is None else observed.input_tokens,
        "observed_output_tokens": 0 if observed is None else observed.output_tokens,
        "observed_hidden_tokens": 0 if observed is None else observed.hidden_tokens,
        # 打折前的 carry: 与 observed_carry_tokens 不等就是「上一轮之后装配期
        # 裁过史」, 日志/回执里一眼能看出这次为什么没拦(或拦得比上一轮松)。
        "observed_carry_before_trim": 0 if observed is None else observed.carry_tokens,
        "observed_wire_tokens": 0 if observed is None else observed.wire_tokens,
    }
    book.record_wire(request_id, wire, model=model)
    if floor > effective:
        # 这一次不会发出去, 所以也不会有 usage 来配对: 立刻把 pending 收回,
        # 不给账本留一条永远等不到响应的悬挂记录。
        book.discard_wire(request_id)
        # 只有数字,没有任何 payload——与 sdk_context_budget_exceeded 同一体例,
        # a6_verify 按这一行判据统计。
        _LOG.warning(
            "sdk_provider_wire_input_budget_exceeded floor=%d effective=%d "
            "wire=%d carry=%d carry_before_trim=%d observed_input=%d "
            "observed_output=%d observed_hidden=%d observed_wire=%d "
            "window=%d ordinal=%d",
            floor,
            effective,
            wire,
            carry,
            facts["observed_carry_before_trim"],
            facts["observed_input_tokens"],
            facts["observed_output_tokens"],
            facts["observed_hidden_tokens"],
            facts["observed_wire_tokens"],
            facts["window_tokens"],
            facts["provider_turn_ordinal"],
        )
        raise WireInputBudgetExceeded(**facts)
    return facts


__all__ = [
    "ObservedInputCarryLedger",
    "ObservedProviderTurn",
    "WireInputBudgetExceeded",
    "check_wire_input_budget",
    "default_observed_input_carry_ledger",
    "effective_budget_for_window",
    "provider_turn_ordinal",
    "resolve_window_tokens",
    "run_key",
    "window_tokens_from_metadata",
    "wire_message_tokens",
    "wire_request_tokens",
]
