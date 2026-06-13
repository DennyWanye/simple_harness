# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deep-research V8 — unified search_provider (region-aware DDG)."""
from __future__ import annotations

from deskpet.tools import search_provider as sp


# --- region inference (the core Chinese-coverage fix) ---

def test_region_chinese_query_uses_cn_zh():
    assert sp.region_for_query("中国新能源汽车现状") == "cn-zh"


def test_region_english_query_uses_us_en():
    assert sp.region_for_query("state of quantum computing 2026") == "us-en"


def test_region_mixed_cjk_counts_as_chinese():
    assert sp.region_for_query("AI 大模型 benchmark") == "cn-zh"


def test_region_empty_defaults_us_en():
    assert sp.region_for_query("") == "us-en"


# --- pure HTML parser (network-free) ---

_SAMPLE_HTML = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">Title A</a>
  <a class="result__snippet" href="x">Snippet A here</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.org/b">Title B</a>
  <a class="result__snippet" href="x">Snippet B</a>
</div>
"""


def test_parse_unwraps_uddg_redirect():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=5)
    urls = [r["url"] for r in out]
    assert "https://example.com/a" in urls  # uddg unwrapped
    assert "https://example.org/b" in urls


def test_parse_respects_max_results():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=1)
    assert len(out) == 1


def test_parse_extracts_title_and_snippet():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=5)
    a = next(r for r in out if r["url"].endswith("/a"))
    assert a["title"] == "Title A"
    assert "Snippet A" in a["snippet"]


def test_parse_regex_fallback_matches_selectolax():
    # Force the regex path directly — should find both results too.
    out = sp._parse_ddg_regex(_SAMPLE_HTML, max_results=5)
    assert len(out) == 2
    assert out[0]["title"] == "Title A"


def test_parse_empty_html_returns_empty():
    assert sp.parse_ddg_html("<html></html>", max_results=5) == []


# --- sync search guards (no network) ---

def test_search_empty_query_no_network():
    r = sp.search("   ")
    assert r["count"] == 0 and r["results"] == [] and "error" in r


def test_search_caps_max_results_value(monkeypatch):
    captured = {}

    class _FakeResp:
        text = _SAMPLE_HTML

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None):
            captured["kl"] = data.get("kl")
            return _FakeResp()

    monkeypatch.setattr(sp.httpx, "Client", _FakeClient)
    r = sp.search("中文测试查询", max_results=99)
    # region inferred Chinese; results parsed from fake html
    assert captured["kl"] == "cn-zh"
    assert r["count"] == 2
