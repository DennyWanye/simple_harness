# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Current-doc7 Host software oracle for the controlled document UI case.

Run only by the main executor. No selected-Task drive, Store seeding, domain
monkeypatch, network Provider or native UI is involved in this test.
"""

import asyncio

import pytest
from agent_orchestrator.governance.domains import DOC_PROFILE
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from deskpet.orchestration.native_arbitration import (
    CASE,
    KEY,
    OUTPUTS,
    REPORT,
    SOURCES,
    document_arbitration_materials,
    document_arbitration_mission,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


@pytest.mark.asyncio
async def test_current_document_conflict_contextual_ruling_survives_cold_reopen(
    tmp_path, principal, monkeypatch
):
    root = tmp_path / ".local-test-evidence" / "document-contextual-arbitration"
    root.mkdir(parents=True)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(root))
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_CASE", CASE)
    library = root / "library"
    service = OrchestrationService(
        library, OrchestrationSettings(), principal=principal,
        test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(service.start(), 10)
    try:
        assert service.status()["available"], service.status()
        provider = service._effective_provider
        request = document_arbitration_mission()
        created = service.create_mission_with_sources({
            "mission": request, "sources": list(document_arbitration_materials()),
        })
        mission_id = created["mission_id"]
        assert await asyncio.wait_for(service.drain(timeout=8), 10), service.status()
        orch = service._orchestrator
        store = orch.store
        assert DOC_PROFILE.version == "9"
        assert orch.commit.domain_for(mission_id).to_json() == DOC_PROFILE.to_json()
        assert list(store.get_mission(mission_id).success_criteria) == request["success_criteria"]

        workers = [task for task in store.list_tasks(mission_id) if task.kind != "conflict"]
        assert len(workers) == 2
        assert all(task.status.value == "COMPLETED" for task in workers)
        assert all(task.accepted_result_id for task in workers)
        accepted = [store.list_claims(task.accepted_result_id)[0] for task in workers]
        # Doc7 deliberately turns a literal source quote into a VERIFIED
        # source-attribution with a source-specific key. These must instead be
        # two source-backed world candidates before the conflict entrance.
        # drain has already detected the conflict and changed both candidates
        # from source-supported statements to the formal disputed state.
        assert {claim.status.value for claim in accepted} == {"DISPUTED"}
        assert {claim.type for claim in accepted} == {"statement"}
        assert {claim.key for claim in accepted} == {KEY}
        assert {claim.stance for claim in accepted} == {"affirms", "refutes"}
        assert all(claim.confidence_metadata["basis"]["grade"] == "supported"
                   and claim.confidence_metadata["basis"].get("attribution") is None
                   for claim in accepted)
        assert store.list_knowledge(mission_id) == []
        [conflict] = store.list_conflicts(mission_id)
        assert conflict["state"] == "OPEN" and conflict["key"] == KEY
        assert len(conflict["claim_ids"]) == 2
        assert {store.get_claim(cid).status.value for cid in conflict["claim_ids"]} == {"DISPUTED"}
        arbitration_task = store.get_task(conflict["task_id"])
        assert arbitration_task.kind == "conflict"
        assert list(arbitration_task.verification_policy) == [
            "format_check", "rule_check", "critic_review", "human_review",
        ]
        [attempt] = store.list_attempts(arbitration_task.id)
        result = store.find_result_for_attempt(attempt.id)
        assert result is not None and result.verification_state == "SUSPENDED"
        assert arbitration_task.accepted_result_id is None
        checks = {row["layer"]: row["status"] for row in store.list_verifications(result.envelope.id)}
        assert all(checks[layer] == "PASS" for layer in (
            "format_check", "rule_check", "critic_review",
        ))
        [approval] = [row for row in service.approvals(mission_id) if row["kind"] == "arbitration"]
        assert approval["state"] == "PENDING" and approval["topic"] == "conflict"
        assert "contextual" in approval["options"]
        assert store.get_approval(approval["request_id"])["binding"]["result_id"] == result.envelope.id

        # Every conclusion and the Arbiter dossier must be reached through real
        # workspace tools. A Provider answer containing these strings is insufficient.
        calls = orch.assembled.gateway.calls
        actual = [(call["tool"], call["arguments"].get("path"), call["view"]) for call in calls]
        for path, output in OUTPUTS.items():
            assert ("workspace_read_file", path, "work") in actual
            assert ("workspace_write_file", output, "work") in actual
        for path in SOURCES:
            # One read by its Worker and a second by the actual Arbiter Attempt.
            assert sum(tool == "workspace_read_file" and target == path and view == "work"
                       for tool, target, view in actual) >= 2
        for path in OUTPUTS.values():
            assert ("workspace_read_file", path, "work") in actual
        assert ("workspace_write_file", REPORT, "work") in actual
        assert ("workspace_read_file", REPORT, "verify") in actual
        assert provider.by_role.get("planner") == 1
        assert provider.by_role.get("worker", 0) >= 6
        assert provider.by_role.get("arbiter", 0) >= 6
        assert provider.by_role.get("critic", 0) >= 6
        assert store.list_knowledge(mission_id) == []
        before_claims = {cid: store.get_claim(cid).to_json() for cid in conflict["claim_ids"]}

        decided = service.decide(
            approval["request_id"], "arbitrate", ruling="contextual",
            basis="已核对甲乙原句及两份 Worker 产物；按室内与室外条件分别保留，世界结论不自动升格。",
            nonce="native-document-contextual-ruling",
        )
        assert decided["request_state"] == "GRANTED"
        assert store.get_conflict(conflict["conflict_id"])["state"] == "RESOLVED_BY_HUMAN"
        assert {cid: store.get_claim(cid).to_json() for cid in conflict["claim_ids"]} == before_claims
        assert store.list_knowledge(mission_id) == []
        assert store.get_task(arbitration_task.id).accepted_result_id is None
        original = store.get_approval(approval["request_id"])
        assert original["ruling"] == "contextual"
    finally:
        await asyncio.wait_for(service.close(), 10)

    # A fresh Host/SDK instance reads the original decision from persisted state;
    # the empty Provider forbids a hidden repeat of any model call on recovery.
    idle = RoleScriptedProvider({})
    reopened = OrchestrationService(
        library, OrchestrationSettings(), principal=principal, provider=idle,
        drive=False,
    )
    await asyncio.wait_for(reopened.start(), 10)
    try:
        assert reopened.status()["available"], reopened.status()
        assert reopened._orchestrator.commit.domain_for(mission_id).to_json() == DOC_PROFILE.to_json()
        visible = reopened.mission_detail(mission_id)
        assert any(row["conflict_id"] == conflict["conflict_id"]
                   and row["state"] == "RESOLVED_BY_HUMAN" for row in visible["conflicts"])
        ruling = reopened._orchestrator.store.get_approval(approval["request_id"])
        assert ruling["request_id"] == original["request_id"]
        assert ruling["state"] == "GRANTED" and ruling["ruling"] == "contextual"
        assert ruling["basis"] == original["basis"]
        assert reopened._orchestrator.store.list_knowledge(mission_id) == []
        assert idle.by_role == {}
    finally:
        await asyncio.wait_for(reopened.close(), 10)
