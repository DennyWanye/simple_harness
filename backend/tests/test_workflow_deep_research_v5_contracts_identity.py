from __future__ import annotations

import json
from pathlib import Path

from deskpet.workflows.definitions.v1 import DEEP_RESEARCH_V1
from deskpet.workflows.definitions.v2 import DEEP_RESEARCH_V2
from deskpet.workflows.definitions.v3 import DEEP_RESEARCH_V3
from deskpet.workflows.definitions.v4 import DEEP_RESEARCH_V4
from deskpet.workflows.definitions.v5 import DEEP_RESEARCH_V5, deep_research_initial_state
from deskpet.workflows.definitions.deep_research_v5_evidence import POLICY_HASH
from deskpet.workflows.definitions.deep_research_v5_report import REPORT_QUALITY_RUBRIC_V1_HASH
from deskpet.workflows.definitions.v5.deep_research import NODE_IDS, PUBLIC_STAGE_IDS


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_identity.json"


def test_v5_manifest_is_frozen_and_legacy_manifests_remain_unchanged() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fixture_schema_version"] == 1
    assert DEEP_RESEARCH_V5.manifest.to_dict() == fixture["v5_manifest"]
    for compiled in (DEEP_RESEARCH_V1, DEEP_RESEARCH_V2, DEEP_RESEARCH_V3, DEEP_RESEARCH_V4):
        assert compiled.manifest.implementation_bundle_hash == fixture["legacy_manifest_bundle_hashes"][compiled.manifest.workflow_version]


def test_v5_graph_identity_has_explicit_checkpointable_quality_loops() -> None:
    nodes = set(NODE_IDS)
    assert {"gap_evaluate", "gap_work", "gap_join", "quality_audit", "repair_work", "repair_join"} <= nodes
    conditional_sources = {edge.source for edge in DEEP_RESEARCH_V5.definition.conditional_edges}
    assert {"gap_evaluate", "gap_join", "quality_audit", "repair_join"} <= conditional_sources
    assert DEEP_RESEARCH_V5.definition.loop_budgets == {
        "gap_work_iterations": 64,
        "repair_iterations": 16,
    }
    assert DEEP_RESEARCH_V5.definition.loop_budget_bindings == {
        "gap_join": "gap_work_iterations",
        "repair_join": "repair_iterations",
    }
    assert DEEP_RESEARCH_V5.definition.max_supersteps == 384
    assert DEEP_RESEARCH_V5.definition.recursion_limit == 512
    assert DEEP_RESEARCH_V5.definition.policy_manifest["evidence_admission_policy_hash"] == POLICY_HASH
    assert DEEP_RESEARCH_V5.definition.policy_manifest["report_quality_rubric_hash"] == REPORT_QUALITY_RUBRIC_V1_HASH
    assert PUBLIC_STAGE_IDS == ("normalize", "plan", "research", "gap", "rerank", "synth", "quality", "persist", "finalize")


def test_v5_initial_state_is_versioned_without_claiming_a_terminal_result() -> None:
    state = deep_research_initial_state(topic="education", run_id="run-v5", thread_id="thread-v5", session_id="session-v5", research_config={"enabled": True})
    assert state["schema_version"] == 5
    assert state["workflow_version"] == "v5"
    values = state["values"]
    assert values["topic"] == "education"
    assert values["mode"] == "standard"
    assert values["research_config"] == {"enabled": True}
    assert values["contract_schema_version"] == 1
    assert values["loop_policy"] is None
    assert values["synthesis_route"] is None
    assert values["delivery_decision"] is None
    assert values["terminal_public"] is None
    assert values["terminal_status"] is None
