# SPDX-License-Identifier: Apache-2.0
"""资源占用环（wait-for）死锁分析与具名停止（原计划 §10.5、§24.1 第 10 条；第 2 批车道 J H03）。

* 四种等待建图，强连通分量成环才算；只收"还在等"的边；
* 停止原因 ``deadlock`` 与码 ``resource_wait_cycle`` 登记（需收敛，不算规划器答错）；恢复协议三个码登记；
* 停滞交规划器那一条请求带上环的事实（``wait_for``）。

* 夜间 N3-01：行为用例跑到"死锁"停止分支——问过规划器、它不改、等待成环 → 以 ``deadlock`` 停，停机详情带
  ``wait_for``；对照：图没有环时仍按 ``no_dispatchable_work`` 停。

**改坏检验**：``cycles()`` 把"大小 ≥ 2 或自环"改成全部分量 → 第 1 条红（孤立节点也算环）；
``wait_for_graph`` 不看 ``completed`` → 第 2 条红；``event_handler`` 停止处的三元式两支对调（或固定写
``NO_DISPATCHABLE_WORK``）→ 死锁行为用例红。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from agent_orchestrator.contracts import MissionStatus
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


# ------------------------------------------------------------------ 夜间 N3-01：行为用例
# 借 ``test_stall_asks_planner_first.stalled`` 那个产品同形停滞局面（唯一一步做完被验收、最终审查一直没
# 结论、规划器被问到时答"不改"）；等待图用 monkeypatch 换成注入的图（``event_handler`` 在函数里导入
# ``collect_wait_facts``，替换模块属性即生效）。只加用例，不改产品代码。

from test_stall_asks_planner_first import _stall_requests, stalled  # noqa: E402

import agent_orchestrator.scheduling.wait_for as wait_for_module  # noqa: E402


@pytest.fixture
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _stop_with_graph(tmp_path, monkeypatch, graph: WaitForGraph, key: str):
    monkeypatch.setattr(wait_for_module, "collect_wait_facts", lambda store, network: graph)

    async def case():
        async with stalled(tmp_path, key=key) as world:
            loop = world.loop
            await loop._record_hierarchical_stall()
            assert await loop._confirm_and_stop_stalled() is True  # 第一次：先问规划器
            await loop._record_hierarchical_stall()  # 同一版计划又停在原地（规划器不改）
            carried = await loop._confirm_and_stop_stalled()
            return carried, world.mission(), world.events()

    return asyncio.run(case())


def test_a_wait_cycle_after_the_planner_changed_nothing_stops_as_deadlock(tmp_path, monkeypatch, _quick):
    cyclic = wait_for_graph(order=[("occ-a", "occ-b")], data=[("occ-b", "occ-a", "req-1")])
    carried, mission, events = _stop_with_graph(tmp_path, monkeypatch, cyclic, "deadlock-stop")
    assert carried is False
    assert len(_stall_requests(events)) == 1, "先问过规划器一次"
    assert mission.status is MissionStatus.FAILED
    report = mission.final_report
    assert report["stop_reason"] == "deadlock"
    detail = report["detail"]
    assert detail["planner_asked"] is not None
    assert detail["planner_asked"]["request_id"] == _stall_requests(events)[0].payload["request_id"]
    assert detail["wait_for"]["code"] == "resource_wait_cycle"
    assert [c["members"] for c in detail["wait_for"]["cycles"]] == [["occ-a", "occ-b"]]
    assert detail["wait_for"] == deadlock_facts(cyclic)
    # 交给规划器的那条请求也带着同一份环的事实
    assert _stall_requests(events)[0].payload["request"]["context"]["wait_for"]["code"] == "resource_wait_cycle"


def test_without_a_wait_cycle_the_same_stop_is_no_dispatchable_work(tmp_path, monkeypatch, _quick):
    calm = wait_for_graph(order=[("occ-a", "occ-b")])
    carried, mission, events = _stop_with_graph(tmp_path, monkeypatch, calm, "deadlock-calm")
    assert carried is False and mission.status is MissionStatus.FAILED
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    detail = mission.final_report["detail"]
    assert detail["planner_asked"] is not None
    assert detail["wait_for"]["code"] is None and detail["wait_for"]["cycles"] == []
