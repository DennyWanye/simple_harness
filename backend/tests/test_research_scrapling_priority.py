# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import pytest

from deskpet.tools import research_tools as r


_ARTICLE_HTML = (
    "<html><head><title>Scrapling Research Page</title></head><body>"
    "<article><h1>Scrapling Research Page</h1><p>"
    + ("This page was fetched through Scrapling before httpx. " * 60)
    + "</p></article></body></html>"
)


@pytest.mark.asyncio
async def test_default_extract_prefers_scrapling_when_no_client(monkeypatch: pytest.MonkeyPatch):
    calls = {"scrapling": 0, "httpx": 0}
    monkeypatch.setattr(r, "_js_render_enabled", lambda: False)
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)

    def _scrapling(url: str, timeout: float):
        calls["scrapling"] += 1
        return {
            "ok": True,
            "status": 200,
            "html": _ARTICLE_HTML,
            "url_final": url,
            "fetcher": "scrapling",
        }

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, url, **kwargs):
            calls["httpx"] += 1
            raise AssertionError("httpx should not fetch when Scrapling succeeds")

        async def aclose(self):
            pass

    monkeypatch.setattr(r, "_scrapling_fetch_html", _scrapling)
    monkeypatch.setattr(r.httpx, "AsyncClient", _AsyncClient)

    out = await r.default_extract("https://example.com/article")
    assert out["ok"] is True
    assert out["extractor"] == "scrapling+trafilatura"
    assert out["fetcher"] == "scrapling"
    assert "Scrapling" in out["title"]
    assert "fetched through Scrapling" in out["text"]
    assert calls == {"scrapling": 1, "httpx": 0}
