from __future__ import annotations

import json
from types import SimpleNamespace

from deskpet.retrieval import runtime
from deskpet.tools.registry import registry


class _Service:
    def __init__(self, *, quote=True): self.quote = quote
    async def fetch_raw(self, request):
        if "xau-usd" in request.url:
            content = '<span data-test="instrument-price-last">4,050.17</span>' if self.quote else "<html>no quote</html>"
        elif "usd-cny" in request.url:
            content = '<span data-test="instrument-price-last">6.8003</span>'
        else:
            content = "abcdef"[:request.max_chars]
        return {"status": 200, "url_final": request.url, "fetcher": "shared", "content_type": "text/html", "content": content}


def _install(monkeypatch, service):
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: SimpleNamespace(fetch_service=service))


def test_gold_price_lookup_extracts_xau_and_cny(monkeypatch):
    _install(monkeypatch, _Service())
    out = json.loads(registry.dispatch("gold_price_lookup", {}))
    assert out["ok"] is True
    assert out["xau_usd_per_troy_ounce"] == 4050.17
    assert out["usd_cny"] == 6.8003
    assert out["cny_per_gram_estimate"] == 885.51
    assert out["fetcher"] == "shared"


def test_gold_price_lookup_reports_extract_failure(monkeypatch):
    _install(monkeypatch, _Service(quote=False))
    out = json.loads(registry.dispatch("gold_price_lookup", {}))
    assert out["ok"] is False
    assert "extract XAU/USD" in out["error"]


def test_scrapling_fetch_truncates_via_shared_service(monkeypatch):
    _install(monkeypatch, _Service())
    out = json.loads(registry.dispatch("scrapling_fetch", {"url": "https://example.com/page", "max_chars": 3}))
    assert out["ok"] is True
    assert out["content"] == "abc"
    assert out["fetcher"] == "shared"
