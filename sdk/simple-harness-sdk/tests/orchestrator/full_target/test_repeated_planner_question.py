"""规划器重复问同一个问题：沿用已有回答，不再打扰人（2026-10-01，审阅升级具名后续）。

真机第 4 局：规划器对同一件事连问 13 次一模一样的问题，直到规划次数用完。两处原因：
* 给规划器看的"已答问题"只列绑定还是最新的那些——任务里每写一次库时钟就变，答过的问题
  就从规划器眼前消失，它自然再问一遍；
* 登记问题时不看有没有问过。
现在：已答问题一律给规划器看（标明绑定是否仍是最新）；同一主题、同一问题再问 → 直接以上次的
回答登记成"已答"（记 ``reused_from``），下一轮规划照常拿到答案，不再等人。

两条用例都在产品同形部署上由主循环真跑（``h1i_seed``）：规划器的提问经收集器登记，人经
回答接口作答。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from h1i_seed import CONFIG, CRITERIA, GOAL, events, run_until
from h1i_seed import planner as seed_planner

from agent_orchestrator.api.planning_answers import answer_planning_question
from agent_orchestrator.orchestrator.planner_views import answered_questions_for_planner
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_FIXTURE = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid" / "request-human.json"
QUESTION = "公司官网的资料在哪里？"
OTHER = "要不要把无法核实的规则单列？"
ANSWER = "没有公司官网的资料，写明无法核实"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _ask(request: Any, question: str) -> str:
    body = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    body["subject_key"] = package_of(request)["planning_subjects"][0]["subject_key"]
    body["payload"] = {"question": question, "options": [], "blocking": True}
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"


def _pending(loop: Any, mission_id: str) -> list[dict[str, Any]]:
    return [row for row in PlanningHumanStore(loop.store).list(mission_id) if row["state"] == "PENDING"]


def _answer(world: Any, mission: Any, row: dict[str, Any], text: str) -> None:
    answer_planning_question(
        world.loop, tenant_id=mission.tenant_id, principal=world.deployment.principal,
        decision_id=row["decision_id"], answer=text, expected_version=row["version"],
        nonce="n-" + row["decision_id"])


def test_a_repeated_question_is_answered_from_the_previous_answer(tmp_path):
    """The planner asks, the person answers; the planner asks the same question again and
    it is answered on the spot from the previous answer; a different question is a real
    question again."""

    script = [QUESTION, QUESTION, OTHER]

    def planner(request: Any) -> Any:
        return _ask(request, script.pop(0))

    async def case():  # type: ignore[no-untyped-def]
        provider = LayeredScriptedProvider(planner=planner)
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            created = world.create({"goal": GOAL, "idempotency_key": "repeat-question",
                                    "success_criteria": list(CRITERIA)})
            loop = world.loop
            mission = loop.store.get_mission(created["mission_id"])
            await run_until(world, lambda: bool(_pending(loop, mission.id)))
            [first] = _pending(loop, mission.id)
            assert first["request"]["payload"]["question"] == QUESTION
            _answer(world, mission, first, ANSWER)

            await run_until(world, lambda: not script and bool(_pending(loop, mission.id)))
            rows = {row["decision_id"]: row for row in PlanningHumanStore(loop.store).list(mission.id)}
            assert len(rows) == 3
            reused = [e for e in events(loop, mission.id, "PlanningHumanAnswered")
                      if e.payload.get("reused_from") == first["decision_id"]]
            assert len(reused) == 1
            second = rows[reused[0].payload["decision_id"]]
            # The same question again: answered from the previous answer, nobody is asked.
            assert second["state"] == "ANSWERED"
            assert second["answer"]["answer"] == ANSWER
            assert second["request"]["payload"]["question"] == QUESTION
            # A different question is still a real question.
            [third] = _pending(loop, mission.id)
            assert third["request"]["payload"]["question"] == OTHER
            # The planner sees both answers.
            shown = answered_questions_for_planner(loop.store, mission.id)
            assert [(q["question"], q["answer"]["answer"]) for q in shown] == [(QUESTION, ANSWER)] * 2
            assert shown[0]["binding_current"] is True

    asyncio.run(case())


def test_an_answer_stays_visible_to_the_planner_after_the_binding_moved(tmp_path):
    """The question is answered before the first plan; the plan the planner commits next
    moves the binding, and the answer is still shown (marked not current)."""

    def planner(request: Any) -> Any:
        if not package_of(request).get("human_answers"):
            return _ask(request, QUESTION)
        return seed_planner(request)

    async def case():  # type: ignore[no-untyped-def]
        provider = LayeredScriptedProvider(planner=planner)
        provider.held.add("worker")
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as world:
                created = world.create({"goal": GOAL, "idempotency_key": "answer-visible",
                                        "success_criteria": list(CRITERIA)})
                loop = world.loop
                mission = loop.store.get_mission(created["mission_id"])
                await run_until(world, lambda: bool(_pending(loop, mission.id)))
                [row] = _pending(loop, mission.id)
                _answer(world, mission, row, "没有")
                await run_until(world, provider.entered.is_set)  # the plan committed, its leaf dispatched
                assert events(loop, mission.id, "PlanRevisionCommitted")
                questions = PlanningHumanStore(loop.store)
                assert not questions.binding_current(questions.get(row["decision_id"]))
                [shown] = answered_questions_for_planner(loop.store, mission.id)
                assert shown["answer"]["answer"] == "没有" and shown["binding_current"] is False
        finally:
            provider.release.set()

    asyncio.run(case())
