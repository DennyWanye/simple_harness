"""The only fetch/fallback/extraction policy used by web and research."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import urllib.robotparser
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from .cache import AsyncTTLCache
from .content_quality import looks_like_app_shell, quality_flags
from .contracts import EvidenceDocument, FetchRequest
from .ranking import canonicalize_url, stable_candidate_id
from .transports import ScraplingTransport


def _metric(event: str, detail: dict) -> None:
    try:
        from observability.metrics_sink import record
        record(event, detail)
    except Exception:
        pass


class FetchExtractError(RuntimeError):
    def __init__(self, code: str, *, retriable: bool = True, status: int | None = None) -> None:
        super().__init__(code)
        self.code, self.retriable, self.status = code, retriable, status


class FetchExtractService:
    def __init__(
        self,
        *,
        transport: ScraplingTransport | None = None,
        client: httpx.AsyncClient | None = None,
        respect_robots: bool = True,
        request_interval_ms: int = 250,
        cache_size: int = 128,
        cache_ttl_s: float = 120.0,
        allow_jina: bool = False,
    ) -> None:
        self.transport = transport or ScraplingTransport()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 DeskPetFetch/1.0"},
            follow_redirects=True,
            max_redirects=5,
        )
        self.respect_robots = respect_robots
        self.request_interval_s = max(0, request_interval_ms) / 1000
        self.allow_jina = allow_jina
        self.cache: AsyncTTLCache[EvidenceDocument] = AsyncTTLCache(max_size=cache_size, ttl_s=cache_ttl_s)
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_lock = asyncio.Lock()
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._url_locks: dict[str, asyncio.Lock] = {}
        self._content_hashes: dict[str, str] = {}
        self._content_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    def from_config(cls, config) -> "FetchExtractService":
        web = ((getattr(config, "raw", {}) or {}).get("tools") or {}).get("web") or {}
        research = (getattr(config, "raw", {}) or {}).get("research") or {}
        sg = config.search_gateway
        return cls(
            respect_robots=bool(web.get("respect_robots_txt", True)),
            request_interval_ms=int(web.get("request_interval_ms", 250)),
            cache_size=sg.cache_size,
            cache_ttl_s=sg.cache_ttl_s,
            allow_jina=bool(research.get("jina_reader", False)),
        )

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        url = canonicalize_url(request.url)
        cached = await self.cache.get(url)
        if cached is not None:
            return cached
        lock = self._url_locks.setdefault(url, asyncio.Lock())
        async with lock:
            cached = await self.cache.get(url)
            if cached is not None:
                return cached
            return await self._fetch_uncached(request, url)

    async def _fetch_uncached(self, request: FetchRequest, url: str) -> EvidenceDocument:
        raw = await self.fetch_raw(request)
        html = str(raw.get("content") or "")
        content_type = str(raw.get("content_type") or "")
        title, text, published, extractor = self._extract(html)
        fetcher = str(raw.get("fetcher") or "httpx")

        should_render = request.render_policy == "always" or (
            request.render_policy == "auto" and looks_like_app_shell(html, text)
        )
        if should_render and (request.request_budget is None or await request.request_budget.claim_cdp()):
            from deskpet.tools.research_cdp_edge import cdp_edge_render
            rendered = await cdp_edge_render(url, timeout=min(request.timeout, 10.0))
            if rendered:
                r_title, r_text, r_published, r_extractor = self._extract(rendered)
                if len(r_text) > len(text):
                    html, title, text, published, extractor, fetcher = rendered, r_title, r_text, r_published, r_extractor, "cdp-edge"
                    _metric("fetch_extract_fallback", {"stage": "cdp", "fetcher": "cdp-edge", "ok": True})

        if len(text) < 300 and request.allow_jina and self.allow_jina:
            jina = await self._jina(url, min(request.timeout, 8.0))
            if jina is not None and len(jina[1]) > len(text):
                title, text, extractor, fetcher = jina[0] or title, jina[1], "jina", "jina"
                _metric("fetch_extract_fallback", {"stage": "jina", "fetcher": "jina", "ok": True})

        text = text[: max(1, request.max_chars)]
        canonical = canonicalize_url(str(raw.get("url_final") or url))
        fetched_at = datetime.now(timezone.utc).isoformat()
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        flags = list(quality_flags(text=text, html=html, content_type=content_type))
        async with self._content_lock:
            previous_url = self._content_hashes.get(content_hash)
            if previous_url is not None and previous_url != canonical:
                flags.append("duplicate")
            else:
                self._content_hashes[content_hash] = canonical
        document = EvidenceDocument(
            stable_id=stable_candidate_id(canonical), url=request.url,
            canonical_url=canonical, title=title, text=text,
            published_at=published or None, provider="fetch", providers=("fetch",),
            provider_rank=0, score=0.0, searched_at=fetched_at, source_kind="web",
            content_hash=content_hash,
            fetcher=fetcher, extractor=extractor, fetched_at=fetched_at,
            quality_flags=tuple(flags),
            html=html[: request.max_chars] if request.include_html else None,
        )
        for flag in flags or ["ok"]:
            _metric("fetch_extract_quality", {
                "status": flag, "fetcher": fetcher,
                "extractor": extractor, "count": len(text),
            })
        await self.cache.put(canonical, document)
        if canonical != url:
            await self.cache.put(url, document)
        return document

    async def fetch_raw(self, request: FetchRequest) -> dict:
        parsed = urlsplit(request.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise FetchExtractError("invalid_url", retriable=False)
        if not await self._allowed_by_robots(request.url, request.timeout):
            raise FetchExtractError("blocked_by_robots", retriable=False)
        await self._throttle(parsed.hostname.lower())

        # Only the blocking raw Scrapling transport runs in a worker; fallback
        # policy remains in this async service.
        scraped = await asyncio.to_thread(self.transport.fetch, request.url, timeout=request.timeout)
        if scraped.get("ok"):
            status = int(scraped.get("status") or 0)
            html = str(scraped.get("html") or "")
            if 200 <= status < 300 and html and "captcha" not in quality_flags(text="", html=html, content_type="text/html"):
                return {
                    "status": status, "url_final": scraped.get("url_final") or request.url,
                    "fetcher": scraped.get("fetcher") or "scrapling",
                    "content_type": scraped.get("content_type") or "text/html; charset=utf-8",
                    "content": html[: request.max_chars],
                }
        _metric("fetch_extract_fallback", {"stage": "httpx", "fetcher": "httpx"})
        try:
            response = await self.client.get(request.url, timeout=request.timeout)
        except httpx.TimeoutException as exc:
            raise FetchExtractError("timeout") from exc
        except httpx.HTTPError as exc:
            raise FetchExtractError("http_error") from exc
        status_code = int(getattr(response, "status_code", 200) or 200)
        headers = getattr(response, "headers", {}) or {}
        content_type = headers.get("content-type", "text/html")
        if status_code in {403, 429}:
            raise FetchExtractError("blocked", status=status_code)
        if status_code >= 500:
            raise FetchExtractError("upstream_error", status=status_code)
        if status_code >= 400:
            raise FetchExtractError("http_status", retriable=False, status=status_code)
        content = response.text[: request.max_chars]
        return {
            "status": status_code, "url_final": str(getattr(response, "url", request.url)),
            "fetcher": "httpx", "content_type": content_type, "content": content,
        }

    async def _allowed_by_robots(self, url: str, timeout: float) -> bool:
        if not self.respect_robots: return True
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        async with self._robots_lock:
            cached = self._robots.get(host)
        if cached is None:
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            parser = urllib.robotparser.RobotFileParser(robots_url)
            try:
                response = await self.client.get(robots_url, timeout=min(timeout, 3.0))
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except Exception:
                parser.parse([])
            async with self._robots_lock:
                self._robots[host] = parser
            cached = parser
        return cached.can_fetch("DeskPetFetch/1.0", url)

    async def _throttle(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self.request_interval_s - (time.monotonic() - self._host_last.get(host, 0.0))
            if wait > 0: await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    @staticmethod
    def _extract(html: str) -> tuple[str, str, str, str]:
        title = text = published = ""
        try:
            import trafilatura  # type: ignore
            text = (trafilatura.extract(html, include_comments=False, include_tables=False, favor_recall=False) or "").strip()
            meta = trafilatura.extract_metadata(html)
            if meta:
                title = str(getattr(meta, "title", None) or "").strip()
                published = str(getattr(meta, "date", None) or "").strip()
        except Exception:
            pass
        if not title:
            match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if match: title = re.sub(r"\s+", " ", match.group(1)).strip()
        if not text:
            try:
                from selectolax.parser import HTMLParser  # type: ignore
                tree = HTMLParser(html)
                for selector in ("script", "style", "noscript"):
                    for node in tree.css(selector): node.decompose()
                text = re.sub(r"\s+", " ", tree.body.text(separator=" ") if tree.body else "").strip()
            except Exception:
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        return title, text, published, "trafilatura" if text else "none"

    async def _jina(self, url: str, timeout: float) -> tuple[str, str] | None:
        try:
            response = await self.client.get("https://r.jina.ai/" + url, timeout=timeout, headers={"Accept": "text/plain"})
            response.raise_for_status()
        except Exception:
            return None
        body = response.text
        title_match = re.search(r"^Title:\s*(.+)$", body, re.M)
        marker = body.find("Markdown Content:")
        text = body[marker + len("Markdown Content:"):].strip() if marker >= 0 else body.strip()
        return ((title_match.group(1).strip() if title_match else ""), text)

    async def close(self) -> None:
        if self._closed: return
        self._closed = True
        await self.cache.clear()
        if self._owns_client: await self.client.aclose()

    shutdown = close
