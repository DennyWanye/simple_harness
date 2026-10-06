# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""执行者上下文第 3 项"父目标与直接上游"（原计划 §10 第 3 项；第 2 批车道 H，K04）。

分层计划下 ``Task.dependency_ids`` 恒空（``occurrence_tasks`` 模块说明），先后关系在类型化网络
里：这一步属于哪个做法实例、做法实例细化的是哪个目标，是它的**父目标**；数据边
（``DataRequirement``）的生产者，是它的**直接上游**。两样都只读现有读接口——网络快照、任务行、
现行要求、黑板摘要层——不另建表。

给执行者的都是事实：父目标的原文与它负责的要求原文；每个上游步骤的目标、状态、已验收结论的
摘要（审阅员核对过忠实的那份，核对记录编号一并给出；没有核对过就给结果里的原话、标明未核对）、
以及这一步从它那里拿到的产物。怎么用这些事实，是执行者的事。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..artifacts.versioning import UpstreamInput
from ..context.knowledge_tools import step_summaries
from ..contracts import Mission, Task
from ..deployment.root import current_criteria

PARENT_GOAL_VERSION = "parent-goal-v1"
UPSTREAM_VERSION = "upstream-steps-v1"


def parent_goal(store: Any, network: Any, mission: Mission, task: Task) -> dict[str, Any] | None:
    """这一步的父目标：它所在做法实例细化的那个复合目标。根步骤没有父目标，返回 None。"""

    binding = network.binding_for_task(task.id)
    placement = binding.occurrence_binding
    if placement is None:
        return None
    draft = network.instance(placement.method_instance_id)
    goal_binding = network.binding_for_task(draft.goal_id)
    goal_row = store.get_task(str(draft.goal_id))
    statements = dict(current_criteria(store, mission))
    return {
        "data_not_instruction": True,
        "version": PARENT_GOAL_VERSION,
        "task_id": str(draft.goal_id),
        "occurrence_id": None if draft.goal_occurrence_id is None else str(draft.goal_occurrence_id),
        "obligation_id": str(draft.obligation_id),
        "goal": goal_binding.goal_signature.statement if goal_row is None else goal_row.goal,
        "goal_type": str(goal_binding.goal_signature.signature_id),
        "requirements": [
            {"criterion_id": ref, "statement": statements.get(ref)}
            for ref in goal_binding.requirement_refs
        ],
        "method": draft.method_ref.to_json(),
        "slot_key": placement.slot_key,
    }


def upstream_steps(
    store: Any, network: Any, mission: Mission, task: Task, inputs: Sequence[UpstreamInput]
) -> list[dict[str, Any]]:
    """直接上游：数据边上把产出交给这一步的生产者，按步骤归并（一个步骤几条边就列几个端口）。"""

    mine = {spec.occurrence_id for spec in network.occurrences if str(spec.task_id) == task.id}
    producers: dict[str, dict[str, Any]] = {}
    for requirement in network.data_requirements:
        if requirement.consumer_occurrence not in mine:
            continue
        producer = network.occurrence(requirement.producer_occurrence)
        producer_id = str(producer.task_id)
        row = producers.get(producer_id)
        if row is None:
            row = producers[producer_id] = {
                "task_id": producer_id,
                "occurrence_id": str(producer.occurrence_id),
                "ports": [],
            }
        row["ports"].append({
            "requirement_id": requirement.requirement_id,
            "output_port": requirement.output_port,
            "input_port": requirement.input_port,
        })
    if not producers:
        return []
    checked = {row["source_task"]: row for row in step_summaries(store, mission.id)}
    for producer_id, row in producers.items():
        producer_task = store.get_task(producer_id)
        result_id = None if producer_task is None else producer_task.accepted_result_id
        summary_row = checked.get(producer_id)
        if summary_row is not None:
            summary, checked_by = summary_row["summary"], summary_row["checked_by"]
        else:
            stored = store.get_result(result_id) if result_id else None
            summary = None if stored is None else str(stored.envelope.summary or "")
            checked_by = None
        row.update({
            "goal": None if producer_task is None else producer_task.goal,
            "status": None if producer_task is None else str(producer_task.status),
            "accepted_result_id": result_id,
            "accepted_summary": summary,
            # 审阅员核对过"摘要忠实于原结果"的记录编号；None = 没核对过，摘要只是执行者自己的话
            "summary_checked_by": checked_by,
            "accepted_artifacts": [item.to_json() for item in inputs if item.task_id == producer_id],
        })
    return [producers[key] for key in sorted(producers)]


__all__ = ("PARENT_GOAL_VERSION", "UPSTREAM_VERSION", "parent_goal", "upstream_steps")
