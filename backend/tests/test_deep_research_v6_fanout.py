from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecValidationError,
    RouteDecisionV1,
    build_route_decision_from_spec,
    conditional_fanout_enabled,
    conditional_fanout_reason,
    format_blob_ref,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec


POLICY_HASH = "a" * 64
SPEC_HASH = "b" * 64
CAPABILITY_HASH = "c" * 64


def _decision(*, groups: int, lanes: int) -> RouteDecisionV1:
    return RouteDecisionV1.create(
        run_id="run-t11",
        spec_hash=SPEC_HASH,
        policy_ref=format_blob_ref(POLICY_HASH),
        policy_hash=POLICY_HASH,
        capability_snapshot_hash=CAPABILITY_HASH,
        source_health=[
            {"authority_id": "general.web", "status": "healthy", "reason_code": "available"}
        ],
        budget={"query": 6, "fetch": 8, "browser": 2, "llm": 4, "lane": lanes},
        work_groups=[
            {
                "work_group_id": f"wg-{index}",
                "requirement_ids": [f"req-{index}"],
                "merge_key": f"requirement:req-{index}",
            }
            for index in range(groups)
        ],
        initial_route="general_search",
        allowed_escalations=[
            {
                "from_route": "general_search",
                "to_route": "conditional_fanout",
                "condition_code": "independent_work_groups_available",
            }
        ],
        reason_codes=["intent_requires_multiple_work_groups"],
    )


def test_route_decision_roundtrip_and_identity_are_strict() -> None:
    decision = _decision(groups=3, lanes=3)
    assert RouteDecisionV1.from_json(decision.to_json()).to_json() == decision.to_json()

    tampered = copy.deepcopy(decision.to_json())
    tampered["budget"]["lane"] = 2
    with pytest.raises(ResearchSpecValidationError):
        RouteDecisionV1.from_json(tampered)


@pytest.mark.parametrize(
    ("groups", "lanes", "reason"),
    [
        (2, 8, "independent_work_groups_below_minimum"),
        (3, 2, "lane_budget_insufficient"),
        (3, 3, "conditional_fanout_enabled"),
    ],
)
def test_conditional_fanout_requires_all_three_conditions(
    groups: int,
    lanes: int,
    reason: str,
) -> None:
    decision = _decision(groups=groups, lanes=lanes)
    assert conditional_fanout_reason(decision, intent_type="comparison") == reason
    assert conditional_fanout_enabled(decision, intent_type="comparison") is (
        reason == "conditional_fanout_enabled"
    )


def test_exact_fact_never_fans_out_even_with_budget_and_groups() -> None:
    decision = _decision(groups=4, lanes=4)
    assert conditional_fanout_reason(
        decision, intent_type="official_exact_fact"
    ) == "exact_fact_fanout_forbidden"
    assert not conditional_fanout_enabled(
        decision, intent_type="official_exact_fact"
    )


def test_route_groups_are_derived_from_frozen_work_dimensions() -> None:
    spec = compile_research_spec(
        "深入研究生成式 AI 对设计行业的影响",
        as_of_date="2026-07-18",
    )
    decision = build_route_decision_from_spec(
        spec,
        run_id="run-open",
        policy_hash=POLICY_HASH,
        capability_snapshot_hash=CAPABILITY_HASH,
        budget={"query": 6, "fetch": 8, "browser": 2, "llm": 4, "lane": 4},
    )
    assert len(decision.to_json()["work_groups"]) == 4
    assert conditional_fanout_enabled(decision, intent_type=spec.intent_type)
    assert decision.to_json()["initial_route"] == "conditional_fanout"


def test_top_n_with_one_work_group_stays_serial() -> None:
    spec = compile_research_spec(
        "当前最值得关注的 10 个 AI 产品及优缺点",
        as_of_date="2026-07-18",
    )
    decision = build_route_decision_from_spec(
        spec,
        run_id="run-topn",
        policy_hash=POLICY_HASH,
        capability_snapshot_hash=CAPABILITY_HASH,
        budget={"query": 6, "fetch": 8, "browser": 2, "llm": 4, "lane": 8},
    )
    assert len(decision.to_json()["work_groups"]) == 1
    assert not conditional_fanout_enabled(decision, intent_type=spec.intent_type)
