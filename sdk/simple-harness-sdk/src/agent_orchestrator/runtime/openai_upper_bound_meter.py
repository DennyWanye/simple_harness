# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""通用 OpenAI 协议模型的上界计数器（AgentOS 迁移第 3 块，2026-10-21）。

没有公开分词器的模型（Luna / Sol 这类）拿不到精确计数，但执行池要一个认证计数器。这里按
"可以多算不可以少算"（用户 2026-09-24 规则）给一个硬上界：

- 把请求按 OpenAI 协议渲染成线路上真正发出的 JSON，按 UTF-8 **字节数**计。字节级 BPE 的每个
  token 至少盖住一个字节，所以 token 数 ≤ 字节数，对任何字节级分词器都成立；JSON 的键名、标点、
  工具说明都在里面，也只会多算。
- 再加线路边际：转发线路会暗加自己的前言（MEETRAN 实测每个请求约 4.2K 缓存 token），本机算
  不出来，按"报告用量 − 本机计数"的最大差值学习，只升不降，落盘在数据目录。起点是
  ``floor``（默认 8192）。

误差边界：输入计数是上界，通常比真实用量大 2～4 倍（英文更多，中文少些），所以预算消耗得更快，
会早一点碰上限；输出按模型报告的真实用量结算。报告缺失的调用记"未知"，不当零（内核已有）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from simple_harness.agents.arp.meter import MeterBinding, PriorBasis, model_limits
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.strict import digest
from simple_harness.contracts import canonical_json
from simple_harness.providers import ProviderRequest
from simple_harness.providers.openai_compatible import openai_chat_request_payload

COUNTER_ID = "openai-chat-byte-upper-bound"
COUNTER_REVISION = 1
BOUND_PROTOCOL = "openai-chat-byte-upper-bound-v1"
DEFAULT_MARGIN_FLOOR = 8192
CERTIFICATION_RULE = (
    "the request is rendered to the exact OpenAI chat JSON body this SDK sends and counted in "
    "UTF-8 bytes: a byte-level BPE token covers at least one byte, so tokens <= bytes for any "
    "byte-level tokenizer; every request is additionally charged a learned relay margin that "
    "starts at the floor and rises to the largest observed (reported - counted) excess plus "
    "headroom, never lowered; output is settled from the provider's reported usage"
)


class RelayMargin:
    """一条线路暗加的前言边际：每个请求都收（不只带工具的），只升不降，可落盘。"""

    def __init__(
        self,
        *,
        floor: int = DEFAULT_MARGIN_FLOOR,
        headroom: int = 256,
        state_path: Path | None = None,
    ) -> None:
        if type(floor) is not int or floor < 0 or type(headroom) is not int or headroom < 0:
            raise ValueError("relay margin floor and headroom must be nonnegative integers")
        self.floor = floor
        self.headroom = headroom
        self.state_path = None if state_path is None else Path(state_path)
        self.value = floor
        self.max_excess: int | None = None
        self.alarms: list[dict[str, int]] = []
        self.observations = 0
        if self.state_path is not None and self.state_path.is_file():
            try:
                saved = json.loads(self.state_path.read_text())
                stored = saved.get("value")
                if type(stored) is int and stored > self.value:
                    self.value = stored
                if type(saved.get("max_excess")) is int:
                    self.max_excess = saved["max_excess"]
            except (OSError, ValueError, AttributeError):
                pass  # 读不到的状态不会降低起点

    def charge(self, request: ProviderRequest) -> int:
        del request  # 前言不看请求内容
        return self.value

    def observe(self, *, reported: int, counted: int) -> bool:
        """一次报告的输入用量对本机计数；边际盖不住的差值抬高边际，记一条告警。"""

        if type(reported) is not int or reported <= 0 or type(counted) is not int:
            return False  # 没有可用的报告（例如回 0 的线路）：什么也学不到
        self.observations += 1
        excess = reported - counted
        self.max_excess = excess if self.max_excess is None else max(self.max_excess, excess)
        if excess <= self.value:
            return False
        self.alarms.append(
            {"reported": reported, "counted": counted, "margin": self.value, "excess": excess}
        )
        self.value = max(self.value, excess + self.headroom)
        self._save()
        return True

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        try:  # 别的进程已经写得更高：不往低写
            saved = json.loads(self.state_path.read_text())
            if type(saved.get("value")) is int and saved["value"] > self.value:
                self.value = saved["value"]
        except (OSError, ValueError, AttributeError):
            pass
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"value": self.value, "max_excess": self.max_excess, "floor": self.floor})
        )
        tmp.replace(self.state_path)


class OpenAIUpperBoundCounter:
    """按字节计的上界计数器；``margin`` 是这条线路的前言边际。"""

    count_mode = "CERTIFIED_UPPER_BOUND"
    requires_prior_output_reserve = False
    tool_schema_mode = "legacy"
    bound_protocol = BOUND_PROTOCOL
    request_counting_protocol = BOUND_PROTOCOL

    def __init__(self, model: str, *, margin: RelayMargin) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("the upper-bound counter needs a model name")
        if not isinstance(margin, RelayMargin):
            raise TypeError("OpenAIUpperBoundCounter needs a RelayMargin")
        self.model = model
        self.margin = margin
        # 身份只含模型：边际是学来的、可配的，不进身份（改起点不该让已有的池起不来）
        self.fingerprint = f"{COUNTER_ID}:v{COUNTER_REVISION}:{model}"
        #: 执行池编号里代表这个模型的一段（不同模型各用各的池）：可读的名字 + 短哈希，
        #: 名字规范化后相撞（gpt-6.1-sol / gpt-6-1-sol）或为空也不会和别的池重名
        readable = "".join(ch if ch.isalnum() else "-" for ch in model.lower()).strip("-")[:40]
        tag = hashlib.sha256(model.encode("utf-8")).hexdigest()[:6]
        self.profile_slug = f"{readable}-{tag}" if readable else tag

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise ValueError("the upper-bound counter counts text only")
        return len(text.encode("utf-8"))

    def exact_input_tokens(self, request: ProviderRequest) -> int:
        """线路上真正发出的请求体的字节数（不含边际）。"""

        if any(not isinstance(message.content, str) for message in request.messages):
            raise ValueError("the upper-bound counter supports text messages only")
        payload = openai_chat_request_payload(
            request, model=self.model, tool_schema_mode=self.tool_schema_mode
        )
        return len(canonical_json(payload).encode("utf-8"))

    def estimate_input_tokens(self, request: ProviderRequest) -> int:
        return self.exact_input_tokens(request) + self.margin.charge(request)

    def count_request_tokens(self, request: ProviderRequest) -> int:
        return self.estimate_input_tokens(request)

    def certification_ref(self) -> Pin:
        body = {
            "rule": CERTIFICATION_RULE,
            "counter_id": COUNTER_ID,
            "counter_revision": COUNTER_REVISION,
            "tokenizer_fingerprint": self.fingerprint,
            "count_mode": self.count_mode,
            "input_limit_scope": "WIRE_ONLY",
        }
        return Pin("receipt", f"meter-certification:{COUNTER_ID}", 0, digest(body))

    def meter_factory(
        self,
        counter: Any,
        *,
        input_limit_tokens: int,
        max_output_tokens: int,
        prior_reserve: Callable[[str], PriorBasis] | None = None,
    ) -> MeterBinding:
        if counter is not self:
            raise TypeError("the upper-bound counter binds only itself")
        if prior_reserve is not None:
            raise TypeError("the endpoint keeps no output beyond the wire; no prior reserve")
        limits = model_limits(
            model=self.model,
            tokenizer=self,
            input_limit_tokens=input_limit_tokens,
            max_output_tokens=max_output_tokens,
            provider_id="openai-compatible",
            counter_id=COUNTER_ID,
            counter_revision=COUNTER_REVISION,
            input_limit_scope="WIRE_ONLY",
            requires_prior_output_reserve=False,
            capability_receipt={
                "kind": "model-capability",
                "model": self.model,
                "input_limit_tokens": input_limit_tokens,
                "max_output_tokens": max_output_tokens,
            },
        )
        return MeterBinding(
            tokenizer=self, model_limits=limits, certification_ref=self.certification_ref()
        )


class MarginLearningProvider:
    """提供方装饰器：每次报告的输入用量都拿来对本机计数，抬高线路边际。"""

    def __init__(self, inner: Any, counter: OpenAIUpperBoundCounter) -> None:
        if not isinstance(counter, OpenAIUpperBoundCounter):
            raise TypeError("MarginLearningProvider needs an OpenAIUpperBoundCounter")
        self._inner = inner
        self._counter = counter

    @property
    def target(self) -> Any:
        return self._inner.target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def invoke(self, request: ProviderRequest, *, cancel: Any) -> Any:
        response = await self._inner.invoke(request, cancel=cancel)
        usage = getattr(response, "usage", None)
        reported = getattr(usage, "input_tokens", None)
        if type(reported) is int and reported > 0:
            try:
                counted = self._counter.exact_input_tokens(request)
            except ValueError:
                return response
            self._counter.margin.observe(reported=reported, counted=counted)
        return response


__all__ = (
    "BOUND_PROTOCOL",
    "COUNTER_ID",
    "DEFAULT_MARGIN_FLOOR",
    "MarginLearningProvider",
    "OpenAIUpperBoundCounter",
    "RelayMargin",
)
