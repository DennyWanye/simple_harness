"""The product Mission door must expose the SDK's scoped knowledge readers."""

from copy import deepcopy

import pytest
from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider, critic_step, envelope_step, graph_proposal_step,
)

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import NOTES_TASK, WORKSPACE_TOOLS, notes_request


@pytest.mark.asyncio
async def test_default_host_mission_can_reach_current_knowledge_reader(orchestration_root, principal):
    task = deepcopy(NOTES_TASK)
    task["allowed_tools"] = [*WORKSPACE_TOOLS, "knowledge_list", "knowledge_read"]
    provider = RoleScriptedProvider({
        "planner": [graph_proposal_step([task])],
        "worker": [
            ("knowledge_list", {}),
            ("knowledge_read", {"id": "not-in-this-mission"}),
            ("workspace_write_file", {"path": "NOTES.md", "content": "No current source was available."}),
            envelope_step(summary="Recorded the absence of current evidence", artifacts=["NOTES.md"], claims=["NOTES.md records the absence of current evidence"]),
        ],
        "critic": [critic_step(verdict="PASS", criteria_met=True)] * 3,
    })
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=provider, principal=principal, drive=False,
    )
    await service.start()
    try:
        assert {"knowledge_list", "knowledge_read"} <= set(service.runtime_tool_names())
        created = service.create_mission(notes_request("knowledge-door"))
        assert await service.drain(timeout=20)
        assert service.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED"
        calls = service._orchestrator.assembled.gateway.calls
        catalog = next(c for c in calls if c["tool"] == "knowledge_list")
        missing = next(c for c in calls if c["tool"] == "knowledge_read")
        assert catalog["outcome"] == "succeeded"
        assert missing["outcome"] != "succeeded"
        assert "not_allowed" not in str(missing)
        assert missing["stage"] == "execute"
        assert missing["error_code"] == "workspace_error"
    finally:
        await service.close()
