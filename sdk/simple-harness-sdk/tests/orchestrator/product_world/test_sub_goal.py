# SPDX-License-Identifier: Apache-2.0
"""产品同形测试世界的代表用例二：子目标（HTN 补齐阶段 A′ 第 2 步）。

根目标的做法 = 一个子目标（承接第一条要求）+ 收尾一步（承接第二条）；子目标再由规划器提一个
一步做法。子目标的做法照样过独立审阅，按上级做法交给它的那条要求审；全部完成后根终审放行。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, planner_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _operator(request: dict[str, Any], suffix: str) -> dict[str, Any]:
    return next(item for item in request["operators"] if str(item["task_type_ref"]["id"]).endswith(suffix))


def root_with_sub_goal(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    part = next(item for item in request["subgoal_types"] if item["task_type_ref"]["id"] == "sub-goal-1")
    tail = _operator(request, "prepare-delivery")
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [
            {"local_id": "part", "task_type_ref": part["task_type_ref"], "form": "compound",
             "arguments": {"goal": {"op": "constant", "value": "写出 notes/a.md，列三条要点"}},
             "required_capabilities": [],
             "obligation_relation": "refines_parent"},
            {"local_id": "tail", "task_type_ref": tail["task_type_ref"], "form": "primitive", "arguments": {},
             "required_capabilities": list(tail["required_capabilities"]), "obligation_relation": "refines_parent"},
        ],
        "ordering": [{"before": "part", "after": "tail"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "part", "child_criterion_id": first,
                 "evidence_requirement": "part 这个子目标完成第一条要求"},
                {"parent_criterion_id": second, "child_step": "tail", "child_criterion_id": second,
                 "evidence_requirement": "tail 这一步完成第二条要求"},
            ],
            "outputs": {}, "finalizer_step": "tail", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def planner(request: Any):
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and str((contexts[0]["request"].get("goal_type_ref") or {}).get("id")) == "user-goal" \
            and not (package.get("method_selection") or [{}])[0].get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": root_with_sub_goal(contexts[0]),
                                             "rationale": "先做一个子目标，再收尾。"}},
                        "根目标拆成一个子目标和一个收尾步骤。")
    return planner_reply(request)


def test_a_sub_goal_is_planned_reviewed_and_completed_on_the_product_deployment(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            created = world.create({"goal": "写两份笔记", "idempotency_key": "sub-goal-1",
                                    "success_criteria": ["file:notes/a.md", "file:NOTES.md"]})
            mission = await world.run_until_settled(created["mission_id"], rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            compound = [b for b in world.deployment.duties.orchestrator.store.connection.execute(
                "SELECT task_id FROM task_semantics WHERE mission_id=? AND form='compound'",
                (mission.id,)).fetchall()]
            assert len({row[0] for row in compound}) == 2  # the root and its sub-goal
            # the sub-goal's own method went through its own independent review
            assert world.deployment.duties.policy_scopes and any(
                key.startswith("method-plan:") and "user-root-" not in key
                for key in world.deployment.duties.policy_scopes)

    asyncio.run(case())


def test_goal_port_schema_mismatch_is_unbound(tmp_path):
    """子目标的端口 = 它收尾步骤的同名端口；两边声明的格式不同就不对外交付（接它的步骤数据未绑定），
    并记一条说明（HTN 补齐阶段 D，补全方案 2.2）。

    **改坏检验**：去掉格式比较 → 格式不同也照样出别名 → 变红。"""
    from dataclasses import replace

    from agent_orchestrator.contracts.semantic_base import VersionedRef

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            created = world.create({"goal": "写两份笔记", "idempotency_key": "sub-goal-ports",
                                    "success_criteria": ["file:notes/a.md", "file:NOTES.md"]})
            mission = await world.run_until_settled(created["mission_id"], rounds=20)
            assert str(mission.status.value) == "COMPLETED"
            dispatch = world.loop._dispatch_for(mission.id)
            network = dispatch.network(mission.id)
            recorded = tuple(dispatch._recorded_outputs(mission.id, network))
            done = dispatch._complete_goals(mission.id, network)
            [goal] = done
            aliases = dispatch.goal_port_outputs(mission.id, network, recorded, complete=done)
            assert aliases and {item.producer_occurrence for item in aliases} == {goal}

            class OtherFormat:
                """The same plan, except the sub-goal declares another format on its ports."""

                def __getattr__(self, name):
                    return getattr(network, name)

                def binding_for_occurrence(self, occurrence):
                    binding = network.binding_for_occurrence(occurrence)
                    if occurrence != goal:
                        return binding
                    other = VersionedRef("user.other-format", 1, "c" * 64)
                    return replace(binding, output_ports=tuple(
                        replace(port, schema_ref=other) for port in binding.output_ports))

            assert dispatch.goal_port_outputs(mission.id, OtherFormat(), recorded, complete=done) == ()
            notes = [e.payload for e in world.store.list_events(mission.id) if e.type == "GoalPortSchemaMismatch"]
            assert [(n["goal_occurrence"], n["port"]) for n in notes] == [
                (str(goal), str(port.port_key)) for port in network.binding_for_occurrence(goal).output_ports]

    asyncio.run(case())
