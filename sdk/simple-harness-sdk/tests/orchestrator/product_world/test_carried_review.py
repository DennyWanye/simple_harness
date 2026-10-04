# SPDX-License-Identifier: Apache-2.0
"""改要求后沿用的步骤按新要求重审——没过的两种局面（TaskGraph 补全第四批）。

通过的局面在 ``test_requirements_amend.py::test_kept_old_step_is_reviewed_again_not_rerun``。

* 重审打回：规划器收到一条修复请求（原因 ``CARRIED_RESULT_REJECTED``，带审阅员的意见），换掉这一步
  重做，任务按新版完成；同一份结果同一版要求只审一次。
* 上游重审没过：读它产出的下游不重审，原样等规划器处理。

**改坏检验**：TG4-02 重审打回不发修复请求 → 第一条变红；TG4-03 重审不看上游 → 第二条变红。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.storage.htn_store import HtnStore
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

_spec = importlib.util.spec_from_file_location("_amend_script", Path(__file__).with_name("test_requirements_amend.py"))
amend_script = importlib.util.module_from_spec(_spec)
sys.modules["_amend_script"] = amend_script
_spec.loader.exec_module(amend_script)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class HeldStep(LayeredScriptedProvider):
    """写指定文件的那一步一直在跑，直到测试放行。"""

    def __init__(self, held_output: str, **roles: Any) -> None:
        super().__init__(**roles)
        self.held_output = held_output
        self.go = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if (role_of(request) == "worker"
                and package_of(request).get("task_contract", {}).get("outputs") == [self.held_output]):
            await self.go.wait()
        return await super().invoke(request, cancel=cancel)


def reject_second_review_of(criterion: str, seen: dict[str, int]):
    """审阅员：负责 ``criterion`` 的那一步的内容，第二次送审（按新版重审）时打回；其余都通过。"""

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if (str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT"
                and list(data.get("criterion_ids") or ()) == [criterion]):
            seen[criterion] = seen.get(criterion, 0) + 1
            if seen[criterion] == 2:
                return review_reply(data, verdict="REWORK", grade="FAIL", reason="按新版要求看，这份内容不够。")
        return review_reply(data)

    return reviewer


def successor(package: dict[str, Any], step: dict[str, Any], why: str) -> Any:
    subject = next(row for row in package["planning_subjects"] if row["task_id"] == step["task_id"])

    def visible(kind: str, identity: str) -> Any:
        return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

    [task_type] = [row for row in package["successor_types"]
                   if row["statement"] == step["statement"] and row["form"] == "primitive"]
    return decision(subject["subject_key"], "REPAIR", {
        "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", step["task_id"]),
        "obligation_ref": visible("obligation", step["obligation_id"]),
        "goal_type_ref": task_type["task_type_ref"], "bindings": dict(step["params"])}, why)


def _carried_rejections(package: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["request"] for entry in package.get("repair_requests") or ()
            if (entry["request"].get("context") or {}).get("reason_code") == "CARRIED_RESULT_REJECTED"]


def test_a_kept_step_rejected_under_the_new_requirements_goes_to_the_planner(tmp_path):
    seen: dict[str, Any] = {"packages": []}
    state: dict[str, Any] = {"second": False, "redone": False, "rejections": []}
    reviews: dict[str, int] = {}
    base = amend_script.replanning_planner(seen)

    def planner(request: Any) -> Any:
        package = package_of(request)
        sources = {entry["request"].get("trigger_source") for entry in package.get("repair_requests") or ()}
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state["second"]:
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return successor(package, unfinished, "要求改了：只换掉还没做完的那一步，做完的那步留着。")
        rejected = _carried_rejections(package)
        if rejected and not state["redone"]:
            state["redone"] = True
            state["rejections"] = rejected
            kept = next(item for item in steps
                        if item["occurrence_id"] == rejected[0]["context"]["occurrence_id"])
            return successor(package, kept, "沿用的那一步按新版没过：换掉重做。")
        return base(request)

    async def case():
        provider = HeldStep("b.md", planner=planner, reviewer=reject_second_review_of("c-user-1", reviews))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "carried-rejected",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            [first] = htn.list_acceptances(mission_id)
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            provider.go.set()
            try:
                mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
            except TimeoutError as error:
                raise AssertionError(f"the mission never settled: {state}") from error
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state)
            # 规划器拿到的是一条带原因与审阅员意见的修复请求
            [request] = state["rejections"]
            context = request["context"]
            assert context["requirements_revision"] == 2 and context["findings"], context
            # 被打回的那份结果：没有重跑（原任务只有一次尝试），按第 2 版只审过一次、没有第 2 版验收
            kept_task = str(first.task_id)
            assert len(world.store.list_attempts(kept_task)) == 1
            result_id = world.store.get_task(kept_task).accepted_result_id
            layers = [row for row in world.store.list_verifications(result_id, requirements_revision=2)
                      if row["layer"] == "critic_review"]
            assert [row["status"] for row in layers] == ["FAIL"]
            assert [int(item.requirements_revision) for item in htn.list_acceptances(mission_id)
                    if str(item.task_id) == kept_task] == [1]
            assert world.store.connection.execute(
                "SELECT COUNT(*) FROM assurance_review_bindings WHERE mission_id=? AND request_command_id=?",
                (mission_id, f"content-review:{result_id}:r2")).fetchone()[0] == 1
            events = list(world.store.list_events(mission_id))
            assert not [e for e in events if e.type == "CarriedResultAccepted"]
            # 被换掉的那一步已不在现行计划里：它的结果读起来是"范围过期"（归档），不是完整性错误
            # （阻断核验 B4：否则这种结果排队等验证时会把主循环冲垮）
            from agent_orchestrator.orchestrator.completion_inputs import load_completion_result_inputs
            from agent_orchestrator.orchestrator.operation_completion import OperationCompletionError

            with pytest.raises(OperationCompletionError) as stale:
                load_completion_result_inputs(world.store, world.store.get_result(result_id))
            assert stale.value.code == "OP_EFFECT_SCOPE_STALE"
            [judged] = [e.payload for e in events if e.type == "MissionSuccessJudged"]
            assert [j["criterion"] for j in judged["judgments"]] == ["file:a.md", "file:b2.md"] and judged["met"]

    asyncio.run(case())


def relay_then_third(context: dict[str, Any]) -> dict[str, Any]:
    """根做法三步：s1 写 a.md；s2 读 s1 的产出写 b.md；s3 写 c.md（互不依赖）。"""
    request = context["request"]
    method = one_step_method(context)
    [step] = method["steps"]
    follow = next(item for item in request["operators"]
                  if str(item["task_type_ref"]["id"]).endswith("continue-delivery"))
    first, second, third = [item["id"] for item in request["criterion_evidence"]]
    method["steps"] = [
        dict(step, local_id="s1"),
        {"local_id": "s2", "task_type_ref": follow["task_type_ref"], "form": "primitive",
         "arguments": {"delivery": {"op": "output", "step": "s1", "port": "delivery"}},
         "required_capabilities": list(follow["required_capabilities"]), "obligation_relation": "refines_parent"},
        dict(step, local_id="s3"),
    ]
    method["ordering"] = []
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": name, "child_step": f"s{n}", "child_criterion_id": name,
         "evidence_requirement": f"s{n} 这一步写出 {name} 要求的文件"}
        for n, name in enumerate((first, second, third), start=1)]
    method["composition"]["finalizer_step"] = "s3"
    return method


def test_a_downstream_step_is_not_reviewed_while_its_upstream_does_not_count(tmp_path):
    state: dict[str, Any] = {"root": False, "second": False, "rejections": []}
    reviews: dict[str, int] = {}

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        requests = package.get("repair_requests") or ()
        sources = {entry["request"].get("trigger_source") for entry in requests}
        if contexts and not state["root"]:
            state["root"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay_then_third(contexts[0]), "rationale": "先写、接着写，另写一份。"}}, "三步。")
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state["second"]:
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return successor(package, unfinished, "要求改了：只换掉还没做完的那一步，做完的两步留着。")
        if _carried_rejections(package):
            state["rejections"] = _carried_rejections(package)
            return None  # 这条用例到此为止：怎么重做上下游是第五批的事
        if requests:
            return None
        return planner_reply(request)

    async def case():
        provider = HeldStep("c.md", planner=planner, reviewer=reject_second_review_of("c-user-1", reviews))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md，据它写 b.md，另写 c.md", "idempotency_key": "carried-order",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(40):
                await world.drain(timeout=20)
                if len(htn.list_acceptances(mission_id)) >= 2:
                    break
            accepted = {str(item.task_id) for item in htn.list_acceptances(mission_id)}
            assert len(accepted) == 2, accepted
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-3", "statement": "file:c2.md"}])
            provider.go.set()
            for _ in range(60):
                await world.drain(timeout=20)
                if state["rejections"]:
                    break
            assert state["rejections"], "the kept upstream step was never rejected under the new requirements"
            for _ in range(3):  # 再转几轮：下游这时也不该被送审
                await world.drain(timeout=5)
            by_output = {tuple(task.outputs): task for task in world.store.list_tasks(mission_id)
                         if task.id in accepted}
            upstream, downstream = by_output[("a.md",)], by_output[("b.md",)]
            assert state["rejections"][0]["context"]["result_id"] == upstream.accepted_result_id
            assert [row["status"] for row in world.store.list_verifications(
                upstream.accepted_result_id, requirements_revision=2) if row["layer"] == "critic_review"] == ["FAIL"]
            assert world.store.list_verifications(downstream.accepted_result_id, requirements_revision=2) == []
            assert reviews == {"c-user-1": 2}  # 负责第 1 条的那步审过两次（旧版、新版），下游没被送审

    asyncio.run(case())


def test_a_replacing_method_may_name_the_kept_step_and_it_is_reviewed_again(tmp_path):
    """改要求后规划器换做法，新做法的第 1 步点名共用按旧版通过的那一步（候选状态
    ``accepted_under_old_requirements``）：那一步不重做，审阅员按新版重审同一份结果，任务按新版完成。"""
    seen: dict[str, Any] = {}
    state: dict[str, Any] = {"named": None, "statuses": []}
    base = amend_script.replanning_planner(seen)

    def planner(request: Any) -> Any:
        import json

        reply = base(request)
        package = package_of(request)
        kept = [row for row in package.get("sharing_candidates") or ()
                if row["status"] == "accepted_under_old_requirements"]
        if isinstance(reply, str) and '"REPLACE_METHOD"' in reply and kept:
            body = json.loads(reply.removeprefix("<planning_decision>").removesuffix("</planning_decision>"))
            body["payload"]["reuse"] = {"s1": kept[0]["occurrence_id"]}
            state["named"] = kept[0]["occurrence_id"]
            state["statuses"] = [row["status"] for row in package["sharing_candidates"]]
            return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"
        return reply

    async def case():
        provider = HeldStep("b.md", planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "carried-named",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            [first] = htn.list_acceptances(mission_id)
            amend_script.amend(world, mission_id, [{"op": "add", "statement": "file:extra.md"}])
            provider.go.set()
            try:
                mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
            except TimeoutError as error:
                raise AssertionError(f"the mission never settled: {state}") from error
            rejected = [e.payload for e in world.store.list_events(mission_id)
                        if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") == "REJECTED"]
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state,
                                                              rejected[-2:])
            assert state["named"], "the planner never saw a kept step to name"
            kept_task = str(first.task_id)
            assert len(world.store.list_attempts(kept_task)) == 1  # 没有重做
            assert sorted(int(item.requirements_revision) for item in htn.list_acceptances(mission_id)
                          if str(item.task_id) == kept_task) == [1, 2]
            # a.md 只有那一个任务在写
            writers = [task for task in world.store.list_tasks(mission_id) if tuple(task.outputs) == ("a.md",)]
            assert [task.id for task in writers] == [kept_task]

    asyncio.run(case())


def test_a_review_that_cannot_run_ends_with_the_planner_not_the_loop(tmp_path, monkeypatch):
    """重审时审阅员一直不可用（模型冷却、预留不够……）：不冲出主循环、不无限重来——到上限后
    一条修复请求交规划器（带出错说明），扫描就此停下（阻断核验 B1、B2）。

    **改坏检验**：TG4-05 出错不计数、每轮重来 → 规划器永远收不到 → 变红。"""
    from agent_orchestrator.assurance.codec import AssuranceError
    from agent_orchestrator.orchestrator import assurance_review_runtime as runtime

    [owner] = [value for value in vars(runtime).values()
               if isinstance(value, type) and "run_task" in vars(value)]
    original = owner.run_task
    calls = {"carried": 0}

    async def unavailable(self, mission, task, *, attempt_id, requirements_revision=None):
        if requirements_revision is not None:
            calls["carried"] += 1
            raise AssuranceError("REVIEW_ROUTE_UNAVAILABLE", "the reviewing model is cooling down")
        return await original(self, mission, task, attempt_id=attempt_id)

    monkeypatch.setattr(owner, "run_task", unavailable)
    seen: dict[str, Any] = {"packages": []}
    state: dict[str, Any] = {"second": False, "rejections": []}
    base = amend_script.replanning_planner(seen)

    def planner(request: Any) -> Any:
        package = package_of(request)
        sources = {entry["request"].get("trigger_source") for entry in package.get("repair_requests") or ()}
        steps = [item for item in package["views"]["goals"] if item["form"] == "primitive"]
        done = {row["producer_occurrence"] for row in package["views"]["accepted_results"]}
        if "REQUIREMENTS_UPDATE" in sources and not state["second"]:
            state["second"] = True
            [unfinished] = [item for item in steps if item["occurrence_id"] not in done]
            return successor(package, unfinished, "要求改了：只换掉还没做完的那一步。")
        if _carried_rejections(package):
            state["rejections"] = _carried_rejections(package)
            return None
        return base(request)

    async def case():
        provider = HeldStep("b.md", planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 和 b.md", "idempotency_key": "carried-unavailable",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            htn = HtnStore(world.store)
            for _ in range(20):
                await world.drain(timeout=20)
                if htn.list_acceptances(mission_id):
                    break
            assert htn.list_acceptances(mission_id)
            amend_script.amend(world, mission_id,
                               [{"op": "rewrite", "criterion_id": "c-user-2", "statement": "file:b2.md"}])
            provider.go.set()
            try:
                for _ in range(60):
                    await world.drain(timeout=20)
                    if state["rejections"]:
                        break
            except Exception as error:  # noqa: BLE001 - 冲出主循环就是这条要抓的缺陷
                raise AssertionError(f"main loop crashed: {type(error).__name__}: {error}") from error
            assert state["rejections"], f"the planner was never told ({calls})"
            context = state["rejections"][0]["context"]
            assert "REVIEW_ROUTE_UNAVAILABLE" in context["review_error"], context
            tried = calls["carried"]
            assert tried == 3
            for _ in range(3):
                await world.drain(timeout=5)
            assert calls["carried"] == tried  # 交给规划器之后不再重来

    asyncio.run(case())
