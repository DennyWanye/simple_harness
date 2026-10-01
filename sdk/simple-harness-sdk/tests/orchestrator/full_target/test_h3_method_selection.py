# ruff: noqa: E402,I001
"""Focused H3 policy, evidence admission, and recovery contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from agent_orchestrator.contracts.htn import MethodRef
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.planning_decisions import (
    H1_DECISION_ENABLEMENT,
    H3_DECISION_ENABLEMENT,
    EvidenceQuestionV1,
    PLANNING_DECISION_V1,
    RequestEvidenceDecision,
)
from agent_orchestrator.contracts.semantic_base import VersionedRef
from agent_orchestrator.knowledge.predicates import PredicateRegistry, PredicateSignature
from agent_orchestrator.orchestrator.planning_protocol_binding import bind_planning_protocol
from agent_orchestrator.planning.htn.applicability import ApplicabilityStatus
from agent_orchestrator.planning.htn.method_selection import (
    validate_evidence_request,
)
from agent_orchestrator.planning.htn.planner_package import MethodApplicability

from test_htn_end_to_end import build_world  # noqa: E402


def test_h3_enables_request_evidence_without_mutating_h1_matrix() -> None:
    assert H1_DECISION_ENABLEMENT["REQUEST_EVIDENCE"].executable is False
    assert H3_DECISION_ENABLEMENT["REQUEST_EVIDENCE"].executable is True
    assert H3_DECISION_ENABLEMENT["REFINE"] == H1_DECISION_ENABLEMENT["REFINE"]


def _evidence_request(predicate_key: str) -> RequestEvidenceDecision:
    return RequestEvidenceDecision(
        questions=(
            EvidenceQuestionV1(
                predicate_key=predicate_key,
                arguments={"subject": "workspace"},
                purpose="choose a method",
                blocking=True,
            ),
        )
    )


def _registry() -> PredicateRegistry:
    registry = PredicateRegistry()
    registry.register(
        PredicateSignature(
            predicate_ref=VersionedRef(id="workspace-readable", version=1, content_hash="a" * 64),
            observer_ids=("workspace-observer",),
            authority_scope="workspace-read",
        )
    )
    return registry


def test_h3_evidence_requires_registered_observer_and_authority() -> None:
    request = _evidence_request("workspace-readable@1")
    registry = _registry()
    assert validate_evidence_request(
        request,
        registry=registry,
        observers={"workspace-readable@1": True},
        authorized_scopes={"workspace-read"},
    ) == request
    with pytest.raises(ContractError, match="not registered"):
        validate_evidence_request(
            _evidence_request("unknown@1"),
            registry=registry,
            observers={"unknown@1": True},
            authorized_scopes={"workspace-read"},
        )
    with pytest.raises(ContractError, match="no observer"):
        validate_evidence_request(
            request,
            registry=registry,
            observers={},
            authorized_scopes={"workspace-read"},
        )
    with pytest.raises(ContractError, match="outside the caller authority"):
        validate_evidence_request(
            request,
            registry=registry,
            observers={"workspace-readable@1": True},
            authorized_scopes=set(),
        )


def test_h3_evidence_contract_caps_questions_and_rejects_duplicates() -> None:
    questions = tuple(
        EvidenceQuestionV1(
            predicate_key=f"workspace-readable@{index}",
            arguments={"subject": "workspace"},
            purpose="choose a method",
            blocking=True,
        )
        for index in range(1, 10)
    )
    with pytest.raises(ContractError, match="more than 8"):
        RequestEvidenceDecision(questions=questions)
    duplicate = RequestEvidenceDecision(
        questions=(
            EvidenceQuestionV1("workspace-readable@1", {"subject": "workspace"}, "x", True),
            EvidenceQuestionV1("workspace-readable@1", {"subject": "workspace"}, "y", True),
        )
    )
    with pytest.raises(ContractError, match="repeats"):
        validate_evidence_request(
            duplicate,
            registry=_registry(),
            observers={"workspace-readable@1": True},
            authorized_scopes={"workspace-read"},
        )


def test_h3_real_hierarchical_dispatch_lists_zero_one_and_many_candidates(tmp_path) -> None:
    """The production dispatch adapter feeds one frozen report set into the filter.

    2026-10-01 HTN 精简片 A：程序不再代选做法（单候选直接选的路由已删），这里只钉
    "派发层把报告集如实过滤、排序后交给规划器"。
    """

    world = build_world(tmp_path, key="h3-selection-dispatch")
    bind_planning_protocol(world.store, world.mission.id, PLANNING_DECISION_V1)
    occurrence_id = str(world.network().occurrences[0].occurrence_id)
    method_a = MethodRef("h3.method-a", 1, "a" * 64)
    method_b = MethodRef("h3.method-b", 1, "b" * 64)

    def report(reference: MethodRef) -> MethodApplicability:
        return MethodApplicability(
            goal_occurrence_id=occurrence_id,
            goal_signature_id="plan.goal",
            method_ref=reference,
            report=ApplicabilityStatus.APPLICABLE,
        )

    zero = world.dispatch.method_candidates(world.mission.id, reports=())
    one = world.dispatch.method_candidates(world.mission.id, reports=(report(method_a),))
    many = world.dispatch.method_candidates(
        world.mission.id, reports=(report(method_b), report(method_a))
    )

    assert zero[occurrence_id].applicable == ()
    assert [item.method_id for item in one[occurrence_id].applicable] == ["h3.method-a"]
    assert [item.method_id for item in many[occurrence_id].applicable] == [
        "h3.method-a",
        "h3.method-b",
    ]
