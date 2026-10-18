from __future__ import annotations

from pathlib import Path


def test_tool_component_has_no_domain_regex_surface_mutation() -> None:
    source = (
        Path(__file__).parents[1] / "deskpet" / "agent" / "assembler" / "components" / "tool.py"
    ).read_text(encoding="utf-8")

    assert "is_deep_research_request" not in source
    assert "forced_deep_research" not in source
    assert "image_tool_filtered" not in source
