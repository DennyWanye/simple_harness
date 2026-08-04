# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mode-neutral per-session provider resolution.

``resolve_session_provider_chain`` chooses which providers AgentLoop will
walk for one ordinary session:

  1. Read the session's optional provider/model binding.
  2. If provider_id set AND provider exists + is enabled → single-element
     chain [provider]; if preferred_model is also set, override the
     provider's model field for THIS session only.
  3. If provider_id set but the provider was deleted/disabled → fail closed;
     the user must explicitly choose a replacement.
  4. If provider_id is NULL → return registry.get_chain() unchanged
     (or with each provider's model overridden by preferred_model).
"""
from __future__ import annotations

from typing import Any

import pytest

from llm.resolution import SessionProviderUnavailable, resolve_session_provider_chain


# ────────────────────── stubs ──────────────────────


class _StubRegistry:
    """Stand-in for LLMProviderRegistry exposing only what resolution needs."""

    def __init__(self, providers: list[dict]) -> None:
        """`providers` is a list of dicts with keys
        id / base_url / model / api_key / enabled (defaults True)."""
        self._providers = providers

    def get_chain(self) -> list[dict]:
        """Return enabled providers in priority order — dict form."""
        return [p for p in self._providers if p.get("enabled", True)]

    def get_entry(self, provider_id: str):
        """Return the in-memory dataclass-style entry or None.

        Used by resolution to check 'still exists + enabled'. We mimic
        the real registry's behaviour: returns the dict (or a tiny
        namespace) when found, None otherwise.
        """
        for p in self._providers:
            if p["id"] == provider_id:
                return _EntryNS(**p)
        return None

    def resolve_api_key(self, provider_id: str) -> str | None:
        for p in self._providers:
            if p["id"] == provider_id:
                return p.get("api_key", "stub-key")
        return None


class _EntryNS:
    """Tiny namespace mirroring ProviderEntry dataclass field access."""

    def __init__(self, **fields) -> None:
        # Defaults match ProviderEntry
        self.id = fields["id"]
        self.name = fields.get("name", self.id)
        self.base_url = fields["base_url"]
        self.model = fields["model"]
        self.api_key_ref = fields.get("api_key_ref", f"keychain://{self.id}")
        self.priority = int(fields.get("priority", 1))
        self.enabled = bool(fields.get("enabled", True))
        self.source = fields.get("source", "user")


class _StubSessionDB:
    """Stand-in for SessionDB.get_session_provider_binding."""

    def __init__(self, bindings: dict[str, dict[str, str | None]]) -> None:
        """bindings: {sid: {provider_id, preferred_model}}."""
        self._bindings = bindings
        self.get_calls: list[str] = []

    async def get_session_provider_binding(
        self, base_session_id: str
    ) -> dict[str, Any]:
        self.get_calls.append(base_session_id)
        return self._bindings.get(
            base_session_id,
            {"provider_id": None, "preferred_model": None},
        )


# ────────────────────── tests ──────────────────────


@pytest.mark.asyncio
async def test_pinned_session_returns_single_chain() -> None:
    """3.8: binding provider_id='relay' → chain = [the relay] only."""
    registry = _StubRegistry([
        {"id": "relay", "base_url": "https://your-llm-relay.example.com/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://openrouter.example/v1",
         "model": "claude-4.7-sonnet", "api_key": "k2", "enabled": True},
        {"id": "ollama", "base_url": "http://localhost:11434/v1",
         "model": "gemma", "api_key": "ollama", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "vpn-tunnel": {"provider_id": "relay", "preferred_model": None},
    })

    chain = await resolve_session_provider_chain(
        "vpn-tunnel",
        registry=registry,
        session_db=sdb,
    )

    assert len(chain) == 1
    assert chain[0].id == "relay"
    # model unchanged because preferred_model is None
    assert chain[0].model == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_unbound_session_returns_global_chain() -> None:
    """3.9: binding all None → registry.get_chain()."""
    registry = _StubRegistry([
        {"id": "relay", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://b.example/v1",
         "model": "m2", "api_key": "k2", "enabled": True},
    ])
    sdb = _StubSessionDB({})  # no binding for any sid

    chain = await resolve_session_provider_chain(
        "fresh-task-session",
        registry=registry,
        session_db=sdb,
    )

    assert [p.id for p in chain] == ["relay", "openrouter"]


@pytest.mark.asyncio
async def test_chain_entries_preserve_source_for_agent_loop() -> None:
    """Regression: AgentLoop reads entry.source to mark relay providers."""
    registry = _StubRegistry([
        {"id": "relay-cloud", "base_url": "https://chinzy.com/v1",
         "model": "gpt-5.5", "api_key": "k1", "enabled": True,
         "source": "relay"},
    ])
    sdb = _StubSessionDB({})

    global_chain = await resolve_session_provider_chain(
        "default",
        registry=registry,
        session_db=sdb,
    )
    assert global_chain[0].source == "relay"

    pinned_sdb = _StubSessionDB({
        "default": {"provider_id": "relay-cloud", "preferred_model": None},
    })
    pinned_chain = await resolve_session_provider_chain(
        "default",
        registry=registry,
        session_db=pinned_sdb,
    )
    assert pinned_chain[0].source == "relay"


@pytest.mark.asyncio
async def test_preferred_model_only_overrides_model_field() -> None:
    """3.10: preferred_model set but no provider_id → global chain with
    every provider's model overridden to preferred_model."""
    registry = _StubRegistry([
        {"id": "relay", "base_url": "https://a.example/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://b.example/v1",
         "model": "claude-4.7-sonnet", "api_key": "k2", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "research": {
            "provider_id": None,
            "preferred_model": "claude-4.7-opus",
        },
    })

    chain = await resolve_session_provider_chain(
        "research",
        registry=registry,
        session_db=sdb,
    )

    # Same two providers but model overridden on each.
    assert len(chain) == 2
    assert chain[0].id == "relay"
    assert chain[0].model == "claude-4.7-opus"
    assert chain[1].id == "openrouter"
    assert chain[1].model == "claude-4.7-opus"


@pytest.mark.asyncio
async def test_pinned_to_deleted_provider_fails_closed() -> None:
    """BC-STALE-001: a deleted explicit provider never changes model silently."""
    registry = _StubRegistry([
        # Only 'relay' exists. Binding points at 'gone-provider'.
        {"id": "relay", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "stale-binding": {
            "provider_id": "gone-provider",
            "preferred_model": None,
        },
    })

    with pytest.raises(SessionProviderUnavailable, match="bound_provider_deleted"):
        await resolve_session_provider_chain(
            "stale-binding", registry=registry, session_db=sdb
        )


@pytest.mark.asyncio
async def test_default_session_stale_binding_fails_closed() -> None:
    """BC-STALE-001 applies to the default product Session too."""
    registry = _StubRegistry([
        {"id": "relay", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "default": {"provider_id": "imaginary", "preferred_model": None},
    })

    with pytest.raises(SessionProviderUnavailable, match="bound_provider_deleted"):
        await resolve_session_provider_chain(
            "default", registry=registry, session_db=sdb
        )
    assert sdb.get_calls == ["default"]


@pytest.mark.asyncio
async def test_companion_session_preferred_model_applies() -> None:
    """The main session's preferred_model overrides the global chain."""
    registry = _StubRegistry([
        {"id": "relay", "base_url": "https://a.example/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "default": {"provider_id": None, "preferred_model": "gpt-5.5",
                    "model_params": {"effort": "high"}},
    })

    chain = await resolve_session_provider_chain(
        "default",
        registry=registry,
        session_db=sdb,
    )

    assert [p.id for p in chain] == ["relay"]
    assert chain[0].model == "gpt-5.5"  # binding overrides companion model
    assert chain[0].code_params.get("reasoning_effort") == "high"
