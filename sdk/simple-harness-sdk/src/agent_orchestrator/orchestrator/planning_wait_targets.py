"""WAIT 的对象是方法实例或目标时，换成它下面的步骤来等（2026-09-30 资料换版真机）。

规划器在修复轮里看到一步正在跑，自然会回 WAIT，等的对象常写成那一步所在的方法实例或
目标。能被唤醒的只有步骤（跑完）和已完成的记录；这里沿当前计划里已采用的方法实例往下
走，列出方法实例 / 目标下面的叶子步骤，由调用方再挑出正在跑的那些去等。
"""
from __future__ import annotations

from typing import Any


def steps_under(network: Any, *, instance_id: str | None = None,
                obligation_id: str | None = None) -> tuple[str, ...]:
    """Task ids of the leaf steps below one adopted method instance or one duty."""

    adopted = {str(item) for item in network.adopted_instance_ids}
    instances = {str(item.instance_id): item for item in network.method_instances
                 if str(item.instance_id) in adopted}
    by_goal = {str(item.effective_goal_occurrence_id): item for item in instances.values()}
    specs = {str(spec.occurrence_id): spec for spec in network.occurrences}
    if instance_id is not None:
        instance = instances.get(instance_id)
        start = [] if instance is None else [str(c.occurrence_id) for c in instance.child_bindings]
    else:
        start = [key for key, spec in specs.items() if str(spec.obligation_id) == obligation_id]
    leaves: list[str] = []
    seen: set[str] = set()
    while start:
        occurrence = start.pop(0)
        if occurrence in seen or occurrence not in specs:
            continue
        seen.add(occurrence)
        refined = by_goal.get(occurrence)
        if refined is not None:
            start.extend(str(c.occurrence_id) for c in refined.child_bindings)
        else:
            leaves.append(str(specs[occurrence].task_id))
    return tuple(dict.fromkeys(leaves))
