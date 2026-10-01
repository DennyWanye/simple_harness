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
"""
from __future__ import annotations

import asyncio

from agent_orchestrator.contracts.models import TaskStatus
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.planning_repair_requests import (
    collect_triggers,
    pending_requests,
)
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from test_h1i_production_entry import _config
from test_h4_retry_runtime_entry import attempt, refined, repair, retry_payload

REWRITE = {"paths": ["src/app.py"], "side_effect_kind": "READ_ONLY", "capabilities": []}


def _running(loop, task_id, retry_of=None, turn="turn-1"):
    row, intent = attempt(loop, task_id, retry_of)
    loop.commit.claim_intent(intent.intent_id, owner=loop._owner, lease_seconds=60)
    loop.commit.record_agent_created(intent.intent_id, agent_id="fixture", expected_turn_id=turn)
    loop.commit.record_submitted(intent.intent_id, receipt={"turn_id": turn, "seq": 1})
    return row


def _facts(loop, mission_id):
    return [row["request"]["context"] for row in pending_requests(loop.store, mission_id)]


def test_a_refused_read_only_rewrite_is_one_request_carrying_count_fingerprint_and_paths(tmp_path):
    async def case():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, dispatch, task_id = await refined(loop, tmp_path, "facts-read-only-rewrite")
            before = (int(dispatch.network(mission.id).plan_revision),
                      sorted(str(i) for i in dispatch.network(mission.id).adopted_instance_ids))
            first = _running(loop, task_id)
            loop.commit.reject_result(first.id, turn_id="turn-1",
                                      reason="read_only_leaf_rewrote_workspace", detail=REWRITE)
            assert collect_triggers(loop, mission)
            [context] = _facts(loop, mission.id)
            assert context["event_type"] == "ResultRejected"
            assert context["detail"]["reason"] == "read_only_leaf_rewrote_workspace"
            assert context["detail"]["detail"]["paths"] == ["src/app.py"]
            occurrence = context["occurrence"]
            assert occurrence["step_failures"] == 1 and occurrence["consecutive_identical"] == 1
            fingerprint = occurrence["failure_fingerprint"]
            assert len(fingerprint) == 64

            # the Planner chooses to retry; the same refusal again is the second of the same
            def payload(package):
                return retry_payload(dispatch, mission, task_id, first.id, package)
            _, row = await repair(loop, mission, dispatch, task_id, payload)
            assert row["status"] == "COMMITTED", row["detail_json"]
            second = _running(loop, task_id, first.id, turn="turn-2")
            loop.commit.reject_result(second.id, turn_id="turn-2",
                                      reason="read_only_leaf_rewrote_workspace", detail=REWRITE)
            assert collect_triggers(loop, mission)
            [again] = _facts(loop, mission.id)
            assert again["occurrence"] == {
                "step_failures": 2, "consecutive_identical": 2, "failure_fingerprint": fingerprint}

            # Harness reported and did nothing else: the step is not cancelled, the
            # method is still adopted, and no round was opened by a dedicated record
            assert loop.store.get_task(task_id).status is not TaskStatus.CANCELLED
            assert (int(dispatch.network(mission.id).plan_revision),
                    sorted(str(i) for i in dispatch.network(mission.id).adopted_instance_ids)) == before
            assert not [e for e in loop.store.list_events(mission.id)
                        if e.type == "PlanningRejected" and e.payload.get("reason") in {
                            "read_only_leaf_needs_write", "repeated_verification_failure"}]
    asyncio.run(case())


def test_a_different_failure_breaks_the_identical_run_but_not_the_step_count(tmp_path):
    async def case():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, dispatch, task_id = await refined(loop, tmp_path, "facts-different-failure")
            first = _running(loop, task_id)
            loop.commit.reject_result(first.id, turn_id="turn-1",
                                      reason="read_only_leaf_rewrote_workspace", detail=REWRITE)
            assert collect_triggers(loop, mission)
            [context] = _facts(loop, mission.id)

            def payload(package):
                return retry_payload(dispatch, mission, task_id, first.id, package)
            _, row = await repair(loop, mission, dispatch, task_id, payload)
            assert row["status"] == "COMMITTED", row["detail_json"]
            second = _running(loop, task_id, first.id, turn="turn-2")
            loop.commit.reject_result(second.id, turn_id="turn-2",
                                      reason="read_only_leaf_rewrote_workspace",
                                      detail={**REWRITE, "paths": ["src/other.py"]})
            assert collect_triggers(loop, mission)
            [again] = _facts(loop, mission.id)
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
    from types import SimpleNamespace

    from agent_orchestrator.orchestrator.planning_repair_requests import address_requests

    async def case():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, dispatch, task_id = await refined(loop, tmp_path, "facts-final-review-scope")
            network = dispatch.network(mission.id)
            root = network.root_occurrence_ids[0]
            record = SimpleNamespace(
                record_id="rec-final-1", verdict="REWORK",
                criteria=(SimpleNamespace(criterion_id="c-root", verdict="FAIL",
                                          limitations=("the summary is missing",)),))
            state = SimpleNamespace(record=record, package=SimpleNamespace(package_id="pkg-final-1"),
                                    task_id=str(network.occurrence(root).task_id), detail="rejected")
            assert loop._request_root_review_repair(mission, dispatch, state) is True
            [pending] = pending_requests(loop.store, mission.id)
            every_step = {str(spec.occurrence_id) for spec in network.occurrences} | {
                str(spec.task_id) for spec in network.occurrences}
            assert len(network.occurrences) > 1 and set(pending["trigger_scope"]) == every_step

            leaf = next(spec for spec in network.occurrences if str(spec.task_id) == task_id)
            package = {
                "planning_subjects": [{"subject_key": "subject-leaf", "task_id": task_id,
                                       "occurrence_id": str(leaf.occurrence_id)}],
                "repair_requests": [pending],
            }
            address_requests(loop.store, mission.id, package=package, decision_id="decision-on-a-leaf",
                             decision_type="REPAIR", status="COMMITTED", subject_key="subject-leaf")
            assert pending_requests(loop.store, mission.id) == []
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
