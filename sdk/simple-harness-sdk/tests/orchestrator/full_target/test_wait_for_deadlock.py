# SPDX-License-Identifier: Apache-2.0
"""资源占用环（wait-for）死锁分析与具名停止（原计划 §10.5、§24.1 第 10 条；第 2 批车道 J H03）。

* 四种等待建图，强连通分量成环才算；只收"还在等"的边；
* 停止原因 ``deadlock`` 与码 ``resource_wait_cycle`` 登记（需收敛，不算规划器答错）；恢复协议三个码登记；
* 停滞交规划器那一条请求带上环的事实（``wait_for``）。

**改坏检验**：``cycles()`` 把"大小 ≥ 2 或自环"改成全部分量 → 第 1 条红（孤立节点也算环）；
``wait_for_graph`` 不看 ``completed`` → 第 2 条红。
"""
from __future__ import annotations

import inspect

from agent_orchestrator.contracts.error_table import (
    RECOVERY_ERRORS,
    SCHEDULING_ERRORS,
    ErrorCategory,
    RecoveryBoundaryCode,
    SchedulingStopCode,
    classify,
)
from agent_orchestrator.contracts.state_machines import MissionStopReason
from agent_orchestrator.orchestrator import planning_repair_requests
from agent_orchestrator.scheduling.wait_for import (
    WaitEdge,
    WaitForGraph,
    deadlock_facts,
    has_deadlock,
    wait_for_graph,
)


def test_a_cycle_of_waits_is_found_named_and_reproducible():
    graph = wait_for_graph(
        order=[("a", "b"), ("c", "d")],          # b 等 a；d 等 c
        data=[("b", "a", "req-1")],               # a 等 b 的产出 → a↔b 成环
        fences=[("job-1", "FENCED", "d")],        # d 被围栏围住：d 等 fence
        live_attempt_tasks=(),
        handoffs=[("c", "act-1", "HANDED_OFF")],  # c 等对外操作
    )
    cycles = graph.cycles()
    assert [c.members for c in cycles] == [("a", "b")]
    assert {(e.waiter, e.holder, e.kind) for e in cycles[0].edges} == {("b", "a", "order"), ("a", "b", "data")}
    assert cycles[0].digest == wait_for_graph(order=[("a", "b")], data=[("b", "a", "req-1")]).cycles()[0].digest
    facts = deadlock_facts(graph)
    assert has_deadlock(facts) and facts["code"] == "resource_wait_cycle"
    assert facts["cycles"][0]["members"] == ["a", "b"] and facts["edges"] == 5
    # 没有环的图：只有事实，没有码
    calm = deadlock_facts(wait_for_graph(order=[("a", "b"), ("b", "c")]))
    assert not has_deadlock(calm) and calm["code"] is None and calm["edges"] == 2
    # 自环也算
    assert WaitForGraph((WaitEdge("x", "x", "handoff"),)).cycles()[0].members == ("x",)


def test_only_live_waits_make_edges():
    task_of = {"a": "ta", "b": "tb", "c": "tc"}
    graph = wait_for_graph(
        order=[("a", "b"), ("b", "c")], data=[("c", "a", "r")], task_of=task_of,
        task_status={"ta": "COMPLETED", "tb": "ACTIVE", "tc": "CANCELLED"},
    )
    # b 等 a：a 已完成，不等了；c 等 b：c 已结束，不在等；a 等 c 的产出：a 已结束
    assert graph.edges == ()
    fenced = wait_for_graph(fences=[("j", "FENCED", "a"), ("j", "APPLIED", "b"), ("j", "WAITING", "c")],
                            task_of=task_of, task_status={"tc": "ACTIVE"}, live_attempt_tasks={"tc"})
    assert {(e.waiter, e.holder) for e in fenced.edges} == {("a", "fence:j"), ("fence:j", "c")}
    assert wait_for_graph(handoffs=[("a", "k", "SUCCEEDED")]).edges == ()


def test_the_deadlock_stop_reason_and_the_lane_codes_are_registered():
    assert MissionStopReason.DEADLOCK.value == "deadlock"
    assert MissionStopReason("deadlock") is MissionStopReason.DEADLOCK
    entry = classify(SchedulingStopCode.RESOURCE_WAIT_CYCLE)
    assert entry.category is ErrorCategory.NEEDS_CONVERGENCE and entry.charges_planner is False
    assert classify("resource_wait_cycle") is entry
    assert set(SCHEDULING_ERRORS) == set(SchedulingStopCode)
    assert set(RECOVERY_ERRORS) == set(RecoveryBoundaryCode)
    assert classify("RECOVERY_LOCK_HELD").category is ErrorCategory.COMMIT_CONFLICT
    assert classify(RecoveryBoundaryCode.DEGRADED_RECOVERY).category is ErrorCategory.SOURCE_UNAVAILABLE
    assert classify("SIDE_EFFECTS_DISABLED").category is ErrorCategory.SOURCE_UNAVAILABLE


def test_the_stall_request_hands_the_wait_for_facts_to_the_planner_once():
    source = inspect.getsource(planning_repair_requests.request_planner_for_stall)
    assert "collect_wait_facts(dispatch.store, network)" in source
    assert '"wait_for": wait_for' in source
