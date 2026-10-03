# SPDX-License-Identifier: Apache-2.0
"""中间目标的产出怎样接到后面的步骤（HTN 精简 片 B 真机第 5 局，2026-10-01）。

真机上最后一步看不到中间目标下面写出的文件：一步执行时只铺通过输入端口接进来的上游产出，
"只排在后面"什么都拿不到，而中间目标类型当时不声明端口，后面的步骤无处可接。

现在中间目标可以声明输出端口；它对外交付的是**收尾步骤的同名产出**：
* 注册时：目标类型声明了输出端口的做法必须写收尾步骤，且那一步的类型声明了同名端口；
* 解析时：中间目标完成（组合审阅通过、结论形成）之后，它的端口对到收尾步骤已验收的产出上；
  没完成之前，接它的步骤照常等数据。
秩序（端口存在、先后、完成才放行），不判断内容。

HTN 补齐阶段 A′：主循环那条跑在产品同形世界（:mod:`_assured_loop`、:mod:`_subgoal_world`：
子目标 ``sub-goal-1`` 与步骤类型都声明 ``delivery`` 端口，``continue-delivery`` 接上游交付），
做法由规划器提出、过独立审阅后采用；注册检查那条只组规划世界（不起主循环），照旧。
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from _assured_loop import assured_loop, method_body, run_until
from _subgoal_world import (
    NEXT,
    PART,
    STATEMENTS,
    USER,
    WRITE,
    by_goal_type,
    output_of,
    plan_revision,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fixtures" / "htn"))
from htn_world import Env, method, param, step  # noqa: E402

from agent_orchestrator.artifacts.input_bindings import (  # noqa: E402
    AcceptedOutput,
    ResourceIdentity,
)
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.semantic_base import VersionedRef  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402

SO_ROOT, SO_PART, SO_WRITE, SO_NEXT = "so.goal", "so.part", "so.write", "so.continue"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _env(mission_id: str) -> Env:
    world = Env(mission=mission_id)
    world.register_type(SO_ROOT, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                        criteria=USER, domain="so", level=0)
    world.register_type(SO_PART, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                        outputs=(("delivery", "so.delivery"),), domain="so", level=1)
    world.register_type(SO_WRITE, parameters=(("subject", "string"),),
                        outputs=(("delivery", "so.delivery"),), capabilities=("plan.read",),
                        criteria=USER, domain="so")
    world.register_type(SO_NEXT, parameters=(("subject", "string"),),
                        inputs=(("delivery", "so.delivery", True),),
                        outputs=(("delivery", "so.delivery"),), capabilities=("plan.read",),
                        criteria=USER, domain="so")
    world.register_type("so.note", parameters=(("subject", "string"),),
                        outputs=(("note", "so.note-out"),), capabilities=("plan.read",),
                        criteria=USER, domain="so")
    return world


def _out(step_name: str):
    return {"op": "output", "step": step_name, "port": "delivery"}


def _inner(method_id: str = "so.inner", *, finalizer: str | None = "b", last: str = SO_NEXT):
    arguments = {"subject": param("subject")}
    if last == SO_NEXT:
        arguments["delivery"] = _out("a")
    return method(
        method_id, SO_PART, parameter_schema=f"{SO_PART}.params",
        steps=(step("a", SO_WRITE, TaskForm.PRIMITIVE, {"subject": param("subject")}, capabilities=("plan.read",)),
               step("b", last, TaskForm.PRIMITIVE, arguments, capabilities=("plan.read",))),
        ordering=(("a", "b"),),
        links=(("c-user-1", "a", None), ("c-user-2", "b", None)), finalizer=finalizer)


def _problems(env: Env, contract) -> list[str]:
    return [f"{item.code}: {item.detail}" for item in env.admit(contract).problems]


def test_a_goal_that_declares_an_output_port_needs_a_finalizer_that_publishes_it():
    env = _env("mission-so")
    assert _problems(env, _inner()) == []
    missing = _problems(env, _inner("so.no-finalizer", finalizer=None))
    assert any(item.startswith("PORT_UNAVAILABLE") and "finalizer" in item for item in missing), missing
    wrong = _problems(env, _inner("so.wrong-port", last="so.note"))
    assert any(item.startswith("PORT_UNAVAILABLE") and "'delivery'" in item for item in wrong), wrong


def _fabricated(occurrence, task_id: str, *, port: str = "delivery") -> AcceptedOutput:
    return AcceptedOutput(
        producer_occurrence=occurrence, producer_task_ref=task_id, output_port=port,
        producer_result_id="result-b", acceptance_id="acc-b", support_revision=1,
        artifact_id="artifact-b", content_hash="c" * 64,
        schema_ref=VersionedRef("so.delivery", 1, "d" * 64), source_revision="1",
        source_identity=ResourceIdentity(namespace="attempt:b", path="check.md"))


def _root_feeding_tail(context):
    """root → the sub-goal, then a step that consumes what the sub-goal delivered."""
    return method_body(context, steps=[("part", PART, {"goal": {"op": "constant", "value": "前两份笔记"}}),
                                       ("tail", NEXT, output_of("part"))],
                       links=[("c-user-1", "part"), ("c-user-2", "part"), ("c-user-3", "tail")], finalizer="tail")


def _part_feeding_b(context):
    """the sub-goal → a, then b consuming a's delivery; b is the finalizer."""
    return method_body(context, steps=[("a", WRITE, {}), ("b", NEXT, output_of("a"))], ordering=[("a", "b")],
                       links=[("c-user-1", "a"), ("c-user-2", "b")], finalizer="b")


def test_a_step_after_the_sub_goal_is_fed_by_the_sub_goals_finalizer_once_the_goal_is_complete(tmp_path):
    async def case():
        scripted = LayeredScriptedProvider(planner=by_goal_type({"user-goal": _root_feeding_tail,
                                                                  PART: _part_feeding_b}))
        scripted.held.add("worker")  # what the steps deliver is not what this case is about
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            # a plan whose later step reads the sub-goal's port commits, before and after refinement
            assert await run_until(world, lambda w: plan_revision(w) == 2)
            dispatch = world.loop._new_mode(world.mission)
            network = dispatch.network(world.mission.id)
            by_slot = {str(child.slot_key): child.occurrence_id
                       for instance in network.method_instances for child in instance.child_bindings}
            part, last, tail = by_slot["part"], by_slot["b"], by_slot["tail"]
            [edge] = [item for item in network.data_requirements if item.consumer_occurrence == tail]
            assert edge.producer_occurrence == part and edge.output_port == "delivery"

            delivered = _fabricated(last, str(network.binding_for_occurrence(last).task_id))
            # not complete yet: the goal's port offers nothing, whatever its steps accepted
            assert dispatch.goal_port_outputs(world.mission.id, network, (delivered,), complete=frozenset()) == ()
            assert dispatch.goal_port_outputs(world.mission.id, network, (delivered,),
                                              complete=frozenset({last, tail})) == ()
            # complete: the goal's port is the finalizer's accepted output, under the goal's name
            [alias] = dispatch.goal_port_outputs(world.mission.id, network, (delivered,),
                                                 complete=frozenset({part}))
            assert alias == replace(delivered, producer_occurrence=part)
            # an accepted output of the other step is not the goal's delivery
            first = by_slot["a"]
            other = _fabricated(first, str(network.binding_for_occurrence(first).task_id))
            assert dispatch.goal_port_outputs(world.mission.id, network, (other,),
                                              complete=frozenset({part})) == ()
            # nothing was accepted yet (the executor is held), so the live index offers the consumer nothing
            assert dispatch.accepted_outputs(world.mission.id, network).at(part, "delivery") == ()
        scripted.release.set()
    asyncio.run(case())
