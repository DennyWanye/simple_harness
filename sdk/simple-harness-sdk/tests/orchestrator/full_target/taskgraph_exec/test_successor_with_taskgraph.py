# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""结构修复真机第 4 局（2026-09-30）：执行图开启时，给一步提后继步骤要能出第 2 版计划。

后继步骤给父目标换一个新方法实例，父目标（这里是任务根目标）的记录因此换代。任务根目标
被"任务本身"独立需要；执行图共享检查把"独立需要的工作在改动目标里"一律拒掉
（TASKGRAPH_INDEPENDENT_WORK_STILL_REQUIRED），于是只要执行图开着，任何改根目标方法的
结构修复都提交不了。独立需要的工作不能被**移除**；保留下来、只是重新组合的要放行。

真实编排器、真实严格执行图；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[3]
for _extra in (_TESTS / "orchestrator" / "full_target", _TESTS / "orchestrator" / "full_target" / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import enabled_world  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402


def test_a_successor_for_a_leaf_commits_a_second_plan_revision_with_the_taskgraph_on(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="tg-successor") as world:
            loop, mission, dispatch = world.loop, world.mission, world.dispatch
            await world.commit_seed()
            network = dispatch.network(mission.id)
            assert int(network.plan_revision) == 1
            # the first leaf of the chain: a producer whose output the next leaf consumes
            old = next(b for b in network.task_bindings if str(b.form) == "primitive" and not b.input_ports)
            world_env = dispatch.require_planning_world()
            task_type = next(s for s in world_env.catalog.task_types() if s.goal_signature == old.goal_signature)
            intent = await loop._create_planner_intent(mission.id, ordinal=2)
            package = intent.config["planning_package"]
            subject = next(row for row in package["planning_subjects"] if row["task_id"] == str(old.task_id))

            def visible(kind, identity):  # type: ignore[no-untyped-def]
                return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

            body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                    "rationale": "The accepted leaf cites an old source; a successor redoes it.",
                    "reason_refs": [], "assumptions": [], "uncertainties": [], "alternatives": [],
                    "replan_triggers": [],
                    "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", str(old.task_id)),
                                "obligation_ref": visible("obligation", str(old.obligation_id)),
                                "goal_type_ref": task_type.task_type_ref.to_json(),
                                "bindings": dict(old.typed_parameters)}}
            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id, principal=Principal(loop._owner)).issue(
                mission.id, command_id="grant-tg-successor", request_id=intent.intent_id)
            await loop._collect_plan_decision(intent, object(), loop.store.get_mission(mission.id),
                                              "<planning_decision>" + json.dumps(body) + "</planning_decision>", dispatch)
            decisions = PlanningDecisionStore(loop.store)
            async with asyncio.timeout(30):  # with the TaskGraph on, the commit lands in later cycles
                while decisions.get_planning_decision_by_attempt(intent.intent_id, 0)["status"] == "COMPILED":
                    await loop._cycle()
                    await asyncio.sleep(.01)
            stored = decisions.get_planning_decision_by_attempt(intent.intent_id, 0)
            rejected = [e.payload for e in loop.store.list_events(mission.id) if e.type == "PlanningRejected"]
            assert stored is not None and stored["status"] == "COMMITTED", (stored and stored["status"], rejected)
            after = dispatch.network(mission.id)
            assert int(after.plan_revision) == 2
            assert old.task_id not in {spec.task_id for spec in after.occurrences}
            # the Mission root is still in the plan, re-versioned under the new method instance
            assert set(after.root_occurrence_ids) == set(network.root_occurrence_ids)
            # re-versioned leaves (input revision +1, often with byte-identical inputs) dispatch again
            revised = {str(b.task_id) for b in after.task_bindings
                       if str(b.form) == "primitive" and int(b.input_binding_revision) >= 1}
            assert revised
            async with asyncio.timeout(30):
                while not any(loop.store.list_attempts(task_id) for task_id in revised):
                    await loop._cycle()
                    await asyncio.sleep(.01)
    asyncio.run(case())
