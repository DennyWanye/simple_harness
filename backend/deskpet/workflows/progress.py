# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""User-safe progress projection for durable workflows."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from .contracts import JsonValue, NodeExecutionIdentity


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
    }
)

_WORKFLOW_LABELS = MappingProxyType(
    {
        "deep_research": "深度调研",
        "ppt_pro": "PPT",
        "code_complex": "代码任务",
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
_V2_METRIC_KEYS = MappingProxyType(
    {
        "normalize": frozenset({"mode"}),
        "plan": frozenset({"question_count", "active_branch_count"}),
        "expand": frozenset({"query_count"}),
        "search": frozenset({"providers", "candidates", "kept"}),
        "direct": frozenset({"direct_sources", "candidates"}),
        "fetch": frozenset({"attempted", "succeeded", "dropped"}),
        "score": frozenset({"passages", "kept"}),
        "gap": frozenset({"iteration", "followup_count", "new_evidence"}),
        "rerank": frozenset({"passages", "domains"}),
        "synth": frozenset({"sections", "claim_count"}),
        "cite": frozenset({"citations", "supported", "unsupported", "support_rate"}),
        "persist": frozenset({"artifact_count", "report_bytes"}),
        "finalize": frozenset({"citations", "status"}),
    }
)


class ProgressPayloadError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validated_v2_stage_text(payload: Mapping[str, Any]) -> str:
    """Return SessionDB-safe text for the one allowed completed-stage payload."""

    if payload.get("schema_version") != 2 or payload.get("kind") != "stage" or payload.get("status") != "completed":
        raise ProgressPayloadError("workflow_stage_payload_not_completed_v2")
    if str(payload.get("stage_id") or "") not in DEEP_RESEARCH_V2_STAGES:
        raise ProgressPayloadError("workflow_stage_payload_unknown_stage")
    text = payload.get("text")
    summary = payload.get("summary")
    if not isinstance(text, str) or not text.strip() or text != summary:
        raise ProgressPayloadError("workflow_stage_payload_text_mismatch")
    return text


def public_stage_for(
    workflow_name: str,
    node_id: str,
    workflow_version: str | None = None,
) -> PublicWorkflowStage | None:
    if workflow_name == "deep_research" and workflow_version == "v2":
        stage_id = node_id[:-5] if node_id.endswith("_join") else node_id
        return DEEP_RESEARCH_V2_STAGES.get(stage_id)
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

        if (identity.workflow_name, identity.workflow_version) != ("deep_research", "v2"):
            return None
        stage_id = str(frozen_projection.get("stage_id") or "")
        stage = DEEP_RESEARCH_V2_STAGES.get(stage_id)
        if stage is None:
            logger.warning("workflow_progress_unknown_v2_stage", extra={"stage_id": stage_id})
            return None
        raw_metrics = frozen_projection.get("metrics", {})
        if not isinstance(raw_metrics, Mapping):
            return None
        allowed = _V2_METRIC_KEYS[stage_id]
        if any(str(key) not in allowed for key in raw_metrics):
            logger.warning("workflow_progress_unsafe_metrics", extra={"stage_id": stage_id})
            return None
        metrics = {str(key): copy.deepcopy(value) for key, value in raw_metrics.items()}
        duration_ms = max(0, int(frozen_projection.get("duration_ms") or 0))
        completed_count = max(0, min(stage.total, int(frozen_projection.get("completed_count") or 0)))
        degraded = bool(frozen_projection.get("degraded", False))
        counts = "、".join(f"{key}={value}" for key, value in sorted(metrics.items()))
        summary = f"{stage.label}已完成"
        if counts:
            summary += f"（{counts}）"
        if degraded:
            summary += "，部分来源已降级"
        stable_identity = f"v2:{identity.node_id}:{identity.task_id}:completed"
        stage_instance_id = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:24]
        payload: dict[str, JsonValue] = {
            "schema_version": 2,
            "kind": "stage",
            "workflow_name": "deep_research",
            "workflow_version": "v2",
            "workflow_label": "深度调研",
            "stage_id": stage_id,
            "stage_instance_id": stage_instance_id,
            "stage": stage.label,
            "ordinal": stage.ordinal,
            "total": stage.total,
            "status": "completed",
            "summary": summary,
            "text": summary,
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
        if (identity.workflow_name, identity.workflow_version) == ("deep_research", "v2"):
            payload = {
                "schema_version": 2,
                "kind": "progress",
                "workflow_name": "deep_research",
                "workflow_version": "v2",
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
                "stage": stage.label,
                "ordinal": stage.ordinal,
                "total": stage.total,
                "status": normalized_transition,
                "text": _progress_text(workflow_label, stage, normalized_transition),
            }
        try:
            event = await self._service.outbox.ensure_event(
                run_id=identity.run_id,
                event_key=(
                    f"progress:{identity.workflow_version}:{identity.workflow_name}:"
                    f"{identity.node_id}:attempt:{identity.attempt}:"
                    f"{normalized_transition}"
                ),
                event_type="workflow.progress",
                payload=payload,
                deliveries=self._targets,
            )
            event_id = str(event["event_id"])
            await self._service.deliver_event_once(event_id)
            return event_id
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "workflow_progress_emit_failed",
                extra={
                    "workflow_name": identity.workflow_name,
                    "node_id": identity.node_id,
                    "transition": normalized_transition,
                },
            )
            return None


__all__ = [
    "PUBLIC_WORKFLOW_STAGES",
    "DEEP_RESEARCH_V2_STAGES",
    "PublicWorkflowStage",
    "ProgressPayloadError",
    "WorkflowProgressReporter",
    "public_stage_for",
    "validated_v2_stage_text",
]
