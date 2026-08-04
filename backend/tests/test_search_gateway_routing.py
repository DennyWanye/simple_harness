from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.cache import AsyncTTLCache
from deskpet.retrieval.contracts import PublicErrorCode, SearchRequest
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.routing import stable_batches
from deskpet.retrieval.search_gateway import SearchGateway


def test_stable_language_batches_and_optional_searxng():
    configured = ["baidu", "duckduckgo", "google-cdp", "bing-cdp", "searxng"]
    assert stable_batches("中文查询", configured, searxng=True, google_reachable=True)[0] == ["searxng", "baidu"]
    assert stable_batches("english", configured, searxng=True, google_reachable=True)[0] == ["searxng", "duckduckgo"]
    batches = stable_batches("english", configured[:-1], searxng=False, google_reachable=False)
    assert batches[0] == ["duckduckgo"]
    assert batches[1] == ["bing-cdp"]


@pytest.mark.asyncio
async def test_ttl_cache_is_bounded_lru_and_expires():
    cache = AsyncTTLCache(max_size=2, ttl_s=.02)
    await cache.put("a", 1); await cache.put("b", 2)
    assert await cache.get("a") == 1
    await cache.put("c", 3)
    assert await cache.get("b") is None
    await asyncio.sleep(.03)
    assert await cache.get("a") is None


class _Failing:
    name = "duckduckgo"
    capabilities = frozenset({"html"})
    def __init__(self): self.calls = 0
    async def is_available(self): return True
    async def search(self, request, budget, client):
        self.calls += 1
        raise ProviderFailure(PublicErrorCode.BLOCKED)


@pytest.mark.asyncio
async def test_provider_failure_enters_shared_cooldown_without_cross_request_diagnostics():
    provider = _Failing()
    cfg = replace(SearchGatewayConfig(), providers=["duckduckgo"], cooldown_threshold=1, cooldown_ttl_s=60)
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=cfg, providers=[provider], client=client)
    try:
        first = await gateway.search(SearchRequest("one"))
        second = await gateway.search(SearchRequest("two"))
    finally:
        await gateway.shutdown(); await client.aclose()
    assert first.attempts[0].status.value == "blocked"
    assert second.attempts[0].status.value == "cooldown"
    assert provider.calls == 1
    assert first.query == "one" and second.query == "two"


class _Cancellable:
    name = "duckduckgo"
    capabilities = frozenset({"html"})
    def __init__(self): self.cancelled = asyncio.Event()
    async def is_available(self): return True
    async def search(self, request, budget, client):
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


@pytest.mark.asyncio
async def test_request_cancellation_cancels_provider_task_and_shutdown_is_clean():
    provider = _Cancellable()
    cfg = replace(SearchGatewayConfig(), providers=["duckduckgo"])
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=cfg, providers=[provider], client=client)
    task = asyncio.create_task(gateway.search(SearchRequest("cancel")))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    await asyncio.wait_for(provider.cancelled.wait(), timeout=.2)
    await gateway.shutdown(); await client.aclose()
    assert not gateway._tasks
