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

事件 Y(HM-TO-A6 第 10a 次,2026-09-09)推翻了上面第 1 条的**归因**:那段
``reasoning_content`` 从来就在 Host 手里 ——
:meth:`...provider._ProductOpenAICompatibleProvider._message_payload` 会把
消息元数据 ``provider_reasoning_content`` 逐字写回 wire payload 的
``reasoning_content`` 字段(落库的 canonical ``request_json`` 里没有它,所以
之前只在「计费 − wire」的残差里看得见)。回灌的是 **Host 自己**,不是中转站。

真机探针(2026-09-09,``deepseek-v4-flash``,tiny 请求)钉死三件事:

* 回传的 ``reasoning_content`` **按普通输入文本计费**:同一条 5 步工具循环上
  给 5 条 assistant 各挂一块 ~772 token 的文本,``prompt_tokens`` 718 → 4580
  (+772.4/块);把同样的文本挂到 ``content`` 上是 +780.0/块 —— 两者同一量纲。
* **省略它不是协议错误**:整条工具循环丢掉全部 ``reasoning_content``、或只留
  最后一条,都是 HTTP 200,5 步循环照样走完并给出正确答案。
* ``thinking={"type":"disabled"}`` 这个端点**真的支持**:usage 里
  ``reasoning_tokens`` 消失、prompt 从 4580 掉到 591(输入侧的
  ``reasoning_content`` 连计费都不进),工具调用照常。

所以这一块质量既**量得到**也**裁得掉**:``wire_message_tokens`` 现在直接
把它计进 wire(精确记账),预算不够时按契约由老到新丢掉回传
(``reasoning_relay_dropped``),carry 只留给真正看不见的那部分残差。

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
中转站都给)才退回 ``output_tokens`` —— 那是保守方向,宁可多算。**这句被事件
W-b 收窄了**:「没给计数」不一定等于「中转站不报」,见下。

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

事件 W-b(HM-TO-A6 第 10 次尝试 10 的第 17 轮,2026-09-09)推翻上面
「``usage`` 没给 ``reasoning_tokens`` 时才退回 ``output_tokens`` —— 那是保守
方向」的一个反例:``reasoning_mode = "fast"`` 把 ``thinking={"type":"disabled"}``
发上去之后,这一轮**本来就没有思考** —— ``usage`` 里没有 ``reasoning_tokens``
不是「中转站不报」,而是「没有这块质量」。退回 ``output_tokens`` 于是把上一轮的
``tool_calls.arguments``(4860 token,回灌的 18 KB 目标文本)当成隐藏质量,而
那 4860 个 token **这一轮已经在 wire 里**(``_wire_messages`` 补回的正是它):
``floor=26857`` 越过 ``effective=26752``,超出 **105** —— 一个真的装得下的请求
(``wire=21997``)被这道终局闸门打死。

所以 ``new_mass`` 的取数分三档,逐次记进回执的 ``carry_basis``:

* ``reasoning_tokens`` —— ``usage`` 给了计数,按计数。唯一的实测档。
* ``no_reasoning`` —— 这一轮**确实没有** reasoning:请求发的是
  ``thinking={"type":"disabled"}``,**或者** ``usage`` 没给计数**且**响应消息里
  也没有 ``reasoning_content``。``carry = hidden`` only(本例 hidden = 计费 −
  已记账的 wire ≈ 0),不加任何新质量。
* ``output_fallback`` —— 响应**有** ``reasoning_content`` 却没有计数(真正的
  「中转站不报」)。只有这一档保留 ``output_tokens`` 的保守估算。

判据是「这一轮有没有思考」,不是「``usage`` 里有没有那个 key」:请求侧的
thinking 开关与响应侧的 ``reasoning_content`` 是两条独立证据,任一条说「没有」,
就没有可加的新质量。给一个不存在的东西记一笔账不叫保守,叫算错 —— 而这道闸门
一响,这次尝试就结束了。
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

#: wire payload 上关掉思考的那个私有字段(``reasoning_wire_fields`` 写的就是它)。
_THINKING_KEY = "thinking"
_THINKING_DISABLED = "disabled"

#: ``carry`` 里「上一轮新增质量」那一项的取数依据(事件 W-b)。进回执与日志,
#: 一眼看得出这次的 carry 是实测、是零、还是保守估算。
CARRY_BASIS_REASONING_TOKENS = "reasoning_tokens"
CARRY_BASIS_NO_REASONING = "no_reasoning"
CARRY_BASIS_OUTPUT_FALLBACK = "output_fallback"
#: 同 (Run, 型号) 上根本没有观测 —— carry 恒为 0, 三档一个都没走。
CARRY_BASIS_NO_OBSERVATION = "no_observation"


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

    def __init__(self, **diagnostics: object) -> None:
        super().__init__(public_message=WireInputBudgetExceeded.error_code)
        # 数字一律收敛成 int; 只有 ``carry_basis`` 这类**判据名**是字符串
        # (事件 W-b)—— 回执里光有数字看不出这次的 carry 是怎么来的。
        self.diagnostics: dict[str, object] = {
            key: value if isinstance(value, str) else int(value)  # type: ignore[arg-type]
            for key, value in diagnostics.items()
        }

    def __str__(self) -> str:
        return WireInputBudgetExceeded.error_code


@dataclass(frozen=True, slots=True)
class ObservedProviderTurn:
    """某一轮真实发生过的 (Host 量得到的 wire, provider 计费) 配对。"""

    wire_tokens: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None
    #: 那一轮 payload 里 Host **自己量到的**回传 reasoning 文本(事件 Y)。
    #: 有了它,``hidden_tokens`` 才真的只剩「结构上看不见」的残差,
    #: 而不再把 Host 自己写上去的那块也算成隐藏质量。
    reasoning_relay_tokens: int = 0
    #: 那一轮的**响应**里真的带回了 ``reasoning_content``(事件 W-b)。
    #: ``usage`` 没给 ``reasoning_tokens`` 时,这一条是「到底有没有思考」的
    #: 第二条独立证据 —— 没有它,``output_tokens`` 的保守回退会把上一轮的
    #: ``tool_calls.arguments`` 再算一遍。
    reasoning_content_seen: bool = False
    #: 那一轮的**请求**是带着 ``thinking={"type":"disabled"}`` 发出去的。
    #: 这是请求侧的证据: 端点被明确关掉了思考, 响应里不可能有 reasoning。
    reasoning_disabled: bool = False

    @property
    def hidden_tokens(self) -> int:
        """provider 计费里 Host 结构上看不见的那部分(实测,非估算)。"""

        return max(0, int(self.input_tokens) - int(self.wire_tokens))

    @property
    def carry_basis(self) -> str:
        """``new_mass_tokens`` 这一次按哪一档取数(事件 W-b,见模块 docstring)。

        ``usage`` 给了计数就按计数;没给,就看**这一轮到底有没有思考** ——
        请求关掉了 thinking、或者响应里连 ``reasoning_content`` 都没有,那就是
        真的没有(``no_reasoning``);只有「有思考文本、缺计数」才是中转站不报,
        才轮得到 ``output_tokens`` 的保守回退。
        """

        if self.reasoning_tokens is not None:
            return CARRY_BASIS_REASONING_TOKENS
        if self.reasoning_disabled or not self.reasoning_content_seen:
            return CARRY_BASIS_NO_REASONING
        return CARRY_BASIS_OUTPUT_FALLBACK

    @property
    def new_mass_tokens(self) -> int:
        """这一轮的产出里,下一轮 payload 有可能量不到的那部分(上界)。

        正文与 ``tool_calls.arguments`` 下一轮都会原样回到 payload 里,而
        :func:`wire_request_tokens` 量的就是那个 payload —— 再加一次就是重复
        计价。剩下的只有 ``reasoning_content``。

        事件 W-b:``reasoning_tokens`` 缺席**不一定**等于「中转站不报」——
        ``thinking={"type":"disabled"}`` 的那一轮压根没有思考。三档见
        :attr:`carry_basis`;``no_reasoning`` 那一档新质量恒为 0,
        ``carry`` 只剩 :attr:`hidden_tokens`。

        事件 Y 之后这只是**上界**:下一轮 payload 里凡是 Host 自己量到的回传
        (``reasoning_relay_tokens`` 的增量)、或 Host 按契约主动丢掉的那部分,
        都要从这里扣掉 —— 见 :meth:`unmeasured_new_mass`。全扣完还剩的,才是
        「计了费、谁也量不到」的那一块(端点自己回灌时就落在这里)。
        """

        basis = self.carry_basis
        if basis == CARRY_BASIS_REASONING_TOKENS:
            return max(0, int(self.reasoning_tokens or 0))
        if basis == CARRY_BASIS_NO_REASONING:
            return 0
        return max(0, int(self.output_tokens))

    def unmeasured_new_mass(self, accounted_tokens: int) -> int:
        """``new_mass`` 里没被本轮 payload 交代掉的那部分。"""

        return max(0, self.new_mass_tokens - max(0, int(accounted_tokens)))

    def carry_before_trim(self, accounted_tokens: int = 0) -> int:
        """下一轮 prompt 至少会带着的隐藏质量(未按裁史打折)。"""

        return self.hidden_tokens + self.unmeasured_new_mass(accounted_tokens)

    @property
    def carry_tokens(self) -> int:
        """``accounted=0`` 的 carry —— 保留给只关心上界的调用方。"""

        return self.carry_before_trim(0)

    def discount_for_wire(self, carry: int, wire_tokens: int) -> int:
        """按本轮 payload 相对上一轮的收缩比给 carry 打折。

        payload 变小 = 装配期裁掉了历史(或把结果换成了页引用),那几轮
        assistant 消息背着的隐藏质量也随之离开了 prompt。这是这道闸门唯一
        能看见的裁史信号,见模块 docstring。
        """

        carry = max(0, int(carry))
        previous = max(0, int(self.wire_tokens))
        current = max(0, int(wire_tokens))
        if previous <= 0 or current >= previous:
            return carry
        return carry * current // previous

    def carry_for_wire(self, wire_tokens: int, *, accounted_tokens: int = 0) -> int:
        """打折后的 carry(``accounted_tokens`` 见 :meth:`unmeasured_new_mass`)。"""

        return self.discount_for_wire(self.carry_before_trim(accounted_tokens), wire_tokens)


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
        # (wire, model, 已量到的回传, 这次请求是否关掉了 thinking)
        self._pending: dict[str, tuple[int, str, int, bool]] = {}

    def record_wire(
        self,
        request_id: object,
        wire_tokens: int,
        *,
        model: object = "",
        reasoning_relay_tokens: int = 0,
        reasoning_disabled: bool = False,
    ) -> None:
        """记下「这一次物理请求 Host 量到的 wire」,等 usage 回来配对。

        单独一步而不是等 usage 一起写,是因为这两个数字**只有在这里**同时
        成立:wire 只有装配 payload 的那一刻知道,usage 只有响应回来才知道,
        而 hidden = usage.input - wire 必须是**同一次请求**的差值。

        ``model`` 一起记下来:观测按 ``(Run, 型号)`` 存,同一条 Run 中途换了
        绑定型号(不同 tokenizer、不同 reasoning 行为)时不会拿旧型号的 carry
        判新型号的请求。

        ``reasoning_disabled`` 是**请求侧**的事实(payload 上的
        ``thinking={"type":"disabled"}``),只有装配 payload 的这一刻知道,所以
        和 wire 一起记 —— 它决定下一轮 ``carry`` 走哪一档(事件 W-b)。
        """

        key = str(getattr(request_id, "value", request_id) or "")
        if not key:
            return
        with self._lock:
            self._pending[key] = (
                max(0, int(wire_tokens)),
                str(model or ""),
                max(0, int(reasoning_relay_tokens)),
                bool(reasoning_disabled),
            )
            while len(self._pending) > self._max_runs:
                self._pending.pop(next(iter(self._pending)))

    def observe_usage(
        self,
        request_id: object,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int | None = None,
        reasoning_content_seen: bool = False,
    ) -> ObservedProviderTurn | None:
        """把真实 usage 与同一次请求的 wire 配对,成为本 Run 的最新观测。

        没有配对的 wire(没走过 :meth:`record_wire`)就**不记**:hidden 会
        变成整条 prompt,下一轮直接误伤。宁可没有观测。

        ``reasoning_content_seen`` 是**响应侧**的事实:这次回来的 assistant
        消息里到底有没有思考文本。``usage`` 缺 ``reasoning_tokens`` 时,它和
        ``record_wire`` 记下的 thinking 开关一起决定 carry 走哪一档
        (事件 W-b)。默认 ``False`` = 没观测到思考。
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
            wire, model, relayed, disabled = recorded
            turn = ObservedProviderTurn(
                wire_tokens=wire,
                input_tokens=max(0, int(input_tokens)),
                output_tokens=max(0, int(output_tokens)),
                reasoning_tokens=(
                    None if reasoning_tokens is None else max(0, int(reasoning_tokens))
                ),
                reasoning_relay_tokens=relayed,
                reasoning_content_seen=bool(reasoning_content_seen),
                reasoning_disabled=disabled,
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


#: wire 上那个私有字段的名字。Host 的 ``_message_payload`` 把消息元数据
#: ``provider_reasoning_content`` 写到这里,DeepSeek thinking 模式的工具循环
#: 靠它认出上一轮的思考(事件 Y)。
REASONING_CONTENT_KEY = "reasoning_content"


def thinking_disabled(payload: Mapping[str, object]) -> bool:
    """这条 payload 是不是带着 ``thinking={"type":"disabled"}`` 发出去的。

    读的是**将要发出的那份 payload**(``_request_payload`` 已经
    ``payload.update(self._reasoning_wire)``),不是会话的 ``model_params`` ——
    量的与发的必须是同一份。落库的 canonical ``request_json`` 里没有这个字段,
    所以只有这里看得见(事件 Y 的 ``reasoning_wire_fields`` 写的就是它)。

    关掉思考 = 这一轮不可能产出 reasoning,``usage`` 里缺 ``reasoning_tokens``
    因此不是「中转站不报」(事件 W-b)。
    """

    if not isinstance(payload, Mapping):
        return False
    thinking = payload.get(_THINKING_KEY)
    if not isinstance(thinking, Mapping):
        return False
    return str(thinking.get("type") or "").strip().lower() == _THINKING_DISABLED


def message_reasoning_tokens(message: Mapping[str, object]) -> int:
    """一条 wire 消息上回传的 ``reasoning_content`` 的 token 量(0 = 没有)。"""

    if not isinstance(message, Mapping):
        return 0
    value = message.get(REASONING_CONTENT_KEY)
    if not isinstance(value, str) or not value:
        return 0
    return _text_tokens(value)


def wire_reasoning_tokens(messages: Iterable[Mapping[str, object]]) -> int:
    """整个 wire ``messages`` 数组上回传的 reasoning 文本的 token 量。

    事件 Y:这块质量以前落在「计费 − wire」的残差里被当成隐藏质量,其实
    Host 自己就有原文,所以直接量。真机探针实测它**按普通输入文本计费**
    (+772.4 token/块 vs 同样文本挂在 ``content`` 上的 +780.0/块)。
    """

    return sum(message_reasoning_tokens(message) for message in messages or ())


def wire_message_tokens(messages: Iterable[Mapping[str, object]]) -> int:
    """已装配好的 wire ``messages`` 数组的 token 量。

    与装配期估算的口径**故意保持一致**(同一个 ``text_tokens``),差别有
    两处、也正是这个模块存在的理由:

    * 这里的 ``tool_calls`` 是 ``_wire_messages`` 补回来的**真实 arguments**
      (事件 K),装配期看不到它;
    * 这里的 ``reasoning_content`` 是 ``_message_payload`` 回灌的上一轮思考
      (事件 Y),它同样只存在于 wire payload 上,落库的 ``request_json``
      里没有。
    """

    total = 0
    for message in messages or ():
        if not isinstance(message, Mapping):
            continue
        total += message_reasoning_tokens(message)
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


#: ``ProviderReasoningCapability.preserve_reasoning`` 的三档在这道闸门里的语义。
#: 只决定「哪些回传是**无条件**可丢的」,预算压力下的逐条丢弃对三档都一样。
_PRESERVE_ALL_TURNS = "all_turns"
_PRESERVE_NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class ReasoningRelayDecision:
    """这一次物理请求实际执行的回传策略(进回执与日志)。"""

    kept_tokens: int = 0
    dropped_tokens: int = 0
    dropped_messages: int = 0
    contract_dropped_messages: int = 0


def reasoning_relay_order(
    messages: Sequence[Mapping[str, object]],
    *,
    preserve: str = "tool_loop",
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """``(契约上无条件可丢的下标, 只在预算压力下才丢的下标)``,都由老到新。

    DeepSeek thinking 模式 + function calling 的契约(真机探针 2026-09-09,
    见模块 docstring)只要求**当前这一轮工具循环**的 assistant 消息带回
    ``reasoning_content`` —— 声明表 ``provider_capabilities`` 把它记作
    ``preserve_reasoning="tool_loop"``。所以:

    * ``tool_loop``(以及未声明的型号,适配器按 ``tool_loop`` 传):最后一条
      ``user`` 消息**之前**的回传契约上从来不需要 → 无条件丢;循环内的按
      由老到新排队,只有预算不够时才丢。
    * ``all_turns``(kimi 系):没有无条件可丢的,预算不够时才由老到新丢 ——
      「丢一部分回传」永远好过「这条 Run 终局失败」。
    * ``not_required``:全部无条件可丢。
    """

    rows = list(messages or ())
    last_user = -1
    for index, message in enumerate(rows):
        if isinstance(message, Mapping) and str(message.get("role") or "") == "user":
            last_user = index
    carriers = [
        index
        for index, message in enumerate(rows)
        if message_reasoning_tokens(message) > 0
    ]
    mode = str(preserve or "").strip().lower()
    if mode == _PRESERVE_ALL_TURNS:
        return (), tuple(carriers)
    if mode == _PRESERVE_NOT_REQUIRED:
        return tuple(carriers), ()
    return (
        tuple(index for index in carriers if index <= last_user),
        tuple(index for index in carriers if index > last_user),
    )


def _drop_reasoning(message: object) -> int:
    """就地摘掉一条消息的 ``reasoning_content``,返回**真的**省下的 token。

    不是可变 ``dict`` 就返回 0 —— 记一笔没发生的节省会让 carry 少算,
    而这条线必须是下界。
    """

    if not isinstance(message, dict):
        return 0
    dropped = message_reasoning_tokens(message)
    if dropped:
        message.pop(REASONING_CONTENT_KEY, None)
    return dropped


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


def _accounted_new_mass(
    observed: ObservedProviderTurn | None,
    *,
    wire_reasoning: int,
    dropped_tokens: int,
) -> int:
    """上一轮的 reasoning 里,**本轮 payload 交代得清楚**的那部分。

    两条来源加起来:本轮 wire 上比上一轮多出来的回传文本(Host 量得到,已经
    计进 ``wire``),以及 Host 按契约主动丢掉、因此**不会**出现在 prompt 里的
    那部分。两者都不该再进 carry —— 前者会被算两遍,后者根本不存在。
    """

    if observed is None:
        return 0
    growth = max(0, int(wire_reasoning) - int(observed.reasoning_relay_tokens))
    return growth + max(0, int(dropped_tokens))


def check_wire_input_budget(
    *,
    request_id: object,
    payload: Mapping[str, object],
    tool_specs: Sequence[object] = (),
    window_tokens: object = None,
    request_metadata: Mapping[str, object] | None = None,
    model_id: object = None,
    ledger: ObservedInputCarryLedger | None = None,
    reasoning_relay: ReasoningRelayDecision | None = None,
) -> dict[str, object]:
    """物理发出之前的最后一道闸门。返回本次的实测账,越界则抛。

    窗口取不到 → 直接放行(见 :func:`resolve_window_tokens`)。没有同 (Run, 型号)
    的观测 → ``carry=0``,退化成「只看 wire」。观测存在时,carry 按本轮 payload
    相对上一轮的收缩比打折(见 :meth:`ObservedProviderTurn.carry_for_wire`)。

    事件 Y 之后 ``wire`` 已经**直接**含回传的 ``reasoning_content``,所以
    carry 里只剩「计了费、Host 结构上量不到」的那点残差;``reasoning_relay``
    把本轮实际执行的回传策略带进回执。payload 上一个 ``reasoning_content``
    都没有时(夹具回放、非 thinking 端点),这两项都是 0,这条线的算术与事件 W
    逐 token 相同。

    事件 W-b:回执多一条 ``carry_basis``,说明 carry 里「上一轮新增质量」那一项
    是实测(``reasoning_tokens``)、是零(``no_reasoning``)、还是保守估算
    (``output_fallback``);没有观测时是 ``no_observation``。
    """

    window = window_tokens_from_metadata(request_metadata) or window_tokens
    if not window:
        return {}
    effective = effective_budget_for_window(window)
    wire = wire_request_tokens(payload, tool_specs=tool_specs)
    messages = payload.get("messages") if isinstance(payload, Mapping) else ()
    wire_reasoning = wire_reasoning_tokens(messages or ())
    relay = reasoning_relay or ReasoningRelayDecision(kept_tokens=wire_reasoning)
    model = model_id if model_id is not None else payload.get("model")
    model = str(model or "")
    book = ledger if ledger is not None else default_observed_input_carry_ledger()
    observed = book.last(request_id, model=model)
    accounted = _accounted_new_mass(
        observed, wire_reasoning=wire_reasoning, dropped_tokens=relay.dropped_tokens
    )
    carry_before_trim = 0 if observed is None else observed.carry_before_trim(accounted)
    carry = 0 if observed is None else observed.discount_for_wire(carry_before_trim, wire)
    floor = wire + carry
    facts: dict[str, object] = {
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
        "observed_carry_before_trim": carry_before_trim,
        "observed_wire_tokens": 0 if observed is None else observed.wire_tokens,
        "observed_reasoning_relay_tokens": (
            0 if observed is None else observed.reasoning_relay_tokens
        ),
        # 事件 Y 回执三件套: 这一轮 wire 上**实际带走**的回传、被丢掉的、
        # 以及其中有多少条是契约上根本不需要的(工具循环之外)。
        "reasoning_relay_tokens": wire_reasoning,
        "reasoning_relay_dropped": max(0, int(relay.dropped_tokens)),
        "reasoning_relay_dropped_messages": max(0, int(relay.dropped_messages)),
        "reasoning_relay_contract_dropped_messages": max(
            0, int(relay.contract_dropped_messages)
        ),
        # 事件 W-b: carry 的「新增质量」那一项按哪一档取的数。
        "carry_basis": (
            CARRY_BASIS_NO_OBSERVATION if observed is None else observed.carry_basis
        ),
    }
    book.record_wire(
        request_id,
        wire,
        model=model,
        reasoning_relay_tokens=wire_reasoning,
        # 事件 W-b: 这次请求关没关思考, 只有这份 payload 知道 —— 记下来,
        # 下一轮 usage 回来配对时才判得出「缺计数」到底是哪一档。
        reasoning_disabled=thinking_disabled(payload),
    )
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
            "window=%d ordinal=%d reasoning_relay=%d reasoning_relay_dropped=%d "
            "reasoning_relay_dropped_messages=%d carry_basis=%s",
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
            facts["reasoning_relay_tokens"],
            facts["reasoning_relay_dropped"],
            facts["reasoning_relay_dropped_messages"],
            facts["carry_basis"],
        )
        raise WireInputBudgetExceeded(**facts)
    return facts


def _measured_floor(
    observed: ObservedProviderTurn | None,
    *,
    wire: int,
    wire_reasoning: int,
    dropped_tokens: int,
) -> int:
    """与 :func:`check_wire_input_budget` **同一条**算术,给裁剪循环用。"""

    if observed is None:
        return int(wire)
    accounted = _accounted_new_mass(
        observed, wire_reasoning=wire_reasoning, dropped_tokens=dropped_tokens
    )
    return int(wire) + observed.discount_for_wire(
        observed.carry_before_trim(accounted), wire
    )


def enforce_wire_input_budget(
    *,
    request_id: object,
    payload: Mapping[str, object],
    tool_specs: Sequence[object] = (),
    window_tokens: object = None,
    request_metadata: Mapping[str, object] | None = None,
    model_id: object = None,
    ledger: ObservedInputCarryLedger | None = None,
    reasoning_preserve: str = "tool_loop",
) -> dict[str, object]:
    """事件 Y:先按契约把 ``reasoning_content`` 的回传收敛到必要范围,再判闸门。

    两步是**同一条**算术(:func:`_measured_floor` 与
    :func:`check_wire_input_budget` 共用),所以「丢到刚好装得下就停」是确定性的:

    1. 契约上无条件不需要的回传(见 :func:`reasoning_relay_order`)先丢干净 ——
       这不是省钱的机会主义,是这些字节本来就不该在 prompt 里。
    2. 还是越界,就在当前工具循环内**由老到新**继续丢,一旦 floor 落回
       ``effective_input_budget`` 以内立刻停手:最近一轮的思考留到最后,
       DeepSeek thinking 模式对当前这一步的连贯性依赖的正是它。

    全丢完仍然越界,就回到事件 W 的终局行为:
    ``sdk_provider_wire_input_budget_exceeded``,一个字节都不发。那时超的已经
    不是回传,而是受保护的正文本身,只能由装配期(强制分页/裁史)去解决。

    ``payload["messages"]`` 会被**就地**改写(只摘掉 ``reasoning_content`` 键)
    —— 它是 ``_request_payload`` 刚装配出来的本地对象,这里改的就是真正要发出去
    的那份,不存在「量的和发的不是同一份」的缝。
    """

    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    rows = messages if isinstance(messages, list) else []
    free, pressured = reasoning_relay_order(rows, preserve=reasoning_preserve)
    dropped_tokens = 0
    dropped_messages = 0
    contract_dropped = 0
    for index in free:
        saved = _drop_reasoning(rows[index])
        if saved:
            dropped_tokens += saved
            dropped_messages += 1
            contract_dropped += 1

    window = window_tokens_from_metadata(request_metadata) or window_tokens
    if window and pressured:
        effective = effective_budget_for_window(window)
        model = model_id if model_id is not None else payload.get("model")
        book = ledger if ledger is not None else default_observed_input_carry_ledger()
        observed = book.last(request_id, model=str(model or ""))
        wire = wire_request_tokens(payload, tool_specs=tool_specs)
        wire_reasoning = wire_reasoning_tokens(rows)
        for index in pressured:
            if (
                _measured_floor(
                    observed,
                    wire=wire,
                    wire_reasoning=wire_reasoning,
                    dropped_tokens=dropped_tokens,
                )
                <= effective
            ):
                break
            saved = _drop_reasoning(rows[index])
            if not saved:
                continue
            wire -= saved
            wire_reasoning -= saved
            dropped_tokens += saved
            dropped_messages += 1

    decision = ReasoningRelayDecision(
        kept_tokens=wire_reasoning_tokens(rows),
        dropped_tokens=dropped_tokens,
        dropped_messages=dropped_messages,
        contract_dropped_messages=contract_dropped,
    )
    if dropped_messages:
        # 只有计数, 没有任何 payload —— 与 tool_call_arguments 那条同一体例。
        _LOG.info(
            "sdk_provider_reasoning_relay_trimmed dropped_messages=%d "
            "contract_dropped_messages=%d dropped_tokens=%d kept_tokens=%d "
            "ordinal=%d preserve=%s",
            decision.dropped_messages,
            decision.contract_dropped_messages,
            decision.dropped_tokens,
            decision.kept_tokens,
            provider_turn_ordinal(request_id),
            str(reasoning_preserve or ""),
        )
    return check_wire_input_budget(
        request_id=request_id,
        payload=payload,
        tool_specs=tool_specs,
        window_tokens=window_tokens,
        request_metadata=request_metadata,
        model_id=model_id,
        ledger=ledger,
        reasoning_relay=decision,
    )


__all__ = [
    "CARRY_BASIS_NO_OBSERVATION",
    "CARRY_BASIS_NO_REASONING",
    "CARRY_BASIS_OUTPUT_FALLBACK",
    "CARRY_BASIS_REASONING_TOKENS",
    "ObservedInputCarryLedger",
    "ObservedProviderTurn",
    "REASONING_CONTENT_KEY",
    "ReasoningRelayDecision",
    "WireInputBudgetExceeded",
    "check_wire_input_budget",
    "default_observed_input_carry_ledger",
    "effective_budget_for_window",
    "enforce_wire_input_budget",
    "message_reasoning_tokens",
    "provider_turn_ordinal",
    "reasoning_relay_order",
    "resolve_window_tokens",
    "run_key",
    "thinking_disabled",
    "window_tokens_from_metadata",
    "wire_message_tokens",
    "wire_reasoning_tokens",
    "wire_request_tokens",
]
