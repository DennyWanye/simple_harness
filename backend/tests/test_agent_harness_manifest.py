# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from pathlib import Path

from context import ServiceContext
from deskpet.agent.harness_manifest import (
    HARNESS_SERVICES,
    KNOWN_DEFECT_REMEDIATIONS,
    REQUEST_LIFECYCLE,
    acceptance_coverage,
    harness_service_names,
    lifecycle_stage_ids,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_IDS = {f"AC-{i}" for i in range(1, 9)}


def _owner_path(owner: str) -> Path:
    path_text = owner.split("::", 1)[0].replace("/", "\\")
    return REPO_ROOT / path_text


def test_lifecycle_stage_ids_are_ordered_and_unique() -> None:
    ids = lifecycle_stage_ids()
    assert ids == (
        "ws_ingress",
        "context_assembly",
        "pre_loop_problem_pipeline",
        "agent_loop",
        "tool_dispatch",
        "completion_gates",
        "subagent_sidecar",
        "ws_egress",
    )
    assert len(ids) == len(set(ids))


def test_lifecycle_owner_files_exist_and_recovery_is_explicit() -> None:
    for stage in REQUEST_LIFECYCLE:
        assert _owner_path(stage.owner).exists(), stage
        assert stage.responsibility.strip()
        assert stage.recovery.strip()
        assert stage.persistence in {"ephemeral", "session", "artifact", "config"}
        assert stage.observability, f"{stage.id} must expose trace vocabulary"


def test_stateful_boundary_documents_current_durable_gap() -> None:
    by_id = {stage.id: stage for stage in REQUEST_LIFECYCLE}
    assert "not durable" in by_id["ws_ingress"].recovery.lower()
    assert "no exact node checkpoint" in by_id["agent_loop"].recovery.lower()
    assert by_id["tool_dispatch"].persistence == "session"
    assert by_id["subagent_sidecar"].persistence == "session"
    assert "process-local" in by_id["subagent_sidecar"].recovery
    assert {
        stage.id for stage in REQUEST_LIFECYCLE if stage.parent == "agent_loop"
    } == {
        "tool_dispatch",
        "completion_gates",
        "subagent_sidecar",
        "ws_egress",
    }


def test_manifest_matches_main_context_then_problem_pipeline_order() -> None:
    source = (REPO_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    context_pos = source.index("_turn_preparer.prepare_context(")
    pipeline_pos = source.index("_turn_preparer.route_intent(")
    assert lifecycle_stage_ids().index("context_assembly") < lifecycle_stage_ids().index(
        "pre_loop_problem_pipeline"
    )
    assert context_pos < pipeline_pos


def test_harness_services_are_valid_service_context_names() -> None:
    ctx = ServiceContext()
    for service in HARNESS_SERVICES:
        ctx.register(service.name, object())
        assert ctx.get(service.name) is not None
        assert service.owner.strip()
        assert service.responsibility.strip()
        assert service.required_for


def test_service_names_are_unique_and_cover_critical_harness_wiring() -> None:
    names = harness_service_names()
    assert len(names) == len(set(names))
    for expected in {
        "context_assembler",
        "goal_checker",
        "skill_loader",
        "subagent_scheduler",
        "subagent_registry",
        "problem_pipeline",
        "pipeline_evidence_gate",
        "pipeline_self_check_gate",
        "pipeline_convergence_controller",
    }:
        assert expected in names


def test_known_defects_cover_all_acceptance_criteria() -> None:
    coverage = acceptance_coverage()
    assert set(coverage) == {
        "D1_CONTROL_FLOW_DISPERSED",
        "D2_NOT_DURABLE_STATE_MACHINE",
        "D3_MAIN_GLUE_TOO_THICK",
        "D4_FLAG_SERVICE_COMPLEXITY",
        "D5_MULTI_AGENT_ADD_ON",
        "D6_TRACE_NOT_STRUCTURED",
        "D7_TEST_COST_HIGH",
        "D8_LOW_FRAMEWORK_REUSE",
    }
    covered = {ac for ids in coverage.values() for ac in ids}
    assert covered == ACCEPTANCE_IDS


def test_defect_remediations_are_actionable() -> None:
    for item in KNOWN_DEFECT_REMEDIATIONS:
        assert item.symptom.strip()
        assert item.remediation.strip()
        assert set(item.acceptance_ids) <= ACCEPTANCE_IDS
        assert item.remediation != item.symptom
