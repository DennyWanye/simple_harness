"""规划器重复问同一个问题：沿用已有回答，不再打扰人（2026-10-01，审阅升级具名后续）。

真机第 4 局：规划器对同一件事连问 13 次一模一样的问题，直到规划次数用完。两处原因：
* 给规划器看的"已答问题"只列绑定还是最新的那些——任务里每写一次库时钟就变，答过的问题
  就从规划器眼前消失，它自然再问一遍；
* 登记问题时不看有没有问过。
现在：已答问题一律给规划器看（标明绑定是否仍是最新）；同一主题、同一问题再问 → 直接以上次的
回答登记成"已答"（记 ``reused_from``），下一轮规划照常拿到答案，不再等人。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from test_h1i_production_entry import _config, _events, _seed_new_protocol

from agent_orchestrator.contracts.planning_decisions import HumanOptionV1, RequestHumanDecision
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.planner_views import answered_questions_for_planner
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def _current(loop, mission):  # type: ignore[no-untyped-def]
    htn = HtnStore(loop.store)
    plan = htn.active_plan_revision(mission.id)
    requirements = htn.latest_requirements_revision(mission.id)
    return SimpleNamespace(plan_revision=0 if plan is None else int(plan.revision),
                           requirements_revision=0 if requirements is None else int(requirements.revision))


def _ask(loop, mission, new_mode, decision_id, question="公司官网的资料在哪里？"):  # type: ignore[no-untyped-def]
    payload = RequestHumanDecision(question, (), True)
    with loop.store.transaction():
        return loop._register_human_question(
            mission, new_mode, decision_id=decision_id, subject_key="root", payload=payload,
            current=_current(loop, mission), next_ordinal=2, repair_context=None)


def test_a_repeated_question_is_answered_from_the_previous_answer(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="repeat-question")
            questions = PlanningHumanStore(loop.store)
            first, detail = _ask(loop, mission, dispatch, "q-1")
            assert first["state"] == "PENDING" and detail["state"] == "PENDING"
            questions.answer(decision_id="q-1", tenant_id=mission.tenant_id, principal=Principal("user-1"),
                             answer="没有公司官网的资料，写明无法核实", expected_version=first["version"], nonce="n-1")
            # The same question again: answered on the spot from the previous answer, nobody is asked.
            second, detail = _ask(loop, mission, dispatch, "q-2")
            assert second["state"] == "ANSWERED" and detail == {"question_id": "q-2", "state": "ANSWERED", "reused_from": "q-1"}
            assert second["answer"]["answer"] == "没有公司官网的资料，写明无法核实"
            [answered] = [e for e in _events(loop, mission.id, "PlanningHumanAnswered") if e.payload["decision_id"] == "q-2"]
            assert answered.payload["reused_from"] == "q-1"
            # A different question is still a real question.
            third, _ = _ask(loop, mission, dispatch, "q-3", question="要不要把无法核实的规则单列？")
            assert third["state"] == "PENDING"
            # The planner sees both answers, whatever the binding clocks did since.
            shown = answered_questions_for_planner(loop.store, mission.id)
            assert [(q["question"], q["answer"]["answer"]) for q in shown] == [
                ("公司官网的资料在哪里？", "没有公司官网的资料，写明无法核实")] * 2
            assert shown[0]["binding_current"] is True
    asyncio.run(case())


def test_an_answer_stays_visible_to_the_planner_after_the_binding_moved(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="answer-visible")
            questions = PlanningHumanStore(loop.store)
            row, _ = _ask(loop, mission, dispatch, "q-1")
            questions.answer(decision_id="q-1", tenant_id=mission.tenant_id, principal=Principal("user-1"),
                             answer="没有", expected_version=row["version"], nonce="n-1")
            for _ in range(2):  # the first bump of an absent row may land on 0
                HtnStore(loop.store).bump_epoch(mission.id, "mission", bumped_by="fixture")
            assert not questions.binding_current(questions.get("q-1"))
            [shown] = answered_questions_for_planner(loop.store, mission.id)
            assert shown["answer"]["answer"] == "没有" and shown["binding_current"] is False
    asyncio.run(case())
