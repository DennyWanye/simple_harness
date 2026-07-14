from __future__ import annotations

import httpx
import pytest

from deskpet.retrieval.contracts import PublicErrorCode, SearchBudget, SearchRequest
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.providers.duckduckgo import DuckDuckGoProvider
from deskpet.retrieval.providers.searxng import SearXNGProvider


def _budget():
    return SearchBudget.create(total_timeout_s=2, provider_concurrency=1, per_provider_timeout_s=1, cdp_budget=0, hydrate_budget=0)


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
