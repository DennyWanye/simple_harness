from __future__ import annotations

import httpx
import pytest

from deskpet.retrieval.contracts import PublicErrorCode, SearchBudget, SearchRequest
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.providers.duckduckgo import DuckDuckGoProvider
from deskpet.retrieval.providers.baidu import BaiduProvider
from deskpet.retrieval.providers.bing_cdp import BingCDPProvider
from deskpet.retrieval.providers.google_cdp import GoogleCDPProvider
from deskpet.retrieval.providers.searxng import SearXNGProvider


def _budget():
    return SearchBudget.create(total_timeout_s=2, provider_concurrency=1, per_provider_timeout_s=1, cdp_budget=0, hydrate_budget=0)


def _cdp_budget():
    return SearchBudget.create(total_timeout_s=2, provider_concurrency=1, per_provider_timeout_s=1, cdp_budget=1, hydrate_budget=0)


@pytest.mark.asyncio
async def test_duckduckgo_adapter_emits_standard_candidates():
    html = '<div class="result"><a class="result__a" href="https://example.test/a">A</a><a class="result__snippet">Alpha</a></div>'
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)))
    try:
        rows = await DuckDuckGoProvider().search(SearchRequest("alpha"), _budget(), client)
    finally:
        await client.aclose()
    assert rows[0].provider == "duckduckgo"
    assert rows[0].providers == ("duckduckgo",)
    assert rows[0].canonical_url == "https://example.test/a"


@pytest.mark.asyncio
async def test_searxng_json_adapter_and_html_disabled_failure():
    async def json_handler(request):
        return httpx.Response(200, json={"results": [{"url": "https://a.test", "title": "A", "content": "Alpha"}]}, headers={"content-type": "application/json"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(json_handler))
    provider = SearXNGProvider("http://localhost:8080/search")
    try:
        rows = await provider.search(SearchRequest("alpha"), _budget(), client)
    finally:
        await client.aclose()
    assert rows[0].provider == "searxng"

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>disabled</html>", headers={"content-type": "text/html"})))
    try:
        with pytest.raises(ProviderFailure) as exc:
            await provider.search(SearchRequest("alpha"), _budget(), client)
    finally:
        await client.aclose()
    assert exc.value.code == PublicErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_http_provider_maps_429_to_typed_blocked_failure():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    try:
        with pytest.raises(ProviderFailure) as exc:
            await DuckDuckGoProvider().search(SearchRequest("alpha"), _budget(), client)
    finally:
        await client.aclose()
    assert exc.value.code == PublicErrorCode.BLOCKED


@pytest.mark.asyncio
async def test_baidu_adapter_emits_standard_candidates(monkeypatch):
    monkeypatch.setattr(
        "deskpet.tools.search_provider.parse_baidu_html",
        lambda html, max_results: [{
            "url": "https://example.cn/a",
            "title": "权威结果",
            "snippet": "摘要",
        }],
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="fixture"))
    )
    try:
        rows = await BaiduProvider().search(SearchRequest("中文"), _budget(), client)
    finally:
        await client.aclose()
    assert rows[0].provider == "baidu"
    assert rows[0].providers == ("baidu",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "parser_path", "captcha_path", "expected_name"),
    [
        (GoogleCDPProvider(), "deskpet.tools.search_provider._parse_google_html", "deskpet.tools.search_provider._looks_like_google_captcha", "google-cdp"),
        (BingCDPProvider(), "deskpet.tools.search_provider.parse_bing_html", "deskpet.tools.search_provider._looks_like_bing_captcha", "bing-cdp"),
    ],
)
async def test_cdp_adapters_share_budget_and_standard_contract(
    monkeypatch, provider, parser_path, captcha_path, expected_name
):
    monkeypatch.setattr(
        "deskpet.tools.research_cdp_edge.cdp_edge_render",
        lambda *args, **kwargs: None,
    )

    async def render(*args, **kwargs):
        return "<html>fixture</html>"

    monkeypatch.setattr("deskpet.tools.research_cdp_edge.cdp_edge_render", render)
    monkeypatch.setattr(
        parser_path,
        lambda html, *args, **kwargs: [{
            "url": "https://example.test/cdp",
            "title": "CDP result",
            "snippet": "rendered",
        }],
    )
    monkeypatch.setattr(captcha_path, lambda html, rows: False)
    client = httpx.AsyncClient()
    budget = _cdp_budget()
    try:
        rows = await provider.search(SearchRequest("alpha"), budget, client)
    finally:
        await client.aclose()
    assert rows[0].provider == expected_name
    assert rows[0].providers == (expected_name,)
    assert budget.cdp_remaining == 0
