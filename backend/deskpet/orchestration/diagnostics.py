# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Read-only, redacted Mission diagnostics and local support receipts.

The caller must obtain ``snapshot_view`` through ``MissionControlV1.snapshot`` while
holding the Host store's ``read_view()``.  This module deliberately does not perform
ownership checks, dispatch work, call providers, or mutate the orchestration store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agent_orchestrator.contracts.models import sha256_hex
from agent_orchestrator.observability.business_replay import coverage_report
from agent_orchestrator.observability.metrics import metrics
from agent_orchestrator.observability.replay import (
    FORMAL_FIELDS,
    REPLAY_VERSION,
    Projection,
    compare,
    events_from_store,
    failure_timeline,
    formal_from_snapshot,
)
from agent_orchestrator.observability.secrets import redact_text
from agent_orchestrator.observability.traces import attribution

MAX_SUPPORT_BYTES = 2 * 1024 * 1024
DIAGNOSTICS_VERSION = "host-mission-diagnostics-v1"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_scalar(value: object) -> str | int | float | bool | None:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _hash(value: object) -> str | None:
    return None if value is None else sha256_hex(value)


def _pick(value: object, names: tuple[str, ...]) -> dict[str, Any]:
    """Copy only named scalar facts from an SDK record; no open-ended payloads."""

    row = _mapping(value)
    return {name: _safe_scalar(row.get(name)) for name in names}


def _rows(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _usage(value: object) -> dict[str, Any]:
    return _pick(value, ("tokens", "rows"))


def _cost(value: object) -> dict[str, Any]:
    cost = _mapping(value)
    ledger = _mapping(cost.get("ledger"))
    services = _mapping(cost.get("services"))
    unclassified = _mapping(cost.get("unclassified"))
    return {
        "success_path": _usage(cost.get("success_path")),
        "exploration": _usage(cost.get("exploration")),
        "services": {role: _usage(services.get(role)) for role in ("planner", "judge")
                     if role in services},
        "unclassified": {
            **_usage(unclassified),
            "subject_sha256": [_hash(item) for item in unclassified.get("subjects", ())
                               if isinstance(item, str)],
        },
        "total": _usage(cost.get("total")),
        "unknown_usage_rows": _safe_scalar(cost.get("unknown_usage_rows")),
        "ledger": {
            **_pick(ledger, (
                "settled_tokens", "usage_of_settled_subjects", "unsettled_usage_tokens",
            )),
            "mismatched_subject_sha256": [
                _hash(item) for item in ledger.get("mismatched_subjects", ())
                if isinstance(item, str)
            ],
        },
        "reconciled": cost.get("reconciled") is True,
    }


def _replay_reference(value: object) -> dict[str, Any]:
    row = _mapping(value)
    # SDK source keys are JSON-encoded [mission_id, path, version_hash]. Action
    # and approval keys may also contain caller-chosen text.
    raw_kind = row.get("object")
    kind = raw_kind if isinstance(raw_kind, str) and raw_kind in FORMAL_FIELDS else None
    identifier = row.get("id")
    raw_field = row.get("field")
    field = raw_field if kind is not None and raw_field in (*FORMAL_FIELDS[kind], "*") else None
    return {
        "object": kind,
        "object_sha256": _hash(raw_kind) if kind is None else None,
        "id": identifier if kind in {"mission", "task", "attempt", "result"}
              else None,
        "id_sha256": _hash(identifier) if kind not in {"mission", "task", "attempt", "result"}
                     else None,
        "field": field,
        "field_sha256": _hash(raw_field) if field is None else None,
    }


def _comparison(value: object) -> dict[str, Any]:
    comparison = _mapping(value)
    by_kind = _mapping(comparison.get("by_kind"))
    return {
        "coverage": _safe_scalar(comparison.get("coverage")),
        "by_kind": {kind: _pick(by_kind.get(kind), ("expected", "decided"))
                    for kind in FORMAL_FIELDS if kind in by_kind},
        "not_covered": [_replay_reference(row) for row in _rows(comparison.get("not_covered"))],
        "mismatches": [
            {**_replay_reference(row), "replayed_sha256": _hash(row.get("replayed")),
             "library_sha256": _hash(row.get("library"))}
            for row in _rows(comparison.get("mismatches"))
        ],
        "consistent": comparison.get("consistent") is True,
    }


_TIMELINE_CODES = frozenset({
    "PASS", "FAIL", "ERROR", "NEEDS_HUMAN", "INCONCLUSIVE", "PENDING", "RUNNING",
    "COMPLETED", "FAILED", "CANCELLED", "LOST", "TIMED_OUT", "SUBMITTED",
    "success", "failure", "no_progress", "verified", "rejected", "budget_exhausted",
    "verification_passed", "insufficient_evidence", "mission_criteria_unmet",
})
_TIMELINE_TYPES = frozenset({
    "AttemptCreated", "AttemptStarted", "ResultSubmitted", "VerificationFailed",
    "VerificationSuspended", "VerificationLayerRecorded", "AttemptLost", "AttemptTimedOut",
    "OutcomeRecorded", "TaskFailed",
    "TaskCancelled", "MissionFailed", "ActionFailed", "ActionOutcomeUnknown",
    "ApprovalRejected",
})


def _timeline(value: object) -> list[dict[str, Any]]:
    lines = []
    for row in _rows(value):
        payload = _mapping(row.get("detail"))
        detail: dict[str, Any] = {"payload_sha256": sha256_hex(dict(payload))}
        for key in ("status", "outcome", "stop_reason"):
            code = payload.get(key)
            if isinstance(code, str):
                detail[key] = code if code in _TIMELINE_CODES else None
                detail[f"{key}_sha256"] = _hash(code) if code not in _TIMELINE_CODES else None
        if isinstance(payload.get("result_id"), str):
            detail["result_id"] = payload["result_id"]
        for key in ("failures", "proposed_tasks", "artifacts"):
            if isinstance(payload.get(key), (list, tuple)):
                detail[f"{key}_count"] = len(payload[key])
        event_type = row.get("type")
        lines.append({
            **_pick(row, ("seq", "task_id", "attempt_id")),
            "type": event_type if event_type in _TIMELINE_TYPES else None,
            "type_sha256": _hash(event_type) if event_type not in _TIMELINE_TYPES else None,
            "detail": detail,
        })
    return lines


def _attribution(value: object, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(value)
    knowledge = _mapping(source.get("knowledge_path"))
    context_versions = {
        row["id"]: _safe_scalar(row.get("context_version"))
        for row in _rows(snapshot.get("attempts")) if isinstance(row.get("id"), str)
    }
    return {
        **_pick(source, ("version", "mission_id", "mission_status", "success_path")),
        "final_products": [
            {**_pick(row, ("content_hash", "artifact_id", "task_id", "result_id", "attempt_id",
                           "agent_id", "role", "model", "runtime_profile_id", "prompt_version")),
             "path_sha256": _hash(row.get("path")),
             "verified_by": [_pick(layer, ("layer", "status", "verifier_version"))
                             for layer in _rows(row.get("verified_by"))]}
            for row in _rows(source.get("final_products"))
        ],
        "path_tasks": [
            {**_pick(row, ("task_id", "kind", "accepted_result_id")),
             "artifacts": [_pick(artifact, ("artifact_id", "in_final_products"))
                           for artifact in _rows(row.get("artifacts"))]}
            for row in _rows(source.get("path_tasks"))
        ],
        "knowledge_path": {
            "knowledge": [item for item in knowledge.get("knowledge", ()) if isinstance(item, str)],
            "edges": [_pick(edge, ("knowledge", "supersedes", "result", "produced", "uses",
                                   "version"))
                      for edge in _rows(knowledge.get("edges"))],
        },
        "attempts": [
            {**_pick(row, ("attempt_id", "task_id", "agent_id", "role", "model",
                           "runtime_profile_id", "prompt_version", "status", "on_success_path",
                           "exploration_reason", "tool_calls")),
             "context_version": context_versions.get(row.get("attempt_id")),
             "work": _usage(row.get("work")), "verification": _usage(row.get("verification"))}
            for row in _rows(source.get("attempts"))
        ],
        "actions": [
            {**_pick(row, ("receipt_hash",)), "action_key_sha256": _hash(row.get("action_key")),
             "target_sha256": _hash(row.get("target")),
             "decision_receipts": [_hash(item) for item in row.get("decision_receipts", ())
                                   if isinstance(item, str)],
             "approved_by_sha256": [_hash(item) for item in row.get("approved_by", ())
                                    if isinstance(item, str)]}
            for row in _rows(source.get("actions"))
        ],
        "action_reservations": [
            {**_pick(row, ("settled_tool_calls", "state")),
             "subject_sha256": _hash(row.get("subject_id"))}
            for row in _rows(source.get("action_reservations"))
        ],
        "human": _pick(source.get("human"), ("wait_seconds", "decisions", "overrides")),
        "cost": _cost(source.get("cost")),
        "breaks": [
            {**_pick(row, ("missing", "task_id")), "id_sha256": _hash(row.get("id")),
             "error_sha256": _hash(row.get("error"))}
            for row in _rows(source.get("breaks"))
        ],
    }


def _input_references(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    mission = _mapping(snapshot.get("mission"))
    report = _mapping(mission.get("final_report"))
    seed = _mapping(report.get("workspace_seed"))
    sources = [row for row in snapshot.get("sources", ()) if isinstance(row, Mapping)]
    return {
        "mission": {
            "goal_sha256": sha256_hex(mission.get("goal", "")),
            "success_criteria_sha256": sha256_hex(mission.get("success_criteria", ())),
            "stop_conditions_sha256": sha256_hex(mission.get("stop_conditions", ())),
        },
        "workspace_seed": {
            "sha256": sha256_hex(dict(seed)),
            "files": len(seed),
        },
        "sources": [
            {
                "version_hash": row.get("version_hash"),
                "reference_sha256": sha256_hex(
                    {
                        "path": row.get("path"),
                        "version_hash": row.get("version_hash"),
                    }
                ),
            }
            for row in sources
        ],
    }


def _verification(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    results = [row for row in snapshot.get("results", ()) if isinstance(row, Mapping)]
    artifacts = [row for row in snapshot.get("artifacts", ()) if isinstance(row, Mapping)]
    return {
        "results": [
            {
                "result_id": _mapping(row.get("envelope")).get("id"),
                "verification_state": row.get("verification_state"),
                "verdict": row.get("verdict"),
                "layers": [
                    {
                        "layer": _mapping(layer).get("layer"),
                        "status": _mapping(layer).get("status"),
                        "verifier_version": _mapping(_mapping(layer).get("detail")).get(
                            "verifier_version"
                        ),
                    }
                    for layer in row.get("verifications", ())
                    if isinstance(layer, Mapping)
                ],
            }
            for row in results
        ],
        "artifacts": [
            {
                "artifact_id": row.get("id"),
                "task_id": row.get("task_id"),
                "attempt_id": row.get("attempt_id"),
                "content_hash": row.get("content_hash"),
                "size_bytes": row.get("size_bytes"),
            }
            for row in artifacts
        ],
    }


def _versions(manifest: Mapping[str, Any]) -> dict[str, Any]:
    distributions = _mapping(manifest.get("distributions"))
    simple_harness = _mapping(distributions.get("simple_harness"))
    orchestrator = _mapping(distributions.get("agent_orchestrator"))
    distribution = _mapping(distributions.get("distribution"))
    pin = _mapping(distributions.get("pin"))
    schemas = _mapping(manifest.get("schemas"))
    identity = _mapping(distributions.get("runtime_identity"))
    source = _mapping(identity.get("source"))
    return {
        "diagnostics": DIAGNOSTICS_VERSION,
        "manifest_schema": _safe_scalar(manifest.get("schema")),
        "host_commit": _safe_scalar(manifest.get("host_commit")),
        "host_dirty": _safe_scalar(manifest.get("host_dirty")),
        "schemas": _pick(schemas, ("orchestrator", "execution")),
        "sdk": {
            "simple_harness": _safe_scalar(simple_harness.get("version")),
            "agent_orchestrator": _safe_scalar(orchestrator.get("version")),
            "distribution": _safe_scalar(distribution.get("version")),
            "pinned": _safe_scalar(pin.get("version")),
            "wheel_sha256": _safe_scalar(pin.get("wheel_sha256")),
            "version_match": _safe_scalar(distributions.get("version_match")),
            "consistent": _safe_scalar(distributions.get("consistent")),
            "runtime_identity": {
                **_pick(identity, ("mode", "verification", "source_verified",
                                   "baseline_artifact_verified", "installed_wheel_verified")),
                "source": _pick(source, ("commit", "inputs_sha256", "input_count")),
            },
        },
    }


def _redact(report: Mapping[str, Any], extra_secrets: Iterable[str]) -> dict[str, Any]:
    """Redact decoded values before JSON escaping (quotes/backslashes included)."""

    secrets = tuple(value for value in extra_secrets if isinstance(value, str) and value)

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            value, _ = redact_text(value)
            for secret in secrets:
                value = value.replace(secret, "<redacted:configured_secret>")
            return value
        if isinstance(value, Mapping):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    return clean(report)


def build_diagnostics(
    orchestrator: Any,
    *,
    mission_id: str,
    snapshot_view: Mapping[str, Any],
    versions: Mapping[str, Any],
    extra_secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a bounded, selected-Mission report from an already-authorized snapshot.

    ``snapshot_view`` must be the same selected Mission returned by
    ``MissionControlV1.snapshot``.  The caller owns both the facade ownership check and
    ``store.read_view()`` lifetime; this function performs reads only.
    """

    if not isinstance(mission_id, str) or not mission_id:
        raise ValueError("mission_id is required")
    if str(snapshot_view.get("mission_id")) != mission_id:
        raise ValueError("snapshot_view does not belong to mission_id")
    snapshot = _mapping(snapshot_view.get("snapshot"))
    if str(_mapping(snapshot.get("mission")).get("id")) != mission_id:
        raise ValueError("snapshot mission does not belong to mission_id")

    store = orchestrator.store
    events = events_from_store(store, mission_id)
    projection = Projection().feed(events)
    projection.check_structure()
    comparison = _comparison(compare(projection.formal(), formal_from_snapshot(snapshot)))
    attribution_report = _attribution(attribution(store, mission_id), snapshot)
    report = {
        "mission_id": mission_id,
        "replay": {
            "version": REPLAY_VERSION,
            "events": len(events),
            "applied": projection.applied,
            "duplicates": projection.duplicates,
            "unknown_event_types": {
                sha256_hex(kind): count for kind, count in projection.unknown.items()
            },
            "gaps": [
                {**_pick(gap, ("rule", "object", "status", "state")),
                 "id_sha256": _hash(gap.get("id")),
                 "at_event_sha256": _hash(gap.get("at_event")),
                 "result_id": _safe_scalar(gap.get("result_id"))}
                for gap in projection.gaps
            ],
            "comparison": comparison,
            "failure_timeline": _timeline(failure_timeline(events, projection)),
        },
        # 全业务重放 v3 的骨架：哪些业务表已能由事件重建（HTN 补齐阶段 A；阶段 G 取代上面的 v2）
        "business_replay": coverage_report(store, mission_id),
        "attribution": attribution_report,
        # 指标统计的唯一入口（HTN 补齐阶段 B）。编排只记 token，不记金额。
        "metrics": metrics(store, mission_id),
        "verification": _verification(snapshot),
        "costs": {
            "usage": _pick(snapshot.get("budget_usage"), (
                "reserved_tokens", "settled_tokens", "attempts_created", "version")),
            "attribution": attribution_report.get("cost"),
        },
        "input_references": _input_references(snapshot),
        "versions": _versions(versions),
        "scope": {
            "selected_only": True,
            "mission_id": mission_id,
            "through_seq": snapshot_view.get("through_seq"),
            "state_version": snapshot_view.get("state_version"),
            "not_covered": {
                "replay": comparison["not_covered"],
                "excluded": [
                    "mission goal and criteria text",
                    "source content and journal payloads",
                    "workspace seed bytes",
                    "artifact bytes and provider responses",
                    "unrelated Mission history",
                ],
            },
        },
    }
    return _redact(report, extra_secrets)


#: 诊断包里最多放多少对相邻版本的结构差异（最新的在后）。
MAX_HISTORY_DIFFS = 32


def taskgraph_history(store: Any, reads: Any, mission_id: str, scratch: Path) -> dict[str, Any]:
    """核对执行图历史（HTN 补齐阶段 B 第 2 条；原计划不变量第 16 条）。

    在临时副本上用 SDK 的 ``replay_taskgraph`` 从种子版本重建到最新版本，只把**重建报告**放进诊断包——
    重建出来的库里有任务原文，用完即删。另附历史版本清单（只读，标"历史，不可执行"）和相邻版本的
    结构差异（对象种类、标识、前后哈希；没有正文）。不调模型，不重放外部动作。
    """
    import tempfile

    from agent_orchestrator.contracts.models import ContractError
    from agent_orchestrator.observability.taskgraph_replay import replay_taskgraph

    rows = store.connection.execute(
        "SELECT revision,source_kind,manifest_hash,parent_revision FROM taskgraph_revision_records "
        "WHERE mission_id=? ORDER BY revision", (mission_id,)).fetchall()
    if not rows:
        return {"status": "NOT_ENABLED"}
    latest = int(rows[-1][0])
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        try:
            built = replay_taskgraph(store, mission_id=mission_id, through_revision=latest,
                                     target_path=Path(directory) / "replay.sqlite")
            replay = {"status": built.status, "runtime_status": built.runtime_status,
                      "through_revision": built.through_revision, "revision_count": built.revision_count,
                      "coverage_start_revision": built.coverage_start_revision,
                      "coverage_start_kind": built.coverage_start_kind}
        except ContractError as error:
            replay = {"status": "REPLAY_FAILED", "error": str(error)[:300]}
    diffs = []
    pairs = [(int(a[0]), int(b[0])) for a, b in zip(rows, rows[1:])][-MAX_HISTORY_DIFFS:]
    for before, after in pairs:
        try:
            diff = reads.diff(mission_id, before, after)
            diffs.append({"from_revision": before, "to_revision": after, "changes": [
                _pick(change, ("kind", "identity", "before_hash", "after_hash")) for change in diff.get("changes", ())]})
        except Exception as error:  # noqa: BLE001 - one unreadable pair is reported, not fatal
            diffs.append({"from_revision": before, "to_revision": after, "error": type(error).__name__})
    return {
        "status": "CHECKED",
        "replay": replay,
        "revisions": [{"revision": int(row[0]), "source_kind": str(row[1]), "manifest_hash": str(row[2]),
                       "parent_revision": row[3], "label": "历史，不可执行" if int(row[0]) != latest else "当前"}
                      for row in rows],
        "diffs": diffs,
        "omitted_diffs": max(0, len(rows) - 1 - len(pairs)),
    }


def export_support(directory: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    """Write one content-addressed local JSON report, refusing reports over 2 MiB."""

    mission_id = report.get("mission_id")
    if not isinstance(mission_id, str) or not mission_id:
        raise ValueError("report mission_id is required")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    body = serialized.encode("utf-8")
    if len(body) > MAX_SUPPORT_BYTES:
        raise ValueError("support report exceeds 2 MiB")
    digest = hashlib.sha256(body).hexdigest()
    target_directory = Path(directory)
    target = target_directory / f"{digest}.json"
    target_directory.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise ValueError("support receipt hash collision")
    else:
        target.write_bytes(body)
    return {
        "path": str(target),
        "sha256": digest,
        "size_bytes": len(body),
        "mission_id": mission_id,
    }


__all__ = ("DIAGNOSTICS_VERSION", "MAX_SUPPORT_BYTES", "build_diagnostics", "export_support", "taskgraph_history")
