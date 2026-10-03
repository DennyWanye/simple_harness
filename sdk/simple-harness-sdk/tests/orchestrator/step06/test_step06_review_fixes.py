# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · code review round 1 dispositions (reports/code-review-round1.md): one
decisive test per P0/P1 fix and for the P2 fixes that changed behaviour.

HTN 补齐阶段 A′（2026-10-03）：P1-6（未知用量占着的预留可见、上时间线）原用 ``leaf_world``
手工建尝试、手写未知用量，改由产品同形主循环守：``p35/test_provider_accounting_loop.py``
``test_a_charge_nobody_can_state_is_held_at_its_bound_and_counted_at_closeout``（调用失败的几种
情形里 ``ReservationHeld`` 恰好一条、预留数与收尾按上限计入的数一致）。"""

from __future__ import annotations

import pytest

from agent_orchestrator.runtime.model_router import (
    ModelRouter,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
    classify_turn_error,
)


# ------------------------------------------------------------------ P2-1 / P2-3 routing details
def test_p2_1_turn_errors_are_classified_by_exact_codes_only():
    assert (
        classify_turn_error({"raw_failures": [{"error_code": "provider_server_error"}]})
        == "provider_unavailable"
    )
    assert classify_turn_error({"error_code": "provider_protocol_error"}) == "provider_error"
    assert (
        classify_turn_error({"error_code": "provider_empty_response"}) == "other"
    )  # already retried in the turn
    assert (
        classify_turn_error({"error_code": "provider_cancelled"}) == "other"
    )  # our own cancel is not ill health
    assert (
        classify_turn_error({"message": "see provider_server_error in the docs"}) == "other"
    )  # free text is not a code


def test_p2_3_a_fallback_that_is_cooling_down_too_is_not_used():
    class _Stub:
        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            raise AssertionError

    profiles = {n: RuntimeProfile(n, _Stub(), f"m-{n}") for n in ("small", "large")}
    router = ModelRouter(profiles, RoutingRules(default="small", fallback={"small": "large"}))
    with pytest.raises(RoutingUnavailable) as unavailable:
        router.route(role="worker", unavailable_until={"small": 100.0, "large": 90.0}, now=50.0)
    assert unavailable.value.profile_id == "large"
