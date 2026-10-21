# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""做法提案额度：固定上限 + 用户答过的规划提问数（人在场要它继续，就多给一次）。"""

from __future__ import annotations

import asyncio
import json

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.orchestrator.planning_method_proposal import (
    MAX_METHOD_PROPOSALS_PER_GOAL,
    proposal_allowance,
)
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore


def _request(store, mission_id: str, decision_id: str, *, state: str) -> None:
    store.connection.execute(
        "INSERT INTO planning_human_requests(decision_id,mission_id,subject_key,request_json,"
        "request_hash,state,answer_json,version,created_at,answered_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id,
            mission_id,
            "subject-1",
            json.dumps({"payload": {"question": "q"}}),
            "h" * 64,
            state,
            json.dumps({"answer": "继续"}) if state == "ANSWERED" else None,
            1,
            1.0,
            2.0 if state == "ANSWERED" else None,
        ),
    )


def test_each_answered_question_allows_one_more_proposal(tmp_path):
    async def case():
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=ScriptedProvider(),
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            ids = [
                host.create_mission(
                    {
                        "goal": f"写 NOTES{n}.md。",
                        "success_criteria": [f"file:NOTES{n}.md"],
                        "idempotency_key": f"allow-{n}",
                    }
                )["mission_id"]
                for n in (1, 2, 3)
            ]
            store = host.orchestrator.store
            assert proposal_allowance(store, ids[0]) == MAX_METHOD_PROPOSALS_PER_GOAL
            with store.transaction():
                _request(store, ids[0], "pd-1", state="ANSWERED")
                _request(store, ids[0], "pd-2", state="PENDING")  # 没答的不算
                _request(store, ids[1], "pd-3", state="ANSWERED")  # 别的任务不算进这个
            assert proposal_allowance(store, ids[0]) == MAX_METHOD_PROPOSALS_PER_GOAL + 1
            assert proposal_allowance(store, ids[1]) == MAX_METHOD_PROPOSALS_PER_GOAL + 1
            assert proposal_allowance(store, ids[2]) == MAX_METHOD_PROPOSALS_PER_GOAL
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=120))


def test_a_reused_answer_is_not_a_person_and_adds_no_allowance(tmp_path):
    """规划器把同一个问题再问一遍，系统自动沿用上次答案登记成"已答"——没有人在场，不加额度。"""

    class _Payload:
        def to_json(self):
            return {"question": "能恢复额度吗", "options": [], "blocking": True}

    async def case():
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=ScriptedProvider(),
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            mission_id = host.create_mission(
                {"goal": "写 N.md。", "success_criteria": ["file:N.md"], "idempotency_key": "reuse"}
            )["mission_id"]
            store = host.orchestrator.store
            with store.transaction():
                _request(store, mission_id, "pd-human", state="ANSWERED")
            assert proposal_allowance(store, mission_id) == MAX_METHOD_PROPOSALS_PER_GOAL + 1
            humans = PlanningHumanStore(store)
            previous = {
                "decision_id": "pd-human",
                "answer": {"decision_id": "pd-human", "answer": "继续", "receipt_hash": "x"},
            }
            for n in range(5):
                humans.register_reusing_answer(
                    decision_id=f"pd-auto-{n}",
                    mission_id=mission_id,
                    subject_key="subject-1",
                    payload=_Payload(),
                    request_binding={},
                    next_ordinal=n + 2,
                    previous=previous,
                )
            assert proposal_allowance(store, mission_id) == MAX_METHOD_PROPOSALS_PER_GOAL + 1
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=120))
