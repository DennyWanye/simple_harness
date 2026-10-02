# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Mission judgment target: the last leaf, in topological order, of the Tasks that
were not cancelled (all Tasks when every one was cancelled)."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import Task, TaskStatus


def terminal_task(tasks: Sequence[Task]) -> Task:
    live = [task for task in tasks if task.status is not TaskStatus.CANCELLED] or list(tasks)
    from ..artifacts.versioning import topological  # review P2-10: order by edges, not ordinal

    ordered = topological(live, {task.id: task for task in live})
    depended = {dep for task in ordered for dep in task.dependency_ids}
    leaves = [task for task in ordered if task.id not in depended]
    return leaves[-1] if leaves else ordered[-1]


__all__ = ("terminal_task",)
