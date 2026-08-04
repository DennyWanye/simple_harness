from __future__ import annotations

import copy

import pytest

from deskpet.companion.personal_workflow import (
    PersonalWorkflowError,
    plan_personal_workflow_tool_node,
    parse_personal_workflow_v1,
)


def _graph() -> dict:
    return {
        "schema_version": 1,
        "name": "safe",
        "description": "safe graph",
        "entry_node": "input",
        "nodes": [
            {"id": "input", "type": "input", "bindings": {}, "config": {}},
            {
                "id": "lookup", "type": "tool_call",
                "bindings": {"query": "/nodes/input/value"},
                "config": {"tool_name": "memory_recall"}, "retry": 2,
            },
            {
                "id": "output", "type": "output",
                "bindings": {"value": "/nodes/lookup/result"}, "config": {},
            },
        ],
        "outputs": {"result": "/nodes/output/value"},
        "max_steps": 3,
    }


def _safe_tool(name: str) -> dict:
    return {
        "stable_handler_id": "core.memory_recall.v1",
        "tool_name": name,
        "spec_ref": "spec:" + "a" * 64,
        "schema_hash": "b" * 64,
        "execution_build_identity": "c" * 64,
        "effect_policy_hash": "d" * 64,
        "effect": "read_only",
        "idempotent": name == "memory_recall",
    }


def test_personal_workflow_accepts_only_bounded_safe_dag() -> None:
    parsed = parse_personal_workflow_v1(_graph(), tool_resolver=_safe_tool)
    assert parsed.max_steps == 3
    assert len(parsed.graph_hash) == 64
    first = plan_personal_workflow_tool_node(
        parsed,
        node_id="lookup",
        child_run_id="child-1",
        selection_id="selection-1",
        attempt_ordinal=0,
    )
    retry = plan_personal_workflow_tool_node(
        parsed,
        node_id="lookup",
        child_run_id="child-1",
        selection_id="selection-1",
        attempt_ordinal=1,
    )
    assert first.logical_effect_id == retry.logical_effect_id
    assert first.stable_call_id == retry.stable_call_id
    assert first.attempt_ordinal != retry.attempt_ordinal


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda g: g.update(max_steps=33), "invalid_max_steps"),
        (lambda g: g["nodes"][1]["bindings"].update(query="bad"), "invalid_json_pointer"),
        (lambda g: g["nodes"][1]["config"].update(callable="evil"), "invalid_tool_call"),
        (lambda g: g["nodes"][0].update(bindings={"x": "/nodes/output/value"}), "cycle"),
        (lambda g: g["outputs"].update(result="/nodes/missing/value"), "output_node_missing"),
        (lambda g: g["nodes"][1]["bindings"].update(query="/secrets/token"), "invalid_pointer_root"),
    ],
)
def test_personal_workflow_rejects_unsafe_graphs(mutate, code: str) -> None:
    graph = copy.deepcopy(_graph())
    mutate(graph)
    with pytest.raises(PersonalWorkflowError) as exc:
        parse_personal_workflow_v1(graph, tool_resolver=_safe_tool)
    assert exc.value.code == code


def test_personal_workflow_rejects_non_idempotent_tool() -> None:
    with pytest.raises(PersonalWorkflowError) as exc:
        parse_personal_workflow_v1(
            _graph(),
            tool_resolver=lambda name: {
                **_safe_tool(name),
                "effect": "opaque_manual",
                "idempotent": False,
            },
        )
    assert exc.value.code == "unsafe_tool_retry"
