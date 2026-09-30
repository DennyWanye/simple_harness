# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""资料换版真机（2026-09-30 B 第 1 局）：规划器说"等这个方法跑完"被拒，白花规划次数。

修复轮里一步正在跑，规划器最自然的回答是 WAIT，等的对象常写成那一步所在的方法实例
（或目标）。旧规则只认"正在跑的步骤"或"已完成的记录"，方法实例一律判"没有可等的
生产者"拒掉。现在：方法实例 / 目标核对身份后，换成它下面正在跑的步骤来等（登记里同时
记下规划器原话）；下面没有正在跑的步骤仍然拒，理由写明。

真实编排器、真实严格执行图；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[3]
for _extra in (_TESTS / "orchestrator" / "full_target", _TESTS / "orchestrator" / "full_target" / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import enabled_world  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.contracts import TaskStatus  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402


def _events(loop, mission_id, kind):  # type: ignore[no-untyped-def]
    return [e for e in loop.store.list_events(mission_id) if e.type == kind]


async def _wait_on_instance(world, *, running: bool, key: str, forged: bool = False):  # type: ignore[no-untyped-def]
    loop, mission, dispatch = world.loop, world.mission, world.dispatch
    await world.commit_seed()
    network = dispatch.network(mission.id)
    instance = next(i for i in network.method_instances if i.instance_id in set(network.adopted_instance_ids))
    leaves = [str(network.binding_for_task(s.task_id).task_id) for s in network.occurrences
              if str(s.form) == "primitive"]
    leaf = loop.store.get_task(leaves[0])
    if running:
        loop.store.update_task(replace(leaf, status=TaskStatus.ACTIVE, version=leaf.version + 1),
                               expected_version=leaf.version)
    intent = await loop._create_planner_intent(mission.id, ordinal=2)
    package = intent.config["planning_package"]
    visible = next(r for r in package["visible_refs"]
                   if r["kind"] == "method_instance" and r["id"] == str(instance.instance_id))
    if forged:
        visible = {**visible, "content_hash": "0" * 64}
    body = {"schema_version": 1, "decision_type": "WAIT",
            "subject_key": package["planning_subjects"][0]["subject_key"],
            "rationale": "The step under this method is still running; wait for it.",
            "reason_refs": [], "assumptions": [], "uncertainties": [], "alternatives": [],
            "replan_triggers": [],
            "payload": {"wait_for": [visible], "reason": "a step of this method is running"}}
    PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id, principal=Principal(loop._owner)).issue(
        mission.id, command_id=f"grant-{key}", request_id=intent.intent_id)
    await loop._collect_plan_decision(intent, object(), loop.store.get_mission(mission.id),
                                      "<planning_decision>" + json.dumps(body) + "</planning_decision>", dispatch)
    return visible, leaves[0]


def test_a_wait_on_a_method_instance_waits_for_its_running_steps(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="wait-instance") as world:
            loop, mission = world.loop, world.mission
            visible, leaf_id = await _wait_on_instance(world, running=True, key="wait-instance")
            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload
            assert evaluated["status"] == "NO_STATE_CHANGE", evaluated
            registered = _events(loop, mission.id, "PlanningWaitRegistered")
            assert len(registered) == 1
            payload = registered[0].payload
            assert payload["requested_wait_for"] == [visible]  # what the planner said
            assert [(r["kind"], r["id"]) for r in payload["wait_for"]] == [("task", leaf_id)]
            # the running step finishes → the planner is asked again
            leaf = loop.store.get_task(leaf_id)
            loop.store.update_task(replace(leaf, status=TaskStatus.COMPLETED, version=leaf.version + 1),
                                   expected_version=leaf.version)
            await loop._wake_planning_waits()
            assert len(_events(loop, mission.id, "PlanningWaitWoken")) == 1
    asyncio.run(case())


def test_a_wait_on_a_method_instance_with_nothing_running_is_refused_in_plain_words(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="wait-idle") as world:
            loop, mission = world.loop, world.mission
            await _wait_on_instance(world, running=False, key="wait-idle")
            assert not _events(loop, mission.id, "PlanningWaitRegistered")
            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload
            assert evaluated["status"] == "REJECTED"
            assert evaluated["rejection_codes"] == ["PARAMETER_INVALID"]
            assert "no step under it is running" in evaluated["detail"]["reason"]
    asyncio.run(case())


def test_a_wait_on_a_method_instance_with_a_forged_digest_is_refused(tmp_path):
    # 伪造的引用在进入 WAIT 翻译之前就被"不是请求给出的引用"拒掉；翻译里的身份核对是
    # 第二道：防的是规划包给出后计划又换了版本的情况。
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="wait-forged") as world:
            loop, mission = world.loop, world.mission
            await _wait_on_instance(world, running=True, key="wait-forged", forged=True)
            assert not _events(loop, mission.id, "PlanningWaitRegistered")
            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload
            assert evaluated["status"] == "REJECTED"
            assert evaluated["rejection_codes"] == ["REF_OUTSIDE_CONTEXT"]
    asyncio.run(case())
