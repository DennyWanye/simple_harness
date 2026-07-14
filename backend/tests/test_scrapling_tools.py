# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json

from deskpet.tools import scrapling_tools
from deskpet.tools.registry import registry


def test_gold_price_lookup_extracts_xau_and_cny(monkeypatch):
    def fake_fetch(url: str, *, timeout: float):
        if "xau-usd" in url:
            html = '<span data-test="instrument-price-last">4,050.17</span>'
        else:
            html = '<span data-test="instrument-price-last">6.8003</span>'
        return {
            "ok": True,
            "status": 200,
            "html": html,
            "url_final": url,
            "fetcher": "scrapling",
        }

    monkeypatch.setattr(scrapling_tools, "_scrapling_get_html", fake_fetch)

    out = json.loads(registry.dispatch("gold_price_lookup", {}))

    assert out["ok"] is True
    assert out["xau_usd_per_troy_ounce"] == 4050.17
    assert out["usd_cny"] == 6.8003
    assert out["cny_per_gram_estimate"] == 885.51
    assert out["fetcher"] == "scrapling"


def test_gold_price_lookup_reports_extract_failure(monkeypatch):
    monkeypatch.setattr(
        scrapling_tools,
        "_scrapling_get_html",
        lambda url, *, timeout: {
            "ok": True,
            "status": 200,
            "html": "<html>no quote here</html>",
            "url_final": url,
            "fetcher": "scrapling",
        },
    )

    out = json.loads(registry.dispatch("gold_price_lookup", {}))

    assert out["ok"] is False
    assert "extract XAU/USD" in out["error"]


def test_scrapling_fetch_truncates_large_html(monkeypatch):
    monkeypatch.setattr(
        scrapling_tools,
        "_scrapling_get_html",
        lambda url, *, timeout: {
            "ok": True,
            "status": 200,
            "html": "abcdef",
            "url_final": url,
            "fetcher": "scrapling",
        },
    )

    out = json.loads(
        registry.dispatch(
            "scrapling_fetch",
            {"url": "https://example.com/page", "max_chars": 3},
        )
    )

    assert out["ok"] is True
    assert out["content"] == "abc"
    assert out["truncated"] is True
