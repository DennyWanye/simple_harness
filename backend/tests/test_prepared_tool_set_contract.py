from __future__ import annotations

import pytest

from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ResolvedToolDraft,
    ToolCapabilityRef,
    ToolEligibilityContext,
    ToolCapabilityScopeStore,
    canonical_hash,
    selector_matches,
)


def _cap(name: str) -> PreparedToolCapability:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    return PreparedToolCapability(
        ToolCapabilityRef(
            capability_id=f"builtin:{name}",
            name=name,
            toolset="core",
            source="builtin",
            description=name,
            schema_hash=canonical_hash(schema),
        ),
        schema,
    )


def test_draft_finalize_never_reads_registry_and_freezes_conditional() -> None:
    direct = _cap("read")
    page_in = _cap("session_history_page_in")
    draft = ResolvedToolDraft(
        registry_revision=7,
        direct=(direct,),
        conditional_direct=(page_in,),
        policy_fingerprint="policy",
    )
    base = draft.finalize(required_conditional_names=(), scope_id="scope")
    paged = draft.finalize(
        required_conditional_names=("session_history_page_in",), scope_id="scope2"
    )
    assert [s["function"]["name"] for s in base.logical_schemas()] == ["read"]
    assert [s["function"]["name"] for s in paged.logical_schemas()] == [
        "read",
        "session_history_page_in",
    ]


def test_activation_returns_new_immutable_revision() -> None:
    initial = PreparedToolSet.create(
        scope_id="scope",
        revision=1,
        registry_revision=3,
        direct=(_cap("read"),),
        deferred=(_cap("write").ref,),
        activated=(),
        denied_names=(),
        policy_fingerprint="p",
        decisions=(),
    )
    updated = initial.activate(_cap("write"))
    assert initial.revision == 1
    assert not initial.has_direct("write")
    assert updated.revision == 2
    assert updated.has_direct("write")


def test_schema_hash_mismatch_fails_closed() -> None:
    ref = _cap("read").ref
    with pytest.raises(ValueError, match="schema hash mismatch"):
        PreparedToolCapability(ref, {"name": "changed"})


def test_selector_grammar_is_explicit() -> None:
    assert selector_matches(
        "source:mcp:*", name="mcp_x_read", toolset="mcp", source="mcp:x"
    )
    assert not selector_matches(
        "source:plugin:*", name="mcp_x_read", toolset="mcp", source="mcp:x"
    )
    assert selector_matches(
        "source:capability:*",
        name="godot__detect",
        toolset="capability",
        source="capability:godot:1.0.4:manifest",
    )
    with pytest.raises(ValueError, match="unknown tool selector"):
        selector_matches("provider:any", name="x", toolset="x", source="builtin")


def test_scope_store_rejects_cross_session() -> None:
    prepared = PreparedToolSet.create(
        scope_id="scope",
        revision=1,
        registry_revision=1,
        direct=(_cap("read"),),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="p",
        decisions=(),
    )
    store = ToolCapabilityScopeStore()
    store.open(
        prepared,
        ToolEligibilityContext("session-a", "request-a", "chat"),
    )
    assert store.get("scope", session_id="session-a", request_id="request-a")
    assert store.get("scope", session_id="session-b", request_id="request-a") is None


def test_scope_store_pin_keeps_live_effect_alive_and_renews_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "deskpet.tools.capabilities.time.monotonic", lambda: now[0]
    )
    prepared = PreparedToolSet.create(
        scope_id="scope",
        revision=1,
        registry_revision=1,
        direct=(_cap("read"),),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="p",
        decisions=(),
    )
    store = ToolCapabilityScopeStore(ttl_seconds=5.0)
    store.open(
        prepared,
        ToolEligibilityContext("session-a", "request-a", "chat"),
    )

    assert store.pin(
        "scope", session_id="session-a", request_id="request-a"
    )
    now[0] = 106.0
    assert store.get(
        "scope", session_id="session-a", request_id="request-a"
    )
    assert store.unpin(
        "scope", session_id="session-a", request_id="request-a"
    )

    now[0] = 110.0
    assert store.get(
        "scope", session_id="session-a", request_id="request-a"
    )
    now[0] = 112.0
    assert (
        store.get("scope", session_id="session-a", request_id="request-a")
        is None
    )
