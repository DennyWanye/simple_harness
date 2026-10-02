"""根终审裁决题的回答不开规划轮（2026-09-30 审阅升级）。

裁决题走规划问题通道（同一张表、同一种卡片），但它的答案是给根审查协调器写裁决回执用的；
若被"服务回执 → 下一轮规划"的常规续跑路捡走，规划器会被无缘无故叫醒一轮。普通问题照旧。

两边都由产品同形部署上的主循环真跑出来（``h1i_seed``）：裁决题是最终审查两位审阅员都判
不下来时系统问人的那一题；普通问题是规划器经收集器提的问题；人经回答接口作答。
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
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    review_input,
    review_reply,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid" / "request-human.json"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _final_review_undecided(request: Any) -> str:
    """Every review passes except the root's final review, which cannot be decided."""

    package = review_input(request)
    assert package is not None
    if (package.get("package") or {}).get("purpose") == "MISSION_FINAL":
        return review_reply(package, verdict="INCONCLUSIVE", grade="UNKNOWN",
                            reason="按现有材料判断不了")
    return review_reply(package)


def _ordinary_question(request: Any) -> str:
    body = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    body["subject_key"] = package_of(request)["planning_subjects"][0]["subject_key"]
    body["payload"] = {"question": "资料在哪里？", "options": [], "blocking": True}
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"


def _pending(loop: Any, mission_id: str) -> list[dict[str, Any]]:
    return [row for row in PlanningHumanStore(loop.store).list(mission_id) if row["state"] == "PENDING"]


@pytest.mark.parametrize("adjudication", (True, False), ids=("adjudication", "ordinary"))
def test_an_answered_adjudication_question_opens_no_planner_round_but_an_ordinary_one_does(
    tmp_path, adjudication: bool
):
    async def case():  # type: ignore[no-untyped-def]
        if adjudication:
            provider = LayeredScriptedProvider(planner=seed_planner, reviewer=_final_review_undecided)
        else:
            provider = LayeredScriptedProvider(planner=_ordinary_question)
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            created = world.create({"goal": GOAL, "idempotency_key": f"adjudication-{adjudication}",
                                    "success_criteria": list(CRITERIA)})
            loop = world.loop
            mission = loop.store.get_mission(created["mission_id"])
            await run_until(world, lambda: bool(_pending(loop, mission.id)))
            [row] = _pending(loop, mission.id)
            resumed_before = len(events(loop, mission.id, "PlanningServiceResumed"))
            requested = [e for e in events(loop, mission.id, "PlanningHumanRequested")
                         if e.payload["decision_id"] == row["decision_id"]]
            assert len(requested) == 1
            assert (requested[0].payload.get("origin") == "review_adjudication") is adjudication
            answer_planning_question(
                loop, tenant_id=mission.tenant_id, principal=world.deployment.principal,
                decision_id=row["decision_id"], answer="pass" if adjudication else "在 docs/ 下",
                expected_version=row["version"], nonce="n-" + row["decision_id"])
            if adjudication:
                assert loop._resume_planning_services(mission) is False
                assert len(events(loop, mission.id, "PlanningServiceResumed")) == resumed_before
            else:
                assert loop._resume_planning_services(mission) is True
                resumed = events(loop, mission.id, "PlanningServiceResumed")[resumed_before:]
                assert {e.payload["decision_id"] for e in resumed} == {row["decision_id"]}

    asyncio.run(case())
