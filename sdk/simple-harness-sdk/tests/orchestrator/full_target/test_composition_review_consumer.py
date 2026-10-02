"""中间层的组合审阅结果真正用起来（完成度评估第 3 项，2026-10-01）。

保证通道下，中间目标（非根的复合目标）的独立组合审阅此前只**发起**、从没人消费正式记录：
通过了也不形成目标结论，后面按顺序排着的步骤永远等，任务以"无事可做"失败。现在：
* 正式记录通过（或判不下来、人裁决通过）→ 形成目标结论（`assured_composition_action` = resolve）；
* 打回 / 拒绝（或人裁决打回）→ 记一条修复请求交规划器（repair），同一份记录只记一次；
* 复审后仍判不下来 → 用规划问题卡片问人（ask），与根终审同一套（通过 / 打回）。
这里测纯判断函数；两个回调由产品同形部署上的主循环真跑出来（``h1i_seed``）：根目标的做法是
"一个子目标 + 收尾一步"，子目标再提一个一步做法；子目标的组合审阅由脚本化审阅员判不下来
（问人）或打回（修复请求），人经回答接口作答。
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from h1i_seed import CONFIG, events, run_until
from test_resolution_commits import (
    HASH_A,
    requirements,
    review_binding,
    review_package,
    review_record,
)

from agent_orchestrator.api.planning_answers import answer_planning_question
from agent_orchestrator.contracts.resolution import (
    CheckExecution,
    CriterionOutcome,
    CriterionVerdict,
    ReviewPurpose,
    ReviewVerdict,
)
from agent_orchestrator.orchestrator.composition_review import assured_composition_action
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    review_reply,
)


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


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _root_with_sub_goal(context: dict[str, Any]) -> dict[str, Any]:
    """根目标 = 一个子目标（承接第一条要求）+ 收尾一步（承接第二条）。"""

    request = context["request"]
    part = next(item for item in request["subgoal_types"] if item["task_type_ref"]["id"] == "sub-goal-1")
    tail = next(item for item in request["operators"]
                if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [
            {"local_id": "part", "task_type_ref": part["task_type_ref"], "form": "compound",
             "arguments": {"goal": {"op": "constant", "value": "写出 notes/a.md，列三条要点"}},
             "required_capabilities": [], "obligation_relation": "refines_parent"},
            {"local_id": "tail", "task_type_ref": tail["task_type_ref"], "form": "primitive", "arguments": {},
             "required_capabilities": list(tail["required_capabilities"]), "obligation_relation": "refines_parent"},
        ],
        "ordering": [{"before": "part", "after": "tail"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "part", "child_criterion_id": first,
                 "evidence_requirement": "part 这个子目标完成第一条要求"},
                {"parent_criterion_id": second, "child_step": "tail", "child_criterion_id": second,
                 "evidence_requirement": "tail 这一步完成第二条要求"},
            ],
            "outputs": {}, "finalizer_step": "tail", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _planner(request: Any) -> Any:
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and str((contexts[0]["request"].get("goal_type_ref") or {}).get("id")) == "user-goal" \
            and not (package.get("method_selection") or [{}])[0].get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": _root_with_sub_goal(contexts[0]),
                                             "rationale": "先做一个子目标，再收尾。"}},
                        "根目标拆成一个子目标和一个收尾步骤。")
    return planner_reply(request)


def _composition_reviewer(verdict: str, grade: str) -> Any:
    def reviewer(request: Any) -> str:
        package = review_input(request)
        assert package is not None
        if (package.get("package") or {}).get("purpose") == "COMPOSITION":
            return review_reply(package, verdict=verdict, grade=grade, reason="组合起来看不出是否满足这条要求")
        return review_reply(package)

    return reviewer


def _sub_goal_task(loop: Any, mission_id: str) -> str:
    rows = loop.store.connection.execute(
        "SELECT DISTINCT task_id FROM task_semantics WHERE mission_id=? AND form='compound' "
        "AND task_id NOT LIKE 'user-root-%'", (mission_id,)).fetchall()
    assert len(rows) == 1, rows
    return str(rows[0][0])


@asynccontextmanager
async def _run(tmp_path: Any, key: str, reviewer: Any, until: Any) -> AsyncIterator[tuple[Any, Any]]:
    """Run the sub-goal Mission on the main loop until ``until(loop, mission_id)``."""

    provider = LayeredScriptedProvider(planner=_planner, reviewer=reviewer)
    try:
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            created = world.create({"goal": "写两份笔记", "idempotency_key": key,
                                    "success_criteria": ["file:notes/a.md", "file:NOTES.md"]})
            mission = world.loop.store.get_mission(created["mission_id"])
            await run_until(world, lambda: until(world.loop, mission.id), timeout=60)
            yield world, mission
    finally:
        provider.release.set()


def _adjudication_questions(loop: Any, mission_id: str) -> list[dict[str, Any]]:
    return [row for row in PlanningHumanStore(loop.store).list(mission_id)
            if (row["request"].get("repair_context") or {}).get("kind") == "review_adjudication"]


def test_an_inconclusive_composition_asks_the_person_and_records_the_ruling(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with _run(
            tmp_path, "comp-ask", _composition_reviewer("INCONCLUSIVE", "UNKNOWN"),
            lambda loop, mission_id: bool(_adjudication_questions(loop, mission_id)),
        ) as (world, mission):
            loop = world.loop
            task_id = _sub_goal_task(loop, mission.id)
            [row] = _adjudication_questions(loop, mission.id)
            assert row["state"] == "PENDING"
            context = row["request"]["repair_context"]
            record_id = context["record_id"]
            assert context["target_id"] == task_id
            assert set(context) == {"kind", "record_id", "package_id", "target_id", "occurrence_id"}
            assert [o["key"] for o in row["request"]["payload"]["options"]] == ["pass", "fail"]
            # One question per official record: further rounds only wait for the person.
            await world.drain(timeout=5)
            assert len(_adjudication_questions(loop, mission.id)) == 1
            answer_planning_question(
                loop, tenant_id=mission.tenant_id, principal=world.deployment.principal,
                decision_id=row["decision_id"], answer="pass", expected_version=row["version"],
                nonce="n-" + row["decision_id"])
            await run_until(world, lambda: loop.store.get_receipt(
                "assurance-review-adjudicated:" + record_id) is not None, timeout=60)
            receipt = loop.store.get_receipt("assurance-review-adjudicated:" + record_id)
            assert receipt["decision"] == "pass" and receipt["target_id"] == task_id
            # The answer is consumed as the ruling, never as a planner round.
            assert not [e for e in events(loop, mission.id, "PlanningServiceResumed")
                        if e.payload.get("decision_id") == row["decision_id"]]

    asyncio.run(case())


def test_a_rejected_composition_records_one_repair_request_for_the_planner(tmp_path):
    def composition_repairs(loop: Any, mission_id: str) -> list[Any]:
        return [e for e in events(loop, mission_id, "PlanningRepairRequested")
                if str(e.payload.get("source_key", "")).startswith("composition-review:")]

    async def case():  # type: ignore[no-untyped-def]
        async with _run(
            tmp_path, "comp-repair", _composition_reviewer("REWORK", "FAIL"),
            lambda loop, mission_id: bool(composition_repairs(loop, mission_id)),
        ) as (world, mission):
            loop = world.loop
            world.provider.held.add("planner")  # the repair round stays with the planner
            [event] = composition_repairs(loop, mission.id)
            detail = event.payload["request"]["context"]
            assert detail["verdict"] == "REWORK"
            assert detail["findings"][0]["criterion_id"] == "c-user-1"
            assert str(detail["source"]) == "composition_review"
            # The same record is recorded once, however many rounds look at it.
            await world.drain(timeout=3)
            assert len(composition_repairs(loop, mission.id)) == 1

    asyncio.run(case())
