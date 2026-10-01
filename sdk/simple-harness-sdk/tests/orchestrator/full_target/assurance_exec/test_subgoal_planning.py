# SPDX-License-Identifier: Apache-2.0
"""中间目标：求做法走通用请求，按"分给它的要求"审，覆盖完整且归属唯一（HTN 精简 片 B）。

* 计划里出现还没有做法的目标 → 一条通用请求（触发源"目标还没有做法"）把规划器叫来，每个
  计划版本一条；此前是主循环里一条专用的"展开未细化目标"入口。
* 规划器为中间目标写做法时拿到的是分给这个目标的要求（原编号、用户原话）和可以往下放的
  子目标类型；独立审阅员按同样的要求审这份做法。
* 中间目标的做法必须恰好覆盖分给它的要求，每一步都要落到某条要求上——提做法时就查，
  计划提交时再查一次（库里的做法也过这道）。

走真实循环（保证通道装配），只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio

import pytest

from _assured_loop import REVIEWER, assured_loop, events_of, propose_step, refine_with_step, run_until
from _subgoal_world import (
    STATEMENTS,
    deepest,
    env,
    inner,
    inner_with_deeper,
    open_goal_requests,
    outer,
    plan_revision,
    review_of,
    scopes,
)
from decision_loop import _envelope, decision_text, refine_step

from agent_orchestrator.contracts.resolution import ReviewPurpose
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of

WORLD = {"env_factory": env, "root_type": "sg.goal", "success_criteria": STATEMENTS, "host_policies": True}


def _decisions(world) -> list[tuple[str, str]]:
    return [(str(event.payload.get("decision_type")), str(event.payload.get("status")))
            for event in events_of(world, "PlanningDecisionEvaluated")]


def _open_goal_context(package: dict) -> dict:
    goal = package["plan"]["open_compound_goals"][0]
    subject = next(row["subject_key"] for row in package["planning_subjects"]
                   if row["occurrence_id"] == goal["occurrence_id"])
    return next(row for row in package["method_proposal_contexts"] if row["subject_key"] == subject)


def test_a_goal_without_a_method_reaches_the_planner_as_a_generic_request_and_is_reviewed_on_its_share(tmp_path):
    contract = inner()
    seen: list[dict] = []
    package_mission: list[str] = []

    def propose(request):
        seen.append(package_of(request))
        return propose_step(contract)(request)

    async def case():
        provider = RoleScriptedProvider({
            "planner": [refine_step(method_id="sg.outer"), propose, refine_with_step(contract)],
            REVIEWER: [review_of("c-user-1", "c-user-2")]})
        async with assured_loop(tmp_path, provider, library=(outer(),), **WORLD) as world:
            package_mission.append(world.mission.id)
            assert await run_until(world, lambda w: plan_revision(w) == 2)

            # one generic request for the first plan revision; the dedicated entry is gone
            [request] = open_goal_requests(world)
            assert request.payload["source_key"] == f"open-goals:{world.mission.id}:1"
            assert not events_of(world, "HierarchicalRefinementRequested")
            network = world.loop._new_mode(world.mission).network(world.mission.id)
            part = next(spec for spec in network.occurrences
                        if str(network.binding_for_occurrence(spec.occurrence_id)
                               .goal_signature.signature_id) == "sg.part")
            assert request.payload["request"]["context"]["open_goals"] == [
                {"task_id": str(part.task_id), "occurrence_id": str(part.occurrence_id),
                 "goal_type": "sg.part"}]
            # the request asks for work that does not exist yet; nothing accepted is in question
            assert request.payload["impact"]["new_work"] == [str(part.task_id)]
            assert request.payload["impact"]["revalidate"] == []
            assert request.payload["impact"]["supersede"] == []

            # what the Planner was shown when it wrote the sub-goal's method
            package = seen[0]
            assert [row["source_key"] for row in package["repair_requests"]] == [
                f"open-goals:{package_mission[0]}:1"]
            context = _open_goal_context(package)["request"]
            assert context["criterion_evidence"] == [
                {"id": "c-user-1", "evidence_requirement": STATEMENTS[0]},
                {"id": "c-user-2", "evidence_requirement": STATEMENTS[1]}]
            assert [(row["task_type_ref"]["id"], row["level"]) for row in context["subgoal_types"]] == [
                ("sg.part-deeper", 2)]

            # the independent review of that method is asked the same two requirements
            [package_row] = [item for item in world.htn.list_review_packages(
                world.mission.id, purpose=ReviewPurpose.METHOD_PLAN)]
            assert [(item.criterion_id, item.statement) for item in package_row.criteria] == [
                ("c-user-1", STATEMENTS[0]), ("c-user-2", STATEMENTS[1])]

            assert _decisions(world) == [("REFINE", "COMMITTED"), ("PROPOSE_METHOD", "NO_STATE_CHANGE"),
                                         ("REFINE", "COMMITTED")]
            addressed = [item for event in events_of(world, "PlanningRepairAddressed")
                         for item in event.payload["repair_request_ids"]]
            assert addressed == [request.payload["request_id"]]
            assert scopes(world) == {
                "sg.goal/root": ["c-user-1", "c-user-2", "c-user-3"],
                "sg.part/part": ["c-user-1", "c-user-2"],
                "sg.write/a": ["c-user-1"], "sg.write/b": ["c-user-2"], "sg.write/tail": ["c-user-3"]}
    asyncio.run(case())


def test_each_plan_revision_asks_once_and_three_levels_are_reached_through_the_same_request(tmp_path):
    async def case():
        provider = RoleScriptedProvider({"planner": [
            refine_step(method_id="sg.outer"), refine_step(method_id="sg.inner-deep"),
            refine_step(method_id="sg.deepest")]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner_with_deeper(), deepest()),
                                **WORLD) as world:
            assert await run_until(world, lambda w: plan_revision(w) == 3)
            assert [event.payload["source_key"] for event in open_goal_requests(world)] == [
                f"open-goals:{world.mission.id}:1", f"open-goals:{world.mission.id}:2"]
            assert scopes(world)["sg.part-deeper/deeper"] == ["c-user-1"]
            assert scopes(world)["sg.write/only"] == ["c-user-1"]
    asyncio.run(case())


def test_a_planner_that_leaves_the_goal_open_is_not_asked_again_for_the_same_plan_revision(tmp_path):
    def no_change(request):
        package = package_of(request)
        goal = package["plan"]["open_compound_goals"][0]
        subject = next(row["subject_key"] for row in package["planning_subjects"]
                       if row["occurrence_id"] == goal["occurrence_id"])
        return decision_text(_envelope(subject, "NO_CHANGE", {"reason": "nothing to change"},
                                       rationale="计划不需要改动。"))

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer"), no_change]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner()), **WORLD) as world:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningDecisionEvaluated")) == 2)
            for _ in range(40):
                await world.loop._cycle()
                await asyncio.sleep(0.005)
            assert len(open_goal_requests(world)) == 1
            assert len(events_of(world, "PlanningDecisionEvaluated")) == 2
            assert plan_revision(world) == 1
    asyncio.run(case())


def test_a_proposed_method_must_cover_exactly_the_requirements_handed_to_its_goal(tmp_path):
    """漏覆盖、越界、有一步没落到任何要求上——提做法时逐条说明，不送审。"""
    missing = inner("sg.missing", links=(("c-user-1", "a", None), ("c-user-1", "b", None)))
    foreign = inner("sg.foreign", links=(("c-user-1", "a", None), ("c-user-2", "b", None),
                                         ("c-user-3", "b", None)))
    idle_step = inner("sg.idle", links=(("c-user-1", "a", None), ("c-user-2", "a", None)))
    good = inner("sg.good")

    async def case():
        provider = RoleScriptedProvider({"planner": [
            refine_step(method_id="sg.outer"), propose_step(missing), propose_step(foreign),
            propose_step(idle_step), propose_step(good)]})
        async with assured_loop(tmp_path, provider, library=(outer(),), max_planning_attempts=5,
                                **WORLD) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodProposed"))
            refusals = [event.payload["detail"]["problems"] for event in events_of(world, "PlanningRejected")]
            assert len(refusals) == 3
            texts = [" ".join(item["detail"] for item in problems) for problems in refusals]
            assert "SUBGOAL_COVERAGE" in texts[0] and "c-user-2" in texts[0]
            assert "SUBGOAL_COVERAGE" in texts[1] and "c-user-3" in texts[1]
            assert "SUBGOAL_COVERAGE" in texts[2] and "'b'" in texts[2]
            [proposed] = events_of(world, "PlanningMethodProposed")
            assert proposed.payload["method_ref"]["method_id"] == "sg.good"
    asyncio.run(case())


@pytest.mark.parametrize("links, named", [
    ((("c-user-1", "a", None), ("c-user-1", "b", None)), "c-user-2"),                      # 漏一条
    ((("c-user-1", "a", None), ("c-user-2", "b", None), ("c-user-3", "b", None)), "c-user-3"),  # 越界
    ((("c-user-1", "a", None), ("c-user-2", "a", None)), "linked to no requirement"),      # 有一步没落地
])
def test_the_plan_commit_refuses_a_library_method_that_does_not_cover_the_sub_goals_share(tmp_path, links, named):
    """库里的做法不过提做法那一关，所以同一条检查在计划提交里再做一次。"""
    short = inner("sg.short", links=links)

    async def case():
        provider = RoleScriptedProvider({"planner": [
            refine_step(method_id="sg.outer"), refine_step(method_id="sg.short")]})
        async with assured_loop(tmp_path, provider, library=(outer(), short), **WORLD) as world:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningDecisionEvaluated")) == 2)
            refused = events_of(world, "PlanningDecisionEvaluated")[-1].payload
            assert refused["status"] == "COMMIT_REJECTED"
            assert "SUBGOAL_COVERAGE" in str(refused["detail"]) and named in str(refused["detail"])
            assert plan_revision(world) == 1
    asyncio.run(case())


def test_a_stale_request_left_pending_is_retired_in_the_loop(tmp_path):
    """接线：计划已经到了第 1 版，却还挂着一条说"第 0 版有目标没做法"的请求——下一轮循环由
    系统了结它（标明是被新计划版本取代），当前版本自己的那条请求不受影响。"""
    from agent_orchestrator.orchestrator.planning_repair_requests import pending_requests, record_request

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer")]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner()), **WORLD) as world:
            assert await run_until(world, lambda w: open_goal_requests(w))
            dispatch = world.loop._new_mode(world.mission)
            goal = open_goal_requests(world)[0].payload["request"]["context"]["open_goals"][0]["task_id"]
            assert record_request(dispatch, world.mission.id, event_type="GoalUnrefined",
                                  trigger_refs=(goal,), source_key=f"open-goals:{world.mission.id}:0",
                                  detail={"reason": "goal_has_no_method"}, new_work=(goal,))
            stale = next(row["request_id"] for row in pending_requests(world.store, world.mission.id)
                         if row["source_key"] == f"open-goals:{world.mission.id}:0")
            await world.loop._cycle()
            retired = [event.payload for event in events_of(world, "PlanningRepairAddressed")
                       if stale in event.payload["repair_request_ids"]]
            assert [item["decision_type"] for item in retired] == ["SYSTEM_SUPERSEDED"]
            assert [row["source_key"] for row in pending_requests(world.store, world.mission.id)] == [
                f"open-goals:{world.mission.id}:1"]
    asyncio.run(case())


def test_a_request_about_an_older_plan_revision_is_retired_by_the_system():
    """"第 N 版计划里这些目标没有做法"在计划到了别的版本之后就过时了：还开着的目标由新版本
    自己的请求去说，不让两条请求指着同一个目标。别的请求不动。"""
    from agent_orchestrator.orchestrator.planning_repair_requests import superseded_open_goal_requests

    pending = [{"request_id": "r1", "source_key": "open-goals:m1:1"},
               {"request_id": "r2", "source_key": "open-goals:m1:2"},
               {"request_id": "r3", "source_key": "event:step-failed"}]
    assert superseded_open_goal_requests(pending, "m1", 2) == ["r1"]
    assert superseded_open_goal_requests(pending, "m1", 3) == ["r1", "r2"]
    assert superseded_open_goal_requests(pending[2:], "m1", 3) == []


def test_the_first_plan_is_not_asked_for_through_a_request(tmp_path):
    """根目标在第一份计划之前没有做法是规划的起点，不是"计划里有目标没做法"。"""
    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer")]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner()), **WORLD) as world:
            for _ in range(3):
                await world.loop._cycle()
                if plan_revision(world) == 0:
                    assert open_goal_requests(world) == []
            assert await run_until(world, lambda w: open_goal_requests(w))
            assert plan_revision(world) == 1
    asyncio.run(case())


# ---------------------------------------------------------------- 带发布要求的任务（独立核验发现）
PUBLISH_STATEMENTS = ("写出 notes/a.md，列三条要点", "file:README.md",
                      "写出 notes/c.md，给出一个例子", "action:file_publish.publish:README.md")


def _publish_world(part_share: tuple[str, ...], tail_share: tuple[str, ...]):
    from _subgoal_world import PART, ROOT, WRITE, _goal, _write
    from htn_world import Env, method

    from agent_orchestrator.contracts.htn import TaskForm

    user = ("c-user-1", "c-user-2", "c-user-3", "c-user-4")

    def factory(mission_id: str) -> Env:
        world = Env(mission=mission_id)
        world.register_type(ROOT, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                            criteria=user, domain="sg", level=0)
        world.register_type(PART, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                            domain="sg", level=1)
        world.register_type(WRITE, parameters=(("subject", "string"),),
                            outputs=(("delivery", "sg.delivery"),), capabilities=("plan.read",),
                            criteria=user, domain="sg")
        return world

    root_method = method(
        "sg.outer", ROOT, parameter_schema=f"{ROOT}.params",
        steps=(_goal("part", PART), _write("tail")), ordering=(("part", "tail"),),
        links=tuple((item, "part", item) for item in part_share)
        + tuple((item, "tail", None) for item in tail_share),
        finalizer="tail")
    return {"env_factory": factory, "root_type": ROOT, "success_criteria": PUBLISH_STATEMENTS,
            "host_policies": True, "library": (root_method,)}


def test_the_publish_source_rule_is_checked_on_the_goals_own_share_only(tmp_path):
    """要发布的文件由收尾步骤负责（没有交给中间目标）：中间目标的做法不该因为"没把那份文件
    链接到恰好一个步骤"被拒——那条要求不归它，链了反而越界。两条规则此前互相矛盾，这样的
    中间目标永远提不出做法。"""
    good = inner("sg.good", links=(("c-user-1", "a", None), ("c-user-3", "b", None)))

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer"), propose_step(good)]})
        world_kwargs = _publish_world(("c-user-1", "c-user-3"), ("c-user-2", "c-user-4"))
        async with assured_loop(tmp_path, provider, **world_kwargs) as world:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningDecisionEvaluated")) == 2)
            assert not events_of(world, "PlanningRejected")
            [proposed] = events_of(world, "PlanningMethodProposed")
            assert proposed.payload["method_ref"]["method_id"] == "sg.good"
    asyncio.run(case())


def test_the_publish_source_rule_still_holds_inside_the_goal_that_owns_the_file(tmp_path):
    """要发布的文件交给了中间目标：它的做法里这份文件仍必须恰好由一个步骤写出。"""
    twice = inner("sg.twice", links=(("c-user-1", "a", None), ("c-user-2", "a", None),
                                     ("c-user-2", "b", None)))

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer"), propose_step(twice)]})
        world_kwargs = _publish_world(("c-user-1", "c-user-2"), ("c-user-3", "c-user-4"))
        async with assured_loop(tmp_path, provider, **world_kwargs) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningRejected"))
            [refusal] = [event.payload["detail"]["problems"] for event in events_of(world, "PlanningRejected")]
            assert any("PUBLISH_SOURCE_AMBIGUOUS" in item["detail"] and "README.md" in item["detail"]
                       for item in refusal)
            assert not events_of(world, "PlanningMethodProposed")
    asyncio.run(case())


def test_a_sub_goal_whose_review_cannot_be_opened_says_why(tmp_path, monkeypatch):
    """片 B 真机第 1 局：中间目标的两步都验收通过后，它的组合审阅没有开起来；开审那一步的
    异常被静默吞掉，任务停在"排队"十几分钟，库里一条线索都没有。现在这种失败记一条事件
    （同一个目标同一个原因只记一次），原因原样写进去。"""
    from agent_orchestrator.contracts.models import ContractError
    from agent_orchestrator.orchestrator import composition_review
    from agent_orchestrator.orchestrator.hierarchical_dispatch import CompoundPhase

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer")]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner()), **WORLD) as world:
            assert await run_until(world, lambda w: plan_revision(w) == 1)
            dispatch = world.loop._new_mode(world.mission)
            assembly = world.loop._composition_assembly(world.mission, dispatch)
            monkeypatch.setattr(composition_review, "next_compound_phase",
                                lambda *args, **kwargs: CompoundPhase.COMPOSITION_REVIEW)

            def refuse(self, mission_id, occurrence_id):
                raise ContractError("Assurance COMPOSITION review unavailable: SOME_REASON")

            monkeypatch.setattr(composition_review.CompositionAcceptanceAssembly, "resolve_one", refuse)
            assert assembly.resolve_ready(world.mission.id) == ()
            assert assembly.resolve_ready(world.mission.id) == ()
            [deferred] = events_of(world, "CompositionReviewDeferred")
            assert "SOME_REASON" in deferred.payload["reason"]
            assert deferred.payload["goal_type"] == "sg.part"
            assert deferred.task_id == deferred.payload["task_id"]
    asyncio.run(case())


def test_another_missions_request_in_the_same_store_does_not_swallow_this_one(tmp_path):
    """片 B 真机第 2、3 局：请求的幂等键原先只有"open-goals:<计划版本号>"，没带任务号。同一个库里
    第一个任务占了"第 1 版"的键之后，后面每个任务的第 1 版请求都撞键、写不进去——规划器永远
    不会被叫来，循环还每轮自称有进展。单元测试每条用新库，所以只有真机暴露。"""
    from agent_orchestrator.orchestrator.hierarchical_dispatch import append_hierarchical_event

    async def case():
        provider = RoleScriptedProvider({"planner": [refine_step(method_id="sg.outer")]})
        async with assured_loop(tmp_path, provider, library=(outer(), inner()), **WORLD) as world:
            # an earlier Mission of this store already recorded "its" first-revision request
            with world.store.transaction():
                append_hierarchical_event(
                    world.store, "PlanningRepairRequested", "mission-earlier", key="open-goals:1",
                    payload={"source_key": "open-goals:1", "request_id": "earlier",
                             "request": {"trigger_source": "GOAL_UNREFINED"}, "impact": {}})
            assert await run_until(world, lambda w: open_goal_requests(w))
            [request] = open_goal_requests(world)
            assert request.mission_id == world.mission.id
            assert world.mission.id in request.payload["source_key"]
    asyncio.run(case())
