"""根终审裁决题的回答不开规划轮（2026-09-30 审阅升级）。

裁决题走规划问题通道（同一张表、同一种卡片），但它的答案是给根审查协调器写裁决回执用的；
若被"服务回执 → 下一轮规划"的常规续跑路捡走，规划器会被无缘无故叫醒一轮。普通问题照旧。
"""
from __future__ import annotations

import asyncio

from test_h1i_production_entry import _config, _events, _seed_new_protocol

from agent_orchestrator.contracts.planning_decisions import HumanOptionV1, RequestHumanDecision
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import append_hierarchical_event
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def _ask_and_answer(loop, mission, decision_id, *, adjudication):  # type: ignore[no-untyped-def]
    htn, questions = HtnStore(loop.store), PlanningHumanStore(loop.store)
    plan = htn.active_plan_revision(mission.id)
    requirements = htn.latest_requirements_revision(mission.id)
    payload = RequestHumanDecision("裁决？", (HumanOptionV1("pass", "通过"), HumanOptionV1("fail", "打回")), True)
    with loop.store.transaction():
        row = questions.register(
            decision_id=decision_id, mission_id=mission.id, subject_key="root", payload=payload,
            request_binding={"plan_revision": 0 if plan is None else int(plan.revision),
                             "requirements_revision": 0 if requirements is None else int(requirements.revision),
                             "manager_epoch": htn.epoch(mission.id, "mission")},
            next_ordinal=2,
            repair_context={"kind": "review_adjudication", "record_id": "rec-x"} if adjudication else None)
        append_hierarchical_event(loop.store, "PlanningHumanRequested", mission.id, key=decision_id,
                                  payload={"decision_id": decision_id, "question_id": decision_id, "state": "PENDING"})
    questions.answer(decision_id=decision_id, tenant_id=mission.tenant_id, principal=Principal("user-1"),
                     answer="pass", expected_version=row["version"], nonce="n-" + decision_id)


def test_an_answered_adjudication_question_opens_no_planner_round_but_an_ordinary_one_does(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, _dispatch = _seed_new_protocol(loop, tmp_path, key="adjudication-no-replan")
            _ask_and_answer(loop, mission, "adjudicate-root:rec-x", adjudication=True)
            assert loop._resume_planning_services(mission) is False
            assert not _events(loop, mission.id, "PlanningServiceResumed")
            _ask_and_answer(loop, mission, "question-ordinary", adjudication=False)
            assert loop._resume_planning_services(mission) is True
            resumed = _events(loop, mission.id, "PlanningServiceResumed")
            assert {e.payload["decision_id"] for e in resumed} == {"question-ordinary"}
    asyncio.run(case())
