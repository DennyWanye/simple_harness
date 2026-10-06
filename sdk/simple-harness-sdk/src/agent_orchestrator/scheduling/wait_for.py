# SPDX-License-Identifier: Apache-2.0
"""资源占用环（wait-for）死锁分析（原计划 §10.5、§24.1 第 10 条；第 2 批车道 J H03）。

执行投影本身是否有环由 ``graph/projection_validation`` 在提交时查（先后边、细化关系）。这里查的是
**运行时的等待关系**：谁在等谁先动。四种等待都从执行图与库里已有的事实读出来，不另造状态：

* ``order`` —— 先后边：``after`` 等 ``before`` 到放行条件（``before`` 的任务没完成、``after`` 的任务没结束）；
* ``data`` —— 数据边：消费者等生产者的产出被验收；
* ``fence`` —— 共用围栏：被改做法围住的步骤等围栏（收敛作业）解除；围栏等它的目标里还在跑的尝试结束；
* ``handoff`` —— 交接等待：步骤等已交出去、结果未明的对外操作。

节点是步骤（occurrence）与资源（``fence:<job>``、``action:<key>``），边 ``waiter → holder``。
强连通分量里成环（含自环）就是死锁候选：互相等着对方先动，谁也动不了。本模块只给事实
（:func:`deadlock_facts`）；要不要改计划归规划器（片 D 第 1 项），规划器不改才按
``MissionStopReason.DEADLOCK`` 停——那一步在主循环的停滞路径。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..contracts.error_table import SchedulingStopCode

#: 等待的四种来源；别的来源先登记到这里再加边。
WAIT_KINDS: tuple[str, ...] = ("order", "data", "fence", "handoff")
#: 围栏作业里"还围着"的状态（``taskgraph_convergence_jobs.state``）
FENCING_STATES: frozenset[str] = frozenset({"FENCED", "WAITING"})
#: 对外操作里"交出去了、结果未明"的状态（``actions.state``）
HANDED_OFF_STATES: frozenset[str] = frozenset({"HANDED_OFF", "UNKNOWN"})
#: 尝试还在跑的状态（``attempts.status``）
LIVE_ATTEMPT_STATES: frozenset[str] = frozenset({"PENDING", "CLAIMED", "RUNNING", "SUBMITTED", "VERIFYING"})
TERMINAL_TASK_STATES: frozenset[str] = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


@dataclass(frozen=True, slots=True)
class WaitEdge:
    waiter: str
    holder: str
    kind: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in WAIT_KINDS:
            raise ValueError(f"unknown wait kind {self.kind!r}")
        if not self.waiter or not self.holder:
            raise ValueError("a wait edge names both ends")

    def to_json(self) -> dict[str, Any]:
        return {"waiter": self.waiter, "holder": self.holder, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class WaitCycle:
    """一个成环的强连通分量：成员（排序）与分量内部的边。"""

    members: tuple[str, ...]
    edges: tuple[WaitEdge, ...]

    @property
    def digest(self) -> str:
        body = {"members": list(self.members), "edges": [e.to_json() for e in self.edges]}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {"members": list(self.members), "edges": [e.to_json() for e in self.edges], "digest": self.digest}


@dataclass(frozen=True, slots=True)
class WaitForGraph:
    edges: tuple[WaitEdge, ...] = ()
    _adjacency: Mapping[str, tuple[str, ...]] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        adjacency: dict[str, list[str]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.waiter, []).append(edge.holder)
            adjacency.setdefault(edge.holder, [])
        object.__setattr__(self, "_adjacency", {k: tuple(v) for k, v in adjacency.items()})

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(sorted(self._adjacency))

    def cycles(self) -> tuple[WaitCycle, ...]:
        """Tarjan 强连通分量；大小 ≥ 2、或有自环的分量才算环。按成员排序，结果可重现。"""

        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        components: list[frozenset[str]] = []
        counter = [0]

        def visit(root: str) -> None:
            # 显式栈，避免深图递归溢出
            work: list[tuple[str, int]] = [(root, 0)]
            index[root] = low[root] = counter[0]
            counter[0] += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, position = work[-1]
                successors = self._adjacency.get(node, ())
                if position < len(successors):
                    work[-1] = (node, position + 1)
                    succ = successors[position]
                    if succ not in index:
                        index[succ] = low[succ] = counter[0]
                        counter[0] += 1
                        stack.append(succ)
                        on_stack.add(succ)
                        work.append((succ, 0))
                    elif succ in on_stack:
                        low[node] = min(low[node], index[succ])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    members: set[str] = set()
                    while True:
                        popped = stack.pop()
                        on_stack.discard(popped)
                        members.add(popped)
                        if popped == node:
                            break
                    components.append(frozenset(members))

        for node in self.nodes:
            if node not in index:
                visit(node)
        found: list[WaitCycle] = []
        for component in components:
            inside = tuple(sorted((e for e in self.edges if e.waiter in component and e.holder in component),
                                  key=lambda e: (e.waiter, e.holder, e.kind, e.detail)))
            if len(component) >= 2 or any(e.waiter == e.holder for e in inside):
                found.append(WaitCycle(tuple(sorted(component)), inside))
        return tuple(sorted(found, key=lambda c: c.members))


# --------------------------------------------------------------------------------------
# 从事实建图
# --------------------------------------------------------------------------------------


def wait_for_graph(
    *,
    order: Iterable[tuple[str, str]] = (),
    data: Iterable[tuple[str, str, str]] = (),
    task_of: Mapping[str, str] | None = None,
    task_status: Mapping[str, str] | None = None,
    fences: Iterable[tuple[str, str, str]] = (),
    live_attempt_tasks: Collection[str] = (),
    handoffs: Iterable[tuple[str, str, str]] = (),
) -> WaitForGraph:
    """按事实建 wait-for 图。

    ``order``：(before, after)；``data``：(producer, consumer, requirement_id)；``task_of``：步骤 → 任务号；
    ``task_status``：任务号 → 状态（没给就当都没结束）；``fences``：(job_id, state, target_occurrence)；
    ``live_attempt_tasks``：有未结束尝试的任务号；``handoffs``：(occurrence, action_key, state)。

    只收"还在等"的边：等待方的任务没结束、被等方的任务没完成。"""

    task_of = dict(task_of or {})
    task_status = dict(task_status or {})

    def ended(occurrence: str) -> bool:
        return task_status.get(task_of.get(occurrence, ""), "") in TERMINAL_TASK_STATES

    def completed(occurrence: str) -> bool:
        return task_status.get(task_of.get(occurrence, ""), "") == "COMPLETED"

    edges: list[WaitEdge] = []
    for before, after in order:
        if not ended(after) and not completed(before):
            edges.append(WaitEdge(after, before, "order"))
    for producer, consumer, requirement_id in data:
        if not ended(consumer) and not completed(producer):
            edges.append(WaitEdge(consumer, producer, "data", requirement_id))
    live = set(live_attempt_tasks)
    for job_id, state, target in fences:
        if state not in FENCING_STATES:
            continue
        fence = f"fence:{job_id}"
        if task_of.get(target) in live:
            edges.append(WaitEdge(fence, target, "fence", state))  # 围栏等目标上还在跑的尝试结束
        elif not ended(target):
            edges.append(WaitEdge(target, fence, "fence", state))  # 被围住的步骤等围栏解除
    for occurrence, action_key, state in handoffs:
        if state in HANDED_OFF_STATES and not ended(occurrence):
            edges.append(WaitEdge(occurrence, f"action:{action_key}", "handoff", state))
    unique = tuple(dict.fromkeys(edges))
    return WaitForGraph(unique)


def collect_wait_facts(store: Any, network: Any) -> WaitForGraph:
    """从库与当前执行图快照读四种等待（只读）。``network`` 是 ``TaskNetworkSnapshot``。"""

    mission_id = str(network.mission_id)
    task_of = {str(spec.occurrence_id): str(spec.task_id) for spec in network.occurrences}
    task_status = {task.id: str(getattr(task.status, "value", task.status)) for task in store.list_tasks(mission_id)}
    live_tasks = {attempt.task_id for attempt in store.list_attempts_by_status(*sorted(LIVE_ATTEMPT_STATES))
                  if attempt.mission_id == mission_id}
    connection = store.connection
    fences = [(str(r[0]), str(r[1]), str(r[2])) for r in connection.execute(
        "SELECT j.job_id, j.state, t.occurrence_id FROM taskgraph_convergence_jobs j"
        " JOIN taskgraph_convergence_targets t ON t.job_id=j.job_id WHERE j.mission_id=?"
        " ORDER BY j.job_id, t.occurrence_id", (mission_id,))]
    by_task: dict[str, list[str]] = {}
    for occurrence, task_id in task_of.items():
        by_task.setdefault(task_id, []).append(occurrence)
    handoffs: list[tuple[str, str, str]] = []
    for action in store.list_actions(mission_id, *sorted(HANDED_OFF_STATES)):
        for occurrence in by_task.get(str(action.get("task_id") or ""), ()):
            handoffs.append((occurrence, str(action["action_key"]), str(action["state"])))
    return wait_for_graph(
        order=[(str(c.before), str(c.after)) for c in network.order_constraints],
        data=[(str(r.producer_occurrence), str(r.consumer_occurrence), str(r.requirement_id))
              for r in network.data_requirements],
        task_of=task_of, task_status=task_status, fences=fences, live_attempt_tasks=live_tasks,
        handoffs=handoffs,
    )


def deadlock_facts(graph: WaitForGraph) -> dict[str, Any]:
    """交给规划器 / 写进停机报告的事实：边数、成环的分量、码。没有环时 ``cycles`` 为空。"""

    cycles = graph.cycles()
    return {"code": str(SchedulingStopCode.RESOURCE_WAIT_CYCLE) if cycles else None,
            "edges": len(graph.edges), "cycles": [cycle.to_json() for cycle in cycles]}


def has_deadlock(facts: Mapping[str, Any]) -> bool:
    return bool(facts.get("cycles"))


__all__ = (
    "FENCING_STATES",
    "HANDED_OFF_STATES",
    "LIVE_ATTEMPT_STATES",
    "WAIT_KINDS",
    "WaitCycle",
    "WaitEdge",
    "WaitForGraph",
    "collect_wait_facts",
    "deadlock_facts",
    "has_deadlock",
    "wait_for_graph",
)
