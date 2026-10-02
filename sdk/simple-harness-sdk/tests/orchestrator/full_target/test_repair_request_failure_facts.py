# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""失败请求只报告事实：发生了什么、第几次、失败指纹、涉及路径（HTN 精简 片 0 第 2 步）。

此前"只读步骤越权改文件"和"同一步反复同样失败"各有一条专用升级：到次数就由 Harness 取消
这一步、退掉根目标的做法、往意见里写死"必须改做法"，再开一轮专用规划。现在这两种失败和
别的失败走同一条通用修复请求，请求里带上规划器做决定要用的事实：

* ``step_failures``——这一步到目前为止失败了几次；
* ``consecutive_identical``——连续几次是同一个失败（同一指纹）；
* ``failure_fingerprint``——失败指纹（去掉耗时、工作区路径这类每次都变的东西）；
* 涉及路径原样留在事件内容里。

重试 / 换做法 / 补步骤 / 问人由规划器选；Harness 不取消步骤、不退做法、不写结论。

产品同形部署上的真实回合（``taskgraph_exec.production_fixture``）：失败是执行者真交上来的结果
没过检查（没写结论 / 结论没引证据），重试是规划器真答的"原样再做一次"，最终审查打回是审阅员
真判的 REWORK。只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_FULL_TARGET = Path(__file__).resolve().parent
for _extra in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import OUTPUT, enabled_world, result_envelope, scripted_worker  # noqa: E402

from agent_orchestrator.contracts.models import TaskStatus  # noqa: E402
from agent_orchestrator.orchestrator.planning_repair_requests import pending_requests  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    decision,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


def _result(mutate):  # type: ignore[no-untyped-def]
    def reply(request):  # type: ignore[no-untyped-def]
        body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
        mutate(body)
        return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"
    return reply


def _no_claims(body):  # type: ignore[no-untyped-def]
    body["claims"] = []


def _claims_without_evidence(body):  # type: ignore[no-untyped-def]
    body["evidence"] = []
    for claim in body["claims"]:
        claim.pop("evidence", None)


def _write():  # type: ignore[no-untyped-def]
    return ("workspace_write_file", {"path": OUTPUT, "content": "# 要点\n"})


def _retrying_planner(request):  # type: ignore[no-untyped-def]
    """Adopt the method; on a failed step, retry it with the same method."""
    return planner_reply(request) or retry_same_method(request)


def _facts(store, mission_id):  # type: ignore[no-untyped-def]
    return [row["request"]["context"] for row in pending_requests(store, mission_id)]


def _failures(store, mission_id):  # type: ignore[no-untyped-def]
    return [e for e in store.list_events(mission_id) if e.type == "VerificationFailed"]


def _plan_shape(world):  # type: ignore[no-untyped-def]
    network = world.dispatch.network(world.mission.id)
    return int(network.plan_revision), sorted(str(i) for i in network.adopted_instance_ids)


def test_a_failed_check_is_one_request_carrying_count_fingerprint_and_paths(tmp_path):
    worker = scripted_worker(_write(), _result(_no_claims), _write(), _result(_no_claims))

    async def case():
        async with enabled_world(tmp_path, key="facts-repeated-failure", planner=_retrying_planner,
                                 worker=worker) as world:
            store, mission = world.store, world.mission
            await world.commit_seed()
            [task_id] = world.leaves()
            before = _plan_shape(world)
            await world.until(lambda: _facts(store, mission.id))
            [context] = _facts(store, mission.id)
            assert context["event_type"] == "VerificationFailed"
            [failure] = context["detail"]["failures"]
            assert failure["detail"]["problems"] == ["no claims were submitted"]
            assert failure["detail"]["checked_artifacts"] == [OUTPUT]  # the paths, as they were
            occurrence = context["occurrence"]
            assert occurrence["step_failures"] == 1 and occurrence["consecutive_identical"] == 1
            fingerprint = occurrence["failure_fingerprint"]
            assert len(fingerprint) == 64

            # the Planner chooses to retry; the same failure again is the second of the same
            await world.until(lambda: len(_failures(store, mission.id)) == 2 and _facts(store, mission.id))
            [again] = _facts(store, mission.id)
            assert again["occurrence"] == {
                "step_failures": 2, "consecutive_identical": 2, "failure_fingerprint": fingerprint}

            # Harness reported and did nothing else: the step is not cancelled, the
            # method is still adopted, and no round was opened by a dedicated record
            assert store.get_task(task_id).status is not TaskStatus.CANCELLED
            assert _plan_shape(world) == before
            assert not [e for e in store.list_events(mission.id)
                        if e.type == "PlanningRejected" and e.payload.get("reason") in {
                            "read_only_leaf_needs_write", "repeated_verification_failure"}]
    asyncio.run(case())


def test_a_different_failure_breaks_the_identical_run_but_not_the_step_count(tmp_path):
    worker = scripted_worker(_write(), _result(_no_claims), _write(), _result(_claims_without_evidence))

    async def case():
        async with enabled_world(tmp_path, key="facts-different-failure", planner=_retrying_planner,
                                 worker=worker) as world:
            store, mission = world.store, world.mission
            await world.commit_seed()
            await world.until(lambda: _facts(store, mission.id))
            [context] = _facts(store, mission.id)
            await world.until(lambda: len(_failures(store, mission.id)) == 2 and _facts(store, mission.id))
            [again] = _facts(store, mission.id)
            assert again["detail"]["failures"][0]["detail"]["problems"] == ["claims cite no evidence"]
            assert again["occurrence"]["step_failures"] == 2
            assert again["occurrence"]["consecutive_identical"] == 1
            assert again["occurrence"]["failure_fingerprint"] != context["occurrence"]["failure_fingerprint"]
    asyncio.run(case())


def test_a_final_review_request_is_about_every_step_so_a_change_on_any_step_answers_it(tmp_path):
    """最终审查打回的请求，范围是整个计划：规划器在任何一步上提交的改动都算处理了它。

    普通步骤失败的请求只认"这一步或它的上级目标"上的决定（否则只重做下游就把上游的请求
    记成已处理）。最终审查审的是整个任务，不指向某一步：规划器给某个步骤补后继、或换掉根
    目标的做法，都是它的回答。
    """
    seen: dict = {"final_reviews": 0}

    def reviewer(request):  # type: ignore[no-untyped-def]
        package = review_input(request)
        if package is None:
            return None
        if str(package["review_key"]).startswith("assurance-mission-final:"):
            seen["final_reviews"] += 1
            if seen["final_reviews"] == 1:  # the first final review sends it back
                return review_reply(package, verdict="REWORK", grade="FAIL", reason="要点没有写全。")
        return review_reply(package)

    def planner(request):  # type: ignore[no-untyped-def]
        package = package_of(request)
        if not package.get("repair_requests"):
            return planner_reply(request)
        world, task_id = seen["world"], seen["leaf"]
        old = world.dispatch.network(world.mission.id).binding_for_task(task_id)
        task_type = next(s for s in world.dispatch.require_planning_world().catalog.task_types()
                         if s.goal_signature == old.goal_signature)
        subject = next(row for row in package["planning_subjects"] if row["task_id"] == task_id)

        def visible(kind, identity):  # type: ignore[no-untyped-def]
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

        # answered on one leaf: a successor that redoes it
        return decision(subject["subject_key"], "REPAIR", {
            "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", task_id),
            "obligation_ref": visible("obligation", str(old.obligation_id)),
            "goal_type_ref": task_type.task_type_ref.to_json(),
            "bindings": {"goal": "把要点写全。"}}, "最终审查说要点没写全，这一步换一个后继重做。")

    provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)

    async def case():
        async with enabled_world(tmp_path, key="facts-final-review-scope", provider=provider) as world:
            store, mission = world.store, world.mission
            seen["world"] = world
            await world.commit_seed()
            [seen["leaf"]] = world.leaves()
            network = world.dispatch.network(mission.id)

            def final_review_request():  # type: ignore[no-untyped-def]
                return [e.payload for e in store.list_events(mission.id) if e.type == "PlanningRepairRequested"
                        and str(e.payload["source_key"]).startswith("root-review:")]

            [requested] = await world.until(final_review_request)
            every_step = {str(spec.occurrence_id) for spec in network.occurrences} | {
                str(spec.task_id) for spec in network.occurrences}
            assert len(network.occurrences) > 1 and set(requested["trigger_scope"]) == every_step

            # the Planner answers on one leaf; that committed change is an answer to it
            await world.until(lambda: world.dispatch.network(mission.id).plan_revision == 2)
            assert requested["request_id"] not in {row["request_id"] for row in pending_requests(store, mission.id)}
            [addressed] = [e.payload for e in store.list_events(mission.id) if e.type == "PlanningRepairAddressed"
                           and requested["request_id"] in e.payload["repair_request_ids"]]
            assert addressed["status"] == "COMMITTED" and addressed["decision_type"] == "REPAIR"
    asyncio.run(case())


def test_the_fingerprint_of_a_verification_failure_ignores_timing_and_workspace_paths() -> None:
    from agent_orchestrator.orchestrator.planning_repair_requests import failure_fingerprint

    def failed(seconds: str, workspace: str) -> dict:
        return {"result_id": "r", "failures": [{"layer": "code_test", "status": "FAIL",
                "summary": f"1 failed in {seconds}s",
                "detail": {"stdout": f"{workspace}/tests/test_a.py::test_x FAILED\nAssertionError\n"
                                     f"1 failed in {seconds}s"}}]}

    one = failure_fingerprint("VerificationFailed", failed("0.06", "/data/workspaces/attempt-1"))
    two = failure_fingerprint("VerificationFailed", failed("0.31", "/data/workspaces/attempt-2"))
    other = failure_fingerprint("VerificationFailed", {"result_id": "r", "failures": [
        {"layer": "rule_check", "status": "FAIL", "summary": "missing port", "detail": {"problems": ["p"]}}]})
    assert one == two and one != other
