# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = BACKEND_ROOT / "main.py"
TURN_PREPARER_PATH = BACKEND_ROOT / "deskpet" / "agent" / "turn_preparer.py"
RUN_PRESENTER_PATH = BACKEND_ROOT / "deskpet" / "agent" / "run_presenter.py"
REACT_DRIVER_PATH = BACKEND_ROOT / "deskpet" / "harness" / "drivers" / "react.py"
REACT_LOOP_PATH = (
    BACKEND_ROOT / "deskpet" / "harness" / "drivers" / "react_loop.py"
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name) and node.func.id == name
            or isinstance(node.func, ast.Attribute) and node.func.attr == name
        )
    ]


def _kw_names(call: ast.Call) -> set[str]:
    return {item.arg for item in call.keywords if item.arg is not None}


def _kw(call: ast.Call, name: str) -> ast.AST:
    return next(item.value for item in call.keywords if item.arg == name)


def test_product_preparer_and_venue_receive_the_v2_tool_registry() -> None:
    assemble = [
        call for call in _calls(_tree(TURN_PREPARER_PATH), "assemble")
        if {"tool_registry", "current_message_id"} <= _kw_names(call)
    ]
    execute = [
        call for call in _calls(_tree(MAIN_PATH), "open")
        if "tool_registry" in _kw_names(call)
        and ast.unparse(call.func.value) == "_harness_venue"
    ]

    assert len(assemble) == len(execute) == 1
    assert ast.unparse(_kw(assemble[0], "tool_registry")) == "tool_registry"
    assert ast.unparse(_kw(execute[0], "tool_registry")) == "deskpet_tool_registry_v2"


def test_product_loop_factory_wires_policy_into_external_only_agent_loop() -> None:
    calls = [
        call for call in _calls(_tree(MAIN_PATH), "build_agent")
        if {"llm_registry", "tool_registry", "receipt_store_getter"} <= _kw_names(call)
    ]
    assert len(calls) == 1
    call = calls[0]
    assert {
        "completion_probe", "session_todo_getter", "signature_repeat_threshold",
        "session_goal_store", "goal_checker", "compressor", "skill_loader",
        "skill_matcher", "memory_curator", "evidence_gate",
        "pipeline_problem_type", "pipeline_needs_investigation",
        "pipeline_observability", "convergence_report_on_stop",
        "response_quality_gate",
    } <= _kw_names(call)
    assert "external_tool_dispatch" not in _kw_names(call)
    assert "tool_path_recorder" not in _kw_names(call)
    assert ast.unparse(_kw(call, "pipeline_problem_type")) == "problem_type"
    assert 'problem_type = loop_options.get("pipeline_problem_type")' in MAIN_PATH.read_text(
        encoding="utf-8"
    )


def test_agent_loop_invocation_is_owned_by_react_collaborator() -> None:
    main_source = MAIN_PATH.read_text(encoding="utf-8")
    react_tree = _tree(REACT_LOOP_PATH)
    run_calls = [
        call for call in _calls(react_tree, "run")
        if isinstance(call.func, ast.Attribute)
        and ast.unparse(call.func.value) == "loop"
    ]

    assert "async def _run_chat(" not in main_source
    assert "async def _run_chat_with_timeout(" not in main_source
    assert len(run_calls) == 1
    assert ast.unparse(run_calls[0].args[0]).startswith("[")
    assert ast.unparse(run_calls[0].keywords[-1].value) == "kwargs"


def test_lifespan_activates_and_closes_the_product_harness() -> None:
    tree = _tree(MAIN_PATH)
    activates = _calls(tree, "_activate_product_harness")
    closes = [
        call for call in _calls(tree, "close")
        if isinstance(call.func, ast.Attribute)
        and ast.unparse(call.func.value) == "_harness_runtime"
    ]

    assert len(activates) == 1
    assert len(closes) == 1
    assert ast.unparse(_kw(closes[0], "timeout")) == "5.0"


def test_plan_confirmation_uses_complete_kernel_decision_fence() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")

    assert "_harness_runtime.run_client.signal" in source
    for field in ("run_id", "decision_id", "nonce", "version"):
        assert f'payload.get("{field}")' in source
    assert "plan confirmation requires the durable decision fence" in source


def test_async_handoff_projection_remains_product_owned() -> None:
    source = RUN_PRESENTER_PATH.read_text(encoding="utf-8")

    assert 'presenter.register("domain", AsyncHandoffEvent, _present_handoff)' in source
    assert "isinstance(event, AsyncHandoffEvent)" in source
    assert '"handoff_run_id": event.run_id' in source
