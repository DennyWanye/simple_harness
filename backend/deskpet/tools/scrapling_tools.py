# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Scrapling-backed web tools.

These tools cover dynamic/modern pages where the plain ``web_fetch``
path often returns a blocked page, huge app shell, or no useful text.
Imports stay inside handlers so a missing optional dependency never
breaks backend startup.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from .registry import registry

log = logging.getLogger(__name__)

_MAX_SCRAPLING_CONTENT_CHARS = 120_000
_INVESTING_XAU_USD = "https://www.investing.com/currencies/xau-usd?output=1"
_INVESTING_USD_CNY = "https://www.investing.com/currencies/usd-cny?output=1"
_TROY_OUNCE_GRAMS = 31.1034768


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _scrapling_get_html(url: str, *, timeout: float) -> dict[str, Any]:
    """Legacy compatibility facade over the raw Scrapling transport."""
    from deskpet.retrieval.transports.scrapling import ScraplingTransport
    return ScraplingTransport().fetch(url, timeout=timeout)


def _parse_number(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9.,-]", "", raw).replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_investing_last(html: str) -> float | None:
    patterns = (
        r'data-test=["\']instrument-price-last["\'][^>]*>\s*([^<]+)',
        r'instrument-price-last[^>]*>\s*([^<]+)',
        r'"last"\s*:\s*"?([0-9][0-9,]*(?:\.[0-9]+)?)',
        r'"lastPrice"\s*:\s*"?([0-9][0-9,]*(?:\.[0-9]+)?)',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        value = _parse_number(match.group(1) if match else None)
        if value is not None:
            return value
    return None


_SCRAPLING_FETCH_SCHEMA: dict[str, Any] = {
    "name": "scrapling_fetch",
    "description": (
        "Use Scrapling to fetch a modern/dynamic web page and return raw HTML/text. "
        "Prefer this when normal web_fetch returns an app shell, 403, or empty content. "
        "For current gold price / XAU/USD, call gold_price_lookup first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."},
            "timeout": {"type": "integer", "default": 20},
            "max_chars": {
                "type": "integer",
                "description": "Maximum returned content characters. Default 120000.",
                "default": _MAX_SCRAPLING_CONTENT_CHARS,
            },
        },
        "required": ["url"],
    },
}


async def _handle_scrapling_fetch(args: dict[str, Any], task_id: str) -> str:
    url = str(args.get("url", "") or "").strip()
    if not url.startswith(("http://", "https://")):
        return _json({"ok": False, "error": "url must be absolute http(s)"})
    timeout = float(args.get("timeout", 20) or 20)
    max_chars = int(args.get("max_chars", _MAX_SCRAPLING_CONTENT_CHARS) or _MAX_SCRAPLING_CONTENT_CHARS)
    from deskpet.retrieval.contracts import FetchRequest
    from deskpet.retrieval.fetch_extract import FetchExtractError
    from deskpet.retrieval.runtime import get_default_gateway
    service = get_default_gateway().fetch_service
    try:
        fetched = await service.fetch_raw(FetchRequest(
            url=url, timeout=timeout, extract=False, include_html=True,
            max_chars=_MAX_SCRAPLING_CONTENT_CHARS,
        ))
    except FetchExtractError as exc:
        return _json({"ok": False, "error": exc.code, "retriable": exc.retriable})
    html = str(fetched.get("content") or "")
    truncated = len(html) > max_chars
    if truncated:
        html = html[:max_chars]
    return _json(
        {
            "ok": True,
            "status": fetched.get("status"),
            "url_final": fetched.get("url_final"),
            "fetcher": fetched.get("fetcher"),
            "content": html,
            "truncated": truncated,
        }
    )


_GOLD_PRICE_SCHEMA: dict[str, Any] = {
    "name": "gold_price_lookup",
    "description": (
        "Lookup the latest spot gold price. Use this FIRST when the user asks "
        "最新金价/今日黄金价格/现货黄金/XAU/USD/黄金多少钱一克. Returns USD/oz, "
        "USD/CNY, and estimated CNY/gram with source URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "currency": {
                "type": "string",
                "enum": ["USD", "CNY", "both"],
                "default": "both",
            },
            "timeout": {"type": "integer", "default": 20},
        },
        "required": [],
    },
}


async def _handle_gold_price_lookup(args: dict[str, Any], task_id: str) -> str:
    timeout = float(args.get("timeout", 20) or 20)
    from deskpet.retrieval.contracts import FetchRequest
    from deskpet.retrieval.runtime import get_default_gateway
    service = get_default_gateway().fetch_service
    try:
        xau_page = await service.fetch_raw(FetchRequest(_INVESTING_XAU_USD, timeout=timeout, extract=False, include_html=True))
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": str(exc), "source": _INVESTING_XAU_USD})
    xau_usd = _extract_investing_last(str(xau_page.get("content") or ""))
    if xau_usd is None:
        return _json(
            {
                "ok": False,
                "error": "could not extract XAU/USD last price",
                "status": xau_page.get("status"),
                "fetcher": xau_page.get("fetcher"),
                "source": _INVESTING_XAU_USD,
            }
        )

    usd_cny: float | None = None
    try:
        cny_page = await service.fetch_raw(FetchRequest(_INVESTING_USD_CNY, timeout=timeout, extract=False, include_html=True))
        usd_cny = _extract_investing_last(str(cny_page.get("content") or ""))
    except Exception as exc:  # noqa: BLE001
        cny_page = {"error": str(exc)}

    out: dict[str, Any] = {
        "ok": True,
        "source": "Investing.com",
        "source_urls": {
            "xau_usd": _INVESTING_XAU_USD,
            "usd_cny": _INVESTING_USD_CNY,
        },
        "fetcher": xau_page.get("fetcher"),
        "observed_at_unix": time.time(),
        "xau_usd_per_troy_ounce": round(xau_usd, 2),
        "note": "Realtime market data can be delayed by the source; verify before trading.",
    }
    if usd_cny is not None:
        out["usd_cny"] = round(usd_cny, 4)
        out["cny_per_gram_estimate"] = round(xau_usd * usd_cny / _TROY_OUNCE_GRAMS, 2)
    else:
        out["usd_cny_error"] = cny_page.get("error", "could not extract USD/CNY")
    return _json(out)


registry.register(
    "scrapling_fetch",
    "web",
    _SCRAPLING_FETCH_SCHEMA,
    _handle_scrapling_fetch,
    permission_category="network",
    timeout_seconds=60.0,
)

registry.register(
    "gold_price_lookup",
    "web",
    _GOLD_PRICE_SCHEMA,
    _handle_gold_price_lookup,
    permission_category="network",
    timeout_seconds=60.0,
)
