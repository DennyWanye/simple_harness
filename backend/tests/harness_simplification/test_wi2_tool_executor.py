from __future__ import annotations

import asyncio
import ast
import json
import time
from pathlib import Path
from typing import Any

import pytest

from deskpet.harness.context import HostContextFactory
from deskpet.harness.tool_executor import (
    DecisionAuthorization,
    LateEffectSupervisor,
    LegacyPreparedCallAdapter,
    PreparedExecutionCall,
    ReservedModelFieldError,
    ToolOutcomeStatus,
    UnifiedToolExecutor,
)
from deskpet.tools.registry import ToolRegistry


SCHEMA = {
    "name": "placeholder",
    "description": "test",
    "parameters": {"type": "object", "properties": {}},
}


CAPABILITY_HASH = "c" * 64
SCOPE_HASH = "d" * 64


def _call(name: str, index: int = 1, **kwargs: Any) -> PreparedExecutionCall:
    return PreparedExecutionCall(
        tool_name=name,
        model_args=kwargs.pop("model_args", {"value": index}),
        call_id=f"call-{index}",
        effect_id=f"effect-{index}",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        **kwargs,
    )


def _context(call: PreparedExecutionCall):
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
        call_id=call.call_id,
        effect_id=call.effect_id,
    )


class RecordingJournal:
    def __init__(self) -> None:
        self.prepared: list[tuple[str, DecisionAuthorization | None]] = []
        self.unknown: list[tuple[str, str]] = []
        self.finalized: list[tuple[str, ToolOutcomeStatus, bool]] = []
        self.late_done = asyncio.Event()

    async def prepare_effect(self, call, context, authorization):
        self.prepared.append((call.effect_id, authorization))

    async def mark_unknown(self, call, context, reason):
        self.unknown.append((call.effect_id, reason))

    async def finalize_effect(self, call, context, outcome, *, late):
        self.finalized.append((call.effect_id, outcome.status, late))
        if late:
            self.late_done.set()


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    context_handler,
    concurrency_safe: bool = True,
    permission_category: str = "read_file",
    timeout_seconds: float = 1.0,
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
        outcome_parser_id="json_error_envelope_v1",
    )


def test_model_cannot_override_reserved_host_fields() -> None:
    with pytest.raises(ReservedModelFieldError) as caught:
        _call(
            "read",
            model_args={
                "path": "safe.txt",
                "session_id": "attacker",
                "_write_scope_root": "C:/outside",
            },
        )
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
    call = _call("read", model_args={"path": "safe.txt"})
    outcome = await registry.execute_call(call, _context(call))

    assert outcome.status is ToolOutcomeStatus.SUCCEEDED
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
    call = _call("mcp_test_remote", model_args={"query": "safe"})
    outcome = await registry.execute_call(
        call, _context(call), journal=RecordingJournal()
    )
    assert outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert observed == {"args": {"query": "safe"}, "task_id": call.call_id}

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
    calls = [_call(name, index) for index, (name, _, _) in enumerate(definitions, 1)]
    outcomes = await UnifiedToolExecutor(registry).execute_batch(
        calls, [_context(call) for call in calls]
    )

    assert [outcome.call_id for outcome in outcomes] == [call.call_id for call in calls]
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
    call = _call("write", requires_authorization=True, recoverable_effect=True)
    context = _context(call)
    journal = RecordingJournal()

    wrong_type = await registry.execute_call(
        call, context, authorization={"allow": True}, journal=journal
    )
    assert wrong_type.status is ToolOutcomeStatus.FAILED
    assert wrong_type.error == "authorization_required"

    wrong_binding = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.call_id,
        effect_id=call.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash="e" * 64,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    denied = await registry.execute_call(
        call, context, authorization=wrong_binding, journal=journal
    )
    assert denied.error == "authorization_capability_hash_mismatch"

    valid = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.call_id,
        effect_id=call.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    succeeded = await registry.execute_call(
        call, context, authorization=valid, journal=journal
    )
    assert succeeded.status is ToolOutcomeStatus.SUCCEEDED
    assert invocations == 1
    assert journal.prepared == [(call.effect_id, valid)]


@pytest.mark.asyncio
async def test_timed_out_sync_write_is_unknown_then_late_finalized_once() -> None:
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
    call = _call(
        "slow-write", requires_authorization=True, recoverable_effect=True
    )
    context = _context(call)
    authorization = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.call_id,
        effect_id=call.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    journal = RecordingJournal()
    supervisor = LateEffectSupervisor()

    outcome = await registry.execute_call(
        call,
        context,
        authorization=authorization,
        journal=journal,
        late_supervisor=supervisor,
    )
    assert outcome.status is ToolOutcomeStatus.UNKNOWN
    assert outcome.retryable is False
    assert journal.unknown == [(call.effect_id, "sync_handler_timeout")]

    await asyncio.wait_for(journal.late_done.wait(), timeout=1)
    assert invocations == 1
    assert journal.finalized == [
        (call.effect_id, ToolOutcomeStatus.SUCCEEDED, True)
    ]
    assert supervisor.pending_effect_ids == ()


def test_legacy_adapter_uses_trusted_refs_and_strips_host_fields() -> None:
    call = LegacyPreparedCallAdapter.from_persisted(
        {
            "tool_name": "read",
            "final_params": {
                "path": "safe.txt",
                "_session_id": "attacker",
                "effect_id": "attacker-effect",
            },
        },
        trusted_call_id="trusted-call",
        trusted_effect_id="trusted-effect",
        trusted_capability_hash="cap-host",
        trusted_scope_hash=SCOPE_HASH,
    )
    assert call.call_id == "trusted-call"
    assert call.effect_id == "trusted-effect"
    assert call.args_copy() == {"path": "safe.txt"}


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
