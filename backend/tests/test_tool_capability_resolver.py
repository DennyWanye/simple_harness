from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
    current_tool_execution_context,
)
from deskpet.tools.registry import (
    ToolCatalogMutationError,
    ToolCatalogRevisionConflict,
    ToolRegistry,
    tool_spec_fingerprint,
)
from deskpet.agent.goal_store import SessionGoalStore
from deskpet.agent.assembler.policy import load_policies


@dataclass
class _ToolsConfig:
    disabled_toolsets: list[str] = field(default_factory=list)
    disabled_toolsets_schema_only: list[str] = field(default_factory=list)
    dangerous_tools_allowlist: list[str] = field(default_factory=list)


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    source: str = "builtin",
    visible_when=None,
    visibility_scope: str = "global",
    handler=None,
    fixture_remote_name: str = "",
) -> None:
    registry.register(
        name=name,
        toolset="test",
        schema={
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handler or (lambda _args, _task_id: json.dumps({"name": name})),
        source=source,
        visible_when=visible_when,
        visibility_scope=visibility_scope,
        fixture_remote_name=fixture_remote_name,
    )


def test_catalog_revision_is_monotonic_only_on_successful_mutation() -> None:
    registry = ToolRegistry()
    assert registry.catalog_snapshot().revision == 0
    _register(registry, "one")
    assert registry.catalog_snapshot().revision == 1
    assert not registry.unregister("missing")
    assert registry.catalog_snapshot().revision == 1
    assert registry.unregister("one")
    assert registry.catalog_snapshot().revision == 2


def test_atomic_catalog_cas_swaps_all_names_in_one_revision() -> None:
    registry = ToolRegistry()
    _register(registry, "old_one", source="capability:fixture:1")
    _register(registry, "old_two", source="capability:fixture:1")
    before = registry.catalog_snapshot()
    old = {spec.name: tool_spec_fingerprint(spec) for spec in before.specs}

    candidate_registry = ToolRegistry()
    _register(
        candidate_registry,
        "new_one",
        source="capability:fixture:2",
    )
    _register(
        candidate_registry,
        "new_two",
        source="capability:fixture:2",
    )
    replacements = candidate_registry.catalog_snapshot().specs
    after = registry.compare_and_swap_catalog(
        expected_revision=before.revision,
        expected_fingerprints={
            **old,
            "new_one": None,
            "new_two": None,
        },
        replacements=replacements,
    )

    assert after.revision == before.revision + 1
    assert {spec.name for spec in after.specs} == {"new_one", "new_two"}


def test_atomic_catalog_cas_conflicts_without_partial_mutation() -> None:
    registry = ToolRegistry()
    _register(registry, "old", source="capability:fixture:1")
    before = registry.catalog_snapshot()
    old_fingerprint = tool_spec_fingerprint(before.specs[0])

    candidate_registry = ToolRegistry()
    _register(candidate_registry, "new", source="capability:fixture:2")
    replacement = candidate_registry.catalog_snapshot().specs[0]
    with pytest.raises(ToolCatalogRevisionConflict):
        registry.compare_and_swap_catalog(
            expected_revision=before.revision + 1,
            expected_fingerprints={"old": old_fingerprint, "new": None},
            replacements=(replacement,),
        )
    with pytest.raises(ToolCatalogMutationError):
        registry.compare_and_swap_catalog(
            expected_revision=before.revision,
            expected_fingerprints={
                "old": "0" * 64,
                "new": None,
            },
            replacements=(replacement,),
        )

    unchanged = registry.catalog_snapshot()
    assert unchanged.revision == before.revision
    assert [spec.name for spec in unchanged.specs] == ["old"]


def test_strict_goal_lookup_never_falls_back_to_another_session() -> None:
    goals = SessionGoalStore()
    goal = goals.set("session-a", "ship it")
    assert goals.get_active_goal_context("session-b") == (
        goal.goal_id,
        "session-a",
    )  # legacy behavior intentionally retained for OFF
    assert goals.get_active_goal_context_for_session("session-b") is None
    assert goals.get_active_goal_context_for_session("session-a") == (
        goal.goal_id,
        "session-a",
    )


def test_resolver_is_session_aware_and_provider_neutral() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    _register(
        registry,
        "session_tool",
        visible_when=lambda ctx: ctx is not None and ctx.session_id == "a",
        visibility_scope="session",
    )
    _register(registry, "mcp_x_read", source="mcp:x")
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        _register(registry, bridge)
    intent = ToolExposureIntent(
        direct_selectors=("source:builtin",),
        discoverable_selectors=("source:mcp:*",),
    )
    resolver = ToolCapabilityResolver(registry)
    draft_a = resolver.resolve_draft(
        intent,
        eligibility=ToolEligibilityContext("a", "r-a", "chat"),
    )
    draft_b = resolver.resolve_draft(
        intent,
        eligibility=ToolEligibilityContext("b", "r-b", "chat"),
    )
    assert [cap.ref.name for cap in draft_a.direct] == [
        "session_tool",
        "tool_search",
        "tool_describe",
        "tool_activate",
    ]
    assert [cap.ref.name for cap in draft_b.direct] == [
        "tool_search",
        "tool_describe",
        "tool_activate",
    ]
    assert [ref.name for ref in draft_a.deferred] == ["mcp_x_read"]


def test_unique_remote_mcp_name_stays_deferred_for_progressive_disclosure() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        _register(registry, bridge)
    canonical_name = "mcp_context-os-e2e_mcp_ctx_fixture_tool_001"
    _register(
        registry,
        canonical_name,
        source="mcp:context-os-e2e",
        fixture_remote_name="mcp_ctx_fixture_tool_001",
    )
    draft = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(discoverable_selectors=("source:mcp:*",)),
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        conditional_direct_names=("mcp_ctx_fixture_tool_001",),
    )
    assert draft.conditional_direct == ()
    assert draft.conditional_name_map == ()
    assert [ref.name for ref in draft.deferred] == [canonical_name]


def test_capability_pack_tools_are_request_scoped_deferred_tools() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        _register(registry, bridge)
    _register(
        registry,
        "godot__detect",
        source="capability:godot:1.0.4:manifest",
    )
    _register(
        registry,
        "godot__project_check",
        source="capability:godot:1.0.4:manifest",
    )

    draft = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(
            discoverable_selectors=("source:capability:*",)
        ),
        eligibility=ToolEligibilityContext("s", "r", "chat"),
    )

    assert [cap.ref.name for cap in draft.direct] == [
        "tool_search",
        "tool_describe",
        "tool_activate",
    ]
    assert [ref.capability_id for ref in draft.deferred] == [
        "capability:godot:1.0.4:manifest:godot__detect",
        "capability:godot:1.0.4:manifest:godot__project_check",
    ]


def test_default_chat_policy_exposes_capability_pack_discovery_bridge() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        _register(registry, bridge)
    _register(
        registry,
        "godot__detect",
        source="capability:godot:1.0.4:manifest",
    )

    exposure = load_policies()["chat"].tool_exposure
    draft = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(
            direct_selectors=exposure.direct,
            discoverable_selectors=exposure.discoverable,
            deny_selectors=exposure.deny,
        ),
        eligibility=ToolEligibilityContext("s", "r", "chat"),
    )

    assert [cap.ref.name for cap in draft.direct] == [
        "tool_search",
        "tool_describe",
        "tool_activate",
    ]
    assert [ref.name for ref in draft.deferred] == ["godot__detect"]


def test_ambiguous_remote_mcp_name_remains_deferred() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        _register(registry, bridge)
    for server in ("alpha", "beta"):
        _register(
            registry,
            f"mcp_{server}_shared_lookup",
            source=f"mcp:{server}",
            fixture_remote_name="shared_lookup",
        )
    draft = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(discoverable_selectors=("source:mcp:*",)),
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        conditional_direct_names=("shared_lookup",),
    )
    assert draft.conditional_direct == ()
    assert draft.conditional_name_map == ()
    assert [ref.name for ref in draft.deferred] == [
        "mcp_alpha_shared_lookup",
        "mcp_beta_shared_lookup",
    ]


def test_strict_policy_failure_blocks_resolve() -> None:
    registry = ToolRegistry()
    _register(registry, "read")
    registry.set_tools_config_provider(lambda: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(RuntimeError, match="tool_policy_unavailable"):
        ToolCapabilityResolver(registry).resolve_draft(
            ToolExposureIntent(direct_selectors=("*",)),
            eligibility=ToolEligibilityContext("s", "r", "chat"),
        )


@pytest.mark.asyncio
async def test_execution_requires_scope_and_propagates_host_context() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())

    def handler(_args, _task_id):
        context = current_tool_execution_context()
        return json.dumps({"scope": context.scope_id if context else None})

    _register(registry, "read", handler=handler)
    resolver = ToolCapabilityResolver(registry)
    eligibility = ToolEligibilityContext("s", "r", "chat")
    prepared = resolver.resolve_draft(
        ToolExposureIntent(direct_selectors=("read",)),
        eligibility=eligibility,
    ).finalize(scope_id="scope")
    scopes = ToolCapabilityScopeStore()
    scopes.open(prepared, eligibility)
    registry.set_capability_scope_store(scopes)
    registry.set_context_os_enabled_provider(lambda: True)

    denied = await registry.execute_tool("read", {}, "s")
    assert denied["error"].startswith("capability_denied")

    allowed = await registry.execute_tool(
        "read",
        {},
        "s",
        execution_context=ToolExecutionContext("scope", "s", "r"),
    )
    assert allowed["ok"] is True
    assert json.loads(allowed["result"])["scope"] == "scope"


@pytest.mark.asyncio
async def test_mcp_execution_does_not_leak_host_session_context() -> None:
    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    received = []
    _register(
        registry,
        "mcp_fixture_call",
        source="mcp:fixture",
        handler=lambda args, _task_id: received.append(args) or "ok",
    )
    eligibility = ToolEligibilityContext("s", "r", "chat")
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("mcp_fixture_call",)),
        eligibility=eligibility,
    ).finalize(scope_id="scope")
    scopes = ToolCapabilityScopeStore()
    scopes.open(prepared, eligibility)
    registry.set_capability_scope_store(scopes)
    registry.set_context_os_enabled_provider(lambda: True)
    registry.set_session_context("s", {"_image_worker": object(), "_project_root": "X"})

    result = await registry.execute_tool(
        "mcp_fixture_call",
        {"marker": "explicit"},
        "s",
        execution_context=ToolExecutionContext("scope", "s", "r"),
    )

    assert result["ok"] is True
    assert received == [{"marker": "explicit"}]


@pytest.mark.asyncio
async def test_policy_read_failure_blocks_execution_without_calling_handler() -> None:
    called = False

    def handler(_args, _task_id):
        nonlocal called
        called = True
        return "ok"

    registry = ToolRegistry()
    registry.set_tools_config_provider(lambda: _ToolsConfig())
    _register(registry, "read", handler=handler)
    eligibility = ToolEligibilityContext("s", "r", "chat")
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("read",)), eligibility=eligibility
    ).finalize(scope_id="scope")
    scopes = ToolCapabilityScopeStore()
    scopes.open(prepared, eligibility)
    registry.set_capability_scope_store(scopes)
    registry.set_context_os_enabled_provider(lambda: True)
    registry.set_tools_config_provider(
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
    )
    result = await registry.execute_tool(
        "read",
        {},
        "s",
        execution_context=ToolExecutionContext("scope", "s", "r"),
    )
    assert result["error"] == "tool_policy_unavailable"
    assert called is False
