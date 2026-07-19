"""Aggregate three real UI DeepResearch runs from the durable workflow DB."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


THRESHOLDS = {
    "support_rate": 0.60,
    "published_factual_claims": 8,
    "citations": 8,
    "independent_domains": 4,
    "body_bytes": 1500,
}


def _latest_report(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT payload_json FROM workflow_events "
        "WHERE run_id=? AND event_type='workflow.report' ORDER BY seq DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        return {}
    envelope = json.loads(str(row[0]))
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    report = payload.get("report") if isinstance(payload, dict) else None
    return dict(report) if isinstance(report, dict) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _normalized_topic(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _query_fingerprint(value: str) -> str:
    """Create a stable benchmark join key without persisting the raw query."""
    normalized = " ".join(value.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _run_record(
    db: sqlite3.Connection,
    run_id: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    row = db.execute(
        "SELECT run_id,status,workflow_version,started_at,ended_at FROM workflow_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown run_id: {run_id}")
    report = _latest_report(db, run_id)
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    topic = str(report.get("topic") or "")
    expected_query = str(expected["query"])
    # The chat UI needs an explicit natural-language DeepResearch trigger, so
    # the durable topic contains a short directive around the fixed benchmark
    # query.  Keep order validation strict on the full normalized query while
    # allowing that UI-only wrapper.
    if _normalized_topic(expected_query) not in _normalized_topic(topic):
        raise ValueError(f"query order mismatch for {run_id}: {topic!r} != {expected_query!r}")

    citations_value = report.get("citations")
    citations = len(citations_value) if isinstance(citations_value, list) else int(
        _number(coverage.get("citations"))
    )
    started_at = _number(row[3])
    ended_at = _number(row[4])
    elapsed_ms = max(0, round((ended_at - started_at) * 1000)) if ended_at else 0
    support_rate = _number(
        coverage.get("support_rate_final", coverage.get("support_rate"))
    )
    published = int(
        _number(
            coverage.get(
                "published_factual_claim_count",
                coverage.get(
                    "published_factual_count",
                    coverage.get("supported_claim_count"),
                ),
            )
        )
    )
    domains = int(_number(
        coverage.get("independent_domain_count", coverage.get("independent_domains"))
    ))
    body_bytes = int(_number(coverage.get("body_bytes")))
    attempt_count = int(_number(coverage.get("provider_attempt_count")))
    probe_count = int(_number(coverage.get("provider_probe_count")))
    status = str(report.get("status") or row[1])
    gate = {
        "support_rate": support_rate >= THRESHOLDS["support_rate"],
        "published_factual_claims": published >= THRESHOLDS["published_factual_claims"],
        "citations": citations >= THRESHOLDS["citations"],
        "independent_domains": domains >= THRESHOLDS["independent_domains"],
        "body_bytes": body_bytes >= THRESHOLDS["body_bytes"],
    }
    successful = status == "completed" and all(gate.values())
    return {
        "category": str(expected["category"]),
        "query_fingerprint": _query_fingerprint(expected_query),
        "run_id": run_id,
        "workflow_version": str(row[2]),
        "status": status,
        "elapsed_ms": elapsed_ms,
        "support_rate": support_rate,
        "published_factual_claims": published,
        "citations": citations,
        "independent_domains": domains,
        "body_bytes": body_bytes,
        "provider_attempt_count": attempt_count,
        "provider_probe_count": probe_count,
        "gate": gate,
        "successful": successful,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-db", type=Path, required=True)
    parser.add_argument("--query-file", type=Path, required=True)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    query_doc = json.loads(args.query_file.read_text(encoding="utf-8"))
    queries = query_doc.get("queries") if isinstance(query_doc, dict) else None
    if not isinstance(queries, list) or len(queries) != 3 or len(args.run_id) != 3:
        raise ValueError("benchmark requires exactly three ordered queries and three run ids")

    with sqlite3.connect(args.workflow_db) as db:
        runs = [
            _run_record(db, run_id, expected)
            for run_id, expected in zip(args.run_id, queries, strict=True)
        ]
    elapsed = sorted(int(run["elapsed_ms"]) for run in runs)
    p95 = elapsed[max(0, math.ceil(0.95 * len(elapsed)) - 1)]
    mean_support = sum(float(run["support_rate"]) for run in runs) / len(runs)
    policy_to_webgpu_attempted = int(runs[2]["provider_attempt_count"]) >= 1
    decision = (
        all(bool(run["successful"]) for run in runs)
        and mean_support >= 0.70
        and p95 <= 360_000
        and policy_to_webgpu_attempted
    )
    result = {
        "schema_version": 3,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "query_schema_version": query_doc.get("schema_version"),
        "thresholds": {**THRESHOLDS, "mean_support_rate": 0.70, "p95_elapsed_ms": 360_000},
        "aggregate": {
            "completed": sum(bool(run["successful"]) for run in runs),
            "sample_size": len(runs),
            "mean_support_rate": mean_support,
            "p95_elapsed_ms": p95,
            "p95_method": "nearest-rank",
            "policy_to_webgpu_attempted": policy_to_webgpu_attempted,
            "decision": "PASS" if decision else "FAIL",
        },
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["aggregate"], ensure_ascii=False))
    return 0 if decision else 1


if __name__ == "__main__":
    sys.exit(main())
