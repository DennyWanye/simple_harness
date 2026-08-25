from __future__ import annotations

import pytest

from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
)
from deskpet.tools.registry import ToolRegistry


def _guarded_runtime():
    calls: list[dict] = []
    registry = ToolRegistry()
    registry.register(
        "guarded_tool",
        "builtin",
        {
            "name": "guarded_tool",
            "description": "guarded",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda args, _task: calls.append(args) or "ok",
        source="builtin",
    )
    scopes = ToolCapabilityScopeStore()
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("guarded_tool",)),
        eligibility=ToolEligibilityContext("session-a", "request-a", "chat"),
    ).finalize(scope_id="scope-a")
    scopes.open(
        prepared,
        ToolEligibilityContext("session-a", "request-a", "chat"),
    )
    registry.set_capability_scope_store(scopes)
    registry.set_context_os_enabled_provider(lambda: True)
    return registry, prepared, calls


@pytest.mark.asyncio
async def test_context_os_fails_closed_without_execution_context() -> None:
    registry, _prepared, calls = _guarded_runtime()
    result = await registry.execute_tool("guarded_tool", {}, "session-a")
    assert result["ok"] is False
    assert "missing execution context" in result["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_capability_scope_is_strictly_session_and_request_bound() -> None:
    registry, _prepared, calls = _guarded_runtime()
    result = await registry.execute_tool(
        "guarded_tool",
        {},
        "session-b",
        execution_context=ToolExecutionContext(
            "scope-a", "session-b", "request-a"
        ),
    )
    assert result == {"ok": False, "result": None, "error": "capability_denied"}
    assert calls == []


@pytest.mark.asyncio
async def test_dynamic_admission_allows_only_exact_run_local_tool() -> None:
    registry, prepared, calls = _guarded_runtime()
    deferred = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(discoverable_selectors=("guarded_tool",)),
        eligibility=ToolEligibilityContext("session-b", "request-b", "chat"),
    ).finalize(scope_id="scope-b")
    assert deferred.capability("guarded_tool") is None
    assert registry.capability_scope_store is not None
    registry.capability_scope_store.open(
        deferred,
        ToolEligibilityContext("session-b", "request-b", "chat"),
    )
    registry.set_dynamic_capability_admission_provider(
        lambda name, context, spec: (
            name == "guarded_tool"
            and context.scope_id == "scope-b"
            and context.session_id == "session-b"
            and context.request_id == "request-b"
            and spec.schema_hash == prepared.capability("guarded_tool").ref.schema_hash
        )
    )

    result = await registry.execute_tool(
        "guarded_tool",
        {},
        "session-b",
        execution_context=ToolExecutionContext(
            "scope-b", "session-b", "request-b", run_id="run-b"
        ),
    )

    assert result["ok"] is True
    assert calls == [{}]


@pytest.mark.asyncio
async def test_hot_replaced_tool_is_stale_and_handler_is_not_called() -> None:
    registry, _prepared, calls = _guarded_runtime()
    replacement_calls: list[dict] = []
    registry.register(
        "guarded_tool",
        "builtin",
        {
            "name": "guarded_tool",
            "description": "changed schema",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        lambda args, _task: replacement_calls.append(args) or "new",
        source="builtin",
        replace_allowed=True,
    )
    result = await registry.execute_tool(
        "guarded_tool",
        {},
        "session-a",
        execution_context=ToolExecutionContext(
            "scope-a", "session-a", "request-a"
        ),
    )
    assert result == {"ok": False, "result": None, "error": "capability_stale"}
    assert calls == []
    assert replacement_calls == []
