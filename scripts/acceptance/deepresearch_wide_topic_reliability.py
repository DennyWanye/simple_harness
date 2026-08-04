"""Produce privacy-safe acceptance evidence for DeepResearch v4 runs.

The command accepts only run identifiers and topic fingerprints.  It hashes the
topic stored in the durable checkpoint internally and never emits the topic,
queries, URLs, passages, report body, or credentials.
"""

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
from typing import Any, Mapping


_ACTUAL_PARTS = (
    "hits",
    "empty",
    "timeouts",
    "blocked",
    "captcha",
    "rate_limits",
    "errors",
)
_SAFE_METRICS = frozenset(
    {
        "actual_requests",
        "routing_decisions",
        "hits",
        "empty",
        "timeouts",
        "blocked",
        "captcha",
        "rate_limits",
        "errors",
        "cooldown_skips",
        "busy_skips",
        "queue_timeouts",
        "unavailable_skips",
        "budget_skips",
        "cache_hits",
        "probes",
        "candidates",
        "citations",
        "independent_domains",
        "published_factual_claims",
        "body_bytes",
    }
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:https?://|authorization\s*:|bearer\s+|tsk_[a-z0-9_-]+|key_[a-z0-9_-]+|"
    r"sk-[a-z0-9_-]{8,}|access[_-]?token|device[_-]?key|raw[_-]?query|report_md|passage)"
)


def _fingerprint(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, (str, bytes, bytearray)):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _latest_checkpoint(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT checkpoint_blob FROM workflow_checkpoints "
        "WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return _json_object(row[0]) if row else {}


def _latest_event_payload(
    db: sqlite3.Connection,
    run_id: str,
    event_type: str,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT payload_json FROM workflow_events "
        "WHERE run_id=? AND event_type=? ORDER BY seq DESC LIMIT 1",
        (run_id, event_type),
    ).fetchone()
    envelope = _json_object(row[0]) if row else {}
    payload = envelope.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else envelope


def _event_count(db: sqlite3.Connection, run_id: str, pattern: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM workflow_events WHERE run_id=? AND event_type LIKE ?",
        (run_id, pattern),
    ).fetchone()
    return int(row[0]) if row else 0


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, min(1_000_000, int(number)))


def _safe_metrics(*sources: object) -> dict[str, int]:
    merged: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            name = str(key)
            if name in _SAFE_METRICS:
                merged[name] = _safe_int(value)
    return dict(sorted(merged.items()))


def _report_projection(payload: Mapping[str, Any]) -> dict[str, int]:
    report = payload.get("report")
    report = report if isinstance(report, Mapping) else payload
    coverage = report.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    citations = report.get("citations")
    citation_count = len(citations) if isinstance(citations, list) else _safe_int(
        coverage.get("citations")
    )
    return {
        "citations": citation_count,
        "independent_domains": _safe_int(
            coverage.get("independent_domain_count", coverage.get("independent_domains"))
        ),
        "published_factual_claims": _safe_int(
            coverage.get(
                "published_factual_claim_count",
                coverage.get("published_factual_count", coverage.get("supported_claim_count")),
            )
        ),
        "body_bytes": _safe_int(coverage.get("body_bytes")),
    }


def _run_record(
    db: sqlite3.Connection,
    run_id: str,
    expected_fingerprint: str,
    expected_outcome: str,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT run_id,status,workflow_version,started_at,ended_at,error_json "
        "FROM workflow_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown run_id: {run_id}")

    checkpoint = _latest_checkpoint(db, run_id)
    state = checkpoint.get("state")
    state = state if isinstance(state, Mapping) else {}
    values = state.get("values")
    values = values if isinstance(values, Mapping) else {}
    actual_fingerprint = _fingerprint(str(values.get("topic") or ""))
    if actual_fingerprint != expected_fingerprint:
        raise ValueError(f"topic fingerprint mismatch for run_id: {run_id}")

    final_payload = _latest_event_payload(db, run_id, "workflow.final")
    card = final_payload.get("card")
    card = card if isinstance(card, Mapping) else {}
    terminal_metrics = _safe_metrics(final_payload.get("metrics"), card.get("metrics"))
    report_payload = _latest_event_payload(db, run_id, "workflow.report")
    report_metrics = _report_projection(report_payload) if report_payload else {}
    metrics = _safe_metrics(terminal_metrics, report_metrics)

    started_at = float(row[3] or 0.0)
    ended_at = float(row[4] or 0.0)
    elapsed_ms = max(0, round((ended_at - started_at) * 1000)) if ended_at else 0
    report_count = _event_count(db, run_id, "workflow.report")
    artifact_count = _event_count(db, run_id, "workflow.artifact%")
    final_assistant_count = _event_count(db, run_id, "workflow.final_assistant")
    actual_equation = None
    if "actual_requests" in metrics:
        # Public progress omits zero-valued categories to stay compact.  The
        # accounting identity must still be decidable instead of weakening a
        # run to ``null`` merely because, for example, there were no hits.
        actual_equation = metrics["actual_requests"] == sum(
            metrics.get(key, 0) for key in _ACTUAL_PARTS
        )

    diagnostics = final_payload.get("diagnostic_codes", card.get("diagnostic_codes", []))
    diagnostics = (
        [str(value) for value in diagnostics if isinstance(value, str)][:16]
        if isinstance(diagnostics, list)
        else []
    )
    skipped = final_payload.get("skipped_stage_ids", card.get("skipped_stage_ids", []))
    skipped_count = len(skipped) if isinstance(skipped, list) else 0
    retry_action = str(
        final_payload.get("retry_action_id", card.get("retry_action_id", "")) or ""
    )
    run_status = str(row[1])
    is_success = (
        expected_outcome == "success"
        and run_status == "completed"
        and str(row[2]) == "v4"
        and report_count == 1
        and artifact_count == 1
        and final_assistant_count == 1
        and not diagnostics
    )
    is_failure = (
        expected_outcome == "failure"
        and run_status in {"failed", "error"}
        and str(row[2]) == "v4"
        and report_count == 0
        and artifact_count == 0
        and final_assistant_count == 0
        and bool(diagnostics)
        and skipped_count > 0
        and retry_action == "retry_from_start"
    )
    passed = (is_success or is_failure) and actual_equation is not False
    return {
        "run_id": str(row[0]),
        "topic_fingerprint": actual_fingerprint,
        "workflow_version": str(row[2]),
        "status": run_status,
        "expected_outcome": expected_outcome,
        "elapsed_ms": elapsed_ms,
        "report_count": report_count,
        "artifact_event_count": artifact_count,
        "final_assistant_event_count": final_assistant_count,
        "metrics": metrics,
        "actual_request_equation": actual_equation,
        "diagnostic_codes": diagnostics,
        "skipped_stage_count": skipped_count,
        "retry_action_id": retry_action or None,
        "passed": passed,
    }


def _privacy_check(result: Mapping[str, Any]) -> None:
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    match = _SECRET_PATTERN.search(rendered)
    if match:
        raise ValueError(f"privacy scan rejected output field class: {match.group(0).lower()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-db", type=Path, required=True)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--topic-fingerprint", action="append", required=True)
    parser.add_argument(
        "--expected-outcome",
        action="append",
        choices=("success", "failure"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (
        len(args.run_id) == len(args.topic_fingerprint) == len(args.expected_outcome)
    ):
        raise ValueError("run-id, topic-fingerprint, and expected-outcome counts must match")
    if any(not re.fullmatch(r"[0-9a-f]{16}", value) for value in args.topic_fingerprint):
        raise ValueError("topic fingerprints must be 16 lowercase hexadecimal characters")

    with sqlite3.connect(args.workflow_db) as db:
        records = [
            _run_record(db, run_id, fingerprint, outcome)
            for run_id, fingerprint, outcome in zip(
                args.run_id,
                args.topic_fingerprint,
                args.expected_outcome,
                strict=True,
            )
        ]
    result = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": sum(bool(record["passed"]) for record in records),
            "sample_size": len(records),
            "decision": "PASS" if all(record["passed"] for record in records) else "FAIL",
        },
        "runs": records,
    }
    _privacy_check(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["decision"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
