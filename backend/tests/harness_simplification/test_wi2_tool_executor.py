from __future__ import annotations

import asyncio
import ast
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from deskpet.execution import OutcomeStatus, RecoveryLease, StaleRecoveryLease
from deskpet.harness.context import HostContextFactory
from deskpet.harness.ports import ToolOutcomesSignal
from deskpet.execution import DecisionAuthorization
from deskpet.harness.tool_executor import UnifiedToolExecutor
from deskpet.tools.context_adapter import ReservedModelFieldError, reject_reserved_model_fields
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.receipt_store import ReceiptStore
from deskpet.workflows.contracts import EffectKind, EffectPolicy
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall, ToolOutcomeState


SCHEMA = {
    "name": "placeholder",
    "description": "test",
    "parameters": {"type": "object", "properties": {}},
}


CAPABILITY_HASH = "c" * 64
SCOPE_HASH = "d" * 64


def _call(registry: ToolRegistry, name: str, index: int = 1, **kwargs: Any) -> PreparedToolCall:
    return registry.prepare_call(
        name, kwargs.pop("model_args", {"value": index}), "session-host", f"call-{index}"
    )


def _context(call: PreparedToolCall):
    factory = HostContextFactory()
    run = factory.create_run_context(
        session_id="session-host",
        root_run_id="run-1",
        request_id="request-host",
        turn_id="turn-host",
        venue="text",
        workspace=".",
        write_scope_root=".",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        provider_plan=("primary", "fallback"),
        trace_id="trace-host",
        principal_id="principal-host",
    )
    return factory.create_tool_context(
        run,
        run_id="run-1",
        call_id=call.stable_call_id,
        effect_id=f"effect-{call.stable_call_id.removeprefix('call-')}",
    )


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    context_handler,
    concurrency_safe: bool = True,
    permission_category: str = "read_file",
    timeout_seconds: float = 1.0,
    effect_policy=None,
) -> None:
    registry.register(
        name,
        "test",
        {**SCHEMA, "name": name},
        lambda args, task_id: json.dumps({"ok": True}),
        context_handler=context_handler,
        concurrency_safe=concurrency_safe,
        permission_category=permission_category,
        timeout_seconds=timeout_seconds,
        effect_policy=effect_policy,
        outcome_parser_id="json_error_envelope_v1",
    )


def test_model_cannot_override_reserved_host_fields() -> None:
    registry = ToolRegistry()
    _register(registry, "read", context_handler=lambda args, context: "{}")
    with pytest.raises(ReservedModelFieldError) as caught:
        reject_reserved_model_fields({"path": "safe.txt", "session_id": "attacker", "_write_scope_root": "C:/outside"})
    assert caught.value.fields == ("_write_scope_root", "session_id")


@pytest.mark.asyncio
async def test_handler_gets_clean_model_args_and_trusted_context_separately() -> None:
    registry = ToolRegistry()
    observed: dict[str, Any] = {}

    async def handler(args, context):
        observed["args"] = args
        observed["session_id"] = context.session_id
        observed["workspace"] = context.workspace
        observed["provider_plan"] = context.provider_plan
        return json.dumps({"ok": True, "value": "done"})

    _register(registry, "read", context_handler=handler)
    call = _call(registry, "read", model_args={"path": "safe.txt"})
    outcome = await UnifiedToolExecutor(registry).execute_one(call, _context(call))

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed["args"] == {"path": "safe.txt"}
    assert observed["session_id"] == "session-host"
    assert observed["workspace"]
    assert observed["provider_plan"] == ("primary", "fallback")


@pytest.mark.asyncio
async def test_mcp_bridge_receives_only_model_args_and_cannot_request_host_context() -> None:
    registry = ToolRegistry()
    observed: dict[str, Any] = {}

    def remote_handler(args, task_id):
        observed["args"] = args
        observed["task_id"] = task_id
        return json.dumps({"isError": False, "value": "done"})

    registry.register(
        "remote",
        "mcp",
        {**SCHEMA, "name": "remote"},
        remote_handler,
        source="mcp:test",
        outcome_parser_id="mcp_explicit_v1",
    )
    call = _call(registry, "mcp_test_remote", model_args={"query": "safe"})
    outcome = await UnifiedToolExecutor(registry).execute_one(call, _context(call))
    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed == {"args": {"query": "safe"}, "task_id": _context(call).effect_id}

    with pytest.raises(ValueError, match="cannot receive trusted host context"):
        registry.register(
            "privileged",
            "mcp",
            {**SCHEMA, "name": "privileged"},
            remote_handler,
            context_handler=lambda args, context: "forbidden",
            source="mcp:test",
        )


@pytest.mark.asyncio
async def test_batch_uses_contiguous_safe_segments_and_unsafe_barriers() -> None:
    registry = ToolRegistry()
    timeline: dict[str, dict[str, float]] = {}

    def make_handler(name: str, delay: float):
        async def handler(args, context):
            timeline[name] = {"start": time.monotonic()}
            await asyncio.sleep(delay)
            timeline[name]["end"] = time.monotonic()
            return json.dumps({"ok": True, "name": name})

        return handler

    definitions = [
        ("safe-a", True, 0.03),
        ("safe-b", True, 0.03),
        ("unsafe-a", False, 0.01),
        ("unsafe-b", False, 0.01),
        ("safe-c", True, 0.001),
    ]
    for name, safe, delay in definitions:
        _register(
            registry,
            name,
            context_handler=make_handler(name, delay),
            concurrency_safe=safe,
        )
    calls = [_call(registry, name, index) for index, (name, _, _) in enumerate(definitions, 1)]
    outcomes = await UnifiedToolExecutor(registry).execute_batch(
        calls, [_context(call) for call in calls]
    )

    assert all(outcome.state is ToolOutcomeState.SUCCESS for outcome in outcomes)
    assert abs(timeline["safe-a"]["start"] - timeline["safe-b"]["start"]) < 0.02
    assert timeline["unsafe-a"]["start"] >= max(
        timeline["safe-a"]["end"], timeline["safe-b"]["end"]
    )
    assert timeline["unsafe-b"]["start"] >= timeline["unsafe-a"]["end"]
    assert timeline["safe-c"]["start"] >= timeline["unsafe-b"]["end"]


@pytest.mark.asyncio
async def test_permission_accepts_only_exact_decision_authorization_binding() -> None:
    registry = ToolRegistry()
    invocations = 0

    def handler(args, context):
        nonlocal invocations
        invocations += 1
        return json.dumps({"ok": True})

    _register(
        registry,
        "write",
        context_handler=handler,
        permission_category="write_file",
    )
    call = _call(registry, "write")
    context = _context(call)
    executor = UnifiedToolExecutor(registry)
    wrong_type = await executor.execute_one(
        call, context, authorization={"allow": True}
    )
    assert wrong_type.state is ToolOutcomeState.FAILURE
    assert wrong_type.error["code"] == "authorization_run_id_mismatch"

    canonical = registry.prepare_call(
        call.tool_name,
        dict(call.final_params),
        context.session_id,
        call.stable_call_id,
        execution_context=context,
    )

    wrong_binding = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=canonical.args_hash,
        capability_hash="e" * 64,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    denied = await executor.execute_one(
        call, context, authorization=wrong_binding
    )
    assert denied.error["code"] == "authorization_capability_hash_mismatch"

    valid = {
        "grant_id": "grant-1",
        "decision_id": "decision-1",
        "run_id": context.run_id,
        "session_id": context.session_id,
        "call_id": call.stable_call_id,
        "effect_id": context.effect_id,
        "tool_name": call.tool_name,
        "args_hash": canonical.args_hash,
        "capability_hash": context.capability_hash,
        "scope_hash": context.scope_hash,
        "permission_policy_version": canonical.permission_policy_version,
        "expires_at": time.time() + 60,
    }
    succeeded = await executor.execute_one(call, context, authorization=valid)
    assert succeeded.state is ToolOutcomeState.SUCCESS
    assert invocations == 1


@pytest.mark.asyncio
async def test_canonical_write_timeout_is_malformed_and_not_reinvoked() -> None:
    registry = ToolRegistry()
    invocations = 0

    def slow_handler(args, context):
        nonlocal invocations
        invocations += 1
        time.sleep(0.05)
        return json.dumps({"ok": True, "written": 1})

    _register(
        registry,
        "slow-write",
        context_handler=slow_handler,
        permission_category="write_file",
        timeout_seconds=0.01,
    )
    call = _call(registry, "slow-write")
    context = _context(call)
    authorization = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    canonical = registry.prepare_call(
        call.tool_name,
        dict(call.final_params),
        context.session_id,
        call.stable_call_id,
        execution_context=context,
    )
    authorization = {
        "grant_id": authorization.grant_id,
        "decision_id": authorization.decision_id,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "call_id": call.stable_call_id,
        "effect_id": context.effect_id,
        "tool_name": call.tool_name,
        "args_hash": canonical.args_hash,
        "capability_hash": context.capability_hash,
        "scope_hash": context.scope_hash,
        "permission_policy_version": canonical.permission_policy_version,
        "expires_at": authorization.expires_at,
    }
    outcome = await UnifiedToolExecutor(registry).execute_one(
        call, context, authorization=authorization
    )
    assert outcome.state is ToolOutcomeState.MALFORMED
    assert outcome.error["code"] == "malformed_tool_outcome"
    assert registry.take_prepared_execution_metadata(context.effect_id) == {
        "late_pending": True
    }
    assert registry.unregister(call.tool_name) is True

    await asyncio.sleep(0.06)
    state, late = await registry.observe_late_prepared(context.effect_id)
    assert state == "complete"
    assert late is not None and late.state is ToolOutcomeState.SUCCESS
    assert await registry.observe_late_prepared(context.effect_id) == (state, late)
    assert registry.take_prepared_execution_metadata(context.effect_id) == {
        "outcome_status": "succeeded"
    }
    registry.acknowledge_prepared_effect(context.effect_id)
    assert await registry.observe_late_prepared(context.effect_id) == ("missing", None)
    assert invocations == 1


@pytest.mark.asyncio
async def test_prepared_receipt_and_artifact_refs_are_exposed_for_atomic_settlement(tmp_path) -> None:
    output = tmp_path / "result.txt"
    output.write_text("durable result", encoding="utf-8")
    registry = ToolRegistry()
    registry.set_receipt_store_provider(
        lambda: ReceiptStore(tmp_path / "receipts", key=b"r" * 32)
    )
    _register(
        registry, "artifact-write",
        context_handler=lambda args, context: json.dumps({"ok": True, "path": str(output)}),
        effect_policy=EffectPolicy("test:artifact", "v7", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(registry, "artifact-write")
    outcome = await UnifiedToolExecutor(registry).execute_one(call, _context(call))
    metadata = registry.take_prepared_execution_metadata("effect-1")

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert call.effect_policy_version == "v7"
    assert metadata["receipt_ref"]
    assert len(metadata["artifact_refs"]) == 1
    assert len(metadata["artifact_refs"][0]) == 64
    assert metadata["evidence_verified"] is True


def test_effect_policy_version_is_durable_and_rechecked() -> None:
    registry = ToolRegistry()
    _register(
        registry, "policy-write", context_handler=lambda args, context: "{}",
        effect_policy=EffectPolicy("test:policy", "v1", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(registry, "policy-write")
    restored = PreparedToolCall.from_dict(call.to_dict())
    assert restored.effect_policy_version == "v1"
    registry.register(
        "policy-write", "test", {**SCHEMA, "name": "policy-write"},
        lambda args, task_id: "{}", context_handler=lambda args, context: "{}",
        effect_policy=EffectPolicy("test:policy", "v2", EffectKind.OPAQUE_MANUAL),
        outcome_parser_id="json_error_envelope_v1", replace_allowed=True,
    )
    with pytest.raises(ValueError, match="stale"):
        registry.prepared_execution_policy(restored)


@pytest.mark.asyncio
async def test_generate_image_generating_result_is_accepted_not_succeeded() -> None:
    registry = ToolRegistry()
    _register(
        registry, "generate_image",
        context_handler=lambda args, context: json.dumps({"ok": True, "status": "generating"}),
    )
    call = _call(registry, "generate_image")
    executor = UnifiedToolExecutor(registry)
    outcome = await executor.execute_one(call, _context(call))
    assert executor.outcome_status(call, outcome) is OutcomeStatus.ACCEPTED


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (NormalizedToolOutcome.success({}), OutcomeStatus.FAILED),
        (NormalizedToolOutcome.failure("failed", "failed"), OutcomeStatus.SUCCEEDED),
        (NormalizedToolOutcome.malformed("unknown"), OutcomeStatus.ACCEPTED),
        (NormalizedToolOutcome.success({}), OutcomeStatus.WAITING),
        (NormalizedToolOutcome.success({}), OutcomeStatus.CANCEL_REQUESTED),
    ],
)
def test_tool_outcome_signal_rejects_incompatible_state_status(outcome, status) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        ToolOutcomesSignal("run", "command", (outcome,), (status,), (0,))


@pytest.mark.asyncio
async def test_persisted_canonical_snapshot_is_rechecked_against_context() -> None:
    registry = ToolRegistry()
    _register(registry, "read", context_handler=lambda args, context: "{}")
    prepared = _call(registry, "read", model_args={"path": "safe.txt"})
    context = _context(prepared)
    call = PreparedToolCall.from_dict(prepared.to_dict())
    assert call == prepared
    wrong = replace(context, call_id="other")
    outcome = await UnifiedToolExecutor(registry).execute_one(call, wrong)
    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "trusted_context_call_binding_mismatch"


def test_migrated_handlers_do_not_read_reserved_fields_from_model_args() -> None:
    tools_root = Path(__file__).resolve().parents[2] / "deskpet" / "tools"
    inventory = [
        tools_root / "file_tools.py",
        tools_root / "image_tools.py",
        tools_root / "ppt_tools.py",
        tools_root / "research_tools.py",
        tools_root / "os_tools" / "read_file.py",
        tools_root / "os_tools" / "write_file.py",
        tools_root / "os_tools" / "edit_file.py",
        tools_root / "os_tools" / "run_shell.py",
        tools_root / "code_tools" / "glob_tool.py",
        tools_root / "code_tools" / "grep_tool.py",
        tools_root / "code_tools" / "clarify_tool.py",
        tools_root / "code_tools" / "agent_tool.py",
        tools_root / "code_tools" / "agent_parallel_tool.py",
        tools_root / "code_tools" / "spawn_subagents_tool.py",
        tools_root / "code_tools" / "spawn_team_tool.py",
        tools_root / "code_tools" / "todo_write_tool.py",
    ]
    reserved = {
        "_session_id",
        "_project_root",
        "_write_scope_root",
        "_request_id",
        "_turn_id",
        "_call_id",
        "_effect_id",
        "_image_worker",
    }
    violations: list[str] = []
    for path in inventory:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value in reserved:
                violations.append(f"{path.name}:{node.lineno}:{key.value}")
    assert violations == []


def test_all_inventory_handlers_expose_explicit_context_adapter_parameter() -> None:
    tools_root = Path(__file__).resolve().parents[2] / "deskpet" / "tools"
    expected = {
        tools_root / "file_tools.py": {
            "_handle_file_read",
            "_handle_file_write",
            "_handle_file_glob",
            "_handle_file_grep",
            "_handle_workspace_recall",
        },
        tools_root / "image_tools.py": {"_handle_generate_image"},
        tools_root / "ppt_tools.py": {"_handle_ppt_create", "_handle_ppt_pro"},
        tools_root / "research_tools.py": {"_handle_deepresearch"},
        tools_root / "os_tools" / "read_file.py": {"read_file"},
        tools_root / "os_tools" / "write_file.py": {"write_file"},
        tools_root / "os_tools" / "edit_file.py": {"edit_file"},
        tools_root / "os_tools" / "run_shell.py": {"run_shell"},
        tools_root / "code_tools" / "glob_tool.py": {"glob_tool"},
        tools_root / "code_tools" / "grep_tool.py": {"grep_tool"},
        tools_root / "code_tools" / "clarify_tool.py": {"_handler"},
        tools_root / "code_tools" / "agent_tool.py": {"_handler"},
        tools_root / "code_tools" / "agent_parallel_tool.py": {"_handle"},
        tools_root / "code_tools" / "spawn_subagents_tool.py": {"_spawn", "_await"},
        tools_root / "code_tools" / "spawn_team_tool.py": {"_handle"},
        tools_root / "code_tools" / "todo_write_tool.py": {"_handler"},
    }
    missing: list[str] = []
    for path, names in expected.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        }
        for name in names:
            node = functions.get(name)
            kwonly = {arg.arg for arg in node.args.kwonlyargs} if node else set()
            if node is None or "execution_context" not in kwonly:
                missing.append(f"{path.name}:{name}")
    assert missing == []
