from __future__ import annotations

import pytest
from host import provider_chain
from host import runtime as host_runtime
from llm.resolution import ProviderRoutingReadiness, SessionProviderUnavailable

from tests.host_patch import patch_host_global


@pytest.mark.asyncio
async def test_product_provider_resolution_cannot_fall_back_before_readiness(monkeypatch):

    previous_registry = host_runtime.service_context.get("provider_registry")
    previous_readiness = host_runtime.service_context.get("provider_routing_readiness")
    readiness = ProviderRoutingReadiness()
    try:
        patch_host_global(monkeypatch, "_e2e_provider_base", "")
        host_runtime.service_context.register("provider_registry", None)
        host_runtime.service_context.register("provider_routing_readiness", readiness)
        with pytest.raises(
            SessionProviderUnavailable,
            match="provider_routing_initializing",
        ):
            await provider_chain._resolve_agent_provider_chain(
                "session-before-ready",
                session_db=object(),
            )
    finally:
        host_runtime.service_context.register("provider_registry", previous_registry)
        host_runtime.service_context.register(
            "provider_routing_readiness",
            previous_readiness,
        )


@pytest.mark.asyncio
async def test_product_provider_resolution_cannot_use_legacy_fallback_after_failure(monkeypatch):

    previous_registry = host_runtime.service_context.get("provider_registry")
    previous_readiness = host_runtime.service_context.get("provider_routing_readiness")
    readiness = ProviderRoutingReadiness()
    readiness.mark_failed("registry_load_failed")
    try:
        patch_host_global(monkeypatch, "_e2e_provider_base", "")
        host_runtime.service_context.register("provider_registry", None)
        host_runtime.service_context.register("provider_routing_readiness", readiness)
        with pytest.raises(SessionProviderUnavailable):
            await provider_chain._resolve_agent_provider_chain(
                "session-after-failure",
                session_db=object(),
            )
    finally:
        host_runtime.service_context.register("provider_registry", previous_registry)
        host_runtime.service_context.register(
            "provider_routing_readiness",
            previous_readiness,
        )
