# SPDX-License-Identifier: Apache-2.0
"""选做法、提做法都是规划器的判断；程序只过滤和做秩序检查（HTN 精简 片 A 第 2～5 项）。

此前：只有一个候选做法时程序直接替规划器选；一个都没有时程序判定"该合成了"并另起一个方法
合成器角色；规划器自己提做法的那条路从没在真实循环里走通过。现在：

* 每个还没有做法的目标都开一轮规划器，候选有几个都一样；库里原有的做法不需要审阅就能采用。
* 写新做法要用的材料对每个还没有做法的目标都给——任务要求的原文、新做法该用的编号——不
  看候选有几个。候选清单里没有程序替它算好的"路由"和"选中项"。
* 草案的秩序检查在规划器这条路上做，被拒时逐条列出可修正的问题：编号撞车、要发布的文件
  没有唯一的产出步骤、每个目标最多提 3 次。
"""
from __future__ import annotations

import asyncio

from _assured_loop import (
    CRITERION,
    REVIEWER,
    assured_loop,
    events_of,
    proposed_method,
    propose_step,
    refine_with_step,
    review_reply,
    run_until,
)
from decision_loop import refine_step
from htn_world import method, param, step

from agent_orchestrator.contracts.htn import TaskForm
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of


def _library_method():
    return method("plan.library", "plan.goal", parameter_schema="plan.goal.params",
                  steps=(step("leaf", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                              capabilities=("plan.read",)),),
                  links=((CRITERION, "leaf", None),), finalizer="leaf")


def _decisions(world) -> list[dict]:
    return [event.payload for event in events_of(world, "PlanningDecisionEvaluated")]


def test_one_candidate_is_still_the_planners_choice_and_a_library_method_needs_no_review(tmp_path):
    seen: list[dict] = []

    def choose(request):
        seen.append(package_of(request))
        return refine_step()(request)

    async def case():
        provider = RoleScriptedProvider({"planner": [choose]})
        async with assured_loop(tmp_path, provider, library=(_library_method(),)) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            [committed] = [item for item in _decisions(world) if item["status"] == "COMMITTED"]
            # the Planner was asked and answered; nothing was decided by the program
            assert committed["decision_origin"] == "planner_reply"
            assert provider.by_role == {"planner": 1}
            assert not events_of(world, "NativePlanningDecisionPrepared")
            assert not events_of(world, "PlanningMethodProposed")

            [package] = seen
            [choice] = package["method_selection"]
            assert [item["method_id"] for item in choice["applicable"]] == ["plan.library"]
            assert choice["applicable_count"] == 1
            assert not {"route", "identity", "selected_method_id"} & set(choice)
            # with a candidate on the table the Planner can still write its own method
            [context] = package["method_proposal_contexts"]
            assert context["request"]["new_method_identity"]["method_id"].startswith("proposed-")
            # a library method carries no review, and is adoptable as it is
            [row] = [item for item in package["views"]["methods"]
                     if item["method_ref"]["id"] == "plan.library"]
            assert "review" not in row
    asyncio.run(case())


def test_the_context_for_writing_a_method_carries_the_requirement_text_and_a_fresh_identity(tmp_path):
    seen: list[dict] = []
    contract = proposed_method()

    def propose(request):
        seen.append(package_of(request))
        return propose_step(contract)(request)

    async def case():
        provider = RoleScriptedProvider({"planner": [propose, refine_with_step(contract)],
                                         REVIEWER: [review_reply("ACCEPT")]})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            [package] = seen
            [choice] = package["method_selection"]
            assert choice["applicable"] == [] and choice["applicable_count"] == 0
            [context] = package["method_proposal_contexts"]
            request = context["request"]
            # the requirement in the person's own words, not the goal statement repeated
            [evidence] = [item for item in request["criterion_evidence"] if item["id"] == CRITERION]
            assert "the report is written" in evidence["evidence_requirement"]
            assert request["new_method_identity"]["method_version"] == 1
            assert request["goal_type_ref"]["id"] == "plan.goal"
            assert [item["task_type_ref"]["id"] for item in request["operators"]] == ["plan.leaf"]
            # nothing addressed to a separate synthesiser role is left in it
            assert not {"output_tag", "role_prompt_version", "schema_feedback", "review_feedback"} & set(request)
            assert not events_of(world, "MethodSynthesisRoundRecorded")
            assert not events_of(world, "PlannerRoundSkippedForSynthesis")
    asyncio.run(case())


def test_a_draft_that_breaks_an_order_rule_is_refused_with_each_problem_listed(tmp_path):
    # the same method id and version as a library method, with a different definition
    library = _library_method()
    clash = method("plan.library", "plan.goal", parameter_schema="plan.goal.params",
                   steps=(step("only", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                               capabilities=("plan.read",)),),
                   links=((CRITERION, "only", None),), finalizer="only")
    # the goal's criterion is carried by nobody (the link names another): the registry's
    # own structural check
    uncovered = method("plan.uncovered", "plan.goal", parameter_schema="plan.goal.params",
                       steps=(step("leaf", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                                   capabilities=("plan.read",)),),
                       links=(("c-somebody-else", "leaf", None),), finalizer="leaf")
    seen: list[dict] = []

    def after_two_refusals(request):
        seen.append(package_of(request))
        return refine_step(method_id="plan.library")(request)

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(clash), propose_step(uncovered), after_two_refusals]})
        async with assured_loop(tmp_path, provider, library=(library,)) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            refused = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            assert len(refused) == 2
            [identity] = refused[0]["problems"]
            assert identity["detail"].startswith("METHOD_IDENTITY_TAKEN")
            assert "method_version=2" in identity["detail"]
            assert identity["field_path"] == "/payload/method_proposal"
            assert any(item["detail"].startswith("ROOT_COVERAGE_GAP") for item in refused[1]["problems"])
            # nothing refused was registered or sent to review
            assert not events_of(world, "PlanningMethodProposed")
            # the next round was shown both refusals, problem by problem
            [package] = seen
            shown = str([row for row in package["views"]["failures"] if row["source"] == "planning"])
            assert "METHOD_IDENTITY_TAKEN" in shown and "ROOT_COVERAGE_GAP" in shown
    asyncio.run(case())


def test_a_goal_takes_three_proposed_methods_and_no_more(tmp_path):
    versions = [proposed_method(version=n) for n in (1, 2, 3, 4)]

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(item) for item in versions],
            REVIEWER: [review_reply("REWORK", limitation="still no step writes the report")] * 3})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: provider.by_role.get("planner") == 4
                                   and len(events_of(w, "PlanningRejected")) == 1)
            assert len(events_of(world, "PlanningMethodProposed")) == 3
            [refusal] = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            [problem] = refusal["problems"]
            assert problem["detail"].startswith("METHOD_PROPOSALS_EXHAUSTED")
            assert problem["code"] == "PLANNING_BOUND_REACHED"
            assert len(events_of(world, "PlanningMethodReviewed")) == 3
    asyncio.run(case())


def test_a_file_to_publish_must_be_written_by_exactly_one_step(tmp_path):
    # the Mission publishes report.md: its ``file:`` criterion has to land on one step
    criteria = ("file:report.md", "action:file_publish.publish:report.md")
    # two steps both claim to write the file
    unlinked = method("plan.two-writers", "plan.goal", parameter_schema="plan.goal.params",
                      steps=(step("draft", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                                  capabilities=("plan.read",)),
                             step("final", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                                  capabilities=("plan.read",))),
                      links=((CRITERION, "draft", None), (CRITERION, "final", None)), finalizer="final")

    async def case():
        provider = RoleScriptedProvider({"planner": [propose_step(unlinked)]})
        async with assured_loop(tmp_path, provider, success_criteria=criteria,
                                approve_method_policy=False) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningRejected"))
            [refusal] = [event.payload["detail"] for event in events_of(world, "PlanningRejected")]
            assert any(item["detail"].startswith("PUBLISH_SOURCE_AMBIGUOUS") and "report.md" in item["detail"]
                       for item in refusal["problems"])
            assert not events_of(world, "PlanningMethodProposed")
    asyncio.run(case())


def test_the_three_proposal_bound_holds_off_the_assured_lane_too(tmp_path):
    """独立核验发现：次数是按"做法已提出"事件里的目标任务数的，而这个字段此前只在保证通道
    写入，别的通道上永远数到 0。"""
    from pathlib import Path

    import test_htn_end_to_end as e2e
    from decision_loop import auto_grant

    from agent_orchestrator.orchestrator.event_handler import Orchestrator
    from agent_orchestrator.runtime.assembly import OrchestratorConfig
    from agent_orchestrator.storage.htn_store import HtnStore

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = e2e.build_world(evidence, key="proposal-bound-plain-lane")
    world.store.close()
    drafts = [e2e._outer(f"plan.draft-{n}") for n in (1, 2, 3, 4)]
    provider = RoleScriptedProvider({"planner": [propose_step(item) for item in drafts]})

    async def case():
        config = OrchestratorConfig(evidence_root=evidence, max_concurrency=3, test_timeout_seconds=60)
        async with Orchestrator(config, provider, poll_interval=0.02) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            auto_grant(loop)
            mission_id = world.mission.id
            await loop._try_planner_intent(mission_id, ordinal=1)

            def kinds(name):
                return [event for event in loop.store.list_events(mission_id) if event.type == name]

            for _ in range(400):
                if kinds("PlanningRejected"):
                    break
                await loop._cycle()
                await asyncio.sleep(0.01)
            assert len(kinds("PlanningMethodProposed")) == 3
            [refusal] = [event.payload["detail"] for event in kinds("PlanningRejected")]
            assert refusal["problems"][0]["detail"].startswith("METHOD_PROPOSALS_EXHAUSTED")
    asyncio.run(case())
