from __future__ import annotations

import pytest

from deskpet.companion.workflows import WorkflowPackAdapterError, WorkflowPackAdapterRegistry


def test_fixed_workflow_overlays_are_bounded() -> None:
    registry = WorkflowPackAdapterRegistry()
    deep = registry.parse(
        "workflow.deep_research",
        {"mode_default": "deep", "max_sub_questions": 6, "disclosure": "d"},
    )
    assert deep.settings["max_sub_questions"] == 6
    slides = registry.parse(
        "workflow.presentation",
        {"pages_default": 12, "theme": "minimal", "image_mode_default": True},
    )
    assert slides.settings["pages_default"] == 12
    assert registry.parse("workflow.durable_task", {"disclosure": "d"}).settings == {}


def test_personal_overlay_does_not_retain_mutable_graph() -> None:
    graph = {
        "schema_version": 1,
        "name": "one",
        "description": "one",
        "entry_node": "input",
        "nodes": [
            {"id": "input", "type": "input", "bindings": {}, "config": {}}
        ],
        "outputs": {"value": "/nodes/input/value"},
        "max_steps": 1,
    }
    overlay = WorkflowPackAdapterRegistry().parse(
        "workflow.personal_v1", {"graph": graph}
    )
    graph["name"] = "mutated"
    assert overlay.settings == {}
    assert overlay.personal_workflow is not None
    assert overlay.personal_workflow.name == "one"


@pytest.mark.parametrize(
    ("workflow_id", "value"),
    [
        ("workflow.deep_research", {"max_sub_questions": 7}),
        ("workflow.presentation", {"output_path": "C:/escape.pptx"}),
        ("workflow.presentation", {"theme": "remote-theme"}),
        ("workflow.durable_task", {"approval_required": False}),
        ("workflow.unknown", {}),
    ],
)
def test_workflow_overlays_reject_host_control_or_unknown_fields(
    workflow_id: str, value: dict
) -> None:
    with pytest.raises(WorkflowPackAdapterError):
        WorkflowPackAdapterRegistry().parse(workflow_id, value)
