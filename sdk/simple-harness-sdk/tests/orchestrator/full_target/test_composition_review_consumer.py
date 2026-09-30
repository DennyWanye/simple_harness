"""中间层的组合审阅结果真正用起来（完成度评估第 3 项，2026-10-01）。

保证通道下，中间目标（非根的复合目标）的独立组合审阅此前只**发起**、从没人消费正式记录：
通过了也不形成目标结论，后面按顺序排着的步骤永远等，任务以"无事可做"失败。现在：
* 正式记录通过（或判不下来、人裁决通过）→ 形成目标结论（`assured_composition_action` = resolve）；
* 打回 / 拒绝（或人裁决打回）→ 记一条修复请求交规划器（repair），同一份记录只记一次；
* 复审后仍判不下来 → 用规划问题卡片问人（ask），与根终审同一套（通过 / 打回）。
这里测纯判断函数与编排循环里的两个回调；组合审阅本身的真实链路由真机验证。
"""
from __future__ import annotations

import asyncio
import dataclasses

from test_h1i_production_entry import _config, _events, _seed_new_protocol
from test_resolution_commits import HASH_A, requirements, review_binding, review_package, review_record

from agent_orchestrator.contracts.resolution import (
    CheckExecution,
    CriterionOutcome,
    CriterionVerdict,
    ReviewPurpose,
    ReviewVerdict,
)
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.composition_review import assured_composition_action
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def _record(mission_id: str, task_id: str, verdict: ReviewVerdict):  # type: ignore[no-untyped-def]
    revision = requirements(mission_id, revision=1)
    package = review_package("pkg-comp", review_binding(mission_id, "duty-x", task_id, HASH_A),
                             revision, purpose=ReviewPurpose.COMPOSITION, method_instance_id="mi-1")
    grade = CriterionVerdict.PASS if verdict is ReviewVerdict.ACCEPT else (
        CriterionVerdict.FAIL if verdict in {ReviewVerdict.REWORK, ReviewVerdict.REJECTED} else CriterionVerdict.UNKNOWN)
    outcomes = tuple(CriterionOutcome(criterion_id=c.criterion_id, verdict=grade,
                                      check_execution=CheckExecution.SUCCEEDED if grade is CriterionVerdict.PASS else CheckExecution.NOT_RUN,
                                      limitations=() if grade is CriterionVerdict.PASS else ("evidence insufficient",))
                     for c in package.criteria)
    return package, review_record("rec-comp", package, verdict=verdict, outcomes=outcomes)


def test_the_action_on_an_official_composition_record():
    package, accepted = _record("m", "t", ReviewVerdict.ACCEPT)
    _, rework = _record("m", "t", ReviewVerdict.REWORK)
    _, inconclusive = _record("m", "t", ReviewVerdict.INCONCLUSIVE)
    assert assured_composition_action(accepted, None) == "resolve"
    assert assured_composition_action(rework, None) == "repair"
    assert assured_composition_action(inconclusive, None) == "ask"
    assert assured_composition_action(inconclusive, {"decision": "pass"}) == "resolve"
    assert assured_composition_action(inconclusive, {"decision": "fail"}) == "repair"
    assert assured_composition_action(dataclasses.replace(accepted, verdict=ReviewVerdict.REJECTED), None) == "repair"


def test_an_inconclusive_composition_asks_the_person_and_records_the_ruling(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="comp-ask")
            task_id = str(dispatch.network(mission.id).occurrences[0].task_id)
            _, record = _record(mission.id, task_id, ReviewVerdict.INCONCLUSIVE)
            assert loop._ask_person_to_adjudicate_compound(mission, record, task_id, "occ-1") is True
            questions = PlanningHumanStore(loop.store)
            [row] = [q for q in questions.list(mission.id) if q["state"] == "PENDING"]
            assert row["request"]["repair_context"] == {"kind": "review_adjudication", "record_id": "rec-comp",
                                                         "package_id": "pkg-comp", "target_id": task_id,
                                                         "occurrence_id": "occ-1"}
            assert [o["key"] for o in row["request"]["payload"]["options"]] == ["pass", "fail"]
            assert loop._ask_person_to_adjudicate_compound(mission, record, task_id, "occ-1") is False  # waiting
            questions.answer(decision_id=row["decision_id"], tenant_id=mission.tenant_id,
                             principal=Principal("user-1"), answer="pass", expected_version=row["version"], nonce="n")
            assert loop._ask_person_to_adjudicate_compound(mission, record, task_id, "occ-1") is True
            receipt = loop.store.get_receipt("assurance-review-adjudicated:rec-comp")
            assert receipt["decision"] == "pass" and receipt["target_id"] == task_id
            assert not _events(loop, mission.id, "PlanningServiceResumed")  # no planner round
    asyncio.run(case())


def test_a_rejected_composition_records_one_repair_request_for_the_planner(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="comp-repair")
            spec = dispatch.network(mission.id).occurrences[0]
            task_id, occurrence = str(spec.task_id), str(spec.occurrence_id)
            package, record = _record(mission.id, task_id, ReviewVerdict.REWORK)
            assert loop._request_composition_repair(mission, dispatch, record, package, task_id, occurrence) is True
            [event] = _events(loop, mission.id, "PlanningRepairRequested")
            assert event.payload["source_key"] == "composition-review:rec-comp"
            detail = event.payload["request"]["context"]
            assert detail["verdict"] == "REWORK" and detail["findings"][0]["criterion_id"] == "c-done"
            assert loop._request_composition_repair(mission, dispatch, record, package, task_id, occurrence) is False
            assert len(_events(loop, mission.id, "PlanningRepairRequested")) == 1
    asyncio.run(case())
