# SPDX-License-Identifier: Apache-2.0
"""修复时换做法，在产品上真的走得通（2026-10-03，迁移裁决 A1 的重写）。

两条要求（facts.md、NOTES.md），规划器先提一个两步并行的做法 v1。写 facts.md 那步的内容审阅被
打回，此时写 NOTES.md 那步的执行者还在跑。修复轮里规划器拿到写新做法的材料，为这个目标提 v2
（一步写完两份文件），做法审阅通过后发 REPLACE_METHOD：

* 替换先经执行图收敛：v1 还在跑的那一步的尝试被取消，那次慢调用晚到的结果不被接受，
  然后新计划版本才提交（不另起"延后决定、冷启动续上"的旁路）；
* 修复请求被这次替换了结；任务按 v2 完成。

判断（重试还是换、换成什么）全在规划器；系统只呈现事实、审阅新做法、原子地提交替换。

**改坏检验**：修复轮的写新做法材料改回"只给没有做法的目标"→ 规划器无从提 v2 → 变红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    review_input,
    review_reply,
)

CRITERIA = ("file:facts.md", "file:NOTES.md")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _parallel_method(context: dict[str, Any]) -> dict[str, Any]:
    """两个互不依赖的"准备交付"步骤，各管一条要求。"""
    request = context["request"]
    operator = next(item for item in request["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]

    def step(local_id: str) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": operator["task_type_ref"], "form": "primitive",
                "arguments": {}, "required_capabilities": list(operator["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [step("facts"), step("notes")], "ordering": [],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "facts", "child_criterion_id": first,
                 "evidence_requirement": "facts 这一步写出第一份文件"},
                {"parent_criterion_id": second, "child_step": "notes", "child_criterion_id": second,
                 "evidence_requirement": "notes 这一步写出第二份文件"},
            ],
            "outputs": {}, "finalizer_step": "notes", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _planner(request: Any) -> Any:
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if not package.get("repair_requests"):
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": _parallel_method(contexts[0]),
                                                 "rationale": "两份文件分开写。"}}, "两步并行。")
        return planner_reply(request)
    [goal] = [item for item in package["views"]["goals"] if item.get("under_repair") and item.get("adopted_method")]
    subject = next(item["subject_key"] for item in package["planning_subjects"]
                   if item["occurrence_id"] == goal["occurrence_id"])
    current = goal["adopted_method"]["method_ref"]
    alternatives = [item["method_ref"] for item in package["views"]["methods"]
                    if item["method_ref"] != current
                    and any(report["verdict"] == "APPLICABLE" and report["goal_occurrence_id"] == goal["occurrence_id"]
                            for report in item.get("applicability", ()))]
    if not alternatives:
        [context] = [item for item in contexts if item["subject_key"] == subject]
        return decision(subject, "PROPOSE_METHOD",
                        {"method_proposal": {"method": one_step_method(context),
                                             "rationale": "换成一步写完两份文件。"}}, "被退回，换做法。")
    instance = next(item for item in package["visible_refs"]
                    if item["kind"] == "method_instance" and item["id"] == goal["adopted_method"]["method_instance_id"])
    return decision(subject, "REPAIR", {"repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                                        "replacement_method_ref": dict(alternatives[0]), "bindings": goal["params"]},
                    "换成审阅通过的新做法。")


class _Provider(LayeredScriptedProvider):
    """facts.md 的第一份内容被审阅打回；v1 里写 NOTES.md 的那一步一直在跑。"""

    def __init__(self) -> None:
        self.rejected = False

        def reviewer(request: Any) -> Any:
            data = review_input(request)
            if data is None:
                return None
            package = data.get("package") or {}
            if (str(package.get("purpose")) == "TASK_CONTENT" and not self.rejected
                    and "facts.md" in json.dumps(package, ensure_ascii=False)):
                self.rejected = True
                return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：facts.md 不满足要求。")
            return review_reply(data)

        super().__init__(planner=_planner, reviewer=reviewer)
        self.notes_running = asyncio.Event()
        self.late = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker" and package_of(request).get("task_contract", {}).get("outputs") == ["NOTES.md"]:
            self.notes_running.set()
            # A slow call: still in flight when the replacement cancels its Attempt; its
            # reply lands only after that.
            await self.late.wait()
        return await super().invoke(request, cancel=cancel)


def test_a_rejected_step_can_have_its_method_replaced_while_a_sibling_still_runs(tmp_path):
    async def case():
        provider = _Provider()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "整理事实并写笔记", "idempotency_key": "replace-method",
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            store = world.loop.store
            stop = asyncio.Event()

            async def drive() -> None:  # the held step keeps run() from going idle
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                for _ in range(900):
                    if str(store.get_mission(mission_id).status.value) in {"COMPLETED", "FAILED", "CANCELLED"}:
                        break
                    if not provider.late.is_set() and any(
                            event.type == "AttemptCancelled"
                            and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")
                            for event in store.list_events(mission_id)):
                        provider.late.set()
                    await asyncio.sleep(0.1)
            finally:
                stop.set()
                provider.late.set()
                await asyncio.wait_for(runner, 30)
            events = list(store.list_events(mission_id))
            kinds = [event.type for event in events]
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED", kinds[-20:]
            assert provider.rejected and provider.notes_running.is_set()

            reviewed = [event.payload["method_ref"] for event in events
                        if event.type == "PlanningMethodReviewed" and event.payload["outcome"] == "PASSED"]
            assert [ref["version"] for ref in reviewed] == [1, 2]
            assert reviewed[0]["method_id"] == reviewed[1]["method_id"]
            revisions = [event for event in events if event.type == "PlanRevisionCommitted"]
            assert [event.payload["base_plan_revision"] for event in revisions] == [0, 1]
            # The v1 step still running was cancelled by the replacement's convergence before
            # the new revision committed; its late reply was not accepted.
            [cancelled] = [event for event in events if event.type == "AttemptCancelled"
                           and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")]
            superseded = [event for event in events
                          if event.type == "ResultRejected" and event.payload.get("reason") == "superseded"]
            assert cancelled.seq < revisions[1].seq
            assert superseded and superseded[0].seq < revisions[1].seq
            [addressed] = [event.payload for event in events if event.type == "PlanningRepairAddressed"]
            assert addressed["decision_type"] == "REPAIR" and addressed["status"] == "COMMITTED"

    asyncio.run(case())


def test_a_refused_method_change_lifts_its_fence(tmp_path, monkeypatch):
    """阶段 B 裁决第 5 类：换做法的计划提交被拒（提交时撞上冲突），它立的围栏随决定一起结束。

    此前被拒只记"提交被拒"、走规划阶梯，收敛作业一直"已立围栏"，被围的步骤永远不派发、不交接。
    现在：作业以"决定被拒"结束；被拒照旧交给规划器；规划器再提一次替换，这次提交成功，任务完成。

    冲突用一次性的提交拒绝注入（真实路径上的版本冲突就是这个异常），只在第一次替换提交时出现。

    **改坏检验**：被拒分支里不解围栏 → 第二次替换的围栏叠在旧围栏上、旧作业一直活着 → 变红。
    """
    from agent_orchestrator.orchestrator import hierarchical_dispatch
    from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected

    original = hierarchical_dispatch.HierarchicalDispatch.commit_preview_plan_proposal
    refused: list[str] = []

    def commit_once_refused(self, mission_id, proposal, **kwargs):  # type: ignore[no-untyped-def]
        if not refused and int(kwargs["preview"].base_revision) >= 1:
            refused.append(mission_id)
            raise PlanCommitRejected("TEST_COMMIT_CONFLICT: the plan moved under this commit")
        return original(self, mission_id, proposal, **kwargs)

    monkeypatch.setattr(hierarchical_dispatch.HierarchicalDispatch, "commit_preview_plan_proposal",
                        commit_once_refused)

    async def case():
        provider = _Provider()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "整理事实并写笔记", "idempotency_key": "refused-replace",
                                       "success_criteria": list(CRITERIA)})["mission_id"]
            store = world.loop.store
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                for _ in range(900):
                    if str(store.get_mission(mission_id).status.value) in {"COMPLETED", "FAILED", "CANCELLED"}:
                        break
                    if not provider.late.is_set() and any(
                            event.type == "AttemptCancelled"
                            and str(event.payload.get("reason", "")).startswith("taskgraph_convergence:")
                            for event in store.list_events(mission_id)):
                        provider.late.set()
                    await asyncio.sleep(0.1)
            finally:
                stop.set()
                provider.late.set()
                await asyncio.wait_for(runner, 30)
            events = list(store.list_events(mission_id))
            assert refused == [mission_id]
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED", [e.type for e in events][-20:]
            ended = [event.payload for event in events if event.type == "TaskGraphConvergenceAdvanced"
                     and event.payload["to_state"] == "ABANDONED"]
            assert [item["reason"] for item in ended] == ["decision_refused"]
            jobs = dict(store.connection.execute(
                "SELECT job_id, state FROM taskgraph_convergence_jobs WHERE mission_id=?", (mission_id,)).fetchall())
            assert sorted(jobs.values()) == ["ABANDONED", "APPLIED"], jobs
            assert any(event.type == "PlanningDecisionEvaluated" and event.payload["status"] == "COMMIT_REJECTED"
                       for event in events)

    asyncio.run(case())
