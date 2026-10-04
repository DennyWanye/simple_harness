# SPDX-License-Identifier: Apache-2.0
"""H4：结构修复换掉一个无关步骤后，已经验收的真实内容原样保留。

产品同形部署（建任务时绑定执行图、保证通道、原生执行池），计划由规划器提出、经独立审阅、采用后
提交，执行与验收是主循环真跑；只有模型回复是脚本。根做法两步：子目标 ``assess``（它自己的做法是
一个叶子 ``leaf``，承接第一条要求）→ 原子步骤 ``act``（承接第二条要求）。子目标的叶子验收、子目标
组合审阅形成目标结论后，``act`` 的执行者调用还在跑；规划器对 ``act`` 提后继步骤
（PROPOSE_SUCCESSOR），执行图收敛取消 ``act`` 的尝试后计划提交第 2 版：

* 叶子的完成范围换了新版本，但它仍算完成——内容就是原来那份验收（那份验收和它的完成范围原样
  还在库里），子目标也仍算完成，当前的子步骤支撑里有这个叶子；
* 子目标仍带着它当前的目标结论。

2026-10-03（HTN 补齐阶段 A′，分诊表：重写 2 → 1）：原来两条建在端到端枢纽世界上（裸
``CommitService`` 建任务、手工提交计划、手工验收叶子），产品上已建不出来。原第一条的后半
（目标结论被标脏后，钉着它的复用提交不了）和原第二条（复用当前目标结论可以提交、冷读回来一致）
都要靠"复用已有目标"（BIND_EXISTING，类型须允许复用已验收结果）才走得到；产品的规划世界没有可复用
的类型，这一块按分诊裁决⑥（只读子目标共享 R14）等阶段 D 带共享类型的测试 world_factory，记偏离。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _extra in (_HERE, _HERE / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import enabled_world  # noqa: E402
from test_nested_compound_composition import _method  # noqa: E402

from agent_orchestrator.orchestrator.completion_status import read_occurrence_completion  # noqa: E402
from agent_orchestrator.orchestrator.completion_support import current_child_supports  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of, role_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    decision,
    planner_reply,
)

CRITERIA = ("file:facts.md", "file:NOTES.md")
SUB = {"goal": {"op": "constant", "value": "写出 facts.md"}}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _planner(request: Any) -> Any:
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
        goal = str((contexts[0]["request"].get("goal_type_ref") or {}).get("id"))
        if goal == "user-goal":
            method = _method(contexts[0], [("assess", "sub-goal-1", SUB), ("act", "prepare-delivery", {})],
                             [("assess", 0), ("act", 1)], "act", ordering=[("assess", "act")])
        else:
            method = _method(contexts[0], [("leaf", "prepare-delivery", {})], [("leaf", 0)], "leaf")
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": method, "rationale": goal}}, goal)
    return planner_reply(request)


class _Provider(LayeredScriptedProvider):
    """``act``（写 NOTES.md 的那一步）的执行者调用一直在跑，直到用例放行。"""

    def __init__(self) -> None:
        super().__init__(planner=_planner)
        self.acting = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker" and "NOTES.md" in json.dumps(
                package_of(request).get("task_contract", {}).get("outputs") or [], ensure_ascii=False):
            self.acting.set()
            await self.release.wait()
        return await super().invoke(request, cancel=cancel)


def _signature(network: Any, occurrence: Any) -> str:
    return str(network.binding_for_occurrence(occurrence).goal_signature.signature_id)


def test_an_unrelated_successor_retains_the_real_accepted_content(tmp_path):
    provider = _Provider()

    async def case() -> None:
        async with enabled_world(tmp_path, key="h4-retained", criteria=CRITERIA, provider=provider,
                                 max_concurrency=2, max_concurrent_model_calls=2) as world:
            store, mission_id = world.store, world.mission.id
            htn = HtnStore(store)
            dispatch = world.dispatch

            def assess_resolved() -> bool:
                network = dispatch.network(mission_id)
                if network is None:
                    return False
                tasks = {str(spec.task_id) for spec in network.occurrences
                         if _signature(network, spec.occurrence_id) == "sub-goal-1"}
                resolved = {str(item.goal_task_id) for item in htn.list_goal_resolutions(mission_id)}
                return provider.acting.is_set() and bool(tasks & resolved)

            await world.until(assess_resolved, timeout=60)
            network = dispatch.network(mission_id)
            assess = next(spec for spec in network.occurrences if _signature(network, spec.occurrence_id) == "sub-goal-1")
            [leaf] = [child.occurrence_id for child in network.adopted_children(assess.occurrence_id)]
            act = next(binding for binding in network.task_bindings
                       if str(binding.form) == "primitive" and binding.task_id != network.occurrence(leaf).task_id)
            [resolution] = [item for item in htn.list_goal_resolutions(mission_id)
                            if str(item.goal_task_id) == str(assess.task_id)]
            leaf_before = read_occurrence_completion(store, mission_id, str(leaf))
            assert leaf_before.complete
            completion = OperationCompletionStore(store)
            [original] = completion.list_scoped_contributions(mission_id, leaf_before.scope.scope_id)
            assert str(resolution.validity) == "CURRENT"

            # 规划器对还在跑的 act 提后继步骤：计划提交第 2 版。
            intent = await world.open_planner_round()
            package = intent.config["planning_package"]
            subject = next(item for item in package["planning_subjects"] if item["task_id"] == str(act.task_id))

            def visible(kind: str, identity: str) -> dict[str, Any]:
                return next(item for item in package["visible_refs"] if item["kind"] == kind and item["id"] == identity)

            task_type = next(spec for spec in dispatch.require_planning_world().catalog.task_types()
                             if spec.goal_signature == act.goal_signature)
            await world.answer(intent, {
                "schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "act 要按新的来源重做。", "reason_refs": [], "assumptions": [], "uncertainties": [],
                "alternatives": [], "replan_triggers": [],
                "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", str(act.task_id)),
                            "obligation_ref": visible("obligation", str(act.obligation_id)),
                            "goal_type_ref": task_type.task_type_ref.to_json(),
                            "bindings": {"goal": "按新的来源写 NOTES.md。"}}})
            # 提交先经执行图收敛：还在跑的 act 的尝试被取消；那次慢调用随后才回来，晚到的结果不被接受。
            await world.until(lambda: any(
                event.type == "AttemptCancelled"
                and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")
                for event in store.list_events(mission_id)), timeout=60)
            provider.release.set()
            decisions = PlanningDecisionStore(store)
            await world.until(lambda: decisions.get_planning_decision_by_attempt(intent.intent_id, 0)["status"]
                              != "COMPILED", timeout=60)
            stored = decisions.get_planning_decision_by_attempt(intent.intent_id, 0)
            assert stored["status"] == "COMMITTED", (stored["status"], stored["detail"])
            after = dispatch.network(mission_id)
            assert int(after.plan_revision) == int(network.plan_revision) + 1
            assert act.task_id not in {spec.task_id for spec in after.occurrences}

            # 叶子的完成范围是新版本的，内容仍是原来那份验收；子目标仍算完成。
            leaf_after = read_occurrence_completion(store, mission_id, str(leaf))
            assert leaf_after.scope.scope_id != leaf_before.scope.scope_id
            assert leaf_after.complete
            assert read_occurrence_completion(store, mission_id, str(assess.occurrence_id)).complete
            assert completion.get_acceptance_scope_exact(mission_id, original["document"].acceptance_id) == original
            nested = after.adopted_instance_for(assess.occurrence_id)
            assert str(leaf) in current_child_supports(store, mission_id, nested.child_bindings)
            [still] = [item for item in htn.list_goal_resolutions(mission_id)
                       if str(item.resolution_id) == str(resolution.resolution_id)]
            assert str(still.validity) == "CURRENT"  # 子目标仍带着它当前的目标结论

    asyncio.run(case())
