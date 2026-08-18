from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agent.agent_loop import (
    AgentLoop,
    ErrorEvent,
    ToolBatchEvent,
    _planned_generation_reserve,
)
from deskpet.agent.assembler.bundle import PreparedContext
from llm.types import ChatResponse, ChatUsage, ToolCall

from deskpet.tools.capabilities import (
    PreparedToolCapability,
    ToolCapabilityBridgeService,
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from deskpet.agent.context_budget import prepare_openai_tool_payload
from deskpet.agent.context_snapshot_store import (
    ContextSnapshotHandle,
    SnapshotCommitCancelled,
    SnapshotConflictError,
    SnapshotWriteReceipt,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.tool_search import register_capability_bridge_tools


def test_activation_replan_reuses_prepared_generation_reserve() -> None:
    class _Budget:
        generation_reserve = 1024

    class _Prepared:
        request_budget = _Budget()

    assert _planned_generation_reserve(_Prepared(), {}) == 1024
    assert _planned_generation_reserve(_Prepared(), {"max_tokens": 8192}) == 1024
    assert _planned_generation_reserve(object(), {"max_tokens": 768}) == 768
    assert _planned_generation_reserve(object(), {}) == 0


def _runtime():
    registry = ToolRegistry()
    registry.register(
        "mcp_demo_lookup",
        "mcp",
        {
            "name": "mcp_demo_lookup",
            "description": "look up demo records",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args, _task: "ok",
        source="mcp:demo",
    )
    scopes = ToolCapabilityScopeStore()
    service = ToolCapabilityBridgeService(registry, scopes)
    register_capability_bridge_tools(registry, service)
    eligibility = ToolEligibilityContext("s", "r", "chat")
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(
            direct_selectors=("source:builtin",),
            discoverable_selectors=("source:mcp:*",),
        ),
        eligibility=eligibility,
    ).finalize(scope_id="scope")
    scopes.open(prepared, eligibility)
    return registry, scopes, service, prepared


def test_deferred_catalog_exposes_only_compact_descriptor_then_exact_schema() -> None:
    _registry, _scopes, service, prepared = _runtime()
    assert prepared.has_direct("tool_search")
    assert [ref.name for ref in prepared.deferred] == ["mcp_demo_lookup"]
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        search = service.search("demo")
        assert search["matches"][0]["name"] == "mcp_demo_lookup"
        assert "schema" not in search["matches"][0]
        described = service.describe("mcp_demo_lookup")
        assert described["capability_id"] == "mcp:demo:mcp_demo_lookup"
        assert described["schema"]["function"]["name"] == "mcp_demo_lookup"
        proposal = service.activate(
            "mcp_demo_lookup",
            described["schema_hash"],
            described["describe_nonce"],
        )
        assert proposal.prepared_capability.ref.name == "mcp_demo_lookup"
    finally:
        reset_tool_execution_context(token)


def test_bare_capability_name_is_ambiguous_fail_closed() -> None:
    _registry, scopes, service, prepared = _runtime()
    duplicate = replace(
        prepared.deferred[0],
        capability_id="plugin:other:mcp_demo_lookup",
        source="plugin:other",
    )
    record = scopes._records["scope"]
    scopes._records["scope"] = replace(
        record,
        prepared=replace(
            record.prepared,
            deferred=(*record.prepared.deferred, duplicate),
        ),
    )
    token = set_tool_execution_context(
        ToolExecutionContext("scope", "s", "r")
    )
    try:
        with pytest.raises(RuntimeError, match="^capability_denied$"):
            service.describe("mcp_demo_lookup")
    finally:
        reset_tool_execution_context(token)


def _install_shell_search_fixtures(scopes, prepared) -> None:
    base = prepared.deferred[0]
    shell = replace(
        base,
        capability_id="builtin:run_shell",
        name="run_shell",
        source="builtin",
        description="Execute a shell command and capture stdout and stderr.",
    )
    process = replace(
        base,
        capability_id="builtin:process_start",
        name="process_start",
        source="builtin",
        description="Start an executable process; shell command strings are rejected.",
    )
    record = scopes._records["scope"]
    scopes._records["scope"] = replace(
        record,
        prepared=replace(record.prepared, deferred=(shell, process)),
    )


def test_multi_term_capability_search_ranks_partial_relevant_match() -> None:
    _registry, scopes, service, prepared = _runtime()
    _install_shell_search_fixtures(scopes, prepared)
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        result = service.search("run command execute process")
    finally:
        reset_tool_execution_context(token)

    assert [item["capability_id"] for item in result["matches"]] == [
        "builtin:run_shell",
        "builtin:process_start",
    ]


def test_denied_guessed_capability_returns_scoped_suggestions() -> None:
    registry, scopes, _service, prepared = _runtime()
    _install_shell_search_fixtures(scopes, prepared)
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        spec = registry.get("tool_describe")
        assert spec is not None
        payload = __import__("json").loads(
            spec.handler({"capability_id": "builtin:run_command"}, "s")
        )
    finally:
        reset_tool_execution_context(token)

    assert payload["error"] == "capability_denied"
    assert payload["replan_required"] is True
    assert payload["suggestions"][0]["capability_id"] == "builtin:run_shell"
    assert "Do not retry guessed capability ids" in payload["next_action"]


def test_nonce_is_one_shot_and_bound_to_scope() -> None:
    _registry, _scopes, service, _prepared = _runtime()
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        described = service.describe("mcp:demo:mcp_demo_lookup")
        service.activate(
            described["capability_id"],
            described["schema_hash"],
            described["describe_nonce"],
        )
        with pytest.raises(RuntimeError, match="nonce"):
            service.activate(
                described["capability_id"],
                described["schema_hash"],
                described["describe_nonce"],
            )
    finally:
        reset_tool_execution_context(token)


def test_nonce_follows_pinned_request_scope_not_model_thinking_time(
    monkeypatch,
) -> None:
    from deskpet.tools import capabilities as capability_module

    monotonic = [10.0]
    monkeypatch.setattr(
        capability_module.time, "monotonic", lambda: monotonic[0]
    )
    _registry, scopes, service, _prepared = _runtime()
    token = set_tool_execution_context(
        ToolExecutionContext("scope", "s", "r")
    )
    try:
        described = service.describe("mcp:demo:mcp_demo_lookup")
        assert scopes.pin("scope", session_id="s", request_id="r") is not None
        monotonic[0] = 1_000.0

        proposal = service.activate(
            described["capability_id"],
            described["schema_hash"],
            described["describe_nonce"],
        )

        assert proposal.prepared_capability.ref.name == "mcp_demo_lookup"
    finally:
        scopes.unpin("scope", session_id="s", request_id="r")
        reset_tool_execution_context(token)


def test_mcp_disconnect_makes_describe_stale() -> None:
    registry, _scopes, service, _prepared = _runtime()
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        registry.unregister("mcp_demo_lookup")
        with pytest.raises(RuntimeError, match="capability_stale"):
            service.describe("mcp:demo:mcp_demo_lookup")
    finally:
        reset_tool_execution_context(token)


def test_mcp_disconnect_after_describe_normalizes_activate_catalog_stale() -> None:
    registry, _scopes, service, _prepared = _runtime()
    stale_sources = []
    registry.set_mcp_catalog_stale_callback(stale_sources.append)
    token = set_tool_execution_context(ToolExecutionContext("scope", "s", "r"))
    try:
        described = service.describe("mcp:demo:mcp_demo_lookup")
        registry.unregister("mcp_demo_lookup")
        with pytest.raises(RuntimeError, match="^tool_catalog_stale$"):
            service.activate(
                described["capability_id"],
                described["schema_hash"],
                described["describe_nonce"],
            )
        assert stale_sources == ["mcp:demo"]
    finally:
        reset_tool_execution_context(token)


class _HydrationLLM:
    def __init__(self) -> None:
        self.calls = []

    async def chat_with_fallback(self, messages, tools=None, **_kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        turn = len(self.calls)
        if turn == 1:
            call = ToolCall(id="search", name="tool_search", arguments={"query": "demo"})
        elif turn == 2:
            result = _tool_domain_result(messages[-1]["content"])
            capability_id = result["matches"][0]["capability_id"]
            call = ToolCall(
                id="describe",
                name="tool_describe",
                arguments={"capability_id": capability_id},
            )
        elif turn == 3:
            result = _tool_domain_result(messages[-1]["content"])
            call = ToolCall(
                id="activate",
                name="tool_activate",
                arguments={
                    "capability_id": result["capability_id"],
                    "schema_hash": result["schema_hash"],
                    "describe_nonce": result["describe_nonce"],
                },
            )
        elif turn == 4:
            call = ToolCall(id="target", name="mcp_demo_lookup", arguments={})
        else:
            return ChatResponse(
                content="done",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="fake",
            )
        return ChatResponse(
            content="",
            tool_calls=[call],
            stop_reason="tool_use",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="fake",
        )


def _tool_domain_result(content: str):
    outer = __import__("json").loads(content)
    return __import__("json").loads(outer["result"])


@pytest.mark.asyncio
async def test_agent_loop_exposes_search_then_yields_external_tool_boundary() -> None:
    registry, scopes, _service, prepared = _runtime()
    target_calls = []
    original = registry.get("mcp_demo_lookup")
    assert original is not None
    registry.register(
        "mcp_demo_lookup",
        "mcp",
        original.schema,
        lambda args, _task: target_calls.append(args) or "ok",
        source="mcp:demo",
        replace_allowed=True,
    )
    # The replacement intentionally makes the old deferred ref stale; resolve
    # a fresh request exactly as production does.
    eligibility = ToolEligibilityContext("s", "r2", "chat")
    fresh = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(
            direct_selectors=("source:builtin",),
            discoverable_selectors=("source:mcp:*",),
        ),
        eligibility=eligibility,
    ).finalize(scope_id="scope2")
    scopes.open(fresh, eligibility)
    registry.set_capability_scope_store(scopes)
    registry.set_context_os_enabled_provider(lambda: True)
    llm = _HydrationLLM()
    loop = AgentLoop(llm, registry, max_iterations=6)
    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "demo"}],
            session_id="s",
            prepared_context=PreparedContext(
                messages=[{"role": "user", "content": "demo"}],
                tool_set=fresh,
            ),
            context_request_id="r2",
        )
    ]
    assert isinstance(events[-1], ToolBatchEvent)
    assert [call.name for call in events[-1].tool_calls] == ["tool_search"]
    assert target_calls == []
    schema_names = [schema["function"]["name"] for schema in llm.calls[0]["tools"]]
    assert "mcp_demo_lookup" not in schema_names
    assert events[-1].canonical_messages[-1]["tool_calls"][0]["function"]["name"] == "tool_search"


@pytest.mark.asyncio
async def test_agent_loop_reports_expired_scope_instead_of_registry_mismatch() -> None:
    registry, scopes, _service, prepared = _runtime()
    registry.set_capability_scope_store(scopes)
    scopes.purge(prepared.scope_id)
    loop = AgentLoop(_HydrationLLM(), registry, max_iterations=2)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "demo"}],
            session_id="s",
            prepared_context=PreparedContext(
                messages=[{"role": "user", "content": "demo"}],
                tool_set=prepared,
            ),
            context_request_id="r",
        )
    ]

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].reason == "tool_capability_scope_expired"
    assert events[-1].detail == "Context OS capability scope is missing or expired"


def _activation_candidate(registry, prepared):
    ref = next(item for item in prepared.deferred if item.name == "mcp_demo_lookup")
    spec = registry.get(ref.name)
    assert spec is not None
    candidate = prepared.activate(
        PreparedToolCapability(
            ref,
            {"type": "function", "function": spec.schema},
        )
    )
    return candidate, prepare_openai_tool_payload(candidate)


@pytest.mark.asyncio
async def test_activation_snapshot_cas_failure_keeps_scope_and_local_set_unchanged() -> None:
    registry, scopes, _service, prepared = _runtime()
    handle = ContextSnapshotHandle("s", "session:s", 1, "before")
    scopes.advance_snapshot_handle_prevalidated(prepared.scope_id, handle)
    prepared_context = PreparedContext(
        messages=[{"role": "user", "content": "demo"}],
        tool_set=prepared,
        active_snapshot_handle=handle,
    )
    candidate, payload = _activation_candidate(registry, prepared)

    class _ConflictStore:
        async def update_tool_context_cas(self, *_args, **kwargs):
            assert kwargs["prepared_toolset_summary"]["activated_names"] == [
                "mcp_demo_lookup"
            ]
            assert kwargs["prepared_toolset_summary"]["adapter_state"] == "prepared"
            raise SnapshotConflictError(1, 2)

    loop = AgentLoop(
        _HydrationLLM(),
        registry,
        context_snapshot_store=_ConflictStore(),
    )
    async with scopes.lock_for(prepared.scope_id):
        record = scopes.get(prepared.scope_id, session_id="s", request_id="r")
        with pytest.raises(RuntimeError, match="tool_activation_snapshot_conflict"):
            await loop._persist_activation_tool_context_locked(
                session_id="s",
                scope_store=scopes,
                scope_record=record,
                candidate=candidate,
                prepared_context=prepared_context,
                tool_payload=payload,
            )

    current = scopes.get(prepared.scope_id, session_id="s", request_id="r")
    assert current.prepared is prepared
    assert current.snapshot_handle == handle
    assert prepared_context.tool_set is prepared
    assert prepared_context.active_snapshot_handle == handle

