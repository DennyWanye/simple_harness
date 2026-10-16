# SPDX-License-Identifier: Apache-2.0
"""Bounded original verification facts for a TaskGraph root review.

These are observations about an accepted result, never authority to accept the
root. Read the immutable VerificationPassed event, not mutable latest reports.
"""

from __future__ import annotations

from typing import Any

from ..contracts.models import VERIFICATION_LAYERS
from ..storage.store import StoreError


def _bounded_layers(payload: dict[str, Any], mission_id: str, result: Any) -> list[dict[str, Any]]:
    raw_layers = payload["layers"]
    if not isinstance(raw_layers, list) or not 0 < len(raw_layers) <= len(VERIFICATION_LAYERS):
        raise ValueError("invalid layers")
    layers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for layer in raw_layers:
        name = layer["layer"]
        if name not in VERIFICATION_LAYERS or name in seen:
            raise ValueError("unknown or duplicate layer")
        seen.add(name)
        item = {"layer": name, "status": _text(layer["status"], 32)}
        summary = layer.get("summary", "")
        item.update(summary=_text(summary, 1024), summary_truncated=len(summary) > 1024)
        if name == "code_test":
            detail = layer.get("detail", {})
            scope = detail.get("observation_scope")
            if scope is not None:
                if any(
                    scope.get(key) != value
                    for key, value in {
                        "mission_id": mission_id,
                        "task_id": result.task_id,
                        "attempt_id": result.attempt_id,
                        "result_id": result.id,
                    }.items()
                ):
                    raise StoreError("TASKGRAPH_REVIEW_EVIDENCE_SCOPE_MISMATCH")
                item["observation_scope"] = {
                    key: _text(scope.get(key), 256)
                    for key in (
                        "schema",
                        "mission_id",
                        "task_id",
                        "attempt_id",
                        "result_id",
                        "workspace_hash",
                        "checker",
                    )
                }
                item["observation_scope"].update(
                    criteria_count=len(scope.get("criteria", [])),
                    artifact_count=len(scope.get("artifact_hashes", {})),
                    projection="identities_and_counts_only",
                )
            runs = detail.get("runs", [])
            if not isinstance(runs, list):
                raise ValueError("invalid runs")
            if runs and scope is None:
                layers.append(
                    {
                        "layer": name,
                        "evidence_status": "UNBOUND",
                        "run_count": len(runs),
                        "summary": "Original test observations lack a result-bound scope; no test verdict is supplied.",
                    }
                )
                continue
            item.update(run_count=len(runs), runs_truncated=len(runs) > 16, runs=[])
            for run in runs[:16]:
                receipt = run.get("receipt", {})
                target = run.get("target", "")
                item["runs"].append(
                    {
                        "target": _text(target, 512),
                        "target_truncated": isinstance(target, str) and len(target) > 512,
                        "passed": _scalar(run.get("passed")),
                        "returncode": _scalar(run.get("returncode")),
                        "timed_out": _scalar(run.get("timed_out")),
                        "receipt": {
                            key: _scalar(receipt.get(key))
                            for key in (
                                "execution_id",
                                "status",
                                "exit_code",
                                "isolated",
                                "timed_out",
                                "environment_digest",
                            )
                        },
                    }
                )
        layers.append(item)
    return layers


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value[:limit]


def _scalar(value: Any) -> str | int | bool | None:
    # Receipt identity/digest/status fields are small protocol values; reject
    # oversized identities rather than displaying a misleading truncated ID.
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and -(2**63) <= value < 2**63:
        return value
    if isinstance(value, str) and len(value) <= 256:
        return value
    raise ValueError("invalid receipt scalar")
