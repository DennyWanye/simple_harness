"""Production web_search wiring is async and preserves its public JSON shape."""
from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

from deskpet.retrieval.contracts import RetrievalCandidate, SearchResponse
from deskpet.tools.code_tools.web_search_tool import web_search


def _candidate(index: int = 1) -> RetrievalCandidate:
    return RetrievalCandidate(
        stable_id=str(index), url=f"https://example.com/{index}",
        canonical_url=f"https://example.com/{index}", title=f"Result {index}",
        snippet="snippet", provider="duckduckgo", providers=("duckduckgo",),
        provider_rank=index, searched_at="2026-07-14T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_web_search_is_async_and_preserves_public_shape():
    gateway = AsyncMock()
    gateway.search.return_value = SearchResponse("python", (_candidate(),), elapsed_ms=12)
    with patch("deskpet.retrieval.runtime.get_default_gateway", return_value=gateway):
        out = json.loads(await web_search({"query": "python", "max_results": 5}))
    assert inspect.iscoroutinefunction(web_search)
    assert out["query"] == "python"
    assert out["count"] == 1
    assert out["results"][0]["title"] == "Result 1"
    assert out["engines_tried"] == []
    assert out["elapsed_ms"] == 12


@pytest.mark.asyncio
async def test_web_search_caps_max_results_and_hydrate_top():
    gateway = AsyncMock()
    gateway.search.return_value = SearchResponse("x", ())
    with patch("deskpet.retrieval.runtime.get_default_gateway", return_value=gateway):
        await web_search({"query": "x", "max_results": 999, "hydrate_top": 999})
    request = gateway.search.await_args.args[0]
    assert request.max_results == 10
    assert request.hydrate_top == 3


@pytest.mark.asyncio
async def test_web_search_structures_empty_provider_failure():
    gateway = AsyncMock()
    gateway.search.return_value = SearchResponse("x", (), degraded=True)
    with patch("deskpet.retrieval.runtime.get_default_gateway", return_value=gateway):
        out = json.loads(await web_search({"query": "x"}))
    assert out["results"] == []
    assert out["degraded"] is True
    assert "error" in out


@pytest.mark.asyncio
async def test_web_search_requires_query_without_touching_gateway():
    with patch("deskpet.retrieval.runtime.get_default_gateway") as gateway:
        out = json.loads(await web_search({}))
    assert "error" in out
    gateway.assert_not_called()
