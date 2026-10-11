# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Mission judgment target: the last leaf, in topological order, of the Tasks that
were not cancelled (all Tasks when every one was cancelled)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..contracts import Task, TaskStatus


def finalizer_task_id(network: Any, semantics: Any, mission_id: str) -> str | None:
    """The step the root's adopted method names as its finalizer (``composition.finalizer_step``),
    as a task id — ``derive_id("task", instance, slot)`` is how grounding names every slot's task.
    None when the method names no finalizer.  (2026-10-11 偏离 5：完成报告不再任取一步。)"""
    from ..planning.htn.grounding import derive_id

    for root in network.root_occurrence_ids:
        draft = network.adopted_instance_for(root)
        if draft is None:
            continue
        stored = semantics.get_method(draft.method_ref.method_id, int(draft.method_ref.version))
        step = stored.contract.composition.finalizer_step
        if step:
            return str(derive_id("task", str(draft.instance_id), str(step)))
    return None


def accepted_tasks(tasks: Sequence[Task]) -> list[Task]:
    """Every task with an accepted result, in the network's topological order."""
    from ..artifacts.versioning import topological

    live = [task for task in tasks if task.status is not TaskStatus.CANCELLED]
    ordered = topological(live, {task.id: task for task in live})
    return [task for task in ordered if task.accepted_result_id]


def terminal_task(tasks: Sequence[Task]) -> Task:
    live = [task for task in tasks if task.status is not TaskStatus.CANCELLED] or list(tasks)
    from ..artifacts.versioning import topological  # review P2-10: order by edges, not ordinal

    ordered = topological(live, {task.id: task for task in live})
    depended = {dep for task in ordered for dep in task.dependency_ids}
    leaves = [task for task in ordered if task.id not in depended]
    return leaves[-1] if leaves else ordered[-1]


__all__ = ("accepted_tasks", "finalizer_task_id", "terminal_task")
