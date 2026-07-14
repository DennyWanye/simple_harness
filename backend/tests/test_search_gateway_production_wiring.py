from __future__ import annotations

import asyncio
import json
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


@pytest.mark.asyncio
async def test_tool_registry_awaits_production_async_web_search(monkeypatch):
    from deskpet.retrieval import runtime
    from deskpet.retrieval.contracts import SearchResponse
    from deskpet.tools.code_tools.registration import register_code_tools
    from deskpet.tools.registry import ToolRegistry

    gateway = _Provider("duckduckgo")
    search = asyncio.Future()
    search.set_result(SearchResponse("query", tuple(await gateway.search(SearchRequest("query"), None, None))))
    fake_gateway = type("Gateway", (), {"search": lambda self, request: search})()
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: fake_gateway)
    registry = ToolRegistry()
    register_code_tools(registry)
    result = await registry.execute_tool("web_search", {"query": "query"}, "sid")
    assert result["ok"] is True
    payload = json.loads(result["result"])
    assert payload["count"] == 1
    assert payload["results"][0]["provider"] == "duckduckgo"


@pytest.mark.asyncio
async def test_research_v2_adapter_returns_response_while_v1_stays_list(monkeypatch):
    from deskpet.retrieval import runtime
    from deskpet.retrieval.contracts import SearchResponse
    from deskpet.tools import research_tools

    candidate = (await _Provider("duckduckgo").search(SearchRequest("topic"), None, None))[0]
    fake = type("Gateway", (), {"search": AsyncSearch(SearchResponse("topic", (candidate,)))})()
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: fake)
    response = await research_tools.gateway_search_response("topic", max_results=20, run_id="run")
    legacy = await research_tools.default_search("topic", max_results=5)
    assert isinstance(response, SearchResponse)
    assert response.results[0].source_kind == "serp"
    assert isinstance(legacy, list)
    assert legacy[0]["url"] == candidate.url


class AsyncSearch:
    def __init__(self, response): self.response = response
    def __call__(self, request):
        async def _result(): return self.response
        return _result()
