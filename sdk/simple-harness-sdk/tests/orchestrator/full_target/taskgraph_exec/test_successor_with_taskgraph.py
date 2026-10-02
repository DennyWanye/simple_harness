# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""结构修复真机第 4 局（2026-09-30）：执行图开启时，给一步提后继步骤要能出第 2 版计划。

后继步骤给父目标换一个新方法实例，父目标（这里是任务根目标）的记录因此换代。任务根目标
被"任务本身"独立需要；执行图共享检查把"独立需要的工作在改动目标里"一律拒掉
（TASKGRAPH_INDEPENDENT_WORK_STILL_REQUIRED），于是只要执行图开着，任何改根目标方法的
结构修复都提交不了。独立需要的工作不能被**移除**；保留下来、只是重新组合的要放行。

结构修复真机第 7 局（2026-09-30，原 ``test_revoked_generation_cleared``）：后继步骤提交第
2 版计划时，被换代的目标（含任务根目标）各记一条"旧派发作废"标记；新一代的根结论提交后，
根目标上的标记要清掉（否则收尾检查判"内容未完成"，终审通过后任务永远收不了尾），别的目标
上的标记不是这条结论的，照旧保留。

产品同形部署上的真实编排器与执行图（两步链：第二步读第一步的交付）；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[3]
for _extra in (_TESTS / "orchestrator" / "full_target", _TESTS / "orchestrator" / "full_target" / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world  # noqa: E402

from agent_orchestrator.contracts import TaskStatus  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402


def _marks(world):  # type: ignore[no-untyped-def]
    rows = world.store.connection.execute(
        "SELECT subject_id, reason, state FROM validity_dirty WHERE mission_id=?", (world.mission.id,))
    return {(str(r[0]), str(r[1])): str(r[2]) for r in rows}


def test_a_successor_for_a_leaf_commits_a_second_plan_revision_with_the_taskgraph_on(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="tg-successor", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA) as world:
            loop, mission, dispatch = world.loop, world.mission, world.dispatch
            await world.commit_seed()
            network = dispatch.network(mission.id)
            assert int(network.plan_revision) == 1
            # the first leaf of the chain: a producer whose output the next leaf consumes
            old = next(b for b in network.task_bindings if str(b.form) == "primitive" and not b.input_ports)
            # it ran and was accepted; the next step is held at its model call
            await world.until(lambda: loop.store.get_task(str(old.task_id)).status is TaskStatus.COMPLETED)
            world.provider.held.add("worker")
            world_env = dispatch.require_planning_world()
            task_type = next(s for s in world_env.catalog.task_types() if s.goal_signature == old.goal_signature)
            intent = await world.open_planner_round()
            package = intent.config["planning_package"]
            subject = next(row for row in package["planning_subjects"] if row["task_id"] == str(old.task_id))

            def visible(kind, identity):  # type: ignore[no-untyped-def]
                return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

            await world.answer(intent, {
                "schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "The accepted leaf cites an old source; a successor redoes it.",
                "reason_refs": [], "assumptions": [], "uncertainties": [], "alternatives": [],
                "replan_triggers": [],
                "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", str(old.task_id)),
                            "obligation_ref": visible("obligation", str(old.obligation_id)),
                            "goal_type_ref": task_type.task_type_ref.to_json(),
                            "bindings": {"goal": "Redo the first file from the current source."}}})
            decisions = PlanningDecisionStore(loop.store)

            def stored():  # type: ignore[no-untyped-def]
                return decisions.get_planning_decision_by_attempt(intent.intent_id, 0)

            # with the TaskGraph on, the commit lands in later cycles
            await world.until(lambda: stored()["status"] != "COMPILED")
            rejected = [e.payload for e in loop.store.list_events(mission.id) if e.type == "PlanningRejected"]
            assert stored()["status"] == "COMMITTED", (stored()["status"], stored()["detail"], rejected)
            after = dispatch.network(mission.id)
            assert int(after.plan_revision) == 2
            assert old.task_id not in {spec.task_id for spec in after.occurrences}
            # the Mission root is still in the plan, re-versioned under the new method instance
            assert set(after.root_occurrence_ids) == set(network.root_occurrence_ids)
            roots = {str(item) for item in after.root_occurrence_ids} | {
                str(after.occurrence(item).task_id) for item in after.root_occurrence_ids}
            revoked = {subject for (subject, reason), state in _marks(world).items()
                       if reason == "dispatch_generation_revoked" and state == "PENDING"}
            assert roots <= revoked and revoked - roots  # the root and the replaced goals
            # re-versioned leaves (input revision +1, often with byte-identical inputs) dispatch again
            revised = {str(b.task_id) for b in after.task_bindings
                       if str(b.form) == "primitive" and int(b.input_binding_revision) >= 1}
            assert revised
            world.provider.release.set()
            await world.until(lambda: any(loop.store.list_attempts(task_id) for task_id in revised))

            # the new generation runs to the root conclusion: the root's revocation mark is
            # cleared by it; the replaced goals' marks are not this conclusion's to clear
            await world.until(lambda: str(loop.store.get_mission(mission.id).status.value) == "COMPLETED", timeout=60)
            marks = _marks(world)
            for subject in roots:
                assert marks[(subject, "dispatch_generation_revoked")] == "CLEARED"
            for subject in revoked - roots:
                assert marks[(subject, "dispatch_generation_revoked")] == "PENDING"
    asyncio.run(case())
