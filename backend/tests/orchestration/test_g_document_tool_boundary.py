"""G source authority is IPC-only; exercise the real registry dispatcher."""

import json
from unittest.mock import Mock

import pytest
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.orchestration.service import OrchestrationService
from deskpet.tool_catalog import load_tool_manifest
from deskpet.tools.orchestration_controls import register_orchestration_controls
from deskpet.tools.registry import ToolRegistry


@pytest.mark.parametrize("name", [
    "mission_create", "mission_create_with_sources", "create_with_sources",
    "mission_source_register", "mission_source_supersede", "mission_source_revoke",
    "register_source", "supersede_source", "revoke_source",
])
@pytest.mark.asyncio
async def test_model_dispatch_cannot_enter_mission_source_facade(name, monkeypatch):
    registry = ToolRegistry()
    register_orchestration_controls(registry, ProfileRegistry((
        ProfileSpec(profile_key="agent.general", route_tag=None, driver_kind="react"),
    )))
    assert name not in {item["name"] for item in load_tool_manifest().tools}
    call = Mock(side_effect=AssertionError("model must not enter Mission facade"))
    monkeypatch.setattr(OrchestrationService, "_call", call)
    result = json.loads(registry.dispatch(name, {
        "origin": "ui", "is_ui": True, "sources": [{"path": "sources/forged.md", "content": "x"}],
    }, task_id="model-task"))
    assert result["error"] == f"unknown tool: {name}"
    assert result["retriable"] is False
    actual = await registry.execute_tool(name, {"origin": "ui", "is_ui": True},
                                         session_id="s", task_id="model-task")
    assert actual == {"ok": False, "result": None, "error": f"unknown tool: {name}"}
    call.assert_not_called()


def test_deployment_reports_actual_domain_profiles(tmp_path):
    from agent_orchestrator.governance.domains import DOMAINS
    from deskpet.orchestration.manifest import build_manifest

    manifest = build_manifest(root=tmp_path, deployment={}, settings={}, test_scenario=None, model=None)
    domains = manifest["features"]["domains"]
    assert domains["default"] == "code-v1"
    assert {item["id"]: item["version"] for item in domains["items"]} == {
        key: profile.version for key, profile in DOMAINS.items()
    }
    assert domains["source_commands"] == ["register", "supersede", "revoke"]
