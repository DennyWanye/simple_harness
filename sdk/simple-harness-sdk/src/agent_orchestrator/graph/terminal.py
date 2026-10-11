# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The completion report's terminal step (the adopted method's finalizer) and the accepted
tasks in topological order.  V36 (2026-10-11): the old "last leaf in topological order"
reading is gone — the report and the lineage share the finalizer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..contracts import Task, TaskStatus


def finalizer_task_id(network: Any, semantics: Any, mission_id: str) -> str | None:
    """The step the root's adopted method names as its finalizer (``composition.finalizer_step``),
    as a task id.  Read off the adopted instance's child bindings (slot → occurrence → task):
    a shared slot is bound to an occurrence another instance opened, whose task id is not
    the one ``derive_id`` would spell for this instance (V35, 核验 opt.184 建议 S1).
    None when the method names no finalizer.  (2026-10-11 偏离 5：完成报告不再任取一步。)"""

    for root in network.root_occurrence_ids:
        draft = network.adopted_instance_for(root)
        if draft is None:
            continue
        stored = semantics.get_method(draft.method_ref.method_id, int(draft.method_ref.version))
        step = stored.contract.composition.finalizer_step
        if not step:
            continue
        child = next((item for item in network.adopted_children(root) if str(item.slot_key) == str(step)), None)
        if child is None:
            return None
        return str(network.occurrence(child.occurrence_id).task_id)
    return None


def accepted_tasks(tasks: Sequence[Task]) -> list[Task]:
    """Every task with an accepted result, in the network's topological order."""
    from ..artifacts.versioning import topological

    live = [task for task in tasks if task.status is not TaskStatus.CANCELLED]
    ordered = topological(live, {task.id: task for task in live})
    return [task for task in ordered if task.accepted_result_id]


__all__ = ("accepted_tasks", "finalizer_task_id")
