# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fail-closed, user-safe progress projection for DeepResearch v5.

The graph owns research state; this module owns the much smaller public
contract.  In particular, it never copies strings from queries, prompts,
pages, URLs, provider errors, gap reasons, or generated content.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..contracts import JsonValue, WorkflowState

CAPABILITY = "deep_research_progress_v5"
SCHEMA_VERSION = 5

NODE_IDS = (
    "normalize", "model", "plan", "expand", "search", "direct", "fetch",
    "score", "gap_evaluate", "gap_work", "gap_join", "rerank", "synth",
    "quality_audit", "repair_work", "repair_join", "persist", "finalize",
    "insufficient_finalize",
)

_PUBLIC_STAGE = {
    "normalize": "normalize",
    "model": "plan",
    "plan": "plan",
    "expand": "research",
    "search": "research",
    "direct": "research",
    "fetch": "research",
    "score": "research",
    "gap_evaluate": "gap",
    "gap_work": "gap",
    "gap_join": "gap",
    "rerank": "rerank",
    "synth": "synth",
    "quality_audit": "quality",
    "repair_work": "quality",
    "repair_join": "quality",
    "persist": "persist",
    "finalize": "finalize",
    "insufficient_finalize": "finalize",
}
PUBLIC_STAGE_IDS = (
    "normalize", "plan", "research", "gap", "rerank", "synth", "quality",
    "persist", "finalize",
)

_ACTION = {
    "normalize": "normalize_request",
    "model": "model_dimensions",
    "plan": "plan_queries",
    "expand": "expand_queries",
    "search": "search_sources",
    "direct": "find_first_party_sources",
    "fetch": "fetch_sources",
    "score": "score_evidence",
    "gap_evaluate": "evaluate_gaps",
    "gap_work": "research_gap",
    "gap_join": "commit_gap_result",
    "rerank": "rerank_evidence",
    "synth": "synthesize_report",
    "quality_audit": "audit_quality",
    "repair_work": "repair_report",
    "repair_join": "commit_repair",
    "persist": "persist_report",
    "finalize": "finalize_delivery",
    "insufficient_finalize": "finalize_insufficient",
}

_DEFAULT_NEXT = {
    "normalize": "model_dimensions",
    "model": "plan_queries",
    "plan": "expand_queries",
    "expand": "search_sources",
    "search": "find_first_party_sources",
    "direct": "fetch_sources",
    "fetch": "score_evidence",
    "score": "evaluate_gaps",
    "gap_evaluate": "research_gap",
    "gap_work": "commit_gap_result",
    "gap_join": "evaluate_gaps",
    "rerank": "synthesize_report",
    "synth": "audit_quality",
    "quality_audit": "repair_report",
    "repair_work": "commit_repair",
    "repair_join": "audit_quality",
    "persist": "finalize_delivery",
    "finalize": "none",
    "insufficient_finalize": "none",
}

_COVERAGE_STATUSES = frozenset(
    {"covered", "partially_covered", "uncovered", "not_applicable"}
)
_HARD_FAILURES = frozenset(
    {
        "completed_core_uncovered",
        "unsupported_key_claim",
        "invalid_or_captcha_citation",
        "secondary_replaces_available_official",
        "citation_dimension_mismatch",
        "internal_diagnostics_leak",
    }
)
_LEASE_REASONS = frozenset(
    {
        "before_soft_or_lease_checkpoint",
        "lease_renewed_measurable_gain",
        "lease_not_renewed_no_gain",
        "automatic_cap",
        "running",
        "generate_now_settling",
        "cancelled",
        "plateau_settling",
        "lease_no_gain_settling",
        "automatic_cap_settling",
    }
)
_CONTROL_ACTIONS = frozenset(
    {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}
)
_CONTROL_STATUSES = frozenset(
    {"open", "accepted", "observed", "settled", "consumed", "rejected", "expired"}
)
_DELIVERY = frozenset({"pending", "completed", "partial", "insufficient_evidence"})
_SAFE_REJECTION_CODES = frozenset(
    {
        "invalid_page",
        "body_too_short",
        "body_span_missing",
        "dimension_relevance_below_threshold",
        "other_rejected",
    }
)
_SAFE_GAP_CODES = frozenset(
    {
        "insufficient_admitted_passages",
        "insufficient_strong_distinct_families",
        "winning_relevance_below_threshold",
        "duplicate_rate_above_threshold",
        "invalid_rate_above_threshold",
        "first_party_requirement_unsatisfied",
        "evidence_gap",
    }
)


class V5ProgressProjectionError(ValueError):
    pass


def _values(state: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = state.get("values")
    if not isinstance(raw, Mapping):
        raise V5ProgressProjectionError("missing_values")
    return raw


def _non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0, int(value))


def _coverages(values: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = values.get("dimension_coverages", [])
    if not isinstance(raw, list):
        raise V5ProgressProjectionError("invalid_dimension_coverages")
    result: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise V5ProgressProjectionError("invalid_dimension_coverage")
        status = item.get("status")
        dimension_id = item.get("dimension_id")
        if status not in _COVERAGE_STATUSES or not isinstance(dimension_id, str) or not dimension_id:
            raise V5ProgressProjectionError("invalid_dimension_coverage")
        result.append(item)
    return result


def _dimension_model(values: Mapping[str, Any]) -> tuple[list[str], set[str]]:
    brief = values.get("research_brief")
    if not isinstance(brief, Mapping):
        return [], set()
    dimensions = brief.get("dimensions", [])
    if not isinstance(dimensions, list):
        raise V5ProgressProjectionError("invalid_research_brief")
    ordered: list[str] = []
    core: set[str] = set()
    for item in dimensions:
        if not isinstance(item, Mapping):
            raise V5ProgressProjectionError("invalid_research_brief")
        dimension_id = item.get("dimension_id")
        importance = item.get("importance")
        if not isinstance(dimension_id, str) or not dimension_id or importance not in {"core", "supporting"}:
            raise V5ProgressProjectionError("invalid_research_brief")
        ordered.append(dimension_id)
        if importance == "core":
            core.add(dimension_id)
    return ordered, core


def _dimension_counts(
    values: Mapping[str, Any], coverages: list[Mapping[str, Any]]
) -> tuple[dict[str, int], list[str], set[str]]:
    ordered, core = _dimension_model(values)
    counts = {status: 0 for status in _COVERAGE_STATUSES}
    for item in coverages:
        counts[str(item["status"])] += 1
    core_scope = core or {str(item["dimension_id"]) for item in coverages}
    core_counts = {status: 0 for status in _COVERAGE_STATUSES}
    for item in coverages:
        if str(item["dimension_id"]) in core_scope:
            core_counts[str(item["status"])] += 1
    return (
        {
            "total": len(ordered) if ordered else len(coverages),
            "core_total": len(core) if ordered else len(coverages),
            "covered": counts["covered"],
            "partially_covered": counts["partially_covered"],
            "uncovered": counts["uncovered"],
            "not_applicable": counts["not_applicable"],
            "core_covered": core_counts["covered"],
            "core_partially_covered": core_counts["partially_covered"],
            "core_uncovered": core_counts["uncovered"],
        },
        ordered,
        core,
    )


def _status_changes(
    current: list[Mapping[str, Any]], previous_state: Mapping[str, Any] | None
) -> dict[str, int | str]:
    if previous_state is None:
        return {"improved": "none", "regressed": "none", "unchanged": "none"}
    previous = _coverages(_values(previous_state))
    old = {str(item["dimension_id"]): str(item["status"]) for item in previous}
    rank = {"uncovered": 0, "partially_covered": 1, "covered": 2, "not_applicable": 0}
    improved = regressed = unchanged = 0
    for item in current:
        prior = old.get(str(item["dimension_id"]))
        if prior is None:
            continue
        now = str(item["status"])
        if rank[now] > rank[prior]:
            improved += 1
        elif rank[now] < rank[prior]:
            regressed += 1
        else:
            unchanged += 1
    return {"improved": improved, "regressed": regressed, "unchanged": unchanged}


def _source_counts(
    values: Mapping[str, Any], coverages: list[Mapping[str, Any]]
) -> dict[str, int]:
    admitted_family_ids: set[str] = set()
    for item in coverages:
        raw_families = item.get("source_family_ids", [])
        if isinstance(raw_families, list):
            admitted_family_ids.update(
                value for value in raw_families if isinstance(value, str) and value
            )
    raw_records = values.get("source_families")
    if not isinstance(raw_records, list):
        fetch = values.get("fetch_result")
        raw_records = fetch.get("source_families") if isinstance(fetch, Mapping) else None
    if not isinstance(raw_records, list):
        snapshot = values.get("evidence_snapshot")
        raw_records = snapshot.get("source_families") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw_records, list):
        # Coverage family ids have already passed admission, but there is no
        # safe basis for claiming which unique family is first-party.
        return {"valid": len(admitted_family_ids), "first_party": 0}
    valid: set[str] = set()
    first_party: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            continue
        family_id = raw.get("family_id")
        if (
            not isinstance(family_id, str)
            or not family_id
            or family_id not in admitted_family_ids
            or raw.get("page_quality") != "valid"
        ):
            continue
        valid.add(family_id)
        if raw.get("source_tier") == "first_party":
            first_party.add(family_id)
    return {"valid": len(valid), "first_party": len(first_party)}


def _active_gap(
    values: Mapping[str, Any], ordered_dimensions: list[str]
) -> JsonValue:
    raw = values.get("active_gap_work")
    if not isinstance(raw, Mapping):
        return "none"
    work_kind = raw.get("work_kind")
    status = raw.get("status")
    dimension_id = raw.get("dimension_id")
    if work_kind not in {"query", "source_target", "fetch"} or status not in {
        "pending", "running", "completed", "failed", "cancelled"
    }:
        return "none"
    ordinal = (
        ordered_dimensions.index(dimension_id) + 1
        if isinstance(dimension_id, str) and dimension_id in ordered_dimensions
        else 0
    )
    return {"status": str(status), "work_kind": str(work_kind), "dimension_ordinal": ordinal}


def _loop_projection(values: Mapping[str, Any]) -> tuple[int, str, str]:
    loop = values.get("loop_policy")
    if not isinstance(loop, Mapping):
        return 0, "none", "none"
    elapsed = _non_negative_int(loop.get("accumulated_active_seconds"))
    soft = loop.get("soft_checkpoint_seconds")
    checkpoint = (
        "reached"
        if isinstance(soft, (int, float)) and not isinstance(soft, bool) and elapsed >= float(soft)
        else "before"
    )
    decision = values.get("loop_decision")
    reason = decision.get("reason") if isinstance(decision, Mapping) else None
    return elapsed, checkpoint, str(reason) if reason in _LEASE_REASONS else "none"


def _audit_projection(values: Mapping[str, Any]) -> tuple[int | str, list[str]]:
    raw = values.get("quality_audit_result")
    if not isinstance(raw, Mapping):
        loop = values.get("loop_policy")
        progress = loop.get("current_progress") if isinstance(loop, Mapping) else None
        score = progress.get("quality_score") if isinstance(progress, Mapping) else None
        return (
            max(0, min(100, int(float(score))))
            if isinstance(score, (int, float)) and not isinstance(score, bool)
            else "none",
            [],
        )
    audit = raw.get("audit")
    source = audit if isinstance(audit, Mapping) else raw
    score = source.get("total_score")
    quality: int | str = (
        max(0, min(100, int(float(score))))
        if isinstance(score, (int, float)) and not isinstance(score, bool)
        else "none"
    )
    failures = source.get("hard_failures", [])
    safe = (
        sorted({str(value) for value in failures if str(value) in _HARD_FAILURES})
        if isinstance(failures, list)
        else []
    )
    return quality, safe


def _budget_ratio(values: Mapping[str, Any]) -> int | str:
    raw = values.get("budget_ledger")
    if not isinstance(raw, Mapping):
        return "none"
    used = sum(
        _non_negative_int(raw.get(key))
        for key in ("committed_input_tokens", "committed_output_tokens")
    )
    maximum = sum(
        _non_negative_int(raw.get(key)) for key in ("max_input_tokens", "max_output_tokens")
    )
    return min(100, round(used * 100 / maximum)) if maximum else "none"


def _control(values: Mapping[str, Any]) -> tuple[str, str]:
    terminal = values.get("terminal_public")
    matrix = terminal.get("action_matrix") if isinstance(terminal, Mapping) else None
    if isinstance(matrix, list):
        for item in matrix:
            if (
                isinstance(item, Mapping)
                and item.get("enabled") is True
                and item.get("action_id") in _CONTROL_ACTIONS
            ):
                return str(item["action_id"]), "open"
    raw = values.get("control_command")
    if isinstance(raw, Mapping):
        action = raw.get("action")
        status = raw.get("status")
        return (
            str(action) if action in _CONTROL_ACTIONS else "none",
            str(status) if status in _CONTROL_STATUSES else "none",
        )
    # The model checkpoint atomically commits both the brief and dimensions.
    # Only completed node projections after that commit expose generate-now.
    if isinstance(values.get("research_brief"), Mapping) and isinstance(
        values.get("dimension_coverages"), list
    ):
        return "generate_now", "open"
    return "none", "none"


def _parent_operation(values: Mapping[str, Any]) -> str:
    lineage = values.get("operation_lineage")
    raw = lineage.get("parent_operation_id") if isinstance(lineage, Mapping) else None
    if raw is None:
        raw = values.get("parent_operation_id")
    if not isinstance(raw, str) or not raw:
        return "none"
    return "op_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _predicted_delivery(
    values: Mapping[str, Any], counts: Mapping[str, int], quality: int | str, hard: list[str]
) -> str:
    decision = values.get("delivery_decision")
    declared = decision.get("status") if isinstance(decision, Mapping) else values.get("delivery_status")
    if declared in _DELIVERY:
        return str(declared)
    core_total = counts["core_total"]
    covered = counts["core_covered"]
    partial = counts["core_partially_covered"]
    if core_total == 0:
        return "pending"
    if covered >= core_total and isinstance(quality, int) and quality >= 80 and not hard:
        return "completed"
    if covered + partial:
        return "partial"
    return "insufficient_evidence"


def _next_step(stage_id: str, values: Mapping[str, Any]) -> str:
    if stage_id == "gap_evaluate" and values.get("gap_evaluate_route") == "synth":
        return "rerank_evidence"
    if stage_id == "gap_join" and values.get("gap_join_route") == "synth":
        return "rerank_evidence"
    if stage_id in {"quality_audit", "repair_join"}:
        route = values.get("quality_route") if stage_id == "quality_audit" else values.get("repair_route")
        return {
            "repair_work": "repair_report",
            "persist": "persist_report",
            "insufficient_finalize": "finalize_insufficient",
            "quality_audit": "audit_quality",
        }.get(str(route), _DEFAULT_NEXT[stage_id])
    return _DEFAULT_NEXT[stage_id]


def _discarded_count(stage_id: str, values: Mapping[str, Any]) -> int | str:
    result = values.get(f"{stage_id}_result")
    if stage_id == "gap_work":
        result = values.get("gap_work_result")
    if stage_id == "quality_audit":
        result = values.get("quality_audit_result")
    if not isinstance(result, Mapping):
        return "none"
    for key in ("discarded", "dropped", "invalid_count", "rejected_count"):
        value = result.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return "none"


def _safe_rejection_code(value: object) -> str:
    raw = str(value or "")
    if raw.startswith(("invalid_page:", "invalid_reason:")):
        return "invalid_page"
    return raw if raw in _SAFE_REJECTION_CODES else "other_rejected"


def _failure_projection(
    values: Mapping[str, Any],
    ordered: list[str],
    coverages: list[Mapping[str, Any]],
) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    ordinals = {dimension_id: index for index, dimension_id in enumerate(ordered)}
    failed: list[dict[str, JsonValue]] = []
    for item in coverages:
        status = str(item.get("status") or "")
        if status not in {"uncovered", "partially_covered"}:
            continue
        dimension_id = str(item.get("dimension_id") or "")
        if dimension_id not in ordinals:
            continue
        raw_reasons = item.get("gap_reasons")
        reason_codes = []
        if isinstance(raw_reasons, list):
            reason_codes = [
                str(reason) if str(reason) in _SAFE_GAP_CODES else "evidence_gap"
                for reason in raw_reasons
            ]
        failed.append(
            {
                "dimension_ordinal": ordinals[dimension_id],
                "status": status,
                "reason_codes": list(dict.fromkeys(reason_codes or ["evidence_gap"]))[:6],
            }
        )
    raw_counts = values.get("rejection_reason_counts")
    if not isinstance(raw_counts, Mapping):
        raw_counts = {}
    safe_counts: dict[str, int] = {}
    for reason, count in raw_counts.items():
        if isinstance(count, bool) or not isinstance(count, (int, float)) or count <= 0:
            continue
        code = _safe_rejection_code(reason)
        safe_counts[code] = safe_counts.get(code, 0) + int(count)
    rejection = [
        {"reason_code": code, "count": count}
        for code, count in sorted(safe_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    return failed[:8], rejection


def build_v5_stage_projection(
    state: WorkflowState | Mapping[str, Any],
    *,
    stage_id: str,
    previous_state: WorkflowState | Mapping[str, Any] | None = None,
) -> dict[str, JsonValue]:
    """Build the only state-to-user projection accepted by the v5 reporter."""

    if stage_id not in NODE_IDS:
        raise V5ProgressProjectionError("unknown_stage")
    values = _values(state)
    coverages = _coverages(values)
    counts, ordered, core = _dimension_counts(values, coverages)
    changes = _status_changes(coverages, previous_state)
    elapsed, checkpoint, lease_reason = _loop_projection(values)
    quality, hard = _audit_projection(values)
    action, status = _control(values)
    core_gaps = sum(
        1
        for item in coverages
        if (not core or str(item["dimension_id"]) in core)
        and item["status"] in {"uncovered", "partially_covered"}
    )
    remaining_gap: int | str = core_gaps if coverages else "none"
    result = "insufficient_evidence" if stage_id == "insufficient_finalize" else "completed"
    failed_dimensions, rejection_reasons = _failure_projection(
        values, ordered, coverages
    )
    return {
        "stage_id": stage_id,
        "public_stage_id": _PUBLIC_STAGE[stage_id],
        "action": _ACTION[stage_id],
        "result": result,
        "discarded": _discarded_count(stage_id, values),
        "remaining_gap": remaining_gap,
        "next_step": _next_step(stage_id, values),
        "dimension_counts": counts,
        "dimension_status_changes": changes,
        "source_counts": _source_counts(values, coverages),
        "active_gap": _active_gap(values, ordered),
        "elapsed_seconds": elapsed,
        "soft_checkpoint": checkpoint,
        "lease_reason": lease_reason,
        "quality_score": quality,
        "hard_failures": hard,
        "predicted_delivery": _predicted_delivery(values, counts, quality, hard),
        "token_budget_ratio": _budget_ratio(values),
        "control_action": action,
        "control_status": status,
        "parent_operation": _parent_operation(values),
        "failed_dimensions": failed_dimensions,
        "rejection_reasons": rejection_reasons,
    }


def build_v5_transition_projection(stage_id: str, transition: str) -> dict[str, JsonValue]:
    """Return a state-free projection for lifecycle events without regressible totals."""

    if stage_id not in NODE_IDS or transition not in {
        "started", "waiting", "failed", "cancelled", "completed"
    }:
        raise V5ProgressProjectionError("invalid_transition")
    return {
        "stage_id": stage_id,
        "public_stage_id": _PUBLIC_STAGE[stage_id],
        "action": _ACTION[stage_id],
        "result": transition,
        "discarded": "none",
        "remaining_gap": "none",
        "next_step": _DEFAULT_NEXT[stage_id],
        "dimension_counts": {
            "total": 0, "core_total": 0, "covered": 0,
            "partially_covered": 0, "uncovered": 0, "not_applicable": 0,
            "core_covered": 0, "core_partially_covered": 0, "core_uncovered": 0,
        },
        "dimension_status_changes": {
            "improved": "none", "regressed": "none", "unchanged": "none",
        },
        "source_counts": {"valid": 0, "first_party": 0},
        "active_gap": "none",
        "elapsed_seconds": 0,
        "soft_checkpoint": "none",
        "lease_reason": "none",
        "quality_score": "none",
        "hard_failures": [],
        "predicted_delivery": "pending",
        "token_budget_ratio": "none",
        "control_action": "none",
        "control_status": "none",
        "parent_operation": "none",
        "failed_dimensions": [],
        "rejection_reasons": [],
    }


__all__ = [
    "CAPABILITY",
    "NODE_IDS",
    "PUBLIC_STAGE_IDS",
    "SCHEMA_VERSION",
    "V5ProgressProjectionError",
    "build_v5_stage_projection",
    "build_v5_transition_projection",
]
