# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""首选失败换备用：只换传输层失败、每请求一次、同一运行黏住、新运行回首选、回执模型规范化、台账。"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.runtime.model_fallback import FallbackProvider, read_model_calls
from simple_harness.contracts import Message, MessageRole
from simple_harness.contracts.identity import RequestId
from simple_harness.providers.base import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from simple_harness.providers.errors import (
    ProviderRequestRejectedError,
    ProviderServerError,
    ProviderTimeoutError,
)


class Stub:
    def __init__(self, model: str, failures: list[Exception] | None = None) -> None:
        self.model = model
        self.failures = list(failures or [])
        self.calls: list[str] = []
        self.target = f"target:{model}"

    async def invoke(self, request, *, cancel):
        self.calls.append(str(request.request_id.value))
        if self.failures:
            raise self.failures.pop(0)
        return ProviderResponse(
            request.request_id,
            Message(MessageRole.ASSISTANT, "ok"),
            usage=ProviderUsage(10, 2, 12),
            model=self.model,
            provider_request_id="prov-1",
        )


def _req(run: str, turn: int) -> ProviderRequest:
    return ProviderRequest(
        RequestId(f"{run}:provider-turn:{turn}"), (Message(MessageRole.USER, "hi"),)
    )


def _go(coro):
    return asyncio.run(coro)


def test_transport_failure_falls_back_once_sticks_per_run_and_normalises_the_echo(tmp_path):
    luna = Stub("gpt-5.6-luna", [ProviderTimeoutError(public_message="timeout")])
    sol = Stub("gpt-6.1-sol")
    provider = FallbackProvider(
        luna,
        sol,
        primary_model="gpt-5.6-luna",
        fallback_model="gpt-6.1-sol",
        ledger_path=tmp_path / "model-calls.jsonl",
    )
    first = _go(provider.invoke(_req("run-a", 1), cancel=CancelToken()))
    assert first.model == "gpt-5.6-luna"  # 内核按绑定名核对回显、信任用量
    assert first.usage.input_tokens == 10
    second = _go(provider.invoke(_req("run-a", 2), cancel=CancelToken()))
    assert second.model == "gpt-5.6-luna"
    assert luna.calls == ["run-a:provider-turn:1"]  # 同一运行后续直接走备用
    assert sol.calls == ["run-a:provider-turn:1", "run-a:provider-turn:2"]
    third = _go(provider.invoke(_req("run-b", 1), cancel=CancelToken()))
    assert (
        third.model == "gpt-5.6-luna" and luna.calls[-1] == "run-b:provider-turn:1"
    )  # 新运行回首选
    rows = read_model_calls(tmp_path / "model-calls.jsonl")
    assert [r["outcome"] for r in rows] == ["fallback_used", "fallback_used"]
    assert rows[0]["actual_model"] == "gpt-6.1-sol" and "ProviderTimeoutError" in rows[0]["reason"]
    assert rows[0]["usage"]["input_tokens"] == 10 and rows[0]["provider_request_id"] == "prov-1"


def test_a_rejected_request_is_not_retried_on_the_fallback():
    luna = Stub("gpt-5.6-luna", [ProviderRequestRejectedError(public_message="bad request")])
    sol = Stub("gpt-6.1-sol")
    provider = FallbackProvider(
        luna, sol, primary_model="gpt-5.6-luna", fallback_model="gpt-6.1-sol"
    )
    with pytest.raises(ProviderRequestRejectedError):
        _go(provider.invoke(_req("run-c", 1), cancel=CancelToken()))
    assert sol.calls == []


def test_when_both_fail_the_fallback_error_surfaces_and_is_booked(tmp_path):
    luna = Stub("gpt-5.6-luna", [ProviderServerError(public_message="500")])
    sol = Stub("gpt-6.1-sol", [ProviderServerError(public_message="503")])
    provider = FallbackProvider(
        luna,
        sol,
        primary_model="gpt-5.6-luna",
        fallback_model="gpt-6.1-sol",
        ledger_path=tmp_path / "model-calls.jsonl",
    )
    with pytest.raises(ProviderServerError, match="503"):
        _go(provider.invoke(_req("run-d", 1), cancel=CancelToken()))
    [row] = read_model_calls(tmp_path / "model-calls.jsonl")
    assert row["outcome"] == "fallback_failed" and "503" in row["error"]
