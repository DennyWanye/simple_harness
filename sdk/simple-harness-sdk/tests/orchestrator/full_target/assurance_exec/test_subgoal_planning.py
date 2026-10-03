# SPDX-License-Identifier: Apache-2.0
"""中间目标：求做法走通用请求，按"分给它的要求"审，覆盖完整且归属唯一（HTN 精简 片 B）。

* 计划里出现还没有做法的目标 → 一条通用请求（触发源"目标还没有做法"）把规划器叫来，每个
  计划版本一条；此前是主循环里一条专用的"展开未细化目标"入口。
* 规划器为中间目标写做法时拿到的是分给这个目标的要求（原编号、用户原话）和可以往下放的
  子目标类型；独立审阅员按同样的要求审这份做法。
* 中间目标的做法必须恰好覆盖分给它的要求，每一步都要落到某条要求上——提做法时就查。

HTN 补齐阶段 A′：跑在产品同形世界（:mod:`_assured_loop` / :mod:`_subgoal_world`），只有模型
回复是脚本；做法一律由规划器提出、过独立审阅后采用（产品没有库内做法）。偏离（已记入迁移
报告）：
* "计划提交时再查一次库内做法的覆盖"三档删除：产品没有库内做法，每份做法提出时已查过，提交
  时那道复查在产品上碰不到（冗余检查，列入孤儿核查）；
* "循环里了结一条挂着的旧版本请求"删除：要手插一条产品自己写不出的请求（第 0 版计划没有
  目标）；了结规则本身由纯函数用例钉住；
* "组合审阅开不起来要说明原因"改为直接测记录函数：原用例靠补丁伪造产品内部失败；
* "别的任务的请求不吞掉这个任务的"改为同一个库里真跑两个任务，不再手插旧格式事件。
"""
from __future__ import annotations

import asyncio

import pytest
from _assured_loop import assured_loop, events_of, no_change, plan_revision, run_until, spin
from _subgoal_world import (
    DEEPER,
    PART,
    STATEMENTS,
    by_goal_type,
    deepest,
    inner,
    inner_with_deeper,
    open_goal_requests,
    outer,
    scopes,
)

from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.resolution import ReviewPurpose
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

ROOT_TYPE = "user-goal"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _held(planner) -> LayeredScriptedProvider:
    """Planner and reviewer scripted; the executor is held — planning is what these cases are about."""
    scripted = LayeredScriptedProvider(planner=planner)
    scripted.held.add("worker")
    return scripted


def _decisions(world) -> list[tuple[str, str]]:
    return [(str(event.payload.get("decision_type")), str(event.payload.get("status")))
            for event in events_of(world, "PlanningDecisionEvaluated")]


def test_a_goal_without_a_method_reaches_the_planner_as_a_generic_request_and_is_reviewed_on_its_share(tmp_path):
    seen: dict[str, list[dict]] = {}

    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer(), PART: inner()}, seen=seen))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            assert await run_until(world, lambda w: plan_revision(w) == 2)

            # one generic request for the first plan revision; the dedicated entry is gone
            [request] = open_goal_requests(world)
            assert request.payload["source_key"] == f"open-goals:{world.mission.id}:1"
            assert not events_of(world, "HierarchicalRefinementRequested")
            network = world.loop._new_mode(world.mission).network(world.mission.id)
            part = next(spec for spec in network.occurrences
                        if str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id) == PART)
            assert request.payload["request"]["context"]["open_goals"] == [
                {"task_id": str(part.task_id), "occurrence_id": str(part.occurrence_id), "goal_type": PART}]
            # the request asks for work that does not exist yet; nothing accepted is in question
            assert request.payload["impact"]["new_work"] == [str(part.task_id)]
            assert request.payload["impact"]["revalidate"] == []
            assert request.payload["impact"]["supersede"] == []

            # what the Planner was shown when it wrote the sub-goal's method
            [package] = seen[PART]
            assert [row["source_key"] for row in package["repair_requests"]] == [f"open-goals:{world.mission.id}:1"]
            [context] = [row["request"] for row in package["method_proposal_contexts"]
                         if row["request"]["goal_type_ref"]["id"] == PART]
            assert context["criterion_evidence"] == [
                {"id": "c-user-1", "evidence_requirement": STATEMENTS[0]},
                {"id": "c-user-2", "evidence_requirement": STATEMENTS[1]}]
            assert [(row["task_type_ref"]["id"], row["level"]) for row in context["subgoal_types"]] == [(DEEPER, 2)]

            # the independent review of that method is asked the same two requirements
            packages = world.htn.list_review_packages(world.mission.id, purpose=ReviewPurpose.METHOD_PLAN)
            shares = sorted([(item.criterion_id, item.statement) for item in row.criteria] for row in packages)
            assert [("c-user-1", STATEMENTS[0]), ("c-user-2", STATEMENTS[1])] in shares
            assert len(packages) == 2  # the root's method and the sub-goal's

            assert _decisions(world) == [("PROPOSE_METHOD", "NO_STATE_CHANGE"), ("REFINE", "COMMITTED"),
                                         ("PROPOSE_METHOD", "NO_STATE_CHANGE"), ("REFINE", "COMMITTED")]
            addressed = [item for event in events_of(world, "PlanningRepairAddressed")
                         for item in event.payload["repair_request_ids"]]
            assert addressed == [request.payload["request_id"]]
            assert scopes(world) == {
                "user-goal/root": ["c-user-1", "c-user-2", "c-user-3"],
                "sub-goal-1/part": ["c-user-1", "c-user-2"],
                "prepare-delivery/a": ["c-user-1"], "prepare-delivery/b": ["c-user-2"],
                "prepare-delivery/tail": ["c-user-3"]}
        scripted.release.set()
    asyncio.run(case())


def test_each_plan_revision_asks_once_and_three_levels_are_reached_through_the_same_request(tmp_path):
    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer(), PART: inner_with_deeper(), DEEPER: deepest()}))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            assert await run_until(world, lambda w: plan_revision(w) == 3)
            assert [event.payload["source_key"] for event in open_goal_requests(world)] == [
                f"open-goals:{world.mission.id}:1", f"open-goals:{world.mission.id}:2"]
            assert scopes(world)["sub-goal-2/deeper"] == ["c-user-1"]
            assert scopes(world)["prepare-delivery/only"] == ["c-user-1"]
        scripted.release.set()
    asyncio.run(case())


def test_a_planner_that_leaves_the_goal_open_is_not_asked_again_for_the_same_plan_revision(tmp_path):
    tree = by_goal_type({ROOT_TYPE: outer()})

    def planner(request):
        reply = tree(request)
        return reply if reply is not None else no_change(request)

    async def case():
        scripted = _held(planner)
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningDecisionEvaluated")) == 3)
            await spin(world, 40)
            assert len(open_goal_requests(world)) == 1
            assert _decisions(world) == [("PROPOSE_METHOD", "NO_STATE_CHANGE"), ("REFINE", "COMMITTED"),
                                         ("NO_CHANGE", "NO_STATE_CHANGE")]
            assert len(events_of(world, "PlanningDecisionEvaluated")) == 3
            assert plan_revision(world) == 1
        scripted.release.set()
    asyncio.run(case())


def test_a_proposed_method_must_cover_exactly_the_requirements_handed_to_its_goal(tmp_path):
    """漏覆盖、越界、有一步没落到任何要求上——提做法时逐条说明，不送审。"""
    missing = inner((("c-user-1", "a"), ("c-user-1", "b")))
    foreign = inner((("c-user-1", "a"), ("c-user-2", "b"), ("c-user-3", "b")))
    idle_step = inner((("c-user-1", "a"), ("c-user-2", "a")))

    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer(), PART: [missing, foreign, idle_step, inner()]}))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS, max_planning_attempts=5) as world:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningMethodProposed")) == 2)
            refusals = [event.payload["detail"]["problems"] for event in events_of(world, "PlanningRejected")]
            assert len(refusals) == 3
            texts = [" ".join(item["detail"] for item in problems) for problems in refusals]
            assert "SUBGOAL_COVERAGE" in texts[0] and "c-user-2" in texts[0]
            assert "SUBGOAL_COVERAGE" in texts[1] and "c-user-3" in texts[1]
            assert "SUBGOAL_COVERAGE" in texts[2] and "'b'" in texts[2]
            # the root's method and the good one; the refused three were never registered or reviewed
            # the root's method and the good one; the refused three were never registered
            root, sub_goal = [event.payload for event in events_of(world, "PlanningMethodProposed")]
            assert root["subject_task_id"] == f"user-root-{world.mission.id}"
            assert sub_goal["subject_task_id"] != root["subject_task_id"]
            assert sub_goal["method_ref"]["version"] == 1
        scripted.release.set()
    asyncio.run(case())


def test_a_request_about_an_older_plan_revision_is_retired_by_the_system():
    """"第 N 版计划里这些目标没有做法"在计划到了别的版本之后就过时了：还开着的目标由新版本
    自己的请求去说，不让两条请求指着同一个目标。别的请求不动。"""
    from agent_orchestrator.orchestrator.planning_repair_requests import (
        superseded_revision_requests,
    )

    pending = [{"request_id": "r1", "source_key": "open-goals:m1:1"},
               {"request_id": "r2", "source_key": "open-goals:m1:2"},
               {"request_id": "r3", "source_key": "event:step-failed"}]
    assert superseded_revision_requests(pending, "m1", 2) == ["r1"]
    assert superseded_revision_requests(pending, "m1", 3) == ["r1", "r2"]
    assert superseded_revision_requests(pending[2:], "m1", 3) == []


def test_the_first_plan_is_not_asked_for_through_a_request(tmp_path):
    """根目标在第一份计划之前没有做法是规划的起点，不是"计划里有目标没做法"。"""
    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer()}))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            assert await run_until(world, lambda w: plan_revision(w) == 1 or open_goal_requests(w))
            assert plan_revision(world) == 1  # nothing asked before the first plan existed
            assert await run_until(world, lambda w: open_goal_requests(w))
            assert plan_revision(world) == 1
        scripted.release.set()
    asyncio.run(case())


# ---------------------------------------------------------------- 带发布要求的任务（独立核验发现）
PUBLISH_STATEMENTS = ("写出 notes/a.md，列三条要点", "file:README.md",
                      "写出 notes/c.md，给出一个例子", "action:file_publish.publish:README.md")


async def _publishing(tmp_path, planner):
    from _assured_loop import confirm_completion

    scripted = _held(planner)
    context = assured_loop(tmp_path, scripted, success_criteria=PUBLISH_STATEMENTS, publishing=True)
    world = await context.__aenter__()
    await run_until(world, lambda w: False, cycles=3)
    confirm_completion(world)  # the person confirms a Mission with an operation
    return context, world


def test_the_publish_source_rule_is_checked_on_the_goals_own_share_only(tmp_path):
    """要发布的文件由收尾步骤负责（没有交给中间目标）：中间目标的做法不该因为"没把那份文件
    链接到恰好一个步骤"被拒——那条要求不归它，链了反而越界。两条规则此前互相矛盾，这样的
    中间目标永远提不出做法。"""
    good = inner((("c-user-1", "a"), ("c-user-3", "b")))

    async def case():
        context, world = await _publishing(tmp_path, by_goal_type(
            {ROOT_TYPE: outer(("c-user-1", "c-user-3"), ("c-user-2",)), PART: good}))
        try:
            assert await run_until(world, lambda w: len(events_of(w, "PlanningMethodProposed")) == 2)
            assert not events_of(world, "PlanningRejected")
        finally:
            world.provider.release.set()
            await context.__aexit__(None, None, None)
    asyncio.run(case())


def test_the_publish_source_rule_still_holds_inside_the_goal_that_owns_the_file(tmp_path):
    """要发布的文件交给了中间目标：它的做法里这份文件仍必须恰好由一个步骤写出。"""
    twice = inner((("c-user-1", "a"), ("c-user-2", "a"), ("c-user-2", "b")))

    async def case():
        context, world = await _publishing(tmp_path, by_goal_type(
            {ROOT_TYPE: outer(("c-user-1", "c-user-2"), ("c-user-3",)), PART: twice}))
        try:
            assert await run_until(world, lambda w: events_of(w, "PlanningRejected"))
            [refusal] = [event.payload["detail"]["problems"] for event in events_of(world, "PlanningRejected")]
            assert any("PUBLISH_SOURCE_AMBIGUOUS" in item["detail"] and "README.md" in item["detail"]
                       for item in refusal), refusal
            assert len(events_of(world, "PlanningMethodProposed")) == 1  # only the root's
        finally:
            world.provider.release.set()
            await context.__aexit__(None, None, None)
    asyncio.run(case())


def test_a_sub_goal_whose_review_cannot_be_opened_says_why(tmp_path):
    """片 B 真机第 1 局：中间目标的两步都验收通过后，它的组合审阅没有开起来；开审那一步的
    异常被静默吞掉，任务停在"排队"十几分钟，库里一条线索都没有。现在这种失败记一条事件
    （同一个目标同一个原因只记一次），原因原样写进去。

    直接测记录函数（组合审阅装配交给它的 ``on_deferred``）；原用例靠补丁伪造产品内部失败。"""

    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer()}))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            assembly = world.loop._composition_assembly(world.mission, world.loop._new_mode(world.mission))
            error = ContractError("Assurance COMPOSITION review unavailable: SOME_REASON")
            for _ in range(2):
                assembly.on_deferred("occ-part", "task-part", PART, error)
            [deferred] = events_of(world, "CompositionReviewDeferred")
            assert "SOME_REASON" in deferred.payload["reason"]
            assert deferred.payload["goal_type"] == PART
            assert deferred.task_id == deferred.payload["task_id"] == "task-part"
            assembly.on_deferred("occ-part", "task-part", PART, ContractError("ANOTHER_REASON"))
            assert len(events_of(world, "CompositionReviewDeferred")) == 2
        scripted.release.set()
    asyncio.run(case())


def test_another_missions_request_in_the_same_store_does_not_swallow_this_one(tmp_path):
    """片 B 真机第 2、3 局：请求的幂等键原先只有"open-goals:<计划版本号>"，没带任务号。同一个库里
    第一个任务占了"第 1 版"的键之后，后面每个任务的第 1 版请求都撞键、写不进去——规划器永远
    不会被叫来，循环还每轮自称有进展。单元测试每条用新库，所以只有真机暴露。这里在同一个库里
    真跑两个任务。"""

    async def case():
        scripted = _held(by_goal_type({ROOT_TYPE: outer()}))
        async with assured_loop(tmp_path, scripted, success_criteria=STATEMENTS) as world:
            second = world.product.create({"goal": "再写一份报告", "idempotency_key": "second-mission",
                                           "success_criteria": list(STATEMENTS)})["mission_id"]

            def requests(mission_id):
                return [event for event in world.store.list_events(mission_id)
                        if event.type == "PlanningRepairRequested"
                        and (event.payload.get("request") or {}).get("trigger_source") == "GOAL_UNREFINED"]

            assert await run_until(world, lambda w: requests(w.mission.id) and requests(second))
            for mission_id in (world.mission.id, second):
                [request] = requests(mission_id)
                assert request.mission_id == mission_id
                assert request.payload["source_key"] == f"open-goals:{mission_id}:1"
        scripted.release.set()
    asyncio.run(case())
