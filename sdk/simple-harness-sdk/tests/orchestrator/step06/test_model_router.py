# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · S6-03 / S6-06 / S6-08 (D6-4 / D6-5): physical model routing.  A runtime
profile is one provider + model + execution library; the route is frozen in the
dispatch intent and proven by the provider's echo; failures climb §9.3's ladder to a
stronger profile with the earlier Attempt and its cost kept; an unavailable service is
replaced by a fallback or waited for (bounded); a restart never hands an Attempt to
another pool."""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts import Attempt, AttemptStatus, Budget
from agent_orchestrator.runtime.model_router import (
    ModelRouter,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
    classify_turn_error,
)


class _Stub:
    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        raise AssertionError("never called")


def _profiles(*names, tiers=None):
    return {
        n: RuntimeProfile(n, _Stub(), f"model-{n}", tier=(tiers or {}).get(n, i))
        for i, n in enumerate(names)
    }


def _attempt(profile, reason, ordinal=1, error_kind=None):
    failure = (
        None
        if reason is None
        else {"reason": reason, **({} if error_kind is None else {"error_kind": error_kind})}
    )
    return Attempt(
        id=f"t:attempt-{ordinal}",
        task_id="t",
        mission_id="m",
        role="worker",
        model=f"model-{profile}",
        prompt_version="w",
        context_version="c",
        budget_reserved=Budget(),
        lease_owner=None,
        lease_expires_at=None,
        status=AttemptStatus.RETRY_WAIT if reason else AttemptStatus.COMPLETED,
        retry_of=None,
        created_at=0.0,
        version=1,
        ordinal=ordinal,
        creation_key="k",
        input_id="i",
        runtime_profile_id=profile,
        failure=failure,
    )


# ------------------------------------------------------------------ unit: the router
def test_routing_by_role_then_task_kind_then_default_and_rule_validation():
    router = ModelRouter(
        _profiles("small", "large"),
        RoutingRules(
            default="small", by_role={"planner": "large"}, by_task_kind={"proof": "large"}
        ),
    )
    assert router.route(role="planner", task_kind="code").profile_id == "large"
    assert router.route(role="worker", task_kind="proof").reason == "by_task_kind:proof"
    decision = router.route(role="worker", task_kind="code")
    assert (decision.profile_id, decision.model, decision.reason) == (
        "small",
        "model-small",
        "default",
    )
    with pytest.raises(ValueError):
        ModelRouter(_profiles("small"), RoutingRules(default="small", escalate={"small": "huge"}))
    with pytest.raises(ValueError):
        ModelRouter({}, RoutingRules())


def test_the_escalation_ladder_climbs_after_failures_on_the_current_rung_only():
    router = ModelRouter(
        _profiles("small", "medium", "large"),
        RoutingRules(
            default="small",
            escalate={"small": "medium", "medium": "large"},
            escalate_after_failures=1,
        ),
    )
    assert router.route(role="worker", previous_attempts=[]).profile_id == "small"
    one = router.route(role="worker", previous_attempts=[_attempt("small", "verification_failed")])
    assert (
        one.profile_id == "medium"
        and one.escalated_from == "small"
        and one.reason.startswith("escalate:small->medium")
    )
    two = router.route(
        role="worker",
        previous_attempts=[
            _attempt("small", "verification_failed"),
            _attempt("medium", "turn_failed", 2, error_kind="provider_error"),
        ],
    )
    assert two.profile_id == "large" and two.escalated_from == "medium"
    # an unavailable provider is a health matter, never a reason to escalate (review P1-8)
    down = router.route(
        role="worker",
        previous_attempts=[_attempt("small", "turn_failed", error_kind="provider_unavailable")],
    )
    assert down.profile_id == "small"
    # a completed Attempt or a non-failure reason does not count
    fine = router.route(
        role="worker",
        previous_attempts=[_attempt("small", None), _attempt("small", "outcome_blocked", 2)],
    )
    assert fine.profile_id == "small"


def test_an_unavailable_profile_falls_back_or_makes_the_caller_wait():
    router = ModelRouter(
        _profiles("small", "large"), RoutingRules(default="small", fallback={"small": "large"})
    )
    decision = router.route(role="worker", unavailable_until={"small": 100.0}, now=50.0)
    assert decision.profile_id == "large" and decision.fallback_from == "small"
    assert (
        router.route(role="worker", unavailable_until={"small": 100.0}, now=150.0).profile_id
        == "small"
    )
    lonely = ModelRouter(_profiles("small"), RoutingRules(default="small"))
    with pytest.raises(RoutingUnavailable) as unavailable:
        lonely.route(role="worker", unavailable_until={"small": 100.0}, now=50.0)
    assert unavailable.value.profile_id == "small" and unavailable.value.until == 100.0


def test_turn_errors_are_classified_by_the_sdk_error_vocabulary():
    assert (
        classify_turn_error({"code": "provider_server_error", "message": "503"})
        == "provider_unavailable"
    )
    assert classify_turn_error({"kind": "provider_rate_limited"}) == "provider_unavailable"
    assert (
        classify_turn_error({"code": "provider_protocol_error", "detail": "tool_parse"})
        == "provider_error"
    )
    assert (
        classify_turn_error({"code": "turn_deadline"}) == "other"
        and classify_turn_error(None) == "other"
    )
