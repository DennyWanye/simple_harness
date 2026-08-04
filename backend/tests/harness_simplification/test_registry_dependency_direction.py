from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.context_adapter import ReservedModelFieldError
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import ToolOutcomeState


SCHEMA = {
    "name": "probe",
    "description": "probe",
    "parameters": {"type": "object", "properties": {}},
}


def _context(*, call_id: str = "call-1", effect_id: str = "effect-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope-1",
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        turn_id="turn-1",
        workspace="F:/workspace",
        write_scope_root="F:/workspace",
        capability_hash="c" * 64,
        scope_hash="d" * 64,
        provider_plan=("fixture",),
        run_id="run-1",
        call_id=call_id,
        effect_id=effect_id,
        trace_id="trace-1",
    )


def test_registry_has_no_harness_dependency_or_legacy_prepared_entrypoints() -> None:
    path = Path(__file__).resolve().parents[2] / "deskpet" / "tools" / "registry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    harness_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "deskpet.harness"
        ):
            harness_imports.append(f"{node.lineno}:{node.module}")
        elif isinstance(node, ast.Import):
            harness_imports.extend(
                f"{node.lineno}:{alias.name}"
                for alias in node.names
                if alias.name.startswith("deskpet.harness")
            )

    assert harness_imports == []
    assert not hasattr(ToolRegistry, "prepare_execution_call")
    assert not hasattr(ToolRegistry, "execute_call")


@pytest.mark.asyncio
async def test_canonical_prepared_path_keeps_host_context_out_of_model_args() -> None:
    registry = ToolRegistry()
    observed: dict[str, object] = {}

    async def handler(args, context):
        observed["args"] = dict(args)
        observed["context"] = context
        return json.dumps({"ok": True, "value": "done"})

    registry.register(
        "probe",
        "test",
        SCHEMA,
        lambda args, task_id: json.dumps({"ok": True}),
        context_handler=handler,
        outcome_parser_id="json_error_envelope_v1",
    )
    registry.set_session_context("session-1", {"_project_root": "attacker-visible"})
    context = _context()
    prepared = registry.prepare_call(
        "probe",
        {"value": 7},
        context.session_id,
        context.call_id,
        execution_context=context,
    )

    outcome = await registry.execute_prepared(
        prepared,
        effect_id=context.effect_id,
        execution_context=context,
    )

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed == {"args": {"value": 7}, "context": context}


@pytest.mark.asyncio
async def test_canonical_read_does_not_require_durable_grant_just_because_legacy_gate_exists() -> None:
    registry = ToolRegistry()
    observed = []

    async def handler(args, context):
        observed.append((dict(args), context))
        return json.dumps({"ok": True, "content": "ready"})

    registry.set_permission_gate(object())
    registry.register(
        "file_read", "file", SCHEMA,
        lambda args, task_id: json.dumps({"ok": True}),
        context_handler=handler,
        permission_category="read_file",
        outcome_parser_id="json_error_envelope_v1",
    )
    context = _context()
    prepared = registry.prepare_call(
        "file_read", {}, context.session_id, context.call_id,
        execution_context=context,
    )

    outcome = await registry.execute_prepared(
        prepared, effect_id=context.effect_id, execution_context=context,
    )

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed == [({}, context)]


@pytest.mark.asyncio
async def test_canonical_write_still_requires_durable_grant_with_legacy_gate() -> None:
    registry = ToolRegistry()
    calls = 0

    async def handler(args, context):
        nonlocal calls
        calls += 1
        return json.dumps({"path": "F:/workspace/result.txt"})

    registry.set_permission_gate(object())
    registry.register(
        "file_write", "file", SCHEMA,
        lambda args, task_id: json.dumps({"path": "F:/workspace/result.txt"}),
        context_handler=handler,
        permission_category="write_file",
        outcome_parser_id="artifact_envelope_v1",
    )
    context = _context()
    prepared = registry.prepare_call(
        "file_write", {}, context.session_id, context.call_id,
        execution_context=context,
    )

    outcome = await registry.execute_prepared(
        prepared, effect_id=context.effect_id, execution_context=context,
    )

    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "authorization_required"
    assert calls == 0


def test_canonical_prepare_rejects_model_owned_host_fields() -> None:
    registry = ToolRegistry()
    registry.register(
        "probe",
        "test",
        SCHEMA,
        lambda args, task_id: json.dumps({"ok": True}),
    )
    context = _context()

    with pytest.raises(ReservedModelFieldError) as caught:
        registry.prepare_call(
            "probe",
            {"value": 7, "session_id": "attacker", "_write_scope_root": "C:/"},
            context.session_id,
            context.call_id,
            execution_context=context,
        )

    assert caught.value.fields == ("_write_scope_root", "session_id")


@pytest.mark.asyncio
async def test_canonical_execute_fails_closed_on_context_binding_mismatch() -> None:
    registry = ToolRegistry()
    calls = 0

    def handler(args, context):
        nonlocal calls
        calls += 1
        return json.dumps({"ok": True})

    registry.register(
        "probe",
        "test",
        SCHEMA,
        lambda args, task_id: json.dumps({"ok": True}),
        context_handler=handler,
    )
    prepared_context = _context()
    prepared = registry.prepare_call(
        "probe",
        {},
        prepared_context.session_id,
        prepared_context.call_id,
        execution_context=prepared_context,
    )

    outcome = await registry.execute_prepared(
        prepared,
        effect_id="wrong-effect",
        execution_context=prepared_context,
    )

    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "trusted_context_effect_binding_mismatch"
    assert calls == 0
