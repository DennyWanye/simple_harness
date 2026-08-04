from __future__ import annotations

from deskpet.companion.risk import (
    CapabilityRiskPolicy,
    CandidateRiskInputV1,
    EffectFactV1,
    EffectTopologyDiffV1,
    StaticRiskPreflight,
)


def _safe_instruction() -> CandidateRiskInputV1:
    return CandidateRiskInputV1(
        candidate_id="candidate-safe",
        package_hash="package-safe",
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


def test_manifest_proven_readonly_instruction_is_safe_auto() -> None:
    preflight = StaticRiskPreflight().inspect(_safe_instruction())
    assessment = CapabilityRiskPolicy().assess(
        preflight, EffectTopologyDiffV1()
    )

    assert preflight.safe_auto_eligible
    assert assessment.risk == "low"
    assert assessment.permit_mode == "safe_auto"


def test_unknown_or_undeclared_tool_fails_closed() -> None:
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-unknown",
        package_hash="package-unknown",
        candidate_kind="instruction",
        declared_tool_refs=("memory_recall@v1",),
        referenced_tool_refs=("memory_recall@v1", "mystery_alias"),
        tool_facts=(
            EffectFactV1(
                tool_ref="memory_recall@v1",
                effect_kind="read_only",
                idempotent=True,
            ),
        ),
    )
    preflight = StaticRiskPreflight().inspect(candidate)
    assessment = CapabilityRiskPolicy().assess(
        preflight, EffectTopologyDiffV1()
    )

    assert not preflight.safe_auto_eligible
    assert assessment.risk == "unknown"
    assert assessment.permit_mode == "user_authorized"
    assert "undeclared_tool_reference" in assessment.reasons


def test_code_and_irreversible_effect_override_low_risk_signals() -> None:
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-code",
        package_hash="package-code",
        candidate_kind="code",
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
    preflight = StaticRiskPreflight().inspect(candidate)
    assessment = CapabilityRiskPolicy().assess(
        preflight,
        EffectTopologyDiffV1(
            effects_added=("payment",),
            topology_expanded=False,
        ),
    )

    assert preflight.direct_os_effects_unverifiable
    assert assessment.risk == "high"
    assert assessment.permit_mode == "user_authorized"
    assert "executable_candidate" in assessment.reasons
    assert "irreversible_or_external_effect" in assessment.reasons


def test_workflow_must_use_known_safe_nodes_and_dataflow() -> None:
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-workflow",
        package_hash="package-workflow",
        candidate_kind="personal_workflow",
        workflow_nodes_known=True,
        workflow_dataflow_safe=False,
    )
    assessment = CapabilityRiskPolicy().assess(
        StaticRiskPreflight().inspect(candidate),
        EffectTopologyDiffV1(),
    )

    assert assessment.risk == "unknown"
    assert assessment.permit_mode == "user_authorized"


def test_update_tool_topology_expansion_is_not_safe_auto() -> None:
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-expanded",
        package_hash="package-expanded",
        candidate_kind="instruction",
        declared_tool_refs=("memory_recall@v1", "new_read_tool@v1"),
        referenced_tool_refs=("memory_recall@v1", "new_read_tool@v1"),
        tool_facts=(
            EffectFactV1("memory_recall@v1", "read_only", True),
            EffectFactV1("new_read_tool@v1", "read_only", True),
        ),
        source_tool_refs=("memory_recall@v1",),
        enforce_source_nonexpansion=True,
    )
    assessment = CapabilityRiskPolicy().assess(
        StaticRiskPreflight().inspect(candidate),
        EffectTopologyDiffV1(),
    )

    assert assessment.risk == "high"
    assert assessment.permit_mode == "safe_static"
