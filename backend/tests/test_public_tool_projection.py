from __future__ import annotations

import json

from deskpet.tools.public_projection import (
    project_public_tool_arguments,
    project_public_tool_calls,
    project_public_tool_result,
)


def test_web_tool_arguments_never_expose_url_or_raw_query() -> None:
    fetch = project_public_tool_arguments(
        "web_fetch", {"url": "https://secret.example/private?q=raw", "max_bytes": 42}
    )
    search = project_public_tool_arguments(
        "web_search", {"query": "secret raw query", "max_results": 5}
    )

    assert fetch == {"target_kind": "web_page"}
    assert search == {"search_scope": "web"}
    assert "secret" not in json.dumps([fetch, search])


def test_all_public_web_reader_arguments_hide_urls_and_keywords() -> None:
    for tool_name in ("web_crawl", "web_read_sitemap", "scrapling_fetch"):
        projected = project_public_tool_arguments(tool_name, {
            "url": "https://secret.example/private?q=raw",
            "start_url": "https://secret.example/start",
            "keywords": ["secret query"],
        })
        assert projected == {"target_kind": "web_page"}
        assert "secret" not in json.dumps(projected)


def test_non_web_tool_arguments_keep_existing_public_shape_without_aliasing() -> None:
    raw = {"path": "notes.md", "line": 4}
    projected = project_public_tool_arguments("read_file", raw)

    assert projected == raw
    projected["line"] = 5
    assert raw["line"] == 4


def test_persisted_tool_call_history_uses_same_public_projection() -> None:
    raw = [{
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "web_fetch",
            "arguments": json.dumps({"url": "https://secret.example/raw"}),
        },
    }]

    projected = project_public_tool_calls(raw)

    assert json.loads(projected[0]["function"]["arguments"]) == {
        "target_kind": "web_page"
    }
    assert "secret.example" not in json.dumps(projected)
    assert "secret.example" in raw[0]["function"]["arguments"]


def test_web_tool_results_expose_only_bounded_semantic_summary() -> None:
    raw = {
        "source_url": "https://secret.example/raw",
        "pages": [{"url": "https://secret.example/page"}],
        "text": "secret query and fetched body",
    }

    projected = project_public_tool_result("web_crawl", raw)

    assert projected == {
        "result_kind": "web_content",
        "status": "completed",
        "item_count": 1,
    }
    assert "secret" not in json.dumps(projected)


def test_direct_web_lookup_results_hide_source_urls() -> None:
    raw = {
        "ok": True,
        "price": 2365.4,
        "source_urls": {
            "xau_usd": "https://www.investing.com/currencies/xau-usd?output=1",
            "usd_cny": "https://www.investing.com/currencies/usd-cny?output=1",
        },
    }

    projected = project_public_tool_result("gold_price_lookup", raw)

    assert projected == {
        "result_kind": "web_content",
        "status": "completed",
        "item_count": 0,
    }
    assert "investing.com" not in json.dumps(projected)
