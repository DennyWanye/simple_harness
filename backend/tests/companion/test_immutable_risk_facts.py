from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from deskpet.companion.build_admission import CandidateDraftReceiptV1
from deskpet.companion.candidate_builder import (
    CandidateFileInputV1,
    CandidateSeedInputV1,
    DeterministicCandidateBuilder,
)
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
)
from deskpet.companion.risk import CapabilityRiskPolicy, EffectTopologyDiffV1, StaticRiskPreflight
from deskpet.companion.risk_facts import (
    CandidateRiskFactsError,
    FixedWorkflowDataflowValidator,
    ImmutableCandidateRiskFactsBuilder,
)
from deskpet.companion.store import canonical_hash


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _tool_manifest(effect: str = "read_only", *, idempotent: bool = True):
    return {
        "memory_recall": {
            "stable_handler_id": "memory-recall-handler",
            "tool_name": "memory_recall",
            "spec_ref": "tool:memory_recall:v1",
            "schema_hash": _hash("memory-schema"),
            "execution_build_identity": _hash("memory-build"),
            "effect_policy_hash": _hash(f"memory-effect:{effect}:{idempotent}"),
            "effect": effect,
            "idempotent": idempotent,
        }
    }


def _target(kind: str) -> GrowthTargetIdentityV1:
    return GrowthTargetIdentityV1(
        kind=kind,
        target_id="daily",
        stable_name="daily",
        pack_id="personal.daily",
    )


def _fence() -> CandidateBindingFenceV1:
    return CandidateBindingFenceV1(
        owner_key="companion:alice:1",
        scope="user",
        scope_key="alice",
        pack_id="personal.daily",
        expected_absent=True,
        binding_generation=0,
    )


def _product(*, workflow: dict | None = None):
    if workflow is None:
        path = "skills/daily/SKILL.md"
        content = (
            b"---\n"
            b"name: daily\n"
            b"description: Daily summary\n"
            b"allowed-tools:\n"
            b"  - memory_recall\n"
            b"---\n"
            b"Use memory_recall to summarize the day.\n"
        )
        entries = {"skills": [{"id": "daily", "path": path}]}
        kind = "skill"
    else:
        path = "workflows/daily.json"
        content = json.dumps(
            workflow, sort_keys=True, separators=(",", ":")
        ).encode()
        entries = {
            "workflows": [
                {
                    "id": "daily",
                    "path": path,
                    "adapter": "workflow.personal_v1",
                }
            ]
        }
        kind = "workflow"
    seed = CandidateSeedInputV1(
        owner_key="companion:alice:1",
        target=_target(kind),
        candidate_mode="genesis",
        target_fence=_fence(),
        files=(CandidateFileInputV1(path, content),),
        manifest_template={
            "schema_version": 2,
            "id": "personal.daily",
            "name": "Daily",
            "entries": entries,
            "permissions": [],
        },
        effect_topology={"effects": []},
    )
    return DeterministicCandidateBuilder().build(seed)


def _receipt(product):
    package = product.package
    return CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id="builder-launch-1",
        child_run_id="child-run-1",
        child_start_hash=_hash("child-start"),
        proposal_ref="proposal-1",
        proposal_hash=_hash("proposal"),
        evidence_set_hash=_hash("evidence"),
        target_fence_hash=canonical_hash(package.target_facts),
        validated_draft_hash=package.candidate_content_hash,
        manifest_hash=package.candidate_manifest_hash,
        archive_hash=package.archive_hash,
        file_set_hash=product.file_set_hash,
        effect_topology_hash=package.effect_topology_hash,
    )


def _workflow() -> dict:
    return {
        "schema_version": 1,
        "name": "daily",
        "description": "Daily summary",
        "entry_node": "input",
        "nodes": [
            {"id": "input", "type": "input", "bindings": {}, "config": {}},
            {
                "id": "recall",
                "type": "tool_call",
                "bindings": {"query": "/input/query"},
                "config": {"tool_name": "memory_recall"},
            },
            {
                "id": "output",
                "type": "output",
                "bindings": {"value": "/nodes/recall/result"},
                "config": {},
            },
        ],
        "outputs": {"value": "/nodes/output/value"},
        "max_steps": 3,
    }


def test_verified_skill_package_produces_trusted_safe_risk_facts() -> None:
    product = _product()
    facts = ImmutableCandidateRiskFactsBuilder().build(
        product.package,
        _receipt(product),
        candidate_id="candidate-1",
        effect_topology={"effects": []},
        tool_manifest=_tool_manifest(),
    )
    preflight = StaticRiskPreflight().inspect(facts.candidate)
    risk = CapabilityRiskPolicy().assess(preflight, EffectTopologyDiffV1())

    assert facts.manifest_hash == product.package.candidate_manifest_hash
    assert facts.archive_hash == product.package.archive_hash
    assert facts.candidate.candidate_kind == "instruction"
    assert facts.candidate.declared_tool_refs == ("tool:memory_recall:v1",)
    assert risk.risk == "low"
    assert risk.permit_mode == "safe_auto"


@pytest.mark.parametrize("tamper", ["file_bytes", "archive_bytes", "package_hash"])
def test_any_immutable_package_tamper_is_rejected(tamper: str) -> None:
    product = _product()
    package = product.package
    if tamper == "file_bytes":
        blobs = tuple(
            replace(item, payload=b"tampered")
            if item.blob_kind == "file"
            else item
            for item in package.blobs
        )
        package = replace(package, blobs=blobs)
    elif tamper == "archive_bytes":
        blobs = tuple(
            replace(item, payload=item.payload + b"x")
            if item.blob_kind == "archive"
            else item
            for item in package.blobs
        )
        package = replace(package, blobs=blobs)
    else:
        package = replace(package, candidate_package_hash=_hash("drift"))

    with pytest.raises(CandidateRiskFactsError):
        ImmutableCandidateRiskFactsBuilder().build(
            package,
            _receipt(product),
            candidate_id="candidate-1",
            effect_topology={"effects": []},
            tool_manifest=_tool_manifest(),
        )


def test_receipt_or_effect_topology_mismatch_is_rejected() -> None:
    product = _product()
    mismatched = CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id="builder-launch-1",
        child_run_id="child-run-1",
        child_start_hash=_hash("child-start"),
        proposal_ref="proposal-1",
        proposal_hash=_hash("proposal"),
        evidence_set_hash=_hash("evidence"),
        target_fence_hash=canonical_hash(product.package.target_facts),
        validated_draft_hash=product.package.candidate_content_hash,
        manifest_hash=product.package.candidate_manifest_hash,
        archive_hash=_hash("other-archive"),
        file_set_hash=product.file_set_hash,
        effect_topology_hash=product.package.effect_topology_hash,
    )
    with pytest.raises(CandidateRiskFactsError, match="receipt"):
        ImmutableCandidateRiskFactsBuilder().build(
            product.package,
            mismatched,
            candidate_id="candidate-1",
            effect_topology={"effects": []},
            tool_manifest=_tool_manifest(),
        )
    with pytest.raises(CandidateRiskFactsError, match="effect topology"):
        ImmutableCandidateRiskFactsBuilder().build(
            product.package,
            _receipt(product),
            candidate_id="candidate-1",
            effect_topology={"effects": ["external"]},
            tool_manifest=_tool_manifest(),
        )


def test_fixed_workflow_validator_computes_dag_and_effect_safety() -> None:
    safe = FixedWorkflowDataflowValidator().validate(
        _workflow(), tool_manifest=_tool_manifest()
    )
    assert safe.nodes_known
    assert safe.dataflow_safe
    assert safe.tool_refs == ("tool:memory_recall:v1",)

    unsafe = FixedWorkflowDataflowValidator().validate(
        _workflow(),
        tool_manifest=_tool_manifest("external", idempotent=False),
    )
    assert unsafe.nodes_known
    assert not unsafe.dataflow_safe


@pytest.mark.parametrize("mutation", ["cycle", "unknown_node"])
def test_invalid_workflow_graph_cannot_produce_risk_facts(mutation: str) -> None:
    graph = _workflow()
    if mutation == "cycle":
        graph["nodes"][1]["bindings"] = {"query": "/nodes/output/value"}
    else:
        graph["nodes"][1]["type"] = "python"
    product = _product(workflow=graph)

    with pytest.raises(CandidateRiskFactsError, match="workflow dataflow"):
        ImmutableCandidateRiskFactsBuilder().build(
            product.package,
            _receipt(product),
            candidate_id="candidate-1",
            effect_topology={"effects": []},
            tool_manifest=_tool_manifest(),
        )
