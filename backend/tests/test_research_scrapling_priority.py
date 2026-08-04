from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deskpet.retrieval import runtime
from deskpet.retrieval.contracts import EvidenceDocument
from deskpet.tools import research_tools as r


@pytest.mark.asyncio
async def test_default_extract_uses_shared_fetch_extract_service(monkeypatch):
    service = SimpleNamespace(fetch=AsyncMock(return_value=EvidenceDocument(
        "id", "https://example.com/article", "https://example.com/article",
        "Scrapling Research Page", "fetched through shared service", None,
        "fetch", ("fetch",), 0, 0.0, "now", "web", "hash", "scrapling",
        "trafilatura", "now",
    )))
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: SimpleNamespace(fetch_service=service))
    out = await r.default_extract("https://example.com/article")
    assert out["ok"] is True
    assert out["extractor"] == "trafilatura"
    assert out["fetcher"] == "scrapling"
    assert "fetched through shared service" in out["text"]
    service.fetch.assert_awaited_once()
