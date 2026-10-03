# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""NEXT-TG-1.0 第二批 B 验收补测（§7.3 第 1、4 条）。

* 第 1 条：A/B→C，C 对 A 是非空 DATA、对 B 是纯 ORDER；前序完成后不额外请求主
  Planner，C 由原循环自动启动并跑到 COMPLETED。
* 第 4 条：C 等 DATA 时独立的 D 仍被派发；另一分支是待细化的复合目标（需要结构
  规划）时，D 同样不被阻断。

2026-10-03 A′：世界换成产品同形部署（:func:`~agent_orchestrator.testing.product_world.product_world`）。
计划由规划器提出、经独立审阅、采用后提交（不再绕过规划器手工提交），步骤用通用"用户目标"世界的
类型：``prepare-delivery``（产出 ``delivery``）、``continue-delivery``（消费上一步的 ``delivery``）、
``sub-goal-1``（复合子目标）。删除：``abc_fixture_really_has_one_data_edge_and_one_pure_order_edge``
（夹具自检；它的前提——C 只有一条来自 A 的 DATA 边、开局 C 在等——改成第 1 条用例里的前提断言）。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.graph.eligibility import ReadinessReason
from agent_orchestrator.orchestrator.planning_repair_requests import collect_triggers
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
)

#: 主编排再规划的全部入口留下的痕迹；FAST 路径上（计划提交之后）一个都不应出现。
PLANNING_TRACES = (
    "PlanningServiceResumed",
    "PlanningRepairRequested",
    "PlanningRepairAddressed",
    "MethodSynthesisRoundRecorded",
    "PlanningRejected",
)
CONFIG = {"max_concurrency": 3, "max_concurrent_model_calls": 3, "max_planning_attempts": 3,
          "test_timeout_seconds": 30}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# The root method the Planner proposes
# ======================================================================================


def _root_method(context: dict[str, Any], steps: list[tuple[str, str, dict[str, Any]]],
                 ordering: list[tuple[str, str]], finalizer: str) -> dict[str, Any]:
    """``steps`` = ``(local_id, task type, arguments)``; step *i* owns the root's *i*-th
    requirement (one requirement per step, so every step has completion criteria)."""
    request = context["request"]
    kinds = {str(item["task_type_ref"]["id"]): item for item in request["operators"]}
    kinds.update({str(item["task_type_ref"]["id"]): item for item in request.get("subgoal_types", ())})
    criteria = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    assert len(criteria) == len(steps)
    body_steps = []
    for local_id, kind, arguments in steps:
        spec = kinds[kind]
        compound = kind.startswith("sub-goal")
        body_steps.append({
            "local_id": local_id, "task_type_ref": spec["task_type_ref"],
            "form": "compound" if compound else "primitive", "arguments": arguments,
            "required_capabilities": [] if compound else list(spec["required_capabilities"]),
            "obligation_relation": "refines_parent"})
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": body_steps,
        "ordering": [{"before": before, "after": after} for before, after in ordering],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": criterion, "child_step": local_id, "child_criterion_id": criterion,
                 "evidence_requirement": f"{local_id} 这一步完成 {criterion}"}
                for criterion, (local_id, _, _) in zip(criteria, steps, strict=True)],
            "outputs": {}, "finalizer_step": finalizer, "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _planner(steps, ordering, finalizer):
    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if (contexts and str((contexts[0]["request"].get("goal_type_ref") or {}).get("id")) == "user-goal"
                and not (package.get("method_selection") or [{}])[0].get("applicable")):
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": _root_method(contexts[0], steps, ordering, finalizer),
                                                 "rationale": "按步骤分工。"}}, "按步骤分工。")
        return planner_reply(request)

    return planner


DELIVERY = {"delivery": {"op": "output", "step": "a", "port": "delivery"}}


def _tasks(world: ProductWorld, mission_id: str) -> dict[str, str]:
    """local step id → task id, read off the committed plan (the step's own goal parameter
    is not unique, so the occurrence's position in the adopted method is used)."""
    network = world.loop._dispatch_for(mission_id).network(mission_id)
    found: dict[str, str] = {}
    for instance in network.method_instances:
        for child in instance.child_bindings:
            found[str(child.slot_key)] = str(network.occurrence(child.occurrence_id).task_id)
    return found


def _reasons(world: ProductWorld, mission_id: str) -> dict[str, ReadinessReason]:
    view = world.loop._dispatch_for(mission_id).read(mission_id)
    return {str(spec.task_id): view.reports[spec.occurrence_id].reason for spec in view.network.occurrences}


def _planner_intents(store, mission_id: str) -> int:
    return int(store.connection.execute(
        "SELECT COUNT(*) FROM dispatch_intents WHERE mission_id=? AND kind='plan' AND subject_id LIKE ?",
        (mission_id, f"{mission_id}:planner:%")).fetchone()[0])


async def _drive_until(world: ProductWorld, done, *, timeout: float = 60.0) -> None:
    stop = asyncio.Event()

    async def drive() -> None:
        while not stop.is_set():
            await world.loop.run()
            await world.deployment.between_cycles(auto=world.auto)
            await asyncio.sleep(0.02)

    runner = asyncio.create_task(drive())
    try:
        async with asyncio.timeout(timeout):
            while not done():
                if runner.done():
                    runner.result()
                await asyncio.sleep(0.02)
    finally:
        stop.set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


# ======================================================================================
# §7.3 第 1 条：A/B→C（非空 DATA + 纯 ORDER）
# ======================================================================================


def test_abc_runs_to_completed_without_asking_the_main_planner_again(tmp_path) -> None:
    """§7.3-1：A、B 完成后 C 由原循环自动启动；计划提交之后不再请求主规划器、没有再规划痕迹。"""

    steps = [("a", "prepare-delivery", {}), ("b", "prepare-delivery", {}),
             ("c", "continue-delivery", DELIVERY)]
    provider = LayeredScriptedProvider(planner=_planner(steps, [("b", "c")], "c"))

    async def case():
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = world.create({"goal": "先写 a、b，再接着 a 写 c", "idempotency_key": "tg2b-abc-run",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            tasks = _tasks(world, mission_id)
            network = world.loop._dispatch_for(mission_id).network(mission_id)
            consumer = next(network.binding_for_task(spec.task_id) for spec in network.occurrences
                            if str(spec.task_id) == tasks["c"])
            events = list(world.store.list_events(mission_id))
            attempts = {local: [a.id for a in world.store.list_attempts(task)] for local, task in tasks.items()}
            return mission, tasks, consumer, events, attempts

    mission, tasks, consumer, events, attempts = asyncio.run(case())
    types = [event.type for event in events]
    assert mission.status is MissionStatus.COMPLETED, (mission.status, mission.stop_reason, types[-30:])
    # 前提：C 唯一的 DATA 输入来自 A（对 B 只是先后）
    assert [port.port_key for port in consumer.input_ports] == ["delivery"]
    # 计划提交之后，FAST 推进不再请求主规划器：只有提做法、采用两轮，仍是开局那一版计划
    assert provider.asked.count("planner") == 2
    assert types.count("PlanRevisionCommitted") == 1
    committed_at = types.index("PlanRevisionCommitted")
    assert not [kind for kind in types[committed_at:] if kind in PLANNING_TRACES]
    # C 只跑一次，并且是在 A 与 B 都正式完成之后才建 Attempt
    assert len(attempts["c"]) == 1
    seq: dict[Any, int] = {}
    for event in events:
        if event.type == "TaskCompleted":
            seq.setdefault(("done", event.task_id), event.seq)
        if event.type == "AttemptCreated" and (event.attempt_id == attempts["c"][0]
                                               or event.payload.get("attempt_id") == attempts["c"][0]):
            seq.setdefault("c_created", event.seq)
    assert seq["c_created"] > seq[("done", tasks["a"])]
    assert seq["c_created"] > seq[("done", tasks["b"])]


# ======================================================================================
# §7.3 第 4 条：C 等数据 / 另一分支需结构规划时，独立的 D 仍推进
# ======================================================================================


def test_c_waiting_for_data_does_not_hold_back_the_independent_d(tmp_path) -> None:
    """§7.3-4 前半：C 等 A 的数据时，独立就绪的 D 同 A 一起被派发（执行者的调用被扣住，A 不会完成）；
    C 不提前派发，也没有请求规划。"""

    steps = [("a", "prepare-delivery", {}), ("c", "continue-delivery", DELIVERY), ("d", "prepare-delivery", {})]
    provider = LayeredScriptedProvider(planner=_planner(steps, [], "c"))
    provider.held.add("worker")

    async def case():
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as world:
                mission_id = world.create({"goal": "a 与 d 各写一份，c 接着 a 写", "idempotency_key": "tg2b-branch-data",
                                           "success_criteria": ["file:a.md", "file:c.md", "file:d.md"]})["mission_id"]
                await _drive_until(world, lambda: provider.asked.count("worker") >= 2)
                tasks = _tasks(world, mission_id)
                reasons = _reasons(world, mission_id)
                attempts = {local: world.store.list_attempts(task) for local, task in tasks.items()}
                return reasons, tasks, attempts, _planner_intents(world.store, mission_id)
        finally:
            provider.release.set()

    reasons, tasks, attempts, planner_rounds = asyncio.run(case())
    assert reasons[tasks["c"]] is ReadinessReason.WAITING_DATA
    assert len(attempts["d"]) == 1, "独立的 D 必须推进"
    assert len(attempts["a"]) == 1
    assert attempts["c"] == [], "C 在等数据，不能提前派发"
    assert planner_rounds == 2  # 提做法、采用；之后没有再请求规划
    assert provider.asked.count("worker") == 2


def test_a_branch_that_needs_structural_planning_does_not_block_the_independent_d(tmp_path) -> None:
    """§7.3-4 后半：S 分支是还没有做法的复合子目标，系统记"目标未细化"的请求并为它开一轮规划；
    这一轮在途（规划器的调用被扣住）时，独立的 D 与 A 仍被派发，S 自身不起执行者，C 在等数据。
    同一版计划上再收集一次触发不产生第二条请求。"""

    steps = [("a", "prepare-delivery", {}), ("c", "continue-delivery", DELIVERY), ("d", "prepare-delivery", {}),
             ("s", "sub-goal-1", {"goal": {"op": "constant", "value": "写出 s.md"}})]
    holder: dict[str, Any] = {}
    root_planner = _planner(steps, [], "c")

    def planner(request: Any) -> Any:
        reply = root_planner(request)
        holder["calls"] = holder.get("calls", 0) + 1
        if holder["calls"] == 2:  # adopted the root method; the sub-goal's round is held from now on
            holder["provider"].held.add("planner")
        return reply

    provider = LayeredScriptedProvider(planner=planner)
    holder["provider"] = provider
    provider.held.add("worker")

    async def case():
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as world:
                mission_id = world.create({"goal": "a、d 各写一份，c 接着 a 写，s 是一个子目标",
                                           "idempotency_key": "tg2b-branch-slow",
                                           "success_criteria": ["file:a.md", "file:c.md", "file:d.md", "file:s.md"]})["mission_id"]
                await _drive_until(world, lambda: provider.asked.count("planner") >= 3
                                   and provider.asked.count("worker") >= 2)
                tasks = _tasks(world, mission_id)
                attempts = {local: world.store.list_attempts(task) for local, task in tasks.items()}
                in_flight = [intent for intent in world.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                             if intent.mission_id == mission_id and intent.kind == "plan"]
                requested = [event for event in world.store.list_events(mission_id)
                             if event.type == "PlanningRepairRequested"]
                again = collect_triggers(world.loop, world.store.get_mission(mission_id))
                return tasks, attempts, in_flight, requested, again, _planner_intents(world.store, mission_id)
        finally:
            provider.release.set()

    tasks, attempts, in_flight, requested, again, planner_rounds = asyncio.run(case())
    assert len(in_flight) == 1, "the sub-goal's planning round is in flight"
    assert [event.payload["request"]["trigger_source"] for event in requested] == ["GOAL_UNREFINED"]
    assert len(attempts["d"]) == 1, "S 的结构规划不应阻断 D"
    assert len(attempts["a"]) == 1
    assert attempts["s"] == []  # 复合目标不起执行者
    assert attempts["c"] == []
    assert again is False, "同一修订上再问一次不产生第二条请求"
    assert planner_rounds == 3
