# SPDX-License-Identifier: Apache-2.0
"""做法跨任务复用（HTN 补齐阶段 C3）：类型与任务无关、全库做法只当先例。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from agent_orchestrator.planning.htn.world import catalog_digest
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world, user_goal_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _events(world: Any, mission_id: str, kind: str) -> list[Any]:
    return [event for event in world.store.list_events(mission_id) if event.type == kind]


def test_types_do_not_depend_on_the_mission(tmp_path):
    """两个目标、要求条数、允许工具都不同的任务：类型目录逐字节相同；用户原话与要求只在根绑定上；
    根目标的做法漏链一条要求，提做法时就退回（不花审阅）。

    **改坏检验**：步骤类型说明文字改回用户原话 → 目录哈希不等；删提案检查的根分支 → 漏链的做法被送审。"""
    dropped: list[str] = []

    def planner(request: Any):
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not dropped and len(contexts[0]["request"]["criterion_evidence"]) == 2:
            method = one_step_method(contexts[0])
            dropped.append(method["composition"]["criterion_links"].pop()["parent_criterion_id"])
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "一步写完。"}}, "先提一个做法。")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            first = world.create({"goal": "写一份笔记", "idempotency_key": "types-1",
                                  "success_criteria": ["file:notes/a.md"]})
            second = world.create({"goal": "整理两份清单", "idempotency_key": "types-2",
                                   "success_criteria": ["file:lists/x.md", "file:lists/y.md"]})
            missions = [world.store.get_mission(item["mission_id"]) for item in (first, second)]
            worlds = [user_goal_world(world.loop, missions[0]),
                      user_goal_world(world.loop, replace(missions[1], allowed_tools=("workspace_read_file",)))]
            assert catalog_digest(worlds[0]) == catalog_digest(worlds[1])
            refs = [sorted((t.task_type_ref.id, t.task_type_ref.content_hash) for t in w.catalog.task_types())
                    for w in worlds]
            assert refs[0] == refs[1] and len(refs[0]) == 5
            assert all(not t.goal_signature.coverage_criteria and mission.goal not in t.goal_signature.statement
                       for w, mission in zip(worlds, missions, strict=True) for t in w.catalog.task_types())

            roots = []
            for mission in missions:
                network = world.loop._dispatch_for(mission.id).network(mission.id)
                [occurrence] = network.root_occurrence_ids
                roots.append(network.binding_for_occurrence(occurrence))
            assert [root.goal_signature.statement for root in roots] == ["写一份笔记", "整理两份清单"]
            assert [root.goal_signature.coverage_criteria for root in roots] == [
                ("c-user-1",), ("c-user-1", "c-user-2")]
            assert roots[0].goal_signature.signature_id == roots[1].goal_signature.signature_id
            assert roots[0].contract_hash != roots[1].contract_hash

            mission = await world.run_until_settled(second["mission_id"], rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            refusals = [" ".join(item["detail"] for item in event.payload["detail"]["problems"])
                        for event in _events(world, mission.id, "PlanningRejected")]
            assert dropped == ["c-user-2"]
            assert len(refusals) == 1 and "ROOT_COVERAGE_GAP" in refusals[0] and "c-user-2" in refusals[0]
            # the refused method never reached a review: one method was proposed and reviewed
            assert len(_events(world, mission.id, "PlanningMethodProposed")) == 1

    asyncio.run(case())
