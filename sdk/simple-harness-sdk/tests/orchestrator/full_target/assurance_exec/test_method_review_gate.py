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

HTN 补齐阶段 A′：跑在产品同形世界（:mod:`_assured_loop`：产品部署组装、执行图建任务即绑定、
部署职责两轮之间代签授权与检查策略），只有模型回复是脚本。原"审阅还没结论就采用、提交核心
自己拒"一条删除：产品上规划器在等审阅时不被叫醒，那道检查只能手搭命令直接调提交核心才碰得到；
同一道闸由下面"被打回""没有结论"两条经真实回复碰到。
"""
from __future__ import annotations

import asyncio

import pytest
from _assured_loop import (
    CRITERION,
    adopt,
    adopted_methods,
    assured_loop,
    events_of,
    propose,
    provider,
    review,
    run_until,
    spin,
)

from agent_orchestrator.contracts import TERMINAL_MISSION
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _versions(world) -> list[int]:
    return [int(item.rpartition("@")[2]) for item in adopted_methods(world)]


def _proposed_ref(world, version: int = 1) -> dict:
    [event] = [item for item in events_of(world, "PlanningMethodProposed")
               if item.payload["method_ref"]["version"] == version]
    return event.payload["method_ref"]


def test_a_proposed_method_waits_for_its_review_and_the_conclusion_wakes_the_planner(tmp_path):
    async def case():
        scripted = provider(planner=[propose(), adopt(1)])
        scripted.held.add("unknown")  # the independent reviewer's call stays out
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodProposed"))
            # the review is out and unanswered: the Planner is not woken, and the
            # Mission is waiting — not stalled, not stopped
            await spin(world)
            assert scripted.by_role.get("planner") == 1
            assert not events_of(world, "PlanningServiceResumed")
            assert not events_of(world, "PlanningMethodReviewed")
            assert world.loop._has_pending_planning_waits(world.mission.id)
            assert world.store.get_mission(world.mission.id).status not in TERMINAL_MISSION

            scripted.held.clear()
            scripted.release.set()
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == "PASSED"
            assert reviewed.payload["verdict"] == "ACCEPT"
            assert reviewed.payload["method_ref"] == _proposed_ref(world)
            assert reviewed.payload["record_id"]
            [resumed] = events_of(world, "PlanningServiceResumed")
            assert resumed.payload["source_type"] == "PlanningMethodReviewed"
            assert scripted.by_role["planner"] == 2
            assert _versions(world) == [1]
            assert not world.loop._has_pending_planning_waits(world.mission.id)
    asyncio.run(case())


def test_a_rejected_method_reaches_the_planner_in_the_reviewers_words_and_cannot_be_adopted(tmp_path):
    seen: list[dict] = []
    seen_last: list[dict] = []

    async def case():
        scripted = provider(
            planner=[propose(), adopt(1, seen=seen), propose(), adopt(2, seen=seen_last)],
            reviewer=[review("REWORK", limitation="no step writes the report"), review("ACCEPT"),
                      *[review("ACCEPT")] * 4])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            rejected, passed = events_of(world, "PlanningMethodReviewed")
            assert rejected.payload["outcome"] == "REJECTED" and rejected.payload["verdict"] == "REWORK"
            [finding] = rejected.payload["findings"]
            assert finding["criterion_id"] == CRITERION
            assert "no step writes the report" in " ".join(finding["limitations"])
            assert passed.payload["outcome"] == "PASSED"
            assert passed.payload["method_ref"] == _proposed_ref(world, 2)

            # the round the rejection woke was shown the method, its review and the words
            [package] = seen
            row = next(item for item in package["views"]["methods"] if item["method_ref"]["semantic_revision"] == 1)
            assert row["review"]["outcome"] == "REJECTED"
            assert "no step writes the report" in str(row["review"]["findings"])

            # adopting the rejected method was refused by the commit gate, by name
            refused = [event.payload for event in events_of(world, "PlanningDecisionEvaluated")
                       if event.payload.get("status") == "COMMIT_REJECTED"]
            assert [item["rejection_codes"] for item in refused] == [["METHOD_NOT_AUTHORIZED"]]
            assert "no step writes the report" in str(refused[0]["detail"])
            assert _versions(world) == [2]

            # only the newest version of a method is on offer (a method id is one method;
            # 迁移裁决 C1): the rejected v1 is gone from the last round, v2 shows its review
            rows = {item["method_ref"]["semantic_revision"]: item for item in seen_last[-1]["views"]["methods"]}
            assert set(rows) == {2} and rows[2]["review"]["outcome"] == "PASSED"
    asyncio.run(case())


@pytest.mark.parametrize(("answer", "outcome", "adopted"), [
    ("pass", "PASSED", [1]),
    ("fail", "REJECTED", []),
])
def test_two_inconclusive_method_reviews_ask_the_person_and_the_ruling_decides(
        tmp_path, answer, outcome, adopted):
    async def case():
        unsure = review("INCONCLUSIVE", limitation="cannot tell whether one step is enough")
        scripted = provider(planner=[propose(), adopt(1)], reviewer=[unsure, unsure, *[review()] * 4])
        async with assured_loop(tmp_path, scripted) as world:
            questions = PlanningHumanStore(world.store)
            assert await run_until(world, lambda w: questions.pending(w.mission.id))
            [question] = [row for row in questions.list(world.mission.id) if row["state"] == "PENDING"]
            assert question["decision_id"].startswith("adjudicate-method:")
            assert "cannot tell whether one step is enough" in question["request"]["payload"]["question"]
            await spin(world, 10)
            assert scripted.by_role.get("planner") == 1  # nobody is woken while the person decides
            assert not events_of(world, "PlanningMethodReviewed")

            # the person answers on the page (the authenticated facade)
            world.control.answer_planning_question({
                "decision_id": question["decision_id"], "answer": answer,
                "expected_version": question["version"], "nonce": "n-1"})
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodReviewed"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == outcome
            assert reviewed.payload["verdict"] == "INCONCLUSIVE"
            assert reviewed.payload["human_ruling"]["decision"] == answer
            # the ruling, not the answered question, is what wakes the Planner — once
            assert await run_until(world, lambda w: w.provider.asked.count("planner") == 2)
            await spin(world, 10)
            assert [e.payload["source_type"] for e in events_of(world, "PlanningServiceResumed")] == [
                "PlanningMethodReviewed"]
            assert _versions(world) == adopted
    asyncio.run(case())


def test_a_method_review_that_ends_without_a_verdict_is_reported_to_the_planner(tmp_path):
    async def case():
        scripted = provider(planner=[propose(), adopt(1)],
                            reviewer=["this is not a verdict", "still not a verdict"])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanningMethodReviewed"))
            [reviewed] = events_of(world, "PlanningMethodReviewed")
            assert reviewed.payload["outcome"] == "NO_VERDICT"
            assert reviewed.payload["record_id"] is None and reviewed.payload["reason"]
            assert await run_until(world, lambda w: w.provider.asked.count("planner") == 2)
            await spin(world, 10)
            # reported, and still not adoptable: no verdict is not a pass
            assert _versions(world) == []
            refused = [event.payload for event in events_of(world, "PlanningDecisionEvaluated")
                       if event.payload.get("status") == "COMMIT_REJECTED"]
            assert [item["rejection_codes"] for item in refused] == [["METHOD_NOT_AUTHORIZED"]]
    asyncio.run(case())


def test_a_ruling_question_is_not_retired_by_an_epoch_change(tmp_path):
    """HTN 补齐阶段 D（偏差单 6）：问人题目只绑计划修订号与要求修订号，不绑作用域纪元。

    纪元管的是依据凭证还能不能用，不是这个问题该不该问；纪元一动就收回用户面前的裁决题，
    等于把系统内部事实变成对用户的打扰。纪元由它唯一的写入函数 ``bump_epoch`` 推进。

    原用例"裁决题过期（纪元变了）→ 按没有结论上报"的触发方式随之失效；过期上报这条路径
    现在只能由计划或要求修订号变化触发，留待有真实写方后补。"""

    async def case():
        unsure = review("INCONCLUSIVE", limitation="cannot tell")
        scripted = provider(planner=[propose(), adopt(1)], reviewer=[unsure, unsure])
        async with assured_loop(tmp_path, scripted) as world:
            questions = PlanningHumanStore(world.store)
            assert await run_until(world, lambda w: questions.pending(w.mission.id))
            world.htn.bump_epoch(world.mission.id, "mission", bumped_by="fixture")
            await spin(world, 10)
            [question] = questions.list(world.mission.id)
            assert question["state"] == "PENDING"
            assert events_of(world, "PlanningMethodReviewed") == []
            assert _versions(world) == []
    asyncio.run(case())
