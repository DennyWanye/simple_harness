# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unified web-search provider — DuckDuckGo HTML SERP (no API key).

This is the **single** search implementation shared by:

* ``web_tools`` chat-mode ``web_search`` tool (quick lookups),
* ``code_tools.web_search_tool`` (Code-mode agent),
* ``research_tools.default_search`` (deep-research pipeline).

Before this module those three carried *three* near-identical DuckDuckGo
scrapers, each with the same bug: a hardcoded ``kl="us-en"`` region that
made Chinese queries return mostly English/irrelevant results. We fix
that here once: the region is **inferred from the query language** (CJK →
``cn-zh``, else ``us-en``) and overridable.

Design constraints (per project decision 2026-06-13):
* **No external/paid search engines.** DuckDuckGo's free HTML endpoint
  only. No Tavily/Exa/Bing/SearXNG. If DDG fails we return ``[]`` + an
  error string; callers degrade, they do not silently swap engines.
* Pure-parser path is unit-testable without network (``parse_ddg_html``).
* Synchronous (``search``) AND async (``search_async``) entry points so
  both the blocking Code tool and the async research pipeline reuse it.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from html import unescape
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

# DuckDuckGo HTML ("lite", no-JS) SERP. Stable across years; returns
# clean <a class="result__a"> / <a class="result__snippet"> blocks.
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 12.0
_MAX_RESULTS_CAP = 10

_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_CJK_RE = re.compile(r"[㐀-鿿぀-ヿ가-힯]")


def region_for_query(query: str) -> str:
    """Infer the DuckDuckGo ``kl`` region from the query language.

    CJK (Chinese/Japanese/Korean) chars → ``cn-zh`` (中文区, far better
    Chinese coverage than the old hardcoded us-en). Otherwise ``us-en``.
    This is the single most impactful fix for Chinese deep-research.
    """
    return "cn-zh" if _CJK_RE.search(query or "") else "us-en"


def _strip(html: str) -> str:
    return unescape(_TAG_STRIP_RE.sub("", html)).strip()


def _clean_url(url: str) -> str:
    """DDG wraps targets in ``/l/?uddg=ENCODED``; unwrap to the real URL."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url or url.startswith("/l/") or "uddg=" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            uddg = qs.get("uddg", [""])[0]
            if uddg:
                return urllib.parse.unquote(uddg)
        except Exception:  # noqa: BLE001
            pass
    return url


def parse_ddg_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    """Pure parser → ``[{url, title, snippet}]`` (network-free, testable).

    Prefers selectolax (robust to attribute order); falls back to regex.
    """
    out: list[dict[str, str]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return _parse_ddg_regex(html, max_results=max_results)

    tree = HTMLParser(html)
    for node in tree.css("div.result"):
        if len(out) >= max_results:
            break
        a = node.css_first("a.result__a")
        s = node.css_first(".result__snippet")
        if a is None:
            continue
        url = _clean_url(a.attributes.get("href", "") or "")
        title = (a.text() or "").strip()
        snippet = (s.text() if s else "").strip()
        if not url or not title:
            continue
        out.append({"url": url, "title": title, "snippet": snippet})
    if out:
        return out
    # selectolax present but layout changed → regex safety net.
    return _parse_ddg_regex(html, max_results=max_results)


def _parse_ddg_regex(html: str, *, max_results: int) -> list[dict[str, str]]:
    block = re.compile(
        r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'(?:.*?<a class="result__snippet"[^>]*>(.*?)</a>)?',
        re.DOTALL,
    )
    out: list[dict[str, str]] = []
    for m in block.finditer(html):
        if len(out) >= max_results:
            break
        url = _clean_url(m.group(1))
        title = _strip(m.group(2) or "")
        snippet = _strip(m.group(3) or "")
        if not url or not title:
            continue
        out.append({"url": url, "title": title, "snippet": snippet})
    return out


def _normalize_max(max_results: int) -> int:
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, _MAX_RESULTS_CAP))


def search(
    query: str,
    *,
    max_results: int = 5,
    region: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Synchronous DDG search → ``{query, count, results, region, [error]}``.

    Never raises: network failure → ``results: []`` + ``error``.
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "count": 0, "results": [], "error": "empty query"}
    n = _normalize_max(max_results)
    kl = region or region_for_query(q)
    try:
        with httpx.Client(
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = client.post(_DDG_HTML_URL, data={"q": q, "kl": kl})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("search failed for %r: %s", q, exc)
        return {"query": q, "count": 0, "results": [], "region": kl, "error": str(exc)}
    results = parse_ddg_html(resp.text, max_results=n)
    return {"query": q, "count": len(results), "results": results, "region": kl}


async def search_async(
    query: str,
    *,
    max_results: int = 5,
    region: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, str]]:
    """Async DDG search → ``[{url, title, snippet}]`` (research pipeline
    shape). Failure → ``[]`` (caller decides recovery)."""
    q = (query or "").strip()
    if not q:
        return []
    n = _normalize_max(max_results)
    kl = region or region_for_query(q)
    owns = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=timeout,
        follow_redirects=True,
    )
    try:
        try:
            resp = await cli.post(_DDG_HTML_URL, data={"q": q, "kl": kl})
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.debug("async search failed for %r: %s", q, exc)
            return []
        return parse_ddg_html(resp.text, max_results=n)
    finally:
        if owns:
            await cli.aclose()


__all__ = ["search", "search_async", "parse_ddg_html", "region_for_query"]
