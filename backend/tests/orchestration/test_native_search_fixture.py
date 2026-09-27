# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""P34 controlled software oracle via public Orchestrator Provider injection.

This is neither a native UI run nor a real-model result. The main runner owns
execution; this file does not seed Store state or manufacture verification.
"""

import asyncio
from pathlib import Path

import pytest
from agent_orchestrator.contracts import Budget
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import package_of, role_of
from deskpet.orchestration.native_search import (
    CASE,
    PROBE,
    SYNTHESIS_PROBE,
    TOOLS,
    native_search_mission,
    native_search_provider,
)
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


@pytest.mark.asyncio
async def test_p34_fragment_crossbranch_public_orchestrator(tmp_path: Path):
    request = native_search_mission()
    assert request["domain"] == "code-v1"
    assert request["budget"] == {"max_tokens": 450_000, "max_attempts": 20}
    assert "allowed_tools" not in request
    assert set(request["synthesis"]) == {"goal", "success_criteria", "budget"}
    root = tmp_path / ".local-test-evidence" / CASE
    config = OrchestratorConfig(
        evidence_root=root, max_concurrency=1, candidates_per_task=1, lease_seconds=0.3,
    )
    assert config.manager_after_failures == 2
    provider = native_search_provider()
    spec = MissionSpec(
        goal=request["goal"], success_criteria=tuple(request["success_criteria"]),
        tenant_id="local-desktop", idempotency_key=request["idempotency_key"],
        allowed_tools=tuple(TOOLS), domain=request["domain"],
        budget=Budget(**request["budget"]), synthesis=request["synthesis"],
    )
    async with Orchestrator(config, provider, owner="p34-first") as orch:
        mission = await orch.submit_mission(spec)
        await asyncio.wait_for(orch.run(), 30)
        store = orch.store
        assert store.get_mission(mission.id).status.value == "COMPLETED"
        by_goal = {task.goal: task for task in store.list_tasks(mission.id)}
        a, b, c = (by_goal[goal] for goal in ("origin A", "independent B", "consumer C"))
        a_attempts = store.list_attempts(a.id)
        assert len(a_attempts) == 2
        assert all((result := store.find_result_for_attempt(attempt.id)) is not None
                   and result.verdict == "FAIL" for attempt in a_attempts)
        assert a.status.value == "CANCELLED"  # old whole A remains failed evidence
        assert b.status.value == "COMPLETED"

        [projection] = store.list_fragment_validations(mission.id)
        receipt = store.get_receipt(projection["projection_receipt_id"])
        f = store.get_task(receipt["validation_task_id"])
        assert f.status.value == "COMPLETED"
        assert store.get_result(f.accepted_result_id).verdict == "PASS"
        mapped = receipt["output_path_mapping"]["good.md"]
        assert set(c.dependency_ids) == {f.id, b.id}
        assert c.status.value == "COMPLETED"
        assert store.get_result(c.accepted_result_id).verdict == "PASS"
        s = next(task for task in by_goal.values() if task.kind == "synthesis")
        assert s.status.value == "COMPLETED"
        assert store.get_result(s.accepted_result_id).verdict == "PASS"
        assert "code_test" in s.verification_policy
        assert f"pytest:{SYNTHESIS_PROBE}" in s.success_criteria
        s_checks = store.list_verifications(store.get_result(s.accepted_result_id).envelope.id)
        assert any(row["layer"] == "code_test" and row["status"] == "PASS"
                   for row in s_checks)
        assert len(store.list_graph_changes(mission.id)) == 2

        calls = orch.assembled.gateway.calls
        actual = [(call["tool"], call["arguments"].get("path"), call["view"])
                  for call in calls]
        for operation in (
            ("workspace_write_file", "good.md", "work"),
            ("workspace_write_file", "b.md", "work"),
            ("workspace_read_file", "good.md", "work"),  # F reads A's real partial file
            ("workspace_write_file", mapped, "work"),
            ("workspace_read_file", mapped, "work"),  # C reads accepted F
            ("workspace_read_file", "b.md", "work"),  # C reads independent B
            ("workspace_write_file", "consumer.md", "work"),
            ("workspace_write_file", PROBE, "work"),
            ("run_tests", PROBE, "work"),
            ("workspace_read_file", "consumer.md", "work"),  # S reads verified C
            ("workspace_write_file", "final.md", "work"),
            ("workspace_write_file", SYNTHESIS_PROBE, "work"),
            ("run_tests", SYNTHESIS_PROBE, "work"),
        ):
            assert operation in actual
        assert provider.by_role["manager"] == 2
        manager_packages = [package_of(item) for item in provider.requests
                            if role_of(item) == "manager"]
        assert "fragment_validation" in manager_packages[0]
        assert "validated_fragment" in manager_packages[1]
        assert manager_packages[1]["validated_fragment"]["validation_task_id"] == f.id
        synthesis_packages = [package_of(item) for item in provider.requests
                              if role_of(item) == "synthesizer"]
        assert any(any(row.get("type") == "test_observation" and PROBE in row.get("content", "")
                       for row in package["verified_knowledge"])
                   for package in synthesis_packages)
        assert provider.by_role["worker"] > 0

    # A fresh Provider can reopen the completed Mission without replaying a role.
    fresh = native_search_provider()
    await asyncio.sleep(0.35)
    async with Orchestrator(config, fresh, owner="p34-second") as reopened:
        await asyncio.wait_for(reopened.run(), 30)
        assert reopened.store.get_mission(mission.id).status.value == "COMPLETED"
        assert fresh.by_role == {}


@pytest.mark.asyncio
async def test_p34_public_ui_shape_through_real_host_service_defaults(
    tmp_path: Path, principal, monkeypatch,
):
    """Requires main's document-ui case wiring; native UI itself is a separate gate."""
    root = tmp_path / ".local-test-evidence" / CASE
    root.mkdir(parents=True)
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_DIR", str(root))
    monkeypatch.setenv("DESKPET_ORCH_UI_FIXTURE_CASE", CASE)
    library = root / "library"
    # NEXT-TG-1.0 §9: the product default is now two concurrent Missions/model calls.  This
    # legacy flat (non-hierarchical) scenario relies on the Manager replacing A after two
    # failures; with a second slot free, A's third retry is allocated before the Manager
    # round lands (recorded in PLAN-STATUS as a legacy-path race).  Run it serially.
    service = OrchestrationService(
        library, OrchestrationSettings(max_concurrency=1, max_concurrent_model_calls=1), principal=principal,
        test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(service.start(), 20)
    try:
        assert service.status()["available"], service.status()
        assert service._config.manager_after_failures == 2
        # Use the real local isolation probe, never a forged sandbox PASS.
        assert service.status()["sandbox"]["ok"], service.status()["sandbox"]
        assert service.status()["code_execution"] == "sandboxed"
        request = native_search_mission()
        assert "allowed_tools" not in request
        created = service.create_mission(request)
        mission_id = created["mission_id"]
        assert await service.drain(timeout=30), service.status()
        orch = service._orchestrator
        store = orch.store
        assert store.get_mission(mission_id).status.value == "COMPLETED"
        by_goal = {task.goal: task for task in store.list_tasks(mission_id)}
        a, b, c = (by_goal[goal] for goal in ("origin A", "independent B", "consumer C"))
        attempts = store.list_attempts(a.id)
        assert len(attempts) == 2
        assert all((result := store.find_result_for_attempt(attempt.id)) is not None
                   and result.verdict == "FAIL" for attempt in attempts)
        [projection] = store.list_fragment_validations(mission_id)
        receipt = store.get_receipt(projection["projection_receipt_id"])
        f = store.get_task(receipt["validation_task_id"])
        assert a.status.value == "CANCELLED"
        assert b.status.value == f.status.value == c.status.value == "COMPLETED"
        assert set(c.dependency_ids) == {f.id, b.id}
        assert store.get_result(f.accepted_result_id).verdict == "PASS"
        assert store.get_result(c.accepted_result_id).verdict == "PASS"
        s = next(task for task in by_goal.values() if task.kind == "synthesis")
        assert s.status.value == "COMPLETED"
        assert "code_test" in s.verification_policy
        assert store.get_result(s.accepted_result_id).verdict == "PASS"
        checks = store.list_verifications(store.get_result(s.accepted_result_id).envelope.id)
        assert any(row["layer"] == "code_test" and row["status"] == "PASS"
                   for row in checks)
        calls = orch.assembled.gateway.calls
        actual = {(call["tool"], call["arguments"].get("path"), call["view"])
                  for call in calls}
        mapped = receipt["output_path_mapping"]["good.md"]
        assert {
            ("workspace_read_file", "good.md", "work"),
            ("workspace_write_file", mapped, "work"),
            ("workspace_read_file", mapped, "work"),
            ("workspace_read_file", "b.md", "work"),
            ("run_tests", PROBE, "work"),
            ("workspace_read_file", "consumer.md", "work"),
            ("run_tests", SYNTHESIS_PROBE, "work"),
        } <= actual
        assert service._effective_provider.by_role["manager"] == 2
    finally:
        await asyncio.wait_for(service.close(), 20)

    # Completed cold reopen only: in-flight Provider/tool recovery is not asserted here.
    reopened = OrchestrationService(
        library, OrchestrationSettings(), principal=principal,
        test_scenario="document-ui", drive=False,
    )
    await asyncio.wait_for(reopened.start(), 20)
    try:
        assert reopened.status()["available"], reopened.status()
        assert await reopened.drain(timeout=10)
        assert reopened._orchestrator.store.get_mission(mission_id).status.value == "COMPLETED"
        assert reopened._effective_provider.by_role == {}
    finally:
        await asyncio.wait_for(reopened.close(), 20)
