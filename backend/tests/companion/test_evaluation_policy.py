from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from deskpet.companion import OwnerRef
from deskpet.companion.evaluation import (
    EvaluationCaseComparisonV1,
    EvaluationGateRequestV1,
    EvaluationIdentityV1,
    EvaluationPermitIssuer,
    ImmutableEvaluationGate,
    IndependentEvaluator,
)
from deskpet.companion.risk import (
    CandidateRiskInputV1,
    EffectFactV1,
    EffectTopologyDiffV1,
)


@dataclass
class FakePermitStore:
    issued: list[Any] = field(default_factory=list)
    risks: dict[str, dict[str, Any]] = field(default_factory=dict)
    activation_calls: int = 0

    def record_risk_assessment(self, owner, **assessment):
        risk_id = assessment["risk_id"]
        existing = self.risks.get(risk_id)
        if existing is not None and existing != assessment:
            raise RuntimeError("risk_assessment_conflict")
        self.risks[risk_id] = assessment
        return assessment

    def issue_evaluation_execution_permit(self, owner, permit):
        self.issued.append((owner, permit))
        return {"permit_id": permit.permit_id, "mode": permit.mode}


def _identity() -> EvaluationIdentityV1:
    return EvaluationIdentityV1(
        evaluation_id="evaluation-1",
        candidate_id="candidate-1",
        package_hash="package-1",
        manifest_hash="manifest-1",
        archive_hash="archive-1",
        suite_hash="suite-1",
        runner_policy_hash="runner-policy-1",
        issued_revocation_epoch=4,
        risk_ref="risk-1",
    )


def _safe_candidate() -> CandidateRiskInputV1:
    return CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="instruction",
        declared_tool_refs=("memory_recall@v1",),
        referenced_tool_refs=("memory_recall@v1",),
        tool_facts=(
            EffectFactV1(
                tool_ref="memory_recall@v1",
                effect_kind="read_only",
                idempotent=True,
            ),
        ),
        source_tool_refs=("memory_recall@v1",),
    )


def test_safe_auto_and_user_authorized_are_distinct_permits() -> None:
    owner = OwnerRef("alice", 1)
    store = FakePermitStore()
    issuer = EvaluationPermitIssuer(store)

    safe = issuer.issue(
        owner,
        EvaluationGateRequestV1(
            identity=_identity(),
            candidate=_safe_candidate(),
            topology_diff=EffectTopologyDiffV1(),
        ),
    )
    assert safe.status == "issued"
    assert safe.permit is not None and safe.permit.mode == "safe_auto"
    assert safe.permit.authorization_id is None

    executable = CandidateRiskInputV1(
        candidate_id="candidate-2",
        package_hash="package-2",
        candidate_kind="code",
    )
    blocked = issuer.issue(
        owner,
        EvaluationGateRequestV1(
            identity=EvaluationIdentityV1(
                **{
                    **_identity().to_dict(),
                    "evaluation_id": "evaluation-2",
                    "candidate_id": "candidate-2",
                    "package_hash": "package-2",
                    "risk_ref": "risk-2",
                }
            ),
            candidate=executable,
            topology_diff=EffectTopologyDiffV1(),
        ),
    )
    assert blocked.status == "awaiting_eval_authorization"
    assert blocked.permit is None
    assert len(store.issued) == 1

    authorized = issuer.issue(
        owner,
        EvaluationGateRequestV1(
            identity=EvaluationIdentityV1(
                **{
                    **_identity().to_dict(),
                    "evaluation_id": "evaluation-2",
                    "candidate_id": "candidate-2",
                    "package_hash": "package-2",
                    "risk_ref": "risk-2",
                }
            ),
            candidate=executable,
            topology_diff=EffectTopologyDiffV1(),
            authorization_id="eval-authorization-1",
        ),
    )
    assert authorized.status == "issued"
    assert authorized.permit is not None
    assert authorized.permit.mode == "user_authorized"
    assert authorized.permit.authorization_id == "eval-authorization-1"
    assert store.activation_calls == 0


def test_unknown_policy_requires_exact_user_evaluation_authorization() -> None:
    store = FakePermitStore()
    outcome = EvaluationPermitIssuer(store).issue(
        OwnerRef("alice", 1),
        EvaluationGateRequestV1(
            identity=_identity(),
            candidate=CandidateRiskInputV1(
                candidate_id="candidate-1",
                package_hash="package-1",
                candidate_kind="unknown",
            ),
            topology_diff=EffectTopologyDiffV1(unknown_effects=("mystery",)),
        ),
    )

    assert outcome.status == "awaiting_eval_authorization"
    assert outcome.risk.risk == "unknown"
    assert store.issued == []

    authorized_unknown = EvaluationPermitIssuer(store).issue(
        OwnerRef("alice", 1),
        EvaluationGateRequestV1(
            identity=_identity(),
            candidate=CandidateRiskInputV1(
                candidate_id="candidate-1",
                package_hash="package-1",
                candidate_kind="unknown",
            ),
            topology_diff=EffectTopologyDiffV1(unknown_effects=("mystery",)),
            authorization_id="authorization-for-exact-unknown-package",
        ),
    )
    assert authorized_unknown.status == "issued"
    assert authorized_unknown.permit is not None
    assert authorized_unknown.permit.mode == "user_authorized"
    assert authorized_unknown.permit.authorization_id == (
        "authorization-for-exact-unknown-package"
    )
    assert len(store.issued) == 1


def test_external_send_instruction_uses_only_frozen_zero_tool_static_eval() -> None:
    store = FakePermitStore()
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="instruction",
        declared_tool_refs=("project_group_send@v1",),
        referenced_tool_refs=("project_group_send@v1",),
        tool_facts=(
            EffectFactV1(
                tool_ref="project_group_send@v1",
                effect_kind="external_send",
                idempotent=False,
            ),
        ),
        permissions_added=("network",),
        topology_expanded=True,
    )
    diff = EffectTopologyDiffV1(
        permissions_added=("network",),
        effects_added=("external_send",),
        topology_expanded=True,
    )
    blocked = EvaluationPermitIssuer(store).issue(
        OwnerRef("alice", 1),
        EvaluationGateRequestV1(
            identity=_identity(),
            candidate=candidate,
            topology_diff=diff,
            zero_tools=False,
        ),
    )
    assert blocked.status == "awaiting_eval_authorization"
    assert blocked.risk.risk == "high"
    assert blocked.risk.permit_mode == "safe_static"
    assert blocked.reason_code == "safe_static_zero_tool_runner_required"
    assert store.issued == []

    issued = EvaluationPermitIssuer(store).issue(
        OwnerRef("alice", 1),
        EvaluationGateRequestV1(
            identity=_identity(),
            candidate=candidate,
            topology_diff=diff,
            zero_tools=True,
        ),
    )
    assert issued.status == "issued"
    assert issued.permit is not None
    assert issued.permit.mode == "safe_static"
    assert issued.permit.authorization_id is None
    assert issued.risk.risk == "high"


def test_immutable_gate_uses_verified_package_facts_before_issuing() -> None:
    store = FakePermitStore()

    class _FactsBuilder:
        calls = 0

        def build(self, package, receipt, **kwargs):
            self.calls += 1
            assert package == "immutable-package"
            assert receipt == "host-receipt"
            assert kwargs["candidate_id"] == "candidate-1"
            return SimpleNamespace(
                candidate=_safe_candidate(),
                manifest_hash="manifest-1",
                archive_hash="archive-1",
            )

    builder = _FactsBuilder()
    outcome = ImmutableEvaluationGate(
        EvaluationPermitIssuer(store),
        facts_builder=builder,
    ).issue(
        OwnerRef("alice", 1),
        identity=_identity(),
        package="immutable-package",  # type: ignore[arg-type]
        receipt="host-receipt",  # type: ignore[arg-type]
        effect_topology={},
        tool_manifest={},
        topology_diff=EffectTopologyDiffV1(),
    )

    assert builder.calls == 1
    assert outcome.permit is not None
    assert outcome.permit.mode == "safe_auto"


def test_evaluator_fails_closed_on_regression_unknown_or_no_improvement() -> None:
    evaluator = IndependentEvaluator()
    passed = evaluator.evaluate(
        required_case_ids=("case-1",),
        comparisons=(
            EvaluationCaseComparisonV1(
                case_id="case-1",
                old_status="passed",
                candidate_status="passed",
                improved=True,
                baseline_regression=False,
            ),
        ),
    )
    assert passed.verdict == "passed"

    assert evaluator.evaluate(
        required_case_ids=("case-1",),
        comparisons=(
            EvaluationCaseComparisonV1(
                case_id="case-1",
                old_status="passed",
                candidate_status="timeout",
                improved=False,
                baseline_regression=False,
            ),
        ),
    ).verdict == "inconclusive"
    assert evaluator.evaluate(
        required_case_ids=("case-1",),
        comparisons=(
            EvaluationCaseComparisonV1(
                case_id="case-1",
                old_status="passed",
                candidate_status="passed",
                improved=False,
                baseline_regression=False,
            ),
        ),
    ).verdict == "inconclusive"
    assert evaluator.evaluate(
        required_case_ids=("case-1",),
        comparisons=(
            EvaluationCaseComparisonV1(
                case_id="case-1",
                old_status="passed",
                candidate_status="passed",
                improved=True,
                baseline_regression=True,
            ),
        ),
    ).verdict == "failed"


def test_evaluation_policy_has_no_activation_or_capability_runtime_import() -> None:
    module_path = (
        Path(__file__).parents[2]
        / "deskpet"
        / "companion"
        / "evaluation.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any("activation" in name for name in imported)
    assert not any("capabilities" in name for name in imported)
