# SPDX-License-Identifier: Apache-2.0
"""选做法、提做法都是规划器的判断；程序只过滤和做秩序检查（HTN 精简 片 A 第 2～5 项）。

此前：只有一个候选做法时程序直接替规划器选；一个都没有时程序判定"该合成了"并另起一个方法
合成器角色；规划器自己提做法的那条路从没在真实循环里走通过。现在：

* 每个还没有做法的目标都开一轮规划器，候选有几个都一样（零个、一个、多个）。
* 写新做法要用的材料对每个还没有做法的目标都给——任务要求的原文、新做法该用的编号——不
  看候选有几个。候选清单里没有程序替它算好的"路由"和"选中项"。
* 草案的秩序检查在规划器这条路上做，被拒时逐条列出可修正的问题：编号撞车、要发布的文件
  没有唯一的产出步骤、每个目标最多提 3 次。

HTN 补齐阶段 A′：跑在产品同形世界（:mod:`_assured_loop`），只有模型回复是脚本。产品没有库内
做法，候选一律是规划器提出、过了独立审阅的做法；"库内做法不需要审阅"那半句随之改成"候选是
审阅通过的做法，规划器照样自己选"。原"三次上限在非保证通道也成立"一条删除：非保证通道已删
（删除批三），上限由本文件同名用例在唯一的通道上钉住。
"""
from __future__ import annotations

import asyncio

import pytest
from _assured_loop import (
    CRITERION,
    adopt,
    assured_loop,
    confirm_completion,
    events_of,
    method_body,
    propose,
    provider,
    review,
    run_until,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _decisions(world) -> list[dict]:
    return [event.payload for event in events_of(world, "PlanningDecisionEvaluated")]


def _one_step(local_id: str = "write", links=None):
    def build(context):
        return method_body(context, steps=[(local_id, "prepare-delivery", {})],
                           links=links or [(CRITERION, local_id)], finalizer=local_id)
    return build


def test_one_candidate_is_still_the_planners_choice_and_a_library_method_needs_no_review(tmp_path):
    """No candidate, then one reviewed candidate (twice: a second proposal for the same goal
    is the same method's next version, which replaces the first on offer): every time the
    Planner is asked and answers itself (the program never picks), the list carries no
    pre-computed route, and the material to write yet another method is still there."""
    seen: list[dict] = []

    async def case():
        scripted = provider(planner=[propose(seen=seen), propose(seen=seen), adopt(seen=seen)])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            [committed] = [item for item in _decisions(world) if item["status"] == "COMMITTED"]
            # the Planner was asked and answered; nothing was decided by the program
            assert committed["decision_origin"] == "planner_reply"
            assert scripted.by_role["planner"] == 3
            assert not events_of(world, "NativePlanningDecisionPrepared")

            counts = []
            for package in seen:
                [choice] = package["method_selection"]
                counts.append(choice["applicable_count"])
                assert len(choice["applicable"]) == choice["applicable_count"]
                assert not {"route", "identity", "selected_method_id"} & set(choice)
                # with candidates on the table the Planner can still write its own method
                [context] = package["method_proposal_contexts"]
                assert context["request"]["new_method_identity"]["method_id"].startswith("proposed-")
            assert counts == [0, 1, 1]  # a method id is one method: only its newest version is offered
            # the candidate is a proposed method that passed its independent review
            [row] = seen[-1]["views"]["methods"]
            assert row["review"] == {"outcome": "PASSED", "verdict": "ACCEPT"}
            assert [item["method_version"] for item in seen[-1]["method_selection"][0]["applicable"]] == [2]
    asyncio.run(case())


def test_the_context_for_writing_a_method_carries_the_requirement_text_and_a_fresh_identity(tmp_path):
    seen: list[dict] = []

    async def case():
        scripted = provider(planner=[propose(seen=seen), adopt(1)])
        async with assured_loop(tmp_path, scripted, success_criteria=("file:report.md", "报告里要写清三个要点")) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            package = seen[0]
            [choice] = package["method_selection"]
            assert choice["applicable"] == [] and choice["applicable_count"] == 0
            [context] = package["method_proposal_contexts"]
            request = context["request"]
            # the requirements in the person's own words, not the goal statement repeated
            first, second = request["criterion_evidence"]
            assert first["id"] == "c-user-1" and first["evidence_requirement"].startswith("file:report.md")
            assert second == {"id": "c-user-2", "evidence_requirement": "报告里要写清三个要点"}
            assert request["new_method_identity"]["method_version"] == 1
            assert request["goal_type_ref"]["id"] == "user-goal"
            assert sorted(item["task_type_ref"]["id"] for item in request["operators"]) == [
                "continue-delivery", "prepare-delivery"]
            # nothing addressed to a separate synthesiser role is left in it
            assert not {"output_tag", "role_prompt_version", "schema_feedback", "review_feedback"} & set(request)
            assert not events_of(world, "MethodSynthesisRoundRecorded")
            assert not events_of(world, "PlannerRoundSkippedForSynthesis")
    asyncio.run(case())


def test_a_draft_that_breaks_an_order_rule_is_refused_with_each_problem_listed(tmp_path):
    seen: list[dict] = []

    def clash(context):
        # the same method id and version as the method already proposed, another definition
        return method_body(context, steps=[("only", "prepare-delivery", {})], links=[(CRITERION, "only")],
                           finalizer="only", version=context["request"]["new_method_identity"]["method_version"] - 1)

    # the goal's requirement is carried by nobody (the link names another one)
    uncovered = _one_step(links=[("c-somebody-else", "write")])

    async def case():
        scripted = provider(planner=[propose(), propose(clash), propose(uncovered), adopt(1, seen=seen)])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            refused = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            assert len(refused) == 2
            [identity] = refused[0]["problems"]
            assert identity["detail"].startswith("METHOD_IDENTITY_TAKEN")
            assert "method_version=2" in identity["detail"]
            assert identity["field_path"] == "/payload/method_proposal"
            assert any(item["detail"].startswith("ROOT_COVERAGE_GAP") for item in refused[1]["problems"]), refused[1]
            # nothing refused was registered or sent to review: one proposal, one review
            assert len(events_of(world, "PlanningMethodProposed")) == 1
            assert len(events_of(world, "PlanningMethodReviewed")) == 1
            # the next round was shown both refusals, problem by problem
            [package] = seen
            shown = str([row for row in package["views"]["failures"] if row["source"] == "planning"])
            assert "METHOD_IDENTITY_TAKEN" in shown and "ROOT_COVERAGE_GAP" in shown
    asyncio.run(case())


def test_a_goal_takes_three_proposed_methods_and_no_more(tmp_path):
    async def case():
        rework = review("REWORK", limitation="still no step writes the report")
        scripted = provider(planner=[propose()] * 4, reviewer=[rework] * 3)
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: w.provider.asked.count("planner") == 4
                                   and len(events_of(w, "PlanningRejected")) == 1)
            assert len(events_of(world, "PlanningMethodProposed")) == 3
            [refusal] = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            [problem] = refusal["problems"]
            assert problem["detail"].startswith("METHOD_PROPOSALS_EXHAUSTED")
            assert problem["code"] == "PLANNING_BOUND_REACHED"
            assert len(events_of(world, "PlanningMethodReviewed")) == 3
    asyncio.run(case())


def test_a_file_to_publish_must_be_written_by_exactly_one_step(tmp_path):
    # the Mission publishes report.md: its ``file:`` requirement has to land on one step
    criteria = ("file:report.md", "action:file_publish.publish:report.md")

    def two_writers(context):
        return method_body(context, steps=[("draft", "prepare-delivery", {}), ("final", "prepare-delivery", {})],
                           links=[(CRITERION, "draft"), (CRITERION, "final")], finalizer="final")

    async def case():
        scripted = provider(planner=[propose(two_writers)])
        async with assured_loop(tmp_path, scripted, success_criteria=criteria, publishing=True) as world:
            # auto mode never signs a Mission with an operation: the person confirms it
            await run_until(world, lambda w: False, cycles=3)
            assert str(world.store.get_mission(world.mission.id).status.value) == "CREATED"
            confirm_completion(world)
            assert await run_until(world, lambda w: events_of(w, "PlanningRejected"))
            [refusal] = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            assert any(item["detail"].startswith("PUBLISH_SOURCE_AMBIGUOUS") and "report.md" in item["detail"]
                       for item in refusal["problems"]), refusal
            assert not events_of(world, "PlanningMethodProposed")
    asyncio.run(case())

