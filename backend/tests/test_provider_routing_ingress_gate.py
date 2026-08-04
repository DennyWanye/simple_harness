from __future__ import annotations

import pytest

from llm.resolution import ProviderRoutingReadiness, SessionProviderUnavailable


@pytest.mark.asyncio
async def test_product_provider_resolution_cannot_fall_back_before_readiness():
    import main

    previous_registry = main.service_context.get("provider_registry")
    previous_readiness = main.service_context.get("provider_routing_readiness")
    previous_e2e_base = main._e2e_provider_base
    readiness = ProviderRoutingReadiness()
    try:
        main._e2e_provider_base = ""
        main.service_context.register("provider_registry", None)
        main.service_context.register("provider_routing_readiness", readiness)
        with pytest.raises(
            SessionProviderUnavailable,
            match="provider_routing_initializing",
        ):
            await main._resolve_agent_provider_chain(
                "session-before-ready",
                session_db=object(),
            )
    finally:
        main._e2e_provider_base = previous_e2e_base
        main.service_context.register("provider_registry", previous_registry)
        main.service_context.register(
            "provider_routing_readiness",
            previous_readiness,
        )


@pytest.mark.asyncio
async def test_product_provider_resolution_cannot_use_legacy_fallback_after_failure():
    import main

    previous_registry = main.service_context.get("provider_registry")
    previous_readiness = main.service_context.get("provider_routing_readiness")
    previous_e2e_base = main._e2e_provider_base
    readiness = ProviderRoutingReadiness()
    readiness.mark_failed("registry_load_failed")
    try:
        main._e2e_provider_base = ""
        main.service_context.register("provider_registry", None)
        main.service_context.register("provider_routing_readiness", readiness)
        with pytest.raises(SessionProviderUnavailable):
            await main._resolve_agent_provider_chain(
                "session-after-failure",
                session_db=object(),
            )
    finally:
        main._e2e_provider_base = previous_e2e_base
        main.service_context.register("provider_registry", previous_registry)
        main.service_context.register(
            "provider_routing_readiness",
            previous_readiness,
        )
