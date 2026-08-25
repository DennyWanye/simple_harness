from __future__ import annotations

from pathlib import Path

def test_tool_component_has_no_domain_regex_surface_mutation() -> None:
    source = (
        Path(__file__).parents[1]
        / "deskpet" / "agent" / "assembler" / "components" / "tool.py"
    ).read_text(encoding="utf-8")

    assert "is_deep_research_request" not in source
    assert "forced_deep_research" not in source
    assert "image_tool_filtered" not in source


def test_main_disables_legacy_deepresearch_starter_before_activation() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    recovery_source = (
        Path(__file__).parents[1]
        / "deskpet"
        / "workflows"
        / "startup_recovery.py"
    ).read_text(encoding="utf-8")

    binding = source.index(
        "_workflow_research_tools.set_deepresearch_workflow_starter(None)"
    )
    recovery = source.index("await activate_and_recover_workflows(", binding)
    assert binding < recovery
    assert "await workflow_service.activate_runtime(" in recovery_source
    assert "async def _start_deepresearch_graph" not in source


def test_main_snapshots_research_source_switches_into_durable_run() -> None:
    source = (
        Path(__file__).parents[1]
        / "deskpet" / "agent" / "turn_preparer.py"
    ).read_text(encoding="utf-8")

    assert "raw.get('research', {})" in source
    assert "('direct_sources', 'source_packs')" in source
    assert "'research_config'" in source
