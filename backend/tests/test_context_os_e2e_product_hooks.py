from __future__ import annotations

from pathlib import Path

import pytest

from agent.context_messages import ProviderAttemptOptions, context_attempt_scope
from deskpet.context_os_e2e_hooks import (
    ContextOSE2EHooks,
    consume_context_os_e2e_fault,
    trusted_context_os_e2e_case,
    trusted_provider_headers,
)
from deskpet.tools.capabilities import (
    ToolCapabilityBridgeService,
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.tool_search import register_capability_bridge_tools


def _clear(monkeypatch):
    for name in ("DESKPET_DEV_MODE","DESKPET_CONTEXT_OS_E2E_DAEMON_URL","DESKPET_CONTEXT_OS_E2E_PROVIDER_URL","DESKPET_CONTEXT_OS_E2E_HOOK_TIMEOUT_MS","DESKPET_CONTEXT_OS_E2E_CASE_ID","DESKPET_CONTEXT_OS_E2E_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)


def test_gate_off_and_non_loopback_construct_no_hook_or_network(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: pytest.fail("network used"))
    assert ContextOSE2EHooks.from_env() is None
    assert trusted_provider_headers("https://provider.example/v1") == {}
    monkeypatch.setenv("DESKPET_DEV_MODE","1");monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","https://example.com")
    assert ContextOSE2EHooks.from_env() is None


def test_trusted_headers_require_dev_loopback_exact_provider_and_attempt(monkeypatch):
    _clear(monkeypatch);monkeypatch.setenv("DESKPET_DEV_MODE","1");monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","http://127.0.0.1:18991");monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL","http://127.0.0.1:18992/v1")
    assert trusted_provider_headers("http://127.0.0.1:18992/v1") == {}
    with context_attempt_scope(ProviderAttemptOptions(purpose="agent_response",request_id="r",attempt_id="a")):
        assert trusted_provider_headers("http://127.0.0.1:18992/v1") == {"X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"r","X-DeskPet-Attempt-Id":"a"}
        assert trusted_provider_headers("http://127.0.0.1:19999/v1") == {}


def test_trusted_case_requires_loopback_case_session_and_source_checkout(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_DAEMON_URL", "http://127.0.0.1:18991")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL", "http://127.0.0.1:18992/v1")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_CASE_ID", "E2E-CTX-10")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_SESSION_ID", "session-a")
    assert trusted_context_os_e2e_case(
        allowed_cases=("E2E-CTX-10",), session_id="session-a"
    )
    assert not trusted_context_os_e2e_case(
        allowed_cases=("E2E-CTX-10",), session_id="session-b"
    )
    assert not trusted_context_os_e2e_case(
        allowed_cases=("E2E-CTX-02",), session_id="session-a"
    )
    monkeypatch.setattr("deskpet.context_os_e2e_hooks.sys.frozen", True, raising=False)
    assert not trusted_context_os_e2e_case(
        allowed_cases=("E2E-CTX-10",), session_id="session-a"
    )


class _Hooks:
    def __init__(self):self.observed=[];self.stale=False;self.unavailable=False
    def observe_describe(self,**value):self.observed.append(value)
    def catalog(self,names):
        if self.unavailable: raise RuntimeError("tool_catalog_stale")
        return {"tools":{name:{"fixture_epoch":2 if self.stale else 1,"fixture_spec_hash":"new" if self.stale else "hash-1","fixture_spec_version":"1"} for name in names}}


def _runtime(*, deferred=True):
    registry=ToolRegistry();hooks=_Hooks();registry.set_context_os_e2e_hooks(hooks)
    registry.register("fixture_tool","mcp",{"name":"fixture_tool","description":"fixture","parameters":{"type":"object","properties":{}}},lambda *_:"ok",source="mcp:fixture",fixture_epoch=1,fixture_spec_hash="hash-1",fixture_spec_version="1",fixture_remote_name="fixture_tool")
    eligibility=ToolEligibilityContext("s","r","chat");scopes=ToolCapabilityScopeStore();service=ToolCapabilityBridgeService(registry,scopes);register_capability_bridge_tools(registry,service)
    intent = (ToolExposureIntent(discoverable_selectors=("source:mcp:*",)) if deferred else ToolExposureIntent(direct_selectors=("source:mcp:*",)))
    prepared=ToolCapabilityResolver(registry).resolve_draft(intent,eligibility=eligibility).finalize(scope_id="scope")
    scopes.open(prepared,eligibility)
    return registry,hooks,prepared,service,eligibility


def test_catalog_stale_fails_closed_invalidates_spec_and_advances_revision():
    registry,hooks,prepared,_service,eligibility=_runtime(deferred=False);before=registry.catalog_snapshot().revision;hooks.stale=True
    with pytest.raises(RuntimeError,match="tool_catalog_stale"):registry.validate_prepared_tool_set(prepared,eligibility=eligibility)
    assert registry.get("mcp_fixture_fixture_tool") is None
    assert registry.catalog_snapshot().revision == before + 1


def test_describe_observer_receives_host_scope_and_hash():
    _registry,hooks,prepared,service,_eligibility=_runtime();token=set_tool_execution_context(ToolExecutionContext("scope","s","r"))
    try:service.describe(prepared.deferred[0].capability_id)
    finally:reset_tool_execution_context(token)
    assert hooks.observed == [{"session_id":"s","request_id":"r","tool":"mcp_fixture_fixture_tool","schema_hash":prepared.deferred[0].schema_hash}]


def test_pre_activate_catalog_change_fails_closed_before_proposal():
    registry,hooks,prepared,service,_eligibility=_runtime();token=set_tool_execution_context(ToolExecutionContext("scope","s","r"))
    try:
        described=service.describe(prepared.deferred[0].capability_id);before=registry.catalog_snapshot().revision;hooks.stale=True
        with pytest.raises(RuntimeError,match="tool_catalog_stale"):
            service.activate(described["capability_id"],described["schema_hash"],described["describe_nonce"])
    finally:reset_tool_execution_context(token)
    assert registry.get("mcp_fixture_fixture_tool") is None
    assert registry.catalog_snapshot().revision == before + 1


@pytest.mark.parametrize("deferred", [False, True])
def test_catalog_unavailable_invalidates_spec_and_advances_revision(deferred):
    registry,hooks,prepared,service,eligibility=_runtime(deferred=deferred)
    stale_sources=[];registry.set_mcp_catalog_stale_callback(stale_sources.append)
    before=registry.catalog_snapshot().revision;hooks.unavailable=True
    if deferred:
        token=set_tool_execution_context(ToolExecutionContext("scope","s","r"))
        try:
            described=service.describe(prepared.deferred[0].capability_id)
            with pytest.raises(RuntimeError,match="tool_catalog_stale"):
                service.activate(described["capability_id"],described["schema_hash"],described["describe_nonce"])
        finally:reset_tool_execution_context(token)
    else:
        with pytest.raises(RuntimeError,match="tool_catalog_stale"):
            registry.validate_prepared_tool_set(prepared,eligibility=eligibility)
    assert registry.get("mcp_fixture_fixture_tool") is None
    assert registry.catalog_snapshot().revision == before + 1
    assert stale_sources == ["mcp:fixture"]


def test_main_wires_hook_without_changing_defaults():
    source=(Path(__file__).resolve().parents[1]/"main.py").read_text(encoding="utf-8")
    assert "ContextOSE2EHooks.from_env()" in source
    assert "set_context_os_e2e_hooks(_context_os_e2e_hooks)" in source
    assert '"ctx-fallback"' in source
    assert '"ctx-compact-fixture"' in source
    assert "_read_context_compaction_model" in source


def test_fault_consumer_is_inert_outside_trusted_e2e(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: pytest.fail("network used"))
    assert consume_context_os_e2e_fault("l3_timeout") is None


def test_fault_consumer_uses_one_shot_endpoint(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_DAEMON_URL", "http://127.0.0.1:18991")
    seen = []

    class _Reply:
        def __enter__(self): return self
        def __exit__(self, *_args): return None

    def _open(request, timeout):
        seen.append((request.full_url, timeout))
        reply = _Reply()
        reply.read = lambda: b'{"ok":true,"value":"forced"}'
        return reply

    monkeypatch.setattr("urllib.request.urlopen", _open)
    assert consume_context_os_e2e_fault("l3_timeout") == "forced"
    assert seen == [("http://127.0.0.1:18991/fault/l3_timeout?consume=1", 0.5)]
