# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""首选模型失败换备用（AgentOS 迁移第 3 块，2026-10-21）。

规则：

- 只换内核本来就当作"确定失败、不计费"的错误：限流（429）与服务端错误（5xx）。超时、断连可能已经
  计费、已经输出了一部分，内核把它们记成"结果不明"并按上界占住预算——这里不换，交给内核。请求被拒
  （4xx）、认证、预算、取消也不换。
- 每个请求最多换一次；换过之后同一次运行（同一个工具循环，按请求号里的运行号）的后续请求直接走
  备用，新运行回到首选。重试只在这里做一次：网关别再重试，否则次数相乘。
- 回执里的 ``model`` 规范成首选的名字——内核按它核对回显、信任用量——实际用的模型、原因、请求号、
  用量另记一份台账（数据目录 ``model-calls.jsonl``），宿主公开读取。已经输出过的内容不会被重放：
  这里只在拿不到回执时换。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from simple_harness.providers import ProviderRequest
from simple_harness.providers.errors import (
    ProviderCancelledError,
    ProviderRateLimitError,
    ProviderServerError,
)

LEDGER_NAME = "model-calls.jsonl"


def run_of(request: ProviderRequest) -> str:
    """请求号 ``<运行号>:provider-turn:<n>`` 里的运行号；没有这个形状就用整个请求号。"""

    raw = str(request.request_id.value)
    return raw.split(":provider-turn:", 1)[0]


def _usage_json(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        name: getattr(usage, name, None)
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_tokens",
            "reasoning_tokens",
        )
    }


def _error_text(error: BaseException) -> str:
    public = getattr(error, "public_message", None)
    return f"{type(error).__name__}: {public or error}"[:300]


class FallbackProvider:
    """首选提供方外面的一层：失败换备用、按运行黏住、回执模型规范化、台账。"""

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        *,
        primary_model: str,
        fallback_model: str,
        ledger_path: Path | None = None,
        sticky: bool = True,
    ) -> None:
        if not primary_model or not fallback_model or primary_model == fallback_model:
            raise ValueError("fallback needs two different model names")
        self._primary = primary
        self._fallback = fallback
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self._ledger_path = None if ledger_path is None else Path(ledger_path)
        self._sticky = sticky
        self._fallen: dict[str, str] = {}  # 运行号 → 换过去的原因
        self.records: list[dict[str, Any]] = []

    @property
    def target(self) -> Any:
        return self._primary.target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)

    @staticmethod
    def should_fall_back(error: BaseException) -> bool:
        """只换确定没计费的失败：限流与服务端错误。超时、断连交给内核记"结果不明"。"""

        if isinstance(error, ProviderCancelledError):
            return False
        return isinstance(error, (ProviderRateLimitError, ProviderServerError))

    def _record(self, body: Mapping[str, Any]) -> None:
        row = {"at": time.time(), **body}
        self.records.append(row)
        if len(self.records) > 1000:
            del self.records[:-1000]
        if self._ledger_path is None:
            return
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 台账写不进去不影响调用；内存里还有

    def _normalised(self, response: Any) -> Any:
        model = getattr(response, "model", None)
        if model is None or model == self.primary_model:
            return response
        return replace(response, model=self.primary_model)

    async def invoke(self, request: ProviderRequest, *, cancel: Any) -> Any:
        run = run_of(request)
        reason = self._fallen.get(run)
        if reason is None:
            try:
                return await self._primary.invoke(request, cancel=cancel)
            except Exception as error:
                if not self.should_fall_back(error):
                    raise
                reason = _error_text(error)
                if self._sticky:
                    self._fallen[run] = reason
                    if len(self._fallen) > 1000:  # 只记最近的运行
                        for stale in list(self._fallen)[:-1000]:
                            self._fallen.pop(stale, None)
        try:
            response = await self._fallback.invoke(request, cancel=cancel)
        except Exception as error:
            self._record(
                {
                    "run": run,
                    "request_id": str(request.request_id.value),
                    "requested_model": self.primary_model,
                    "actual_model": self.fallback_model,
                    "reason": reason,
                    "outcome": "fallback_failed",
                    "error": _error_text(error),
                }
            )
            raise
        self._record(
            {
                "run": run,
                "request_id": str(request.request_id.value),
                "requested_model": self.primary_model,
                "actual_model": getattr(response, "model", None) or self.fallback_model,
                "reason": reason,
                "outcome": "fallback_used",
                "provider_request_id": getattr(response, "provider_request_id", None),
                "usage": _usage_json(getattr(response, "usage", None)),
            }
        )
        return self._normalised(response)


def read_model_calls(path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """台账最后 ``limit`` 行（最新的在后）。"""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, int(limit)) :]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


__all__ = ("LEDGER_NAME", "FallbackProvider", "read_model_calls", "run_of")
