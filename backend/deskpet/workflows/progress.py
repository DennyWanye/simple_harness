# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""User-safe progress projection for durable workflows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from .contracts import JsonValue, NodeExecutionIdentity


logger = logging.getLogger(__name__)

ProgressTransition = Literal["started", "waiting", "failed", "cancelled"]
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


def public_stage_for(workflow_name: str, node_id: str) -> PublicWorkflowStage | None:
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

    def __init__(self, service: Any, targets: Sequence[tuple[str, str]]) -> None:
        self._service = service
        self._targets = tuple((str(channel), str(target)) for channel, target in targets)

    @property
    def targets(self) -> tuple[tuple[str, str], ...]:
        return self._targets

    async def report(
        self,
        identity: NodeExecutionIdentity,
        transition: ProgressTransition | str,
    ) -> str | None:
        stage = public_stage_for(identity.workflow_name, identity.node_id)
        if stage is None or transition not in _TRANSITIONS:
            return None
        normalized_transition = transition  # narrowed by the fixed allowlist above
        workflow_label = _WORKFLOW_LABELS[identity.workflow_name]
        payload: dict[str, JsonValue] = {
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
                    f"progress:v1:{identity.workflow_name}:"
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
    "PublicWorkflowStage",
    "WorkflowProgressReporter",
    "public_stage_for",
]
