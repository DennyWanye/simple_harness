# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Evaluation meter terminal admission uses the SDK's existing stop contract.

删旧平面模式 第三刀：拿平面任务跑主循环、看计量器拒绝后任务停下的那条删了（full_target 已有等价）。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agent_orchestrator.evaluation.experiment import (
    ARMS,
    ArmSpec,
    ExperimentBudget,
    ExperimentManifest,
    RunContext,
)
from agent_orchestrator.evaluation.metered_provider import (
    ExperimentBudgetExhausted,
    MeteredProvider,
    RunWindowDenied,
)
from agent_orchestrator.testing.fixtures import MODEL
from simple_harness import Message, MessageRole
from simple_harness.contracts import RequestId
from simple_harness.execution.provider_admission import ProviderAdmissionDenied
from simple_harness.providers import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderUsage,
)

TARGET = ProviderTarget("fixture", MODEL, "pricing-v1", "https://example.test", "v1")


class ScriptedPhysicalProvider:
    target = TARGET

    def __init__(self, usage: ProviderUsage) -> None:
        self.usage = usage
        self.calls = 0

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        del cancel
        self.calls += 1
        return ProviderResponse(
            request.request_id,
            Message(MessageRole.ASSISTANT, "physical response"),
            model=MODEL,
            usage=self.usage,
        )


def make_meter(
    provider: ScriptedPhysicalProvider,
    *,
    calls: int = 1,
    estimate: int = 1,
    before_handoff: Callable[[float], None] | None = None,
) -> MeteredProvider:
    manifest = ExperimentManifest(
        "terminal-admission-test",
        TARGET.provider_id,
        TARGET.model,
        ExperimentBudget(100, 100, 200, calls, 30),
        ("task",),
        1,
        100,
        1,
        tuple(ArmSpec(arm, f"arm-{arm}") for arm in ARMS),
    )
    context = RunContext(manifest, manifest.runs()[0], lambda counters: None)
    return MeteredProvider(
        provider,
        context,
        estimate_input_tokens=lambda request: estimate,
        before_handoff=before_handoff,
    )


def request(name: str, *, output_cap: int = 1) -> ProviderRequest:
    return ProviderRequest(
        RequestId(name),
        (Message(MessageRole.USER, name),),
        max_output_tokens=output_cap,
    )


@pytest.mark.anyio
async def test_pre_handoff_adapter_preserves_public_catch_and_run_window_distinction():
    physical = ScriptedPhysicalProvider(ProviderUsage(1, 1, 2))
    meter = make_meter(physical)
    await meter.invoke(request("prime"), cancel=CancelToken())

    with pytest.raises(ExperimentBudgetExhausted) as caught:
        await meter.invoke(request("denied"), cancel=CancelToken())
    assert isinstance(caught.value, ProviderAdmissionDenied)
    assert caught.value.code == "provider_admission_denied"
    assert caught.value.public_message == "experiment call/token allowance exhausted"
    assert caught.value.detail["reason_code"] == "budget_exhausted"

    def deny_window(seconds: float) -> None:
        del seconds
        raise RunWindowDenied("window closed")

    window_physical = ScriptedPhysicalProvider(ProviderUsage(1, 1, 2))
    window_meter = make_meter(window_physical, calls=2, before_handoff=deny_window)
    with pytest.raises(RunWindowDenied) as window:
        await window_meter.invoke(request("window"), cancel=CancelToken())
    assert not isinstance(window.value, ProviderAdmissionDenied)
    assert window.value.code == "run_window_denied"
    assert window_physical.calls == window_meter.counters.calls == 0
    assert window_meter.admission_denials[-1]["reason"] == "RunWindowDenied"


@pytest.mark.anyio
async def test_physical_overrun_retains_original_error_and_actual_usage_charge():
    physical = ScriptedPhysicalProvider(ProviderUsage(3, 2, 5))
    meter = make_meter(physical, estimate=1)

    with pytest.raises(ExperimentBudgetExhausted, match="physical usage") as caught:
        await meter.invoke(request("overrun"), cancel=CancelToken())

    assert not isinstance(caught.value, ProviderAdmissionDenied)
    assert caught.value.code == "experiment_budget_exhausted"
    assert caught.value.detail["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "cache_tokens": None,
        "reasoning_tokens": None,
    }
    assert physical.calls == meter.counters.calls == 1
    assert meter.counters.input_tokens == 3
    assert meter.counters.output_tokens == 2
    assert meter.counters.total_tokens == 5
    assert meter.admission_denials == []
    assert meter.observations[0]["status"] == "failed"
    assert meter.observations[0]["total_tokens"] == 5
