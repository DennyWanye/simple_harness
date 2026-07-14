from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.contracts import RetrievalCandidate, SearchRequest
from deskpet.retrieval.search_gateway import SearchGateway


class _Provider:
    capabilities = frozenset({"html"})
    def __init__(self, name, delay=0, rows=1):
        self.name, self.delay, self.rows = name, delay, rows
        self.calls = 0
    async def is_available(self): return True
    async def search(self, request, budget, client):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return [RetrievalCandidate(
            stable_id=f"{self.name}-{i}", url=f"https://{self.name}{i}.test/a",
            canonical_url=f"https://{self.name}{i}.test/a", title=f"{request.query} {self.name} {i}",
            provider=self.name, providers=(self.name,), provider_rank=i + 1,
            searched_at="2026-07-14T00:00:00+00:00",
        ) for i in range(self.rows)]


@pytest.mark.asyncio
async def test_gateway_batches_deterministically_and_caches_success():
    cfg = replace(SearchGatewayConfig(), providers=["duckduckgo", "baidu"], quick_min_results=2, quick_min_domains=2)
    ddg, baidu = _Provider("duckduckgo", delay=.01), _Provider("baidu", rows=1)
    gateway = SearchGateway(config=cfg, providers=[ddg, baidu], client=httpx.AsyncClient())
    try:
        first = await gateway.search(SearchRequest("hello", max_results=5))
        second = await gateway.search(SearchRequest("hello", max_results=5))
    finally:
        await gateway.client.aclose()
        await gateway.shutdown()
    assert first.count == 2
    assert first.engines_tried == ["duckduckgo", "baidu"]
    assert second.cache_hit is True
    assert ddg.calls == baidu.calls == 1


@pytest.mark.asyncio
async def test_concurrent_request_diagnostics_do_not_cross_talk():
    cfg = replace(SearchGatewayConfig(), providers=["duckduckgo"], quick_min_results=1, quick_min_domains=1)
    provider = _Provider("duckduckgo")
    gateway = SearchGateway(config=cfg, providers=[provider], client=httpx.AsyncClient())
    try:
        one, two = await asyncio.gather(
            gateway.search(SearchRequest("one")), gateway.search(SearchRequest("two")),
        )
    finally:
        await gateway.client.aclose()
        await gateway.shutdown()
    assert one.query == "one" and two.query == "two"
    assert one.results[0].title.startswith("one")
    assert two.results[0].title.startswith("two")
