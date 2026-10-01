# SPDX-License-Identifier: Apache-2.0
"""新做法审阅：闸门在计划提交里，结论入库才唤醒规划器（HTN 精简 片 A 第 6、7 项）。

规划器为目标提出的做法只是"本任务试用"的草案。此前它一落库就把规划器叫醒，规划器（或
程序代答）马上就能采用——新做法的独立审阅还在路上，结论也没有任何人读。现在：

* 提出做法后规划器不被叫醒；等这份做法的审阅有了结论（通过 / 打回 / 人已裁决 / 给不出
  结论），由"新做法审阅结论已入库"这条服务事件叫醒，事件里带审阅员的原话。等待期间任务
  算"在等"，不算卡住。
* 计划提交事务里有一道闸：采用本任务试用的做法，必须有通过（或人裁决通过）的新做法审阅
  正式记录；否则提交被拒，原因是具名的 ``METHOD_NOT_AUTHORIZED``。
* 两位审阅员都判不下来 → 问人裁决；人答"通过"按通过处理，答"打回"按打回处理。

走真实循环（保证通道装配、真实存储 / 提交 / 审阅消费者 / 导入器），只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio

import pytest

from _assured_loop import (
    CRITERION,
    REVIEWER,
    ROOT_DUTY,
    ROOT_TASK,
    HeldProvider,
    assured_loop,
    events_of,
    proposed_method,
    propose_step,
    refine_with_step,
    review_reply,
    run_until,
)
from admitted_plans import compile_scripted
from scripted_plans import plan_revision_proposal_step, scripted_plan_proposal

from agent_orchestrator.contracts import TERMINAL_MISSION
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected, PlanPrincipal
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of


def _ref_json(contract):
    return contract.method_ref().to_json()


def _adopted_methods(world) -> list[str]:
    network = world.loop._new_mode(world.mission).network(world.mission.id)
    return sorted(str(draft.method_ref.method_id) + "@" + str(int(draft.method_ref.version))
                  for draft in network.method_instances
                  if draft.instance_id in network.adopted_instance_ids)


async def _spin(world, cycles: int = 25) -> None:
    for _ in range(cycles):
        await world.loop._cycle()
        await asyncio.sleep(0.01)


def test_a_proposed_method_waits_for_its_review_and_the_conclusion_wakes_the_planner(tmp_path):
    contract = proposed_method()

    async def case():
        provider = HeldProvider({"planner": [propose_step(contract), refine_with_step(contract)],
                                 REVIEWER: [review_reply("ACCEPT")]}, held=(REVIEWER,))
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodProposed"))
            # the review is out and unanswered: the Planner is not woken, and the
            # Mission is waiting — not stalled, not stopped
            await _spin(world)
            assert provider.by_role.get("planner") == 1
            assert not events_of(world, "PlanningServiceResumed")
            assert not events_of(world, "PlanningMethodReviewed")
            assert world.loop._has_pending_planning_waits(world.mission.id)
            assert world.store.get_mission(world.mission.id).status not in TERMINAL_MISSION

            provider.release.set()
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == "PASSED"
            assert reviewed.payload["verdict"] == "ACCEPT"
            assert reviewed.payload["method_ref"] == _ref_json(contract)
            assert reviewed.payload["record_id"]
            [resumed] = events_of(world, "PlanningServiceResumed")
            assert resumed.payload["source_type"] == "PlanningMethodReviewed"
            assert provider.by_role["planner"] == 2
            assert _adopted_methods(world) == ["plan.proposed@1"]
            assert not world.loop._has_pending_planning_waits(world.mission.id)
    asyncio.run(case())


def test_a_rejected_method_reaches_the_planner_in_the_reviewers_words_and_cannot_be_adopted(tmp_path):
    first, second = proposed_method(), proposed_method(version=2)
    seen: list[dict] = []

    seen_last: list[dict] = []

    def refine_the_rejected_one(request):
        seen.append(package_of(request))
        return refine_with_step(first)(request)

    def refine_the_passed_one(request):
        seen_last.append(package_of(request))
        return refine_with_step(second)(request)

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(first), refine_the_rejected_one, propose_step(second),
                        refine_the_passed_one],
            REVIEWER: [review_reply("REWORK", limitation="no step writes the report"),
                       review_reply("ACCEPT")]})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            rejected, passed = events_of(world, "PlanningMethodReviewed")
            assert rejected.payload["outcome"] == "REJECTED" and rejected.payload["verdict"] == "REWORK"
            [finding] = rejected.payload["findings"]
            assert finding["criterion_id"] == CRITERION
            assert "no step writes the report" in " ".join(finding["limitations"])
            assert passed.payload["outcome"] == "PASSED"
            assert passed.payload["method_ref"] == _ref_json(second)

            # the round the rejection woke was shown the method, its review and the words
            [package] = seen
            row = next(item for item in package["views"]["methods"]
                       if (item["method_ref"]["id"], item["method_ref"]["semantic_revision"])
                       == (first.method_id, first.method_version))
            assert row["review"]["outcome"] == "REJECTED"
            assert "no step writes the report" in str(row["review"]["findings"])

            # adopting the rejected method was refused by the commit gate, by name
            decisions = PlanningDecisionStore(world.store)
            refused = [event.payload for event in events_of(world, "PlanningDecisionEvaluated")
                       if event.payload.get("status") == "COMMIT_REJECTED"]
            assert [item["rejection_codes"] for item in refused] == [["METHOD_NOT_AUTHORIZED"]]
            assert "no step writes the report" in str(refused[0]["detail"])
            assert decisions is not None
            assert _adopted_methods(world) == ["plan.proposed@2"]

            # both versions stay visible as two rows, each with its own review
            final = seen_last[-1]
            rows = {item["method_ref"]["semantic_revision"]: item for item in final["views"]["methods"]
                    if item["method_ref"]["id"] == "plan.proposed"}
            assert rows[1]["review"]["outcome"] == "REJECTED"
            assert rows[2]["review"]["outcome"] == "PASSED"
    asyncio.run(case())


def test_adopting_a_method_whose_review_is_still_out_is_refused_inside_the_plan_commit(tmp_path):
    contract = proposed_method()

    async def case():
        provider = HeldProvider({"planner": [propose_step(contract)], REVIEWER: [review_reply("ACCEPT")]},
                                held=(REVIEWER,))
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodProposed"))
            reference = contract.method_ref()
            text = plan_revision_proposal_step(
                expected_plan_revision=0,
                read_set=[{"kind": "method", "id": reference.method_id,
                           "semantic_revision": reference.version, "content_hash": reference.content_hash}],
                operations=[{"op": "refine", "goal_id": ROOT_TASK, "obligation_id": ROOT_DUTY,
                             "method_ref": {"id": reference.method_id, "version": reference.version,
                                            "content_hash": reference.content_hash},
                             "bindings": {"subject": "alpha"}}])
            # 审阅员这一轮还挂着，带准入的入口会先看到"有工作没收敛"；这条测的是提交核心
            # 自己的那道检查，所以把编译好的命令直接交给提交核心。
            dispatch = world.loop._new_mode(world.mission)
            principal = PlanPrincipal("manager-1", "mission", 0)
            proposal = scripted_plan_proposal(text, mission_id=world.mission.id)
            command = dispatch.build_command(
                world.mission.id, proposal, compile_scripted(dispatch, world.mission.id, proposal),
                principal=principal, command_id="cmd-unreviewed", source={})
            with pytest.raises(PlanCommitRejected) as refused:
                world.commit.commit_plan_revision(command, principal)
            assert refused.value.reason == "METHOD_NOT_AUTHORIZED"
            assert not events_of(world, "PlanRevisionCommitted")
            provider.release.set()
            await _spin(world, 5)
    asyncio.run(case())


@pytest.mark.parametrize(("answer", "outcome", "adopted"), [
    ("pass", "PASSED", ["plan.proposed@1"]),
    ("fail", "REJECTED", []),
])
def test_two_inconclusive_method_reviews_ask_the_person_and_the_ruling_decides(
        tmp_path, answer, outcome, adopted):
    contract = proposed_method()

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(contract), refine_with_step(contract)],
            REVIEWER: [review_reply("INCONCLUSIVE", limitation="cannot tell whether one step is enough"),
                       review_reply("INCONCLUSIVE", limitation="cannot tell whether one step is enough")]})
        async with assured_loop(tmp_path, provider) as world:
            questions = PlanningHumanStore(world.store)
            assert await run_until(world, lambda w: questions.pending(w.mission.id))
            [question] = [row for row in questions.list(world.mission.id) if row["state"] == "PENDING"]
            assert question["decision_id"].startswith("adjudicate-method:")
            assert "cannot tell whether one step is enough" in question["request"]["payload"]["question"]
            await _spin(world, 10)
            assert provider.by_role.get("planner") == 1  # nobody is woken while the person decides
            assert not events_of(world, "PlanningMethodReviewed")

            questions.answer(decision_id=question["decision_id"], tenant_id=world.mission.tenant_id,
                             principal=Principal("assured-loop-user"), answer=answer,
                             expected_version=question["version"], nonce="n-1")
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodReviewed"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == outcome
            assert reviewed.payload["verdict"] == "INCONCLUSIVE"
            assert reviewed.payload["human_ruling"]["decision"] == answer
            # the ruling, not the answered question, is what wakes the Planner — once
            assert await run_until(world, lambda w: provider.by_role.get("planner") == 2)
            await _spin(world, 10)
            assert [e.payload["source_type"] for e in events_of(world, "PlanningServiceResumed")] == [
                "PlanningMethodReviewed"]
            assert _adopted_methods(world) == adopted
    asyncio.run(case())


def test_a_method_review_that_ends_without_a_verdict_is_reported_to_the_planner(tmp_path):
    contract = proposed_method()

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(contract), refine_with_step(contract)],
            REVIEWER: ["this is not a verdict", "still not a verdict"]})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodReviewed"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == "NO_VERDICT"
            assert reviewed.payload["record_id"] is None and reviewed.payload["reason"]
            assert await run_until(world, lambda w: provider.by_role.get("planner") == 2)
            await _spin(world, 10)
            # reported, and still not adoptable: no verdict is not a pass
            assert _adopted_methods(world) == []
            refused = [event.payload for event in events_of(world, "PlanningDecisionEvaluated")
                       if event.payload.get("status") == "COMMIT_REJECTED"]
            assert [item["rejection_codes"] for item in refused] == [["METHOD_NOT_AUTHORIZED"]]
    asyncio.run(case())


def test_a_ruling_question_that_went_stale_ends_the_wait_instead_of_holding_it_for_ever(tmp_path):
    """独立核验发现：裁决题在用户回答前过期（计划、要求或管理纪元变了），这份提案此前会
    永远停在"在等"——既不出结论，也让停滞检测对这个任务失效。现在按"没有结论"上报。"""
    contract = proposed_method()

    async def case():
        provider = RoleScriptedProvider({
            "planner": [propose_step(contract), refine_with_step(contract)],
            REVIEWER: [review_reply("INCONCLUSIVE", limitation="cannot tell"),
                       review_reply("INCONCLUSIVE", limitation="cannot tell")]})
        async with assured_loop(tmp_path, provider) as world:
            questions = PlanningHumanStore(world.store)
            assert await run_until(world, lambda w: questions.pending(w.mission.id))
            # the world the question was asked in is superseded before the person answers
            asked_at = world.htn.epoch(world.mission.id, "mission")
            while world.htn.epoch(world.mission.id, "mission") == asked_at:
                world.htn.bump_epoch(world.mission.id, "mission", bumped_by="fixture")
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodReviewed"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == "NO_VERDICT"
            assert reviewed.payload["verdict"] == "INCONCLUSIVE"
            assert "stale" in reviewed.payload["reason"]
            [question] = questions.list(world.mission.id)
            assert question["state"] == "STALE"
            await _spin(world, 10)
            assert not world.loop._has_pending_planning_waits(world.mission.id)
            assert _adopted_methods(world) == []
    asyncio.run(case())
