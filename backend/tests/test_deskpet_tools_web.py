"""Compatibility coverage for async web-tool adapters and pure parsers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deskpet.retrieval.fetch_extract import FetchExtractError
from deskpet.retrieval import runtime
from deskpet.tools import web_tools
from deskpet.tools.registry import registry


class _Service:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls = []
    async def fetch_raw(self, request):
        self.calls.append(request.url)
        value = self.pages.get(request.url)
        if isinstance(value, Exception): raise value
        if value is None:
            value = "<html><title>A</title><body>article text</body></html>"
        content_type = "application/xml" if "sitemap" in request.url else "text/html"
        return {"status": 200, "url_final": request.url, "fetcher": "fake", "content_type": content_type, "content": value}


def _install(monkeypatch, service):
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: SimpleNamespace(fetch_service=service))


def test_web_fetch_registry_dispatch_awaits_async_handler(monkeypatch):
    service = _Service()
    _install(monkeypatch, service)
    out = json.loads(registry.dispatch("web_fetch", {"url": "https://example.test/"}))
    assert out["status"] == 200
    assert out["fetcher"] == "fake"
    assert "article text" in out["content"]


def test_web_fetch_structures_service_error(monkeypatch):
    service = _Service({"https://example.test/": FetchExtractError("timeout")})
    _install(monkeypatch, service)
    out = json.loads(registry.dispatch("web_fetch", {"url": "https://example.test/"}))
    assert out == {"error": "timeout", "retriable": True}


def test_web_fetch_schema_explains_scrapling_first_compatibility_name():
    schema = next(item["function"] for item in registry.schemas() if item["function"]["name"] == "web_fetch")
    description = schema["description"]
    assert "Scrapling" in description
    assert "回退 httpx" in description


def test_web_extract_article_from_raw_html_gets_title_and_text():
    html = "<html><head><title>Hello</title></head><body><h1>Hello</h1><p>Body text here.</p></body></html>"
    out = json.loads(registry.dispatch("web_extract_article", {"url_or_html": html}))
    assert out["title"] == "Hello"
    assert "Body text" in (out["text"] or "")


def test_web_extract_article_missing_fields_return_null():
    out = json.loads(registry.dispatch("web_extract_article", {"url_or_html": "<html><body></body></html>"}))
    assert set(("title", "author", "date", "text", "language")) <= set(out)


def test_web_extract_article_url_uses_shared_service(monkeypatch):
    service = _Service()
    _install(monkeypatch, service)
    out = json.loads(registry.dispatch("web_extract_article", {"url_or_html": "https://example.test/a"}))
    assert out["title"] == "A"
    assert out["fetcher"] == "fake"
    assert service.calls == ["https://example.test/a"]


def test_web_read_sitemap_parses_and_dedupes(monkeypatch):
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://site.test/a</loc><lastmod>2026-01-01</lastmod></url>
      <url><loc>https://site.test/a</loc></url><url><loc>https://site.test/b</loc></url>
    </urlset>"""
    service = _Service({"https://site.test/sitemap.xml": xml})
    _install(monkeypatch, service)
    out = json.loads(registry.dispatch("web_read_sitemap", {"sitemap_url": "https://site.test/sitemap.xml"}))
    assert out["count"] == 2
    assert [row["url"] for row in out["urls"]] == ["https://site.test/a", "https://site.test/b"]


def test_web_crawl_same_origin_and_keyword_scoring(monkeypatch):
    pages = {
        "https://site.test/": '<html><body><a href="/hot">h</a><a href="/cold">c</a><a href="https://off.test/x">off</a></body></html>',
        "https://site.test/hot": "<html><title>Python Python</title><body>python python rocks</body></html>",
        "https://site.test/cold": "<html><title>Rocks</title><body>nothing</body></html>",
    }
    service = _Service(pages)
    _install(monkeypatch, service)
    out = json.loads(registry.dispatch("web_crawl", {
        "start_url": "https://site.test/", "keywords": ["python"],
        "max_depth": 1, "max_pages": 10,
    }))
    assert out["count"] == 3
    assert out["pages"][0]["url"].endswith("/hot")
    assert "https://off.test/x" not in service.calls


def test_sitemap_parser_accepts_non_namespaced_index():
    urls, children = web_tools._parse_sitemap_xml(
        "<sitemapindex><sitemap><loc>https://a.test/sitemap.xml</loc></sitemap></sitemapindex>"
    )
    assert urls == []
    assert children == ["https://a.test/sitemap.xml"]
