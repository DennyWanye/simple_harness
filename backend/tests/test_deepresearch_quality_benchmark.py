from __future__ import annotations

import json
import sqlite3

from scripts.acceptance.deepresearch_quality_benchmark import _run_record


def _all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).casefold()
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def _assert_safe_benchmark_record(result, raw_query):
    forbidden = {"query", "url", "text", "body", "content", "secret", "token", "api_key"}
    assert forbidden.isdisjoint(set(_all_keys(result)))
    serialized = json.dumps(result, ensure_ascii=False)
    assert raw_query not in serialized
    assert "http://" not in serialized
    assert "https://" not in serialized


def test_benchmark_reads_v3_publish_coverage_field_names(tmp_path):
    db = sqlite3.connect(tmp_path / "workflow.db")
    db.execute(
        "CREATE TABLE workflow_runs (run_id TEXT, status TEXT, workflow_version TEXT, "
        "started_at REAL, ended_at REAL)"
    )
    db.execute(
        "CREATE TABLE workflow_events (run_id TEXT, event_type TEXT, seq INTEGER, payload_json TEXT)"
    )
    query = "Official browser support status for WebGPU"
    coverage = {
        "support_rate": 0.75,
        "published_factual_count": 9,
        "citation_count": 9,
        "independent_domain_count": 5,
        "body_bytes": 1800,
        "provider_attempt_count": 2,
        "provider_probe_count": 1,
    }
    report = {
        "topic": query,
        "status": "completed",
        "citations": [{"citation_id": value} for value in range(1, 10)],
        "coverage": coverage,
    }
    envelope = {"payload": {"report": report}}
    db.execute(
        "INSERT INTO workflow_runs VALUES (?,?,?,?,?)",
        ("run-1", "completed", "v3", 10.0, 12.0),
    )
    db.execute(
        "INSERT INTO workflow_events VALUES (?,?,?,?)",
        ("run-1", "workflow.report", 1, json.dumps(envelope)),
    )
    db.commit()

    result = _run_record(
        db,
        "run-1",
        {"category": "web_standard_dynamic", "query": query},
    )

    assert result["published_factual_claims"] == 9
    assert result["independent_domains"] == 5
    assert result["provider_attempt_count"] == 2
    assert result["successful"] is True
    assert "query" not in result
    assert result["query_fingerprint"]
    _assert_safe_benchmark_record(result, query)


def test_benchmark_accepts_ui_deepresearch_directive_around_fixed_query(tmp_path):
    db = sqlite3.connect(tmp_path / "workflow.db")
    db.execute(
        "CREATE TABLE workflow_runs (run_id TEXT, status TEXT, workflow_version TEXT, "
        "started_at REAL, ended_at REAL)"
    )
    db.execute(
        "CREATE TABLE workflow_events (run_id TEXT, event_type TEXT, seq INTEGER, payload_json TEXT)"
    )
    query = "Official browser support status for WebGPU"
    report = {
        "topic": f"请对 {query} 做深度调研，输出带引用报告",
        "status": "completed",
        "citations": [{"citation_id": value} for value in range(1, 9)],
        "coverage": {
            "support_rate": 0.75,
            "published_factual_count": 8,
            "independent_domain_count": 4,
            "body_bytes": 1500,
            "provider_attempt_count": 1,
        },
    }
    db.execute("INSERT INTO workflow_runs VALUES (?,?,?,?,?)", ("run-ui", "completed", "v3", 10.0, 12.0))
    db.execute(
        "INSERT INTO workflow_events VALUES (?,?,?,?)",
        ("run-ui", "workflow.report", 1, json.dumps({"payload": {"report": report}})),
    )
    db.commit()

    result = _run_record(db, "run-ui", {"category": "web", "query": query})

    assert result["successful"] is True
    assert "query" not in result
    _assert_safe_benchmark_record(result, query)
