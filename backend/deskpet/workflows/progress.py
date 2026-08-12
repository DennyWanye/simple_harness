# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""User-safe progress projection for durable workflows."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from .contracts import JsonValue, NodeExecutionIdentity
from .definitions.deep_research_v5_progress import (
    CAPABILITY as DEEP_RESEARCH_PROGRESS_V5_CAPABILITY,
    NODE_IDS as DEEP_RESEARCH_V5_NODE_IDS,
    PUBLIC_STAGE_IDS as DEEP_RESEARCH_V5_PUBLIC_STAGE_IDS,
    SCHEMA_VERSION as DEEP_RESEARCH_PROGRESS_V5_SCHEMA_VERSION,
    V5ProgressProjectionError,
    build_v5_transition_projection,
)


logger = logging.getLogger(__name__)

ProgressTransition = Literal["started", "waiting", "failed", "cancelled", "completed"]
_TRANSITIONS = frozenset({"started", "waiting", "failed", "cancelled"})


@dataclass(frozen=True)
class PublicWorkflowStage:
    node_id: str
    ordinal: int
    total: int
    label: str


def _stages(total: int, values: Sequence[tuple[str, int, str]]) -> Mapping[str, PublicWorkflowStage]:
    return MappingProxyType(
        {
            node_id: PublicWorkflowStage(node_id, ordinal, total, label)
            for node_id, ordinal, label in values
        }
    )


PUBLIC_WORKFLOW_STAGES: Mapping[str, Mapping[str, PublicWorkflowStage]] = MappingProxyType(
    {
        "deep_research": _stages(
            7,
            (
                ("normalize", 1, "理解任务"),
                ("plan", 2, "规划调研"),
                ("search", 3, "搜索资料"),
                ("synth", 4, "整理研究结论"),
                ("cite", 5, "检查引用"),
                ("persist", 6, "保存研究报告"),
                ("finalize", 7, "准备交付"),
            ),
        ),
        "ppt_pro": _stages(
            12,
            (
                ("normalize", 1, "理解 PPT 需求"),
                ("research_plan", 2, "规划调研"),
                ("research_search", 3, "收集资料"),
                ("research_synth", 4, "整理研究内容"),
                ("outline", 5, "生成 PPT 大纲"),
                ("wait_outline_decision", 6, "等待确认大纲"),
                ("revise_outline", 6, "根据反馈修改大纲"),
                ("preflight", 7, "检查生成环境"),
                ("image_map", 8, "生成完整页面"),
                ("render", 9, "组装 PPT 文件"),
                ("preview", 10, "生成预览"),
                ("visual_evaluate", 11, "检查页面质量"),
                ("visual_revise", 11, "修订问题页面"),
                ("publish", 12, "发布 PPT"),
            ),
        ),
        "code_complex": _stages(
            9,
            (
                ("intake", 1, "理解代码任务"),
                ("clarify", 2, "确认需求"),
                ("plan", 3, "制定执行计划"),
                ("wait_approval", 4, "等待计划确认"),
                ("llm_proposal", 5, "准备代码修改"),
                ("tool_execution", 6, "执行代码修改"),
                ("test", 7, "运行测试"),
                ("audit", 8, "完成质量审计"),
                ("finalize", 9, "准备交付"),
            ),
        ),
        "durable_task": _stages(
            9,
            (
                ("intake", 1, "理解任务"),
                ("clarify", 2, "确认需求"),
                ("plan", 3, "制定执行计划"),
                ("wait_approval", 4, "等待计划确认"),
                ("llm_proposal", 5, "规划下一步"),
                ("tool_execution", 6, "执行操作"),
                ("test", 7, "验证结果"),
                ("audit", 8, "完成质量检查"),
                ("finalize", 9, "准备交付"),
            ),
        ),
    }
)

_WORKFLOW_LABELS = MappingProxyType(
    {
        "deep_research": "深度调研",
        "ppt_pro": "PPT",
        "code_complex": "代码任务",
        "durable_task": "多步骤任务",
    }
)

_V2_STAGE_VALUES = (
    ("normalize", 1, "理解任务"), ("plan", 2, "规划调研"),
    ("expand", 3, "扩展查询"), ("search", 4, "搜索资料"),
    ("direct", 5, "补充一手来源"), ("fetch", 6, "抓取正文"),
    ("score", 7, "筛选证据"), ("gap", 8, "检查证据缺口"),
    ("rerank", 9, "全局整理证据"), ("synth", 10, "撰写报告"),
    ("cite", 11, "核验论断与引用"), ("persist", 12, "保存报告"),
    ("finalize", 13, "准备交付"),
)
DEEP_RESEARCH_V2_STAGES = _stages(13, _V2_STAGE_VALUES)
DEEP_RESEARCH_STAGE_VERSIONS = frozenset({"v2", "v3", "v4"})
DEEP_RESEARCH_V7_STAGES = _stages(
    6,
    (
        ("normalize", 1, "理解调研方向"),
        ("plan", 2, "拆分子方向"),
        ("search", 3, "子代理调研与补救"),
        ("synth", 4, "综合调研结果"),
        ("persist", 5, "保存研究报告"),
        ("finalize", 6, "准备交付"),
    ),
)
DEEP_RESEARCH_V7_CHILD_STATUSES = frozenset(
    {"queued", "running", "retrying", "valid", "insufficient"}
)
DEEP_RESEARCH_V5_STAGES = _stages(
    len(DEEP_RESEARCH_V5_PUBLIC_STAGE_IDS),
    tuple(
        (stage_id, index + 1, label)
        for index, (stage_id, label) in enumerate(
            (
                ("normalize", "理解任务"),
                ("plan", "规划调研"),
                ("research", "收集证据"),
                ("gap", "补充证据缺口"),
                ("rerank", "整理证据"),
                ("synth", "撰写报告"),
                ("quality", "审计报告质量"),
                ("persist", "保存报告"),
                ("finalize", "准备交付"),
            )
        )
    ),
)
DEEP_RESEARCH_TERMINAL_METRIC_KEYS = frozenset({
    "actual_requests", "hits", "empty", "timeouts", "cooldown_skips",
    "busy_skips", "queue_timeouts", "probes", "rescue_considered_count",
    "rescue_executed_count", "candidates",
})
DEEP_RESEARCH_SKIPPED_STAGE_IDS = frozenset({
    "fetch", "score", "gap", "rerank", "synth", "cite", "persist",
})
DEEP_RESEARCH_RETRY_ACTION_IDS = frozenset({"retry_from_start"})
_DEEP_RESEARCH_METRIC_KEYS = MappingProxyType(
    {
        "normalize": frozenset({"mode"}),
        "plan": frozenset({"question_count", "active_branch_count"}),
        "expand": frozenset({"query_count"}),
        "search": frozenset({
            "providers", "providers_attempted", "providers_hit", "candidates", "kept",
            *DEEP_RESEARCH_TERMINAL_METRIC_KEYS,
        }),
        "direct": frozenset({"direct_sources", "candidates"}),
        "fetch": frozenset({"attempted", "succeeded", "dropped"}),
        "score": frozenset({"passages", "kept"}),
        "gap": frozenset({"iteration", "followup_count", "new_evidence"}),
        "rerank": frozenset({"passages", "domains"}),
        "synth": frozenset({"sections", "claim_count"}),
        "cite": frozenset({
            "citations", "domains", "supported", "unsupported", "support_rate",
            "factual_claims_pre_repair", "supported_factual", "published", "discarded",
            "repaired", "body_bytes",
        }),
        "persist": frozenset({"artifact_count", "report_bytes", "status"}),
        "finalize": frozenset({"citations", "status", "published", "discarded", "repaired"}),
    }
)

_STAGE_ACTIONS = MappingProxyType(
    {
        "normalize": "确认调研主题与深度",
        "plan": "拆分可独立核验的问题",
        "expand": "生成可检索查询",
        "search": "搜索可用资料来源",
        "direct": "补充官方和一手来源",
        "fetch": "抓取并提取来源正文",
        "score": "筛选相关证据段落",
        "gap": "检查证据缺口",
        "rerank": "整理并去重证据",
        "synth": "基于证据生成原子论断",
        "cite": "核验论断与引用",
        "persist": "保存调研报告",
        "finalize": "整理最终交付",
    }
)

_RESULT_CODES = frozenset({
    "stage_ok", "stage_degraded", "success", "partial", "degraded", "completed",
    "insufficient_evidence", "no_results",
})
_DIAGNOSTIC_CODES = frozenset({
    "cooldown", "half_open_busy", "queue_timeout", "timeout", "blocked", "captcha", "rate_limit",
    "http_error", "invalid_response", "parse_error", "other", "budget_exhausted", "provider_degraded",
    "partial_results", "evidence_missing", "low_quality_evidence", "missing_exact_token",
    "insufficient_support", "claim_unsupported", "claim_pruned", "deterministic_repair",
    "insufficient_evidence", "artifact_missing", "no_results", "degraded",
    "deadline_exhausted", "search_port_unavailable", "provider_failure",
    "direct_failure", "fetch_failure", "blob_unavailable", "low_quality_source",
    "low_quality_content", "search_degraded", "support_rate_below_threshold",
    "published_factual_below_threshold", "citation_count_below_threshold",
    "domain_count_below_threshold", "body_bytes_below_threshold",
})
DEEP_RESEARCH_DIAGNOSTIC_CODES = _DIAGNOSTIC_CODES


def _is_deep_research_stage_contract(workflow_name: str, workflow_version: str | None) -> bool:
    return workflow_name == "deep_research" and workflow_version in DEEP_RESEARCH_STAGE_VERSIONS


def _is_deep_research_v5(workflow_name: str, workflow_version: str | None) -> bool:
    return workflow_name == "deep_research" and workflow_version == "v5"


def _is_deep_research_v7(workflow_name: str, workflow_version: str | None) -> bool:
    return workflow_name == "deep_research" and workflow_version == "v7"


def _number(metrics: Mapping[str, JsonValue], key: str) -> int | float | None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _stage_result(stage_id: str, metrics: Mapping[str, JsonValue]) -> str:
    """Render one bounded, user-readable result without exposing raw key/value data."""

    def n(key: str, fallback: int | float = 0) -> int | float:
        return _number(metrics, key) if _number(metrics, key) is not None else fallback

    if stage_id == "normalize":
        return f"已确认 {str(metrics.get('mode') or 'standard')} 调研模式"
    if stage_id == "plan":
        return f"拆分 {n('question_count')} 个问题，启用 {n('active_branch_count')} 个分支"
    if stage_id == "expand":
        return f"生成 {n('query_count')} 条检索查询"
    if stage_id == "search":
        if any(
            key in metrics
            for key in (
                "actual_requests", "hits", "empty", "timeouts", "cooldown_skips",
                "busy_skips", "queue_timeouts", "probes", "rescue_considered_count",
                "rescue_executed_count",
            )
        ):
            return (
                f"真实请求 {n('actual_requests')} / 命中 {n('hits')} / 空结果 {n('empty')} / "
                f"超时 {n('timeouts')} / cooldown 跳过 {n('cooldown_skips')} / "
                f"busy 跳过 {n('busy_skips')} / 排队超时 {n('queue_timeouts')} / "
                f"probe {n('probes')}，候选 {n('candidates')}"
            )
        providers = n("providers_attempted", n("providers"))
        candidates = n("candidates")
        kept = n("kept", candidates)
        return f"尝试 {providers} 个来源，找到 {candidates} 条候选，保留 {kept} 条"
    if stage_id == "direct":
        return f"补充 {n('direct_sources')} 个一手来源，累计 {n('candidates')} 条候选"
    if stage_id == "fetch":
        return f"抓取 {n('attempted')} 条，成功 {n('succeeded')} 条，丢弃 {n('dropped')} 条"
    if stage_id == "score":
        return f"评估 {n('passages')} 个段落，保留 {n('kept')} 个证据段落"
    if stage_id == "gap":
        return f"第 {n('iteration')} 轮检查，补充 {n('followup_count')} 条查询和 {n('new_evidence')} 条证据"
    if stage_id == "rerank":
        return f"整理 {n('passages')} 个证据段落，覆盖 {n('domains')} 个独立域名"
    if stage_id == "synth":
        return f"形成 {n('claim_count')} 条原子论断"
    if stage_id == "cite":
        supported = n("supported_factual", n("supported"))
        discarded = n("discarded", n("unsupported"))
        repaired = n("repaired")
        raw_rate = _number(metrics, "support_rate")
        rate = f"{float(raw_rate) * 100:.0f}%" if raw_rate is not None else "待统计"
        return f"发布 {n('published', supported)} 条，丢弃 {discarded} 条，修复 {repaired} 条，支持率 {rate}"
    if stage_id == "persist":
        return f"保存 {n('artifact_count')} 个报告产物，共 {n('report_bytes')} 字节"
    if stage_id == "finalize":
        return f"交付状态：{str(metrics.get('status') or 'completed')}，引用 {n('citations')} 条"
    return "阶段处理完成"


class ProgressPayloadError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_V5_PROJECTION_KEYS = frozenset({
    "stage_id", "public_stage_id", "action", "result", "discarded",
    "remaining_gap", "next_step", "dimension_counts",
    "dimension_status_changes", "source_counts", "active_gap",
    "elapsed_seconds", "soft_checkpoint", "lease_reason", "quality_score",
    "hard_failures", "predicted_delivery", "token_budget_ratio",
    "control_action", "control_status", "parent_operation",
    "failed_dimensions", "rejection_reasons",
})
_V5_PAYLOAD_META_KEYS = frozenset({
    "schema_version", "capability", "kind", "workflow_name", "workflow_version",
    "workflow_label", "stage_instance_id", "stage", "ordinal", "total",
    "status", "summary", "text", "visibility",
})
_V5_RESULTS = frozenset({
    "started", "waiting", "failed", "cancelled", "completed",
    "insufficient_evidence",
})
_V5_ACTIONS = frozenset({
    "normalize_request", "model_dimensions", "plan_queries", "expand_queries",
    "search_sources", "find_first_party_sources", "fetch_sources", "score_evidence",
    "evaluate_gaps", "research_gap", "commit_gap_result", "rerank_evidence",
    "synthesize_report", "audit_quality", "repair_report", "commit_repair",
    "persist_report", "finalize_delivery", "finalize_insufficient",
})
_V5_NEXT_STEPS = frozenset({"none", *_V5_ACTIONS})
_V5_LEASE_REASONS = frozenset({
    "none", "before_soft_or_lease_checkpoint", "lease_renewed_measurable_gain",
    "lease_not_renewed_no_gain", "automatic_cap", "running",
    "generate_now_settling", "cancelled", "plateau_settling",
    "lease_no_gain_settling", "automatic_cap_settling",
})


def _validate_v5_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != _V5_PROJECTION_KEYS | _V5_PAYLOAD_META_KEYS:
        raise ProgressPayloadError("workflow_progress_v5_fields")
    if (
        payload.get("schema_version") != DEEP_RESEARCH_PROGRESS_V5_SCHEMA_VERSION
        or payload.get("capability") != DEEP_RESEARCH_PROGRESS_V5_CAPABILITY
        or payload.get("workflow_name") != "deep_research"
        or payload.get("workflow_version") != "v5"
        or payload.get("kind") not in {"stage", "progress"}
        or payload.get("status") not in {"started", "waiting", "failed", "cancelled", "completed"}
    ):
        raise ProgressPayloadError("workflow_progress_v5_identity")
    if (
        (payload.get("kind") == "stage" and payload.get("status") != "completed")
        or (payload.get("kind") == "progress" and payload.get("status") not in _TRANSITIONS)
        or payload.get("workflow_label") != "深度调研"
    ):
        raise ProgressPayloadError("workflow_progress_v5_identity")
    stage_id = payload.get("stage_id")
    public_stage_id = payload.get("public_stage_id")
    stage = DEEP_RESEARCH_V5_STAGES.get(str(public_stage_id))
    if stage_id not in DEEP_RESEARCH_V5_NODE_IDS or stage is None:
        raise ProgressPayloadError("workflow_progress_v5_stage")
    if payload.get("result") not in _V5_RESULTS:
        raise ProgressPayloadError("workflow_progress_v5_result")
    if payload.get("action") not in _V5_ACTIONS or payload.get("next_step") not in _V5_NEXT_STEPS:
        raise ProgressPayloadError("workflow_progress_v5_summary_enum")
    if payload.get("visibility") not in {"hidden", "visible"}:
        raise ProgressPayloadError("workflow_progress_v5_visibility")
    expected_visible = (
        payload.get("status") in {"waiting", "failed", "cancelled"}
        or public_stage_id == "finalize"
        or payload.get("control_action") != "none"
    )
    if payload.get("visibility") != ("visible" if expected_visible else "hidden"):
        raise ProgressPayloadError("workflow_progress_v5_visibility")
    for key in ("stage_instance_id", "stage", "summary", "text", "action", "next_step"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 120:
            raise ProgressPayloadError("workflow_progress_v5_text")
    if payload["text"] != payload["summary"]:
        raise ProgressPayloadError("workflow_progress_v5_text")
    expected_summary = {
        "completed": f"{stage.label}已完成",
        "waiting": f"{stage.label}正在等待操作",
        "failed": f"{stage.label}未完成",
        "cancelled": f"{stage.label}已取消",
        "started": f"正在{stage.label}",
    }[str(payload["status"])]
    instance = payload.get("stage_instance_id")
    if (
        payload.get("stage") != stage.label
        or payload.get("ordinal") != stage.ordinal
        or payload.get("total") != stage.total
        or payload.get("summary") != expected_summary
        or not isinstance(instance, str)
        or len(instance) != 24
        or any(char not in "0123456789abcdef" for char in instance)
    ):
        raise ProgressPayloadError("workflow_progress_v5_text")
    for key in ("ordinal", "total", "elapsed_seconds"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProgressPayloadError("workflow_progress_v5_count")
    for key in ("discarded", "remaining_gap", "quality_score", "token_budget_ratio"):
        value = payload.get(key)
        if value != "none" and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ProgressPayloadError("workflow_progress_v5_count")
    if any(
        isinstance(payload.get(key), int) and int(payload[key]) > 100
        for key in ("quality_score", "token_budget_ratio")
    ):
        raise ProgressPayloadError("workflow_progress_v5_count")
    dimensions = payload.get("dimension_counts")
    if not isinstance(dimensions, Mapping) or set(dimensions) != {
        "total", "core_total", "covered", "partially_covered", "uncovered",
        "not_applicable", "core_covered", "core_partially_covered", "core_uncovered",
    } or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in dimensions.values()):
        raise ProgressPayloadError("workflow_progress_v5_dimensions")
    changes = payload.get("dimension_status_changes")
    if not isinstance(changes, Mapping) or set(changes) != {"improved", "regressed", "unchanged"} or any(
        value != "none" and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
        for value in changes.values()
    ):
        raise ProgressPayloadError("workflow_progress_v5_dimension_changes")
    sources = payload.get("source_counts")
    if not isinstance(sources, Mapping) or set(sources) != {"valid", "first_party"} or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in sources.values()
    ):
        raise ProgressPayloadError("workflow_progress_v5_sources")
    gap = payload.get("active_gap")
    if gap != "none" and (
        not isinstance(gap, Mapping)
        or set(gap) != {"status", "work_kind", "dimension_ordinal"}
        or gap.get("status") not in {"pending", "running", "completed", "failed", "cancelled"}
        or gap.get("work_kind") not in {"query", "source_target", "fetch"}
        or isinstance(gap.get("dimension_ordinal"), bool)
        or not isinstance(gap.get("dimension_ordinal"), int)
        or int(gap["dimension_ordinal"]) < 0
    ):
        raise ProgressPayloadError("workflow_progress_v5_gap")
    hard = payload.get("hard_failures")
    if not isinstance(hard, list) or any(
        value not in {
            "completed_core_uncovered", "unsupported_key_claim",
            "invalid_or_captcha_citation", "secondary_replaces_available_official",
            "citation_dimension_mismatch", "internal_diagnostics_leak",
        }
        for value in hard
    ):
        raise ProgressPayloadError("workflow_progress_v5_quality")
    failed_dimensions = payload.get("failed_dimensions")
    safe_gap_codes = {
        "insufficient_admitted_passages", "insufficient_strong_distinct_families",
        "winning_relevance_below_threshold", "duplicate_rate_above_threshold",
        "invalid_rate_above_threshold", "first_party_requirement_unsatisfied",
        "evidence_gap",
    }
    if not isinstance(failed_dimensions, list) or len(failed_dimensions) > 8:
        raise ProgressPayloadError("workflow_progress_v5_failures")
    for item in failed_dimensions:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"dimension_ordinal", "status", "reason_codes"}
            or isinstance(item.get("dimension_ordinal"), bool)
            or not isinstance(item.get("dimension_ordinal"), int)
            or int(item["dimension_ordinal"]) < 0
            or item.get("status") not in {"uncovered", "partially_covered"}
            or not isinstance(item.get("reason_codes"), list)
            or not 1 <= len(item["reason_codes"]) <= 6
            or any(reason not in safe_gap_codes for reason in item["reason_codes"])
        ):
            raise ProgressPayloadError("workflow_progress_v5_failures")
    rejection_reasons = payload.get("rejection_reasons")
    safe_rejections = {
        "invalid_page", "body_too_short", "body_span_missing",
        "dimension_relevance_below_threshold", "other_rejected",
    }
    if not isinstance(rejection_reasons, list) or len(rejection_reasons) > 5:
        raise ProgressPayloadError("workflow_progress_v5_rejections")
    for item in rejection_reasons:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"reason_code", "count"}
            or item.get("reason_code") not in safe_rejections
            or isinstance(item.get("count"), bool)
            or not isinstance(item.get("count"), int)
            or int(item["count"]) < 1
        ):
            raise ProgressPayloadError("workflow_progress_v5_rejections")
    if payload.get("predicted_delivery") not in {
        "pending", "completed", "partial", "insufficient_evidence"
    }:
        raise ProgressPayloadError("workflow_progress_v5_delivery")
    if payload.get("soft_checkpoint") not in {"none", "before", "reached"}:
        raise ProgressPayloadError("workflow_progress_v5_checkpoint")
    if payload.get("lease_reason") not in _V5_LEASE_REASONS:
        raise ProgressPayloadError("workflow_progress_v5_lease")
    if payload.get("control_action") not in {
        "none", "generate_now", "continue_research", "retry_from_start", "cancel_settle"
    } or payload.get("control_status") not in {
        "none", "open", "accepted", "observed", "settled", "consumed", "rejected", "expired"
    }:
        raise ProgressPayloadError("workflow_progress_v5_control")
    parent = payload.get("parent_operation")
    if parent != "none" and (
        not isinstance(parent, str) or len(parent) != 23 or not parent.startswith("op_")
    ):
        raise ProgressPayloadError("workflow_progress_v5_parent")


def validated_v2_stage_text(payload: Mapping[str, Any]) -> str:
    """Return SessionDB-safe text for a v2/v3 summary or completed-stage payload.

    The historical name is retained because product delivery imports it. The
    contract is selected by workflow name + schema + public stage allowlist;
    workflow v1 remains summary-only.
    """

    if payload.get("schema_version") == DEEP_RESEARCH_PROGRESS_V5_SCHEMA_VERSION:
        _validate_v5_payload(payload)
        return str(payload["text"])

    kind = payload.get("kind")
    status = payload.get("status")
    if payload.get("schema_version") != 2 or not _is_deep_research_stage_contract(
        str(payload.get("workflow_name") or ""), str(payload.get("workflow_version") or "")
    ) or (
        (kind == "stage" and status != "completed")
        or (kind == "progress" and status not in _TRANSITIONS)
        or kind not in {"stage", "progress"}
    ):
        raise ProgressPayloadError("workflow_stage_payload_not_completed_v2")
    if str(payload.get("stage_id") or "") not in DEEP_RESEARCH_V2_STAGES:
        raise ProgressPayloadError("workflow_stage_payload_unknown_stage")
    text = payload.get("text")
    summary = payload.get("summary")
    if not isinstance(text, str) or not text.strip() or (
        kind == "stage" and text != summary
    ):
        raise ProgressPayloadError("workflow_stage_payload_text_mismatch")
    return text


def public_stage_for(
    workflow_name: str,
    node_id: str,
    workflow_version: str | None = None,
) -> PublicWorkflowStage | None:
    if _is_deep_research_v5(workflow_name, workflow_version):
        try:
            projection = build_v5_transition_projection(node_id, "started")
        except V5ProgressProjectionError:
            return None
        return DEEP_RESEARCH_V5_STAGES.get(str(projection["public_stage_id"]))
    if _is_deep_research_stage_contract(workflow_name, workflow_version):
        stage_id = node_id[:-5] if node_id.endswith("_join") else node_id
        return DEEP_RESEARCH_V2_STAGES.get(stage_id)
    if _is_deep_research_v7(workflow_name, workflow_version):
        return DEEP_RESEARCH_V7_STAGES.get(node_id)
    stages = PUBLIC_WORKFLOW_STAGES.get(workflow_name)
    return stages.get(node_id) if stages is not None else None


def _progress_text(
    workflow_label: str,
    stage: PublicWorkflowStage,
    transition: ProgressTransition,
) -> str:
    position = f"（{stage.ordinal}/{stage.total}）"
    if transition == "started":
        return f"{workflow_label}进度：{stage.label}{position}"
    if transition == "waiting":
        return f"{workflow_label}进度：{stage.label}，等待你的操作{position}"
    if transition == "failed":
        return f"{workflow_label}进度：{stage.label}未完成，请稍后重试或查看任务状态{position}"
    return f"{workflow_label}已取消：{stage.label}{position}"


def _v5_stage_instance(identity: NodeExecutionIdentity) -> tuple[str, str] | None:
    if not identity.activation_id or not identity.invocation_key:
        return None
    stable = f"v5:{identity.activation_id}:{identity.invocation_key}"
    return stable, hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _v5_payload(
    identity: NodeExecutionIdentity,
    projection: Mapping[str, Any],
    *,
    kind: str,
    status: str,
) -> dict[str, JsonValue] | None:
    stable = _v5_stage_instance(identity)
    if stable is None or set(projection) != _V5_PROJECTION_KEYS:
        return None
    stage_id = str(projection.get("stage_id") or "")
    if stage_id != identity.node_id:
        return None
    public_stage_id = str(projection.get("public_stage_id") or "")
    stage = DEEP_RESEARCH_V5_STAGES.get(public_stage_id)
    if stage is None:
        return None
    visible = (
        status in {"waiting", "failed", "cancelled"}
        or public_stage_id == "finalize"
        or projection.get("control_action") != "none"
    )
    if status == "completed":
        summary = f"{stage.label}已完成"
    elif status == "waiting":
        summary = f"{stage.label}正在等待操作"
    elif status == "failed":
        summary = f"{stage.label}未完成"
    elif status == "cancelled":
        summary = f"{stage.label}已取消"
    else:
        summary = f"正在{stage.label}"
    payload: dict[str, JsonValue] = {
        "schema_version": DEEP_RESEARCH_PROGRESS_V5_SCHEMA_VERSION,
        "capability": DEEP_RESEARCH_PROGRESS_V5_CAPABILITY,
        "kind": kind,
        "workflow_name": "deep_research",
        "workflow_version": "v5",
        "workflow_label": "深度调研",
        "stage_instance_id": stable[1],
        "stage": stage.label,
        "ordinal": stage.ordinal,
        "total": stage.total,
        "status": status,
        "summary": summary,
        "text": summary,
        "visibility": "visible" if visible else "hidden",
        **copy.deepcopy(dict(projection)),
    }
    try:
        _validate_v5_payload(payload)
    except ProgressPayloadError:
        return None
    return payload


class WorkflowProgressReporter:
    """Persist and deliver fixed, user-safe workflow progress events."""

    def __init__(self, service: Any, targets: Sequence[tuple[str, str]], notify_dispatcher: Any = None) -> None:
        self._service = service
        self._targets = tuple((str(channel), str(target)) for channel, target in targets)
        self._notify_dispatcher = notify_dispatcher

    @property
    def targets(self) -> tuple[tuple[str, str], ...]:
        return self._targets

    def notify_dispatcher(self) -> None:
        if callable(self._notify_dispatcher):
            self._notify_dispatcher()

    def build_completion_intent(
        self,
        identity: NodeExecutionIdentity,
        frozen_projection: Mapping[str, Any],
    ) -> dict[str, JsonValue] | None:
        """Purely build one v2 stage intent; persistence belongs to commit_frontier."""

        if _is_deep_research_v5(identity.workflow_name, identity.workflow_version):
            payload = _v5_payload(
                identity, frozen_projection, kind="stage", status="completed"
            )
            stable = _v5_stage_instance(identity)
            if payload is None or stable is None:
                logger.warning(
                    "workflow_progress_unsafe_v5_projection",
                    extra={"node_id": identity.node_id},
                )
                return None
            stable_identity = f"v5:{stable[1]}:completed"
            return {
                "intent_id": f"{identity.run_id}:{stable_identity}",
                "event_key": f"progress:{stable_identity}",
                "event_type": "workflow.progress",
                "channel": "progress",
                "payload": payload,
                "deliveries": [
                    {"channel": channel, "target_id": target_id}
                    for channel, target_id in self._targets
                ],
            }
        if not _is_deep_research_stage_contract(identity.workflow_name, identity.workflow_version):
            return None
        stage_id = str(frozen_projection.get("stage_id") or "")
        stage = DEEP_RESEARCH_V2_STAGES.get(stage_id)
        if stage is None:
            logger.warning("workflow_progress_unknown_v2_stage", extra={"stage_id": stage_id})
            return None
        raw_metrics = frozen_projection.get("metrics", {})
        if not isinstance(raw_metrics, Mapping):
            return None
        allowed = _DEEP_RESEARCH_METRIC_KEYS[stage_id]
        if any(str(key) not in allowed for key in raw_metrics):
            logger.warning("workflow_progress_unsafe_metrics", extra={"stage_id": stage_id})
            return None
        metrics = {str(key): copy.deepcopy(value) for key, value in raw_metrics.items()}
        duration_ms = max(0, int(frozen_projection.get("duration_ms") or 0))
        completed_count = max(0, min(stage.total, int(frozen_projection.get("completed_count") or 0)))
        degraded = bool(frozen_projection.get("degraded", False))
        action = _STAGE_ACTIONS[stage_id]
        result = _stage_result(stage_id, metrics)
        raw_result_code = str(frozen_projection.get("result_code") or "")
        result_code = raw_result_code if raw_result_code in _RESULT_CODES else (
            "stage_degraded" if degraded else "stage_ok"
        )
        raw_diagnostics = frozen_projection.get("diagnostic_codes", [])
        diagnostics = []
        if isinstance(raw_diagnostics, Sequence) and not isinstance(raw_diagnostics, (str, bytes)):
            diagnostics = sorted({
                str(value) for value in raw_diagnostics
                if str(value) in _DIAGNOSTIC_CODES
            })
        summary = f"{stage.label}已完成：{result}"
        if diagnostics:
            summary += "（存在降级或限制）"
        stable_identity = f"v2:{identity.node_id}:{identity.task_id}:completed"
        stage_instance_id = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:24]
        payload: dict[str, JsonValue] = {
            "schema_version": 2,
            "kind": "stage",
            "workflow_name": "deep_research",
            "workflow_version": identity.workflow_version,
            "workflow_label": "深度调研",
            "stage_id": stage_id,
            "stage_instance_id": stage_instance_id,
            "stage": stage.label,
            "ordinal": stage.ordinal,
            "total": stage.total,
            "status": "completed",
            "summary": summary,
            "text": summary,
            "action": action,
            "result": result,
            "result_code": result_code,
            "diagnostic_codes": diagnostics,
            "published": int(_number(metrics, "published") or 0),
            "discarded": int(_number(metrics, "discarded") or _number(metrics, "unsupported") or 0),
            "repaired": int(_number(metrics, "repaired") or 0),
            "completed_count": completed_count,
            "metrics": metrics,
            "duration_ms": duration_ms,
            "degraded": degraded,
            "next_stage": str(frozen_projection.get("next_stage") or ""),
        }
        return {
            "intent_id": f"{identity.run_id}:{stable_identity}",
            "event_key": f"progress:{stable_identity}",
            "event_type": "workflow.progress",
            "channel": "progress",
            "payload": payload,
            "deliveries": [
                {"channel": channel, "target_id": target_id}
                for channel, target_id in self._targets
            ],
        }

    async def report(
        self,
        identity: NodeExecutionIdentity,
        transition: ProgressTransition | str,
    ) -> str | None:
        stage = public_stage_for(
            identity.workflow_name,
            identity.node_id,
            identity.workflow_version,
        )
        if stage is None or transition not in _TRANSITIONS:
            return None
        normalized_transition = transition  # narrowed by the fixed allowlist above
        workflow_label = _WORKFLOW_LABELS[identity.workflow_name]
        payload: dict[str, JsonValue]
        if _is_deep_research_v5(identity.workflow_name, identity.workflow_version):
            stable = _v5_stage_instance(identity)
            if stable is None:
                return None
            try:
                projection = build_v5_transition_projection(
                    identity.node_id, str(normalized_transition)
                )
            except V5ProgressProjectionError:
                return None
            built = _v5_payload(
                identity,
                projection,
                kind="progress",
                status=str(normalized_transition),
            )
            if built is None:
                return None
            payload = built
        elif _is_deep_research_stage_contract(identity.workflow_name, identity.workflow_version):
            payload = {
                "schema_version": 2,
                "kind": "progress",
                "workflow_name": "deep_research",
                "workflow_version": identity.workflow_version,
                "workflow_label": workflow_label,
                "stage_id": stage.node_id,
                "stage": stage.label,
                "ordinal": stage.ordinal,
                "total": stage.total,
                "status": normalized_transition,
                "text": _progress_text(workflow_label, stage, normalized_transition),
                "action": _STAGE_ACTIONS[stage.node_id],
                "result": _progress_text(workflow_label, stage, normalized_transition),
                "result_code": normalized_transition,
                "diagnostic_codes": [],
            }
        elif _is_deep_research_v7(identity.workflow_name, identity.workflow_version):
            payload = {
                "schema_version": 7,
                "kind": "progress",
                "workflow_name": "deep_research",
                "workflow_version": "v7",
                "workflow_label": workflow_label,
                "stage_id": stage.node_id,
                "stage": stage.label,
                "ordinal": stage.ordinal,
                "total": stage.total,
                "status": normalized_transition,
                "text": _progress_text(workflow_label, stage, normalized_transition),
            }
        else:
            payload = {
                "kind": "progress",
                "workflow_name": workflow_label,
                "workflow_label": workflow_label,
                "stage": stage.label,
                "ordinal": stage.ordinal,
                "total": stage.total,
                "status": normalized_transition,
                "text": _progress_text(workflow_label, stage, normalized_transition),
            }
        try:
            event_key = (
                f"progress:v5:{stable[1]}:{normalized_transition}"
                if _is_deep_research_v5(identity.workflow_name, identity.workflow_version)
                else (
                    (
                        f"progress:{identity.workflow_version}:durable_task:"
                        f"{identity.node_id}:attempt:{identity.attempt}:"
                        f"{normalized_transition}"
                    )
                    if identity.workflow_name == "durable_task"
                    else (
                        f"progress:{identity.workflow_version}:{identity.workflow_name}:"
                        f"{identity.node_id}:attempt:{identity.attempt}:"
                        f"{normalized_transition}"
                    )
                )
            )
            emit = getattr(self._service, "emit_progress_event", None)
            if callable(emit):
                return await emit(
                    run_id=identity.run_id,
                    event_key=event_key,
                    payload=payload,
                    deliveries=self._targets,
                )
            event = await self._service.outbox.ensure_event(
                run_id=identity.run_id, event_key=event_key,
                event_type="workflow.progress", payload=payload,
                deliveries=self._targets,
            )
            event_id = str(event["event_id"])
            await self._service.deliver_event_once(event_id)
            return event_id
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) == "execution_run_terminal":
                # A terminal node commit and its final event are already the
                # durable authority. A trailing best-effort progress update is
                # redundant and must not be reported as a second failure.
                return None
            logger.warning(
                "workflow_progress_emit_failed",
                extra={
                    "workflow_name": identity.workflow_name,
                    "node_id": identity.node_id,
                    "transition": normalized_transition,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                },
                exc_info=True,
            )
            return None

    async def report_deep_research_v7_children(
        self,
        identity: NodeExecutionIdentity,
        children: Sequence[Mapping[str, Any]],
    ) -> str | None:
        """Persist one complete, user-safe v7 research-direction snapshot."""

        if not _is_deep_research_v7(identity.workflow_name, identity.workflow_version):
            return None
        if identity.node_id not in {"plan", "search"} or not 2 <= len(children) <= 6:
            return None
        normalized: list[dict[str, JsonValue]] = []
        seen: set[str] = set()
        for raw in children:
            child_id = str(raw.get("child_id") or "").strip()
            question = str(raw.get("question") or "").strip()
            status = str(raw.get("status") or "").strip()
            if (
                not re.fullmatch(r"dr-\d+", child_id)
                or child_id in seen
                or not question
                or len(question) > 600
                or status not in DEEP_RESEARCH_V7_CHILD_STATUSES
            ):
                return None
            max_attempts = int(raw.get("max_attempts") or 2)
            attempt = int(raw.get("attempt") or 0)
            n_sources = int(raw.get("n_sources") or 0)
            if not 1 <= max_attempts <= 3 or not 0 <= attempt <= max_attempts:
                return None
            if not 0 <= n_sources <= 10_000:
                return None
            reason_code = str(raw.get("reason_code") or "").strip()[:128]
            normalized.append({
                "child_id": child_id,
                "question": question,
                "status": status,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "n_sources": n_sources,
                "reason_code": reason_code,
            })
            seen.add(child_id)
        normalized.sort(key=lambda item: int(str(item["child_id"]).split("-", 1)[1]))
        snapshot_json = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        payload: dict[str, JsonValue] = {
            "schema_version": 7,
            "kind": "research_children",
            "workflow_name": "deep_research",
            "workflow_version": "v7",
            "workflow_label": "深度调研",
            "stage_id": "search",
            "stage": "子代理调研与补救",
            "ordinal": 3,
            "total": 6,
            "status": "started",
            "text": f"主 Agent 已拆分 {len(normalized)} 个调研子方向",
            "children": normalized,
        }
        try:
            event_key = f"progress:v7:research_children:{snapshot_hash}"
            emit = getattr(self._service, "emit_progress_event", None)
            if callable(emit):
                return await emit(
                    run_id=identity.run_id,
                    event_key=event_key,
                    payload=payload,
                    deliveries=self._targets,
                )
            event = await self._service.outbox.ensure_event(
                run_id=identity.run_id, event_key=event_key,
                event_type="workflow.progress", payload=payload,
                deliveries=self._targets,
            )
            event_id = str(event["event_id"])
            await self._service.deliver_event_once(event_id)
            return event_id
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "workflow_v7_children_progress_emit_failed",
                extra={
                    "run_id": identity.run_id,
                    "node_id": identity.node_id,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                },
                exc_info=True,
            )
            return None


__all__ = [
    "PUBLIC_WORKFLOW_STAGES",
    "DEEP_RESEARCH_V2_STAGES",
    "DEEP_RESEARCH_V5_STAGES",
    "DEEP_RESEARCH_V7_STAGES",
    "DEEP_RESEARCH_PROGRESS_V5_CAPABILITY",
    "DEEP_RESEARCH_STAGE_VERSIONS",
    "DEEP_RESEARCH_DIAGNOSTIC_CODES",
    "DEEP_RESEARCH_RETRY_ACTION_IDS",
    "DEEP_RESEARCH_SKIPPED_STAGE_IDS",
    "DEEP_RESEARCH_TERMINAL_METRIC_KEYS",
    "PublicWorkflowStage",
    "ProgressPayloadError",
    "WorkflowProgressReporter",
    "public_stage_for",
    "validated_v2_stage_text",
]
