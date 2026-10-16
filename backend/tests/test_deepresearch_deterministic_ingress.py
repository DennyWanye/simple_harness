from __future__ import annotations

from pathlib import Path

from tests.host_source import host_source_text


def test_tool_component_has_no_domain_regex_surface_mutation() -> None:
    source = (
        Path(__file__).parents[1] / "deskpet" / "agent" / "assembler" / "components" / "tool.py"
    ).read_text(encoding="utf-8")

    assert "is_deep_research_request" not in source
    assert "forced_deep_research" not in source
    assert "image_tool_filtered" not in source


def test_main_disables_legacy_deepresearch_starter_before_activation() -> None:
    source = host_source_text()
    recovery_source = (
        Path(__file__).parents[1] / "deskpet" / "workflows" / "startup_recovery.py"
    ).read_text(encoding="utf-8")

    binding = source.index("_workflow_research_tools.set_deepresearch_workflow_starter(None)")
    recovery = source.index("await activate_and_recover_workflows(", binding)
    assert binding < recovery
    assert "await workflow_service.activate_runtime(" in recovery_source
    assert "async def _start_deepresearch_graph" not in source
