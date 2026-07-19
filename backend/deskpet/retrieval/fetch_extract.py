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


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TEXT_CONTENT_TYPES = ("text/", "application/xhtml", "application/xml", "application/json")


def _supported_content_type(value: str) -> bool:
    lowered = value.lower().split(";", 1)[0].strip()
    return not lowered or any(kind in lowered for kind in _TEXT_CONTENT_TYPES)


def _metric(event: str, detail: dict) -> None:
    try:
        from observability.metrics_sink import record
        record(event, detail)
    except Exception:
        pass


def _timing_metric(
    request: FetchRequest,
    *,
    stage: str,
    started_at: float,
    status: str,
    fetcher: str | None = None,
    extractor: str | None = None,
    error_code: str | None = None,
    code: int | None = None,
    count: int | None = None,
) -> None:
    """Persist one privacy-safe DeepResearch fetch substage timing sample."""

    if not request.run_id or request.run_id == "default":
        return
    detail = {
        "run_id": request.run_id,
        "stage": stage,
        "status": status,
        "duration_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
    }
    if fetcher:
        detail["fetcher"] = fetcher
    if extractor:
        detail["extractor"] = extractor
    if error_code:
        detail["error_code"] = error_code
    if code is not None:
        detail["code"] = int(code)
    if count is not None:
        detail["count"] = max(0, int(count))
    _metric("deepresearch_fetch_attempt_timing", detail)


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
        render_call=None,
        playwright_renderer=None,
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
        self.render_call = render_call
        self.playwright_renderer = playwright_renderer
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
        playwright_renderer = None
        if bool(getattr(sg, "playwright_renderer_enabled", True)):
            from deskpet.playwright_bundle import resolve_browser_bundle
            from .playwright_renderer import PlaywrightRendererPool

            playwright_renderer = PlaywrightRendererPool(
                browser_root_resolver=lambda: resolve_browser_bundle().owner,
                maximum_concurrency=max(1, min(2, int(sg.max_concurrency))),
            )
        return cls(
            respect_robots=bool(web.get("respect_robots_txt", True)),
            request_interval_ms=int(web.get("request_interval_ms", 250)),
            cache_size=sg.cache_size,
            cache_ttl_s=sg.cache_ttl_s,
            allow_jina=bool(research.get("jina_reader", False)),
            playwright_renderer=playwright_renderer,
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
        from .playwright_renderer import FetchRenderBudget, PlaywrightRenderError

        render_budget = request.render_budget or FetchRenderBudget()
        raw: dict | None = None
        static_error: FetchExtractError | None = None
        if await render_budget.claim("static"):
            try:
                raw = await self.fetch_raw(request)
            except FetchExtractError as exc:
                if request.render_policy == "never" or exc.code in {
                    "invalid_url",
                    "blocked_by_robots",
                    "unsupported_content_type",
                    "response_too_large",
                }:
                    raise
                static_error = exc
        else:
            static_error = FetchExtractError("static_budget_exhausted")

        raw = raw or {
            "content": "",
            "content_type": "text/html",
            "fetcher": "static-failed",
            "url_final": url,
        }
        html = str(raw.get("content") or "")
        content_type = str(raw.get("content_type") or "")
        extract_started = time.perf_counter()
        title, text, published, extractor = self._extract(html)
        fetcher = str(raw.get("fetcher") or "httpx")
        _timing_metric(
            request,
            stage="extract",
            started_at=extract_started,
            status="succeeded",
            fetcher=fetcher,
            extractor=extractor,
            count=len(text),
        )

        should_render = request.render_policy == "always" or (
            request.render_policy == "auto"
            and (static_error is not None or looks_like_app_shell(html, text))
        )
        invalid_render = False
        playwright_valid = False
        if (
            should_render
            and self.playwright_renderer is not None
            and await render_budget.claim("playwright")
        ):
            deadline = request.deadline_monotonic
            if deadline is None and request.request_budget is not None:
                deadline = request.request_budget.deadline
            playwright_started = time.perf_counter()
            try:
                outcome = await self.playwright_renderer.render(
                    url,
                    timeout=min(request.timeout, 10.0),
                    deadline=deadline,
                    cancel_event=request.cancel_event,
                )
            except asyncio.CancelledError:
                _timing_metric(
                    request,
                    stage="playwright",
                    started_at=playwright_started,
                    status="cancelled",
                    fetcher="playwright",
                )
                raise
            except PlaywrightRenderError as exc:
                _timing_metric(
                    request,
                    stage="playwright",
                    started_at=playwright_started,
                    status="failed",
                    fetcher="playwright",
                    error_code=exc.code,
                )
                _metric("fetch_extract_fallback", {
                    "stage": "playwright", "fetcher": "playwright",
                    "ok": False, "error_code": exc.code,
                })
            else:
                rendered = outcome.html
                if len(rendered.encode("utf-8", errors="replace")) <= _MAX_RESPONSE_BYTES:
                    r_title, r_text, r_published, r_extractor = self._extract(rendered)
                    r_flags = set(quality_flags(
                        text=r_text, html=rendered, content_type="text/html"
                    ))
                    invalid_render = bool(
                        r_flags
                        & {"captcha", "login_wall", "security_verification", "app_shell"}
                    )
                    playwright_valid = bool(r_text.strip()) and not invalid_render
                    if static_error is not None or len(r_text) > len(text) or invalid_render:
                        html, title, text = rendered, r_title, r_text
                        published, extractor, fetcher = r_published, r_extractor, "playwright"
                        raw["url_final"] = outcome.final_url
                        raw["content_type"] = "text/html"
                        static_error = None
                    _metric("fetch_extract_fallback", {
                        "stage": "playwright", "fetcher": "playwright",
                        "ok": not invalid_render,
                        "error_code": next(iter(sorted(r_flags & {"captcha", "login_wall", "security_verification", "app_shell"})), None),
                    })
                    _timing_metric(
                        request,
                        stage="playwright",
                        started_at=playwright_started,
                        status="succeeded" if not invalid_render else "rejected",
                        fetcher="playwright",
                        extractor=r_extractor,
                        error_code=next(iter(sorted(r_flags & {"captcha", "login_wall", "security_verification", "app_shell"})), None),
                        count=len(r_text),
                    )
                else:
                    _metric("fetch_extract_fallback", {
                        "stage": "playwright", "fetcher": "playwright",
                        "ok": False, "error_code": "response_too_large",
                    })
                    _timing_metric(
                        request,
                        stage="playwright",
                        started_at=playwright_started,
                        status="rejected",
                        fetcher="playwright",
                        error_code="response_too_large",
                    )

        need_edge = should_render and not invalid_render and (
            static_error is not None
            or looks_like_app_shell(html, text)
            or len(text) < 300
            or (request.render_policy == "always" and not playwright_valid)
        )
        if should_render and self.playwright_renderer is None:
            need_edge = True
        if need_edge and await render_budget.claim("edge") and (
            request.request_budget is None or await request.request_budget.claim_cdp()
        ):
            render_call = self.render_call
            if render_call is None:
                from deskpet.tools.research_cdp_edge import cdp_edge_render

                render_call = cdp_edge_render
            edge_started = time.perf_counter()
            try:
                try:
                    rendered = await render_call(url, timeout=min(request.timeout, 10.0))
                except TypeError:
                    # Legacy injected renderers accepted only the URL.
                    rendered = await render_call(url)
            except asyncio.CancelledError:
                _timing_metric(
                    request,
                    stage="cdp",
                    started_at=edge_started,
                    status="cancelled",
                    fetcher="cdp-edge",
                )
                raise
            except Exception as exc:
                _timing_metric(
                    request,
                    stage="cdp",
                    started_at=edge_started,
                    status="failed",
                    fetcher="cdp-edge",
                    error_code=type(exc).__name__,
                )
                raise
            if rendered:
                if len(rendered.encode("utf-8", errors="replace")) <= _MAX_RESPONSE_BYTES:
                    r_title, r_text, r_published, r_extractor = self._extract(rendered)
                    if len(r_text) > len(text):
                        html, title, text, published, extractor, fetcher = rendered, r_title, r_text, r_published, r_extractor, "cdp-edge"
                        static_error = None
                        _metric("fetch_extract_fallback", {"stage": "cdp", "fetcher": "cdp-edge", "ok": True})
                    _timing_metric(
                        request,
                        stage="cdp",
                        started_at=edge_started,
                        status="succeeded",
                        fetcher="cdp-edge",
                        extractor=r_extractor,
                        count=len(r_text),
                    )
                else:
                    _metric("fetch_extract_fallback", {"stage": "cdp", "fetcher": "cdp-edge", "ok": False, "error_code": "response_too_large"})
                    _timing_metric(
                        request,
                        stage="cdp",
                        started_at=edge_started,
                        status="rejected",
                        fetcher="cdp-edge",
                        error_code="response_too_large",
                    )
            else:
                _timing_metric(
                    request,
                    stage="cdp",
                    started_at=edge_started,
                    status="empty",
                    fetcher="cdp-edge",
                )

        if static_error is not None and not text.strip():
            raise FetchExtractError("render_fallback_failed") from static_error

        if len(text) < 300 and request.allow_jina and self.allow_jina:
            jina_started = time.perf_counter()
            jina = await self._jina(url, min(request.timeout, 8.0))
            jina_valid = bool(
                jina is not None
                and len(jina[1].encode("utf-8", errors="replace")) <= _MAX_RESPONSE_BYTES
                and len(jina[1]) > len(text)
            )
            _timing_metric(
                request,
                stage="jina",
                started_at=jina_started,
                status="succeeded" if jina_valid else "empty",
                fetcher="jina",
                extractor="jina" if jina_valid else None,
                count=len(jina[1]) if jina_valid and jina is not None else 0,
            )
            if (
                jina_valid
            ):
                assert jina is not None
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
        robots_started = time.perf_counter()
        allowed = await self._allowed_by_robots(request.url, request.timeout)
        _timing_metric(
            request,
            stage="robots",
            started_at=robots_started,
            status="succeeded" if allowed else "blocked",
        )
        if not allowed:
            raise FetchExtractError("blocked_by_robots", retriable=False)
        await self._throttle(parsed.hostname.lower())

        # Only the blocking raw Scrapling transport runs in a worker; fallback
        # policy remains in this async service.
        scrapling_started = time.perf_counter()
        if request.prefer_httpx:
            scraped = {"ok": False, "error": "httpx_preferred"}
        else:
            try:
                scraped = await asyncio.to_thread(
                    self.transport.fetch, request.url, timeout=request.timeout
                )
            except asyncio.CancelledError:
                _timing_metric(
                    request,
                    stage="scrapling",
                    started_at=scrapling_started,
                    status="cancelled",
                    fetcher="scrapling",
                )
                raise
        _timing_metric(
            request,
            stage="scrapling",
            started_at=scrapling_started,
            status=(
                "skipped"
                if request.prefer_httpx
                else ("succeeded" if scraped.get("ok") else "failed")
            ),
            fetcher=str(scraped.get("fetcher") or "scrapling"),
            error_code=(str(scraped.get("error")) if scraped.get("error") else None),
            code=(int(scraped.get("status")) if scraped.get("status") else None),
            count=len(str(scraped.get("html") or "")),
        )
        if scraped.get("ok"):
            status = int(scraped.get("status") or 0)
            html = str(scraped.get("html") or "")
            scraped_content_type = str(scraped.get("content_type") or "text/html")
            if not _supported_content_type(scraped_content_type):
                raise FetchExtractError("unsupported_content_type", retriable=False, status=status)
            if len(html.encode("utf-8", errors="replace")) > _MAX_RESPONSE_BYTES:
                raise FetchExtractError("response_too_large", retriable=False, status=status)
            if 200 <= status < 300 and html and "captcha" not in quality_flags(text="", html=html, content_type="text/html"):
                return {
                    "status": status, "url_final": scraped.get("url_final") or request.url,
                    "fetcher": scraped.get("fetcher") or "scrapling",
                    "content_type": scraped.get("content_type") or "text/html; charset=utf-8",
                    "content": html[: request.max_chars],
                }
        _metric("fetch_extract_fallback", {"stage": "httpx", "fetcher": "httpx"})
        httpx_started = time.perf_counter()
        try:
            response, raw_content = await self._bounded_httpx_get(
                request.url, timeout=request.timeout
            )
        except asyncio.CancelledError:
            _timing_metric(
                request,
                stage="httpx",
                started_at=httpx_started,
                status="cancelled",
                fetcher="httpx",
            )
            raise
        except httpx.TimeoutException as exc:
            _timing_metric(
                request,
                stage="httpx",
                started_at=httpx_started,
                status="failed",
                fetcher="httpx",
                error_code="timeout",
            )
            raise FetchExtractError("timeout") from exc
        except httpx.HTTPError as exc:
            _timing_metric(
                request,
                stage="httpx",
                started_at=httpx_started,
                status="failed",
                fetcher="httpx",
                error_code=type(exc).__name__,
            )
            # The v6 page-read policy owns retries.  One logical attempt may
            # call each transport at most once, so a transport failure must
            # surface to the render/route fallback instead of opening a fresh
            # HTTPX connection and issuing a hidden second upstream request.
            raise FetchExtractError("http_error") from exc
        status_code = int(getattr(response, "status_code", 200) or 200)
        headers = getattr(response, "headers", {}) or {}
        content_type = headers.get("content-type", "text/html")
        if status_code in {403, 429}:
            _timing_metric(
                request, stage="httpx", started_at=httpx_started,
                status="failed", fetcher="httpx", error_code="blocked",
                code=status_code, count=len(raw_content),
            )
            raise FetchExtractError("blocked", status=status_code)
        if status_code >= 500:
            _timing_metric(
                request, stage="httpx", started_at=httpx_started,
                status="failed", fetcher="httpx", error_code="upstream_error",
                code=status_code, count=len(raw_content),
            )
            raise FetchExtractError("upstream_error", status=status_code)
        if status_code >= 400:
            _timing_metric(
                request, stage="httpx", started_at=httpx_started,
                status="failed", fetcher="httpx", error_code="http_status",
                code=status_code, count=len(raw_content),
            )
            raise FetchExtractError("http_status", retriable=False, status=status_code)
        if not _supported_content_type(content_type):
            _timing_metric(
                request, stage="httpx", started_at=httpx_started,
                status="rejected", fetcher="httpx",
                error_code="unsupported_content_type", code=status_code,
                count=len(raw_content),
            )
            raise FetchExtractError("unsupported_content_type", retriable=False, status=status_code)
        if not raw_content and not isinstance(self.client, httpx.AsyncClient):
            # Compatibility with lightweight injected clients used by the
            # legacy v1 port and tests; real httpx responses expose content.
            response_text = str(getattr(response, "text", "") or "")
            raw_content = response_text.encode("utf-8")
        if len(raw_content) > _MAX_RESPONSE_BYTES:
            _timing_metric(
                request, stage="httpx", started_at=httpx_started,
                status="rejected", fetcher="httpx",
                error_code="response_too_large", code=status_code,
                count=len(raw_content),
            )
            raise FetchExtractError("response_too_large", retriable=False, status=status_code)
        encoding = getattr(response, "encoding", None) or "utf-8"
        try:
            content = raw_content.decode(encoding, errors="replace")[: request.max_chars]
        except LookupError:
            content = raw_content.decode("utf-8", errors="replace")[: request.max_chars]
        _timing_metric(
            request,
            stage="httpx",
            started_at=httpx_started,
            status="succeeded",
            fetcher="httpx",
            code=status_code,
            count=len(raw_content),
        )
        return {
            "status": status_code, "url_final": str(getattr(response, "url", request.url)),
            "fetcher": "httpx", "content_type": content_type, "content": content,
        }

    async def _bounded_httpx_get(
        self,
        url: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
    ):
        """Stream real httpx responses and stop once the hard cap is crossed."""

        if isinstance(self.client, httpx.AsyncClient):
            async with self.client.stream(
                "GET", url, timeout=timeout, headers=headers
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > _MAX_RESPONSE_BYTES:
                            raise FetchExtractError(
                                "response_too_large",
                                retriable=False,
                                status=response.status_code,
                            )
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        raise FetchExtractError(
                            "response_too_large",
                            retriable=False,
                            status=response.status_code,
                        )
                    chunks.append(chunk)
                return response, b"".join(chunks)
        if headers is None:
            response = await self.client.get(url, timeout=timeout)
        else:
            response = await self.client.get(url, timeout=timeout, headers=headers)
        return response, bytes(getattr(response, "content", b""))

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
            response, raw_content = await self._bounded_httpx_get(
                "https://r.jina.ai/" + url,
                timeout=timeout,
                headers={"Accept": "text/plain"},
            )
            response.raise_for_status()
        except Exception:
            return None
        if not raw_content and not isinstance(self.client, httpx.AsyncClient):
            raw_content = str(getattr(response, "text", "") or "").encode("utf-8")
        encoding = getattr(response, "encoding", None) or "utf-8"
        try:
            body = raw_content.decode(encoding, errors="replace")
        except LookupError:
            body = raw_content.decode("utf-8", errors="replace")
        title_match = re.search(r"^Title:\s*(.+)$", body, re.M)
        marker = body.find("Markdown Content:")
        text = body[marker + len("Markdown Content:"):].strip() if marker >= 0 else body.strip()
        return ((title_match.group(1).strip() if title_match else ""), text)

    async def close(self) -> None:
        if self._closed: return
        self._closed = True
        await self.cache.clear()
        if self.playwright_renderer is not None:
            await self.playwright_renderer.close()
        if self._owns_client: await self.client.aclose()

    shutdown = close
