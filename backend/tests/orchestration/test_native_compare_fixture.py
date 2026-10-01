# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""P34 COMPARE software control: real SDK selection, tools and Verifier.

This is fixture policy plumbing and source runtime evidence, not native UI or
real-model comparison quality.  The parent runner owns execution.
"""

import asyncio
from pathlib import Path

import pytest
from agent_orchestrator.artifacts.store import read_verified
from agent_orchestrator.contracts import Budget
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.planning.candidate_selection import candidate_path
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import package_of, role_of
from deskpet.orchestration.native_compare import (
    CASE,
    COMBINED,
    FINAL,
    PROBE,
    RESULT,
    TOOLS,
    install_compare_policy,
    native_compare_mission,
    native_compare_provider,
    selection_policy,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


def _spec(request):
    return MissionSpec(
        goal=request["goal"], success_criteria=tuple(request["success_criteria"]),
        tenant_id="local-desktop", idempotency_key=request["idempotency_key"],
        allowed_tools=TOOLS, domain=request["domain"], budget=Budget(**request["budget"]),
        search_policy_version_id=request.get("search_policy_version_id"),
        orchestration_semantics_version="legacy",
    )


@pytest.mark.asyncio
async def test_public_compare_and_unbound_first_then_cold_reopen(tmp_path: Path):
    root = tmp_path / ".local-test-evidence" / CASE
    config = OrchestratorConfig(
        evidence_root=root, model="agent-model", candidates_per_task=1,
        max_concurrency=1, lease_seconds=0.3,
    )
    provider = native_compare_provider()
    async with Orchestrator(config, provider, owner="compare-first") as orch:
        version = install_compare_policy(orch, Principal("fixture-host"))
        assert install_compare_policy(orch, Principal("fixture-host")) == version
        approved = orch.commit.approved_search_policy(version)
        assert approved["policy"] == selection_policy()
        proposal = next(p for p in orch.store.list_policy_proposals()
                        if p["version_id"] == version)
        assert proposal["last_evaluation"]["evidence_kind"] == "fixture"
        assert proposal["state"] == "PROMOTED"

        first_request = native_compare_mission()
        first_request["idempotency_key"] += "-unbound"
        first = await orch.submit_mission(_spec(first_request))
        await asyncio.wait_for(orch.run(), 30)
        [first_task] = orch.store.list_tasks(first.id)
        assert first_task.status.value == "COMPLETED"
        assert orch.commit.selection_round(first_task.id) is None
        assert all(a.role != "synthesizer" for a in orch.store.list_attempts(first_task.id))

        request = native_compare_mission(version)
        assert request["search_policy_version_id"] == version
        mission = await orch.submit_mission(_spec(request))
        await asyncio.wait_for(orch.run(), 30)
        store = orch.store
        assert store.get_mission(mission.id).status.value == "COMPLETED"
        [task] = store.list_tasks(mission.id)
        assert task.status.value == "COMPLETED"
        attempts = store.list_attempts(task.id)
        workers = [a for a in attempts if a.role == "worker"]
        [c_attempt] = [a for a in attempts if a.role == "synthesizer"]
        assert len(workers) == 2 and len(attempts) == 3
        round_ = orch.commit.selection_round(task.id)
        assert round_ and round_["decision_id"]
        decision = store.get_receipt(round_["decision_id"])
        assert decision["action"] == "synthesize"
        chosen = decision["selected_inputs"]
        assert len({row["result_id"] for row in chosen}) == 2
        assert {row["state"] for row in orch.commit.selection_candidates(task.id)
                if row["result_id"] in {item["result_id"] for item in chosen}} == {"READY"}
        assert {row["result_id"] for row in chosen} == {
            store.find_result_for_attempt(a.id).envelope.id for a in workers
        }
        for candidate in chosen:
            result = store.get_result(candidate["result_id"])
            # Selection retains both READY inputs; once C commits, originals
            # become superseded results rather than independent final deliveries.
            assert result.verification_state == "REJECTED" and result.verdict == "superseded"
            checks = store.list_verifications(result.envelope.id)
            assert any(row["layer"] == "code_test" and row["status"] == "PASS"
                       for row in checks)
        c = store.find_result_for_attempt(c_attempt.id)
        assert c.verdict == "PASS" and task.accepted_result_id == c.envelope.id
        assert c.envelope.id not in {row["result_id"] for row in chosen}
        c_checks = store.list_verifications(c.envelope.id)
        assert any(row["layer"] == "code_test" and row["status"] == "PASS"
                   for row in c_checks)
        artifacts = {a.path: a for a in store.list_artifacts(c_attempt.id)}
        artifact_paths = set(artifacts)
        assert {RESULT, PROBE, COMBINED} <= artifact_paths
        assert read_verified(artifacts[RESULT]).decode() == FINAL
        combined = read_verified(artifacts[COMBINED]).decode()
        assert "# C read both candidates" in combined
        assert all(candidate_path(candidate["result_id"], RESULT) in combined
                   for candidate in chosen)

        calls = orch.assembled.gateway.calls
        c_reads = {(call["arguments"].get("path"), call["view"])
                   for call in calls if call["tool"] == "workspace_read_file"}
        for candidate in chosen:
            result_ref = next(ref for ref in candidate["artifact_refs"]
                              if ref["path"] == RESULT)
            assert (candidate_path(candidate["result_id"], result_ref["path"]), "work") in c_reads
        assert ("run_tests", PROBE, "work") in {
            (call["tool"], call["arguments"].get("path"), call["view"])
            for call in calls
        }
        synthesis_packages = [package_of(r) for r in provider.requests
                              if role_of(r) == "synthesizer"]
        assert synthesis_packages and all(
            len(p["selection_inputs"]["inputs"]) == 4 for p in synthesis_packages
        )

    fresh = native_compare_provider()
    await asyncio.sleep(0.35)
    async with Orchestrator(config, fresh, owner="compare-second") as reopened:
        assert install_compare_policy(reopened, Principal("fixture-host")) == version
        await asyncio.wait_for(reopened.run(), 30)
        assert reopened.store.get_mission(mission.id).status.value == "COMPLETED"
        assert fresh.by_role == {}


@pytest.mark.asyncio
async def test_host_compare_case_exposes_approved_selector(
    tmp_path: Path, principal, monkeypatch,
):
    root = tmp_path / ".local-test-evidence" / CASE
    root.mkdir(parents=True)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(root))
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_CASE", CASE)
    service = OrchestrationService(
        root / "library", OrchestrationSettings(), principal=principal,
        test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(service.start(), 20)
    try:
        assert service.status()["available"], service.status()
        assert service._config.model == service._effective_provider.model == "agent-model"
        assert service.status()["sandbox"]["ok"], service.status()
        eligible = service.policy_status()["eligible_search_policies"]
        assert len(eligible) == 1 and eligible[0]["policy"] == selection_policy()
        version = eligible[0]["version_id"]
        created = service.create_mission(native_compare_mission(version))
        assert await service.drain(timeout=30), service.status()
        [task] = service._orchestrator.store.list_tasks(created["mission_id"])
        assert task.status.value == "COMPLETED"
        assert service._orchestrator.commit.selection_round(task.id)["decision_id"]
    finally:
        await asyncio.wait_for(service.close(), 20)

    reopened = OrchestrationService(
        root / "library", OrchestrationSettings(), principal=principal,
        test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(reopened.start(), 20)
    try:
        assert reopened.status()["available"], reopened.status()
        assert await reopened.drain(timeout=10)
        assert reopened._effective_provider.by_role == {}
        assert reopened.policy_status()["active_version_id"] == version
    finally:
        await asyncio.wait_for(reopened.close(), 20)
