# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The revision a Task's protected budget tail is held under.

A tail hold (the allowance kept back for a Task's first review, or a Mission's system
tail) names the Task it was made for by this revision: the Task's contract, the
Mission's contract, and everything that decides what the Task may spend and on what.
When any of that changes the hold no longer describes the Task and is refused.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Task
from ..contracts.models import sha256_hex
from ..verification.criteria import mission_contract_revision, task_contract_revision

TAIL_REVISION_VERSION = "task-tail-v1"


def _task_contract(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "kind": task.kind,
        "goal": task.goal,
        "rationale": task.rationale,
        "success_criteria": list(task.success_criteria),
        "verification_policy": list(task.verification_policy),
        "outputs": list(task.outputs),
    }


def task_tail_revision(store: Any, task: Task) -> str:
    mission = store.get_mission(task.mission_id)
    return sha256_hex(
        {
            "version": TAIL_REVISION_VERSION,
            "task_contract_revision": task_contract_revision(_task_contract(task)),
            "mission_contract_revision": mission_contract_revision(mission),
            "constraints": {
                "allowed_tools": list(task.allowed_tools),
                "dependencies": list(task.dependency_ids),
                "context": dict(task.context),
                "budget": task.budget.to_json(),
            },
            "domain": store.get_mission_domain(task.mission_id),
            "policy": store.get_mission_policy(task.mission_id),
        }
    )


__all__ = ("TAIL_REVISION_VERSION", "task_tail_revision")
