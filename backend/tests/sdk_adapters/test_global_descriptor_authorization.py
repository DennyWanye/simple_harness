from __future__ import annotations

from deskpet.sdk_adapters.tools import (
    ProductToolInventoryEntry,
    filter_sdk_catalog_for_workspace,
)


def test_descriptor_catalog_preserves_unavailable_tools_without_executing_them() -> None:
    inventory = (
        ProductToolInventoryEntry(
            "tool_search", "async", "read_file", "simple_harness", "1", "a" * 64, "safe"
        ),
        ProductToolInventoryEntry(
            "write_file", "async", "write_file", "simple_harness", "1", "b" * 64, "requires_project"
        ),
    )
    specs = [
        {"name": item.name, "description": item.name, "input_schema": {"type": "object"}}
        for item in inventory
    ]
    selected, projected = filter_sdk_catalog_for_workspace(
        {
            "specs": specs,
            "tool_names": [item.name for item in inventory],
            "tool_count": len(inventory),
            "schema_fingerprints": {item.name: item.execution_identity for item in inventory},
        },
        inventory,
        workspace_resolution_kind="projectless",
    )
    assert {item.name for item in projected} == {"tool_search", "write_file"}
    assert {item["name"] for item in selected["descriptor_specs"]} == {
        "tool_search", "write_file"
    }
    assert selected["tool_names"] == ["tool_search"]
    unavailable = next(item for item in projected if item.name == "write_file")
    assert unavailable.availability_reason == "workspace_unscoped"
