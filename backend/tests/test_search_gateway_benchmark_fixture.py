from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "search_gateway_queries.json"


def test_search_gateway_query_fixture_is_versioned_and_covers_required_categories() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["dataset_version"]

    queries = payload["queries"]
    assert len(queries) >= 10
    assert len({item["id"] for item in queries}) == len(queries)
    assert {item["language"] for item in queries} >= {"zh-CN", "en"}
    assert {item["category"] for item in queries} >= {
        "fact",
        "latest",
        "official_docs",
        "academic",
        "policy",
        "financial_report",
        "javascript_page",
        "duplicate_reprints",
        "conflicting_sources",
    }
    for item in queries:
        assert item["query"].strip()
        assert item["expected_keywords"]
        assert item["expected_domains"]
        assert all("/" not in domain for domain in item["expected_domains"])
