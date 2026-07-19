from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from deskpet.retrieval.contracts import EvidenceDocument, FetchRequest, SearchBudget
from deskpet.retrieval.content_quality import quality_flags
from deskpet.retrieval.fetch_extract import FetchExtractError, FetchExtractService


class _Transport:
    def __init__(self, result, *, delay=0):
        self.result, self.delay, self.calls = result, delay, []
    def fetch(self, url, *, timeout):
        import time
        self.calls.append(url)
        if self.delay: time.sleep(self.delay)
        result = dict(self.result)
        result.setdefault("url_final", url)
        return result


@pytest.mark.asyncio
async def test_fallback_order_is_raw_scrapling_then_async_httpx():
    order = []
    transport = _Transport({"ok": False, "error": "scrapling_failed"})
    original_fetch = transport.fetch
    def tracked(*args, **kwargs):
        order.append("scrapling")
        return original_fetch(*args, **kwargs)
    transport.fetch = tracked
    async def handler(request):
        order.append("httpx")
        return httpx.Response(200, text="<title>A</title><body>article</body>", headers={"content-type": "text/html"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FetchExtractService(transport=transport, client=client, respect_robots=False, request_interval_ms=0)
    try:
        raw = await service.fetch_raw(FetchRequest("https://example.test/a"))
    finally:
        await service.close(); await client.aclose()
    assert order == ["scrapling", "httpx"]
    assert raw["fetcher"] == "httpx"


@pytest.mark.asyncio
async def test_httpx_preferred_skips_scrapling_transport():
    transport = _Transport({"ok": True, "status": 200, "html": "should not run"})

    async def handler(request):
        return httpx.Response(
            200,
            text="<title>A</title><body>official article</body>",
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FetchExtractService(
        transport=transport,
        client=client,
        respect_robots=False,
        request_interval_ms=0,
    )
    try:
        raw = await service.fetch_raw(
            FetchRequest("https://example.test/a", prefer_httpx=True)
        )
    finally:
        await service.close()
        await client.aclose()

    assert transport.calls == []
    assert raw["fetcher"] == "httpx"


@pytest.mark.asyncio
async def test_deepresearch_fetch_substages_emit_run_scoped_safe_timings(monkeypatch):
    events = []
    monkeypatch.setattr(
        "deskpet.retrieval.fetch_extract._metric",
        lambda event, detail: events.append((event, detail)),
    )
    transport = _Transport({"ok": False, "error": "scrapling_failed"})

    async def handler(request):
        return httpx.Response(
            200,
            text="<title>A</title><body>article</body>",
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FetchExtractService(
        transport=transport,
        client=client,
        respect_robots=False,
        request_interval_ms=0,
    )
    try:
        await service.fetch(
            FetchRequest(
                "https://example.test/a",
                run_id="run-fetch-timing",
                render_policy="never",
            )
        )
    finally:
        await service.close()
        await client.aclose()

    timings = [
        detail
        for event, detail in events
        if event == "deepresearch_fetch_attempt_timing"
    ]
    assert [row["stage"] for row in timings] == [
        "robots", "scrapling", "httpx", "extract"
    ]
    assert [row["status"] for row in timings] == [
        "succeeded", "failed", "succeeded", "succeeded"
    ]
    assert all(row["run_id"] == "run-fetch-timing" for row in timings)
    assert all(row["duration_ms"] >= 0 for row in timings)
    assert all("url" not in row and "query" not in row for row in timings)


@pytest.mark.asyncio
async def test_httpx_transport_error_never_hidden_retries(monkeypatch):
    events = []
    monkeypatch.setattr(
        "deskpet.retrieval.fetch_extract._metric",
        lambda event, detail: events.append((event, detail)),
    )
    transport = _Transport({"ok": False, "error": "scrapling_failed"})
    service = FetchExtractService(
        transport=transport,
        respect_robots=False,
        request_interval_ms=0,
    )
    calls = 0

    async def flaky_get(url, *, timeout, headers=None, client=None):
        nonlocal calls
        calls += 1
        raise httpx.RemoteProtocolError("stale pooled connection")

    monkeypatch.setattr(service, "_bounded_httpx_get", flaky_get)
    try:
        with pytest.raises(FetchExtractError, match="http_error"):
            await service.fetch(
                FetchRequest(
                    "https://example.test/a",
                    timeout=5,
                    render_policy="never",
                    run_id="run-httpx-no-hidden-retry",
                )
            )
    finally:
        await service.close()

    assert calls == 1
    timings = [
        detail
        for event, detail in events
        if event == "deepresearch_fetch_attempt_timing"
    ]
    assert any(
        row["stage"] == "httpx"
        and row["status"] == "failed"
        and row["error_code"] == "RemoteProtocolError"
        for row in timings
    )
    assert all(row["stage"] != "httpx_retry" for row in timings)


@pytest.mark.asyncio
async def test_robots_policy_blocks_page_before_transport():
    transport = _Transport({"ok": True, "status": 200, "html": "leaked"})
    async def handler(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FetchExtractService(transport=transport, client=client, respect_robots=True, request_interval_ms=0)
    try:
        from deskpet.retrieval.fetch_extract import FetchExtractError
        with pytest.raises(FetchExtractError, match="blocked_by_robots"):
            await service.fetch_raw(FetchRequest("https://example.test/private"))
    finally:
        await service.close(); await client.aclose()
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dynamic_shell_requires_and_consumes_request_cdp_budget():
    shell = "<html><body><div id='root'></div>" + "<script>x</script>" * 100 + "</body></html>"
    transport = _Transport({"ok": True, "status": 200, "html": shell, "fetcher": "scrapling"})
    service = FetchExtractService(transport=transport, client=httpx.AsyncClient(), respect_robots=False, request_interval_ms=0)
    budget = SearchBudget.create(total_timeout_s=5, provider_concurrency=1, per_provider_timeout_s=2, cdp_budget=1, hydrate_budget=0)
    rendered = "<html><title>Rendered</title><body>" + ("useful evidence " * 100) + "</body></html>"
    try:
        with patch("deskpet.tools.research_cdp_edge.cdp_edge_render", new=AsyncMock(return_value=rendered)) as cdp:
            doc = await service.fetch(FetchRequest("https://app.test/", request_budget=budget))
    finally:
        await service.close(); await service.client.aclose()
    cdp.assert_awaited_once()
    assert budget.cdp_remaining == 0
    assert doc.fetcher == "cdp-edge"
    assert "useful evidence" in doc.text


@pytest.mark.asyncio
async def test_jina_is_double_opt_in_only():
    shell = "<html><title>Shell</title><body>short</body></html>"
    transport = _Transport({"ok": True, "status": 200, "html": shell})
    service = FetchExtractService(transport=transport, client=httpx.AsyncClient(), respect_robots=False, request_interval_ms=0, allow_jina=True)
    service._jina = AsyncMock(return_value=("Jina", "long body " * 100))
    try:
        first = await service.fetch(FetchRequest("https://a.test/", render_policy="never", allow_jina=False))
        second = await service.fetch(FetchRequest("https://b.test/", render_policy="never", allow_jina=True))
    finally:
        await service.close(); await service.client.aclose()
    assert first.fetcher != "jina"
    assert second.fetcher == "jina"
    service._jina.assert_awaited_once()


@pytest.mark.asyncio
async def test_quality_flags_duplicate_mojibake_ai_and_unsupported_content():
    html = "<html><title>X</title><body>Generated by AI ÃÃÃÃ content</body></html>"
    transport = _Transport({"ok": True, "status": 200, "html": html, "content_type": "text/html"})
    service = FetchExtractService(transport=transport, client=httpx.AsyncClient(), respect_robots=False, request_interval_ms=0)
    try:
        one = await service.fetch(FetchRequest("https://a.test/", render_policy="never"))
        two = await service.fetch(FetchRequest("https://b.test/", render_policy="never"))
    finally:
        await service.close(); await service.client.aclose()
    assert {"mojibake", "ai_disclosure"} <= set(one.quality_flags)
    assert "unsupported_content_type" in quality_flags(
        text=one.text,
        html=html,
        content_type="application/pdf",
    )
    assert "duplicate" in two.quality_flags
    assert one.content_hash == two.content_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_payload", "expected_code"),
    [
        ({"ok": True, "status": 200, "html": "%PDF binary", "content_type": "application/pdf"}, "unsupported_content_type"),
        ({"ok": True, "status": 200, "html": "x" * (2 * 1024 * 1024 + 1), "content_type": "text/html"}, "response_too_large"),
    ],
)
async def test_raw_transport_hard_rejects_binary_and_oversized_bodies(
    transport_payload,
    expected_code,
):
    service = FetchExtractService(
        transport=_Transport(transport_payload),
        client=httpx.AsyncClient(),
        respect_robots=False,
        request_interval_ms=0,
    )
    try:
        with pytest.raises(FetchExtractError, match=expected_code) as caught:
            await service.fetch(FetchRequest("https://limit.test/", render_policy="never"))
    finally:
        await service.close()
        await service.client.aclose()
    assert caught.value.code == expected_code


@pytest.mark.asyncio
async def test_render_and_jina_fallbacks_share_the_two_megabyte_gate():
    shell = "<html><title>Shell</title><body>short</body></html>"
    transport = _Transport({"ok": True, "status": 200, "html": shell})
    oversized = "x" * (2 * 1024 * 1024 + 1)
    service = FetchExtractService(
        transport=transport,
        client=httpx.AsyncClient(),
        respect_robots=False,
        request_interval_ms=0,
        allow_jina=True,
        render_call=AsyncMock(return_value=oversized),
    )
    service._jina = AsyncMock(return_value=("Jina", oversized))
    budget = SearchBudget.create(
        total_timeout_s=5,
        provider_concurrency=1,
        per_provider_timeout_s=2,
        cdp_budget=1,
        hydrate_budget=0,
    )
    try:
        document = await service.fetch(FetchRequest(
            "https://limit.test/",
            request_budget=budget,
            allow_jina=True,
        ))
    finally:
        await service.close()
        await service.client.aclose()
    assert document.fetcher not in {"cdp-edge", "jina"}


@pytest.mark.asyncio
async def test_httpx_fallback_stream_aborts_when_body_crosses_hard_cap():
    transport = _Transport({"ok": False, "error": "scrapling_failed"})

    async def handler(request):
        return httpx.Response(
            200,
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = FetchExtractService(
        transport=transport,
        client=client,
        respect_robots=False,
        request_interval_ms=0,
    )
    try:
        with pytest.raises(FetchExtractError, match="response_too_large"):
            await service.fetch_raw(FetchRequest("https://limit.test/stream"))
    finally:
        await service.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_same_url_fetch_is_coalesced_into_cache():
    transport = _Transport({"ok": True, "status": 200, "html": "<html><body>cached text</body></html>"}, delay=.05)
    service = FetchExtractService(transport=transport, client=httpx.AsyncClient(), respect_robots=False, request_interval_ms=0)
    try:
        one, two = await asyncio.gather(
            service.fetch(FetchRequest("https://cache.test/", render_policy="never")),
            service.fetch(FetchRequest("https://cache.test/", render_policy="never")),
        )
    finally:
        await service.close(); await service.client.aclose()
    assert one is two
    assert transport.calls == ["https://cache.test/"]


@pytest.mark.asyncio
async def test_all_production_web_and_research_adapters_share_service(monkeypatch):
    from deskpet.retrieval import runtime
    from deskpet.tools import research_tools, scrapling_tools, web_tools

    service = SimpleNamespace(fetch_raw=AsyncMock(), fetch=AsyncMock())
    def raw(request):
        if "sitemap" in request.url:
            content = "<urlset><url><loc>https://site.test/a</loc></url></urlset>"
            content_type = "application/xml"
        else:
            content = "<html><title>A</title><body>article</body></html>"
            content_type = "text/html"
        return {"status": 200, "url_final": request.url, "fetcher": "fake", "content_type": content_type, "content": content}
    service.fetch_raw.side_effect = raw
    service.fetch.return_value = EvidenceDocument(
        "id", "https://site.test/a", "https://site.test/a", "A", "article",
        None, "fetch", ("fetch",), 0, 0.0, "now", "web", "hash", "fake",
        "trafilatura", "now",
    )
    monkeypatch.setattr(runtime, "get_default_gateway", lambda: SimpleNamespace(fetch_service=service))

    assert all(inspect.iscoroutinefunction(fn) for fn in (
        web_tools._handle_web_fetch, web_tools._handle_web_extract_article,
        web_tools._handle_web_crawl, web_tools._handle_web_read_sitemap,
        scrapling_tools._handle_scrapling_fetch, research_tools.default_extract,
    ))
    assert json.loads(await web_tools._handle_web_fetch({"url": "https://site.test/a"}, ""))["fetcher"] == "fake"
    assert json.loads(await web_tools._handle_web_extract_article({"url_or_html": "https://site.test/a"}, ""))["title"] == "A"
    assert json.loads(await web_tools._handle_web_crawl({"start_url": "https://site.test/a", "max_pages": 1}, ""))["count"] == 1
    assert json.loads(await web_tools._handle_web_read_sitemap({"sitemap_url": "https://site.test/sitemap.xml"}, ""))["count"] == 1
    assert json.loads(await scrapling_tools._handle_scrapling_fetch({"url": "https://site.test/a"}, ""))["fetcher"] == "fake"
    assert (await research_tools.default_extract("https://site.test/a"))["fetcher"] == "fake"
    assert service.fetch_raw.await_count >= 5
    service.fetch.assert_awaited_once()
