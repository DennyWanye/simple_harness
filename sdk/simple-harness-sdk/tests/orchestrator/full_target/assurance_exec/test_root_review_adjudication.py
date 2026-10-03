# SPDX-License-Identifier: Apache-2.0
"""最终审查两次判不下来 → 问人（2026-10-03 迁到产品同形世界）。

人选"通过"：根结论成立、任务完成，不再调第三次终审；人选"打回"：不形成根结论，人的裁决连同
审阅员的疑点作为一条通用修复请求交给规划器。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.storage.planning_human_store import PlanningHumanStore  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


@pytest.mark.parametrize("ruling", ("pass", "fail"))
def test_two_inconclusive_final_reviews_ask_the_person(tmp_path, ruling):
    provider = ReviewScript(verdicts={"MISSION_FINAL": ["INCONCLUSIVE", "INCONCLUSIVE"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            await case.run_until(lambda: bool(case.events("PlanningHumanRequested")))
            [question] = PlanningHumanStore(case.store).list(case.mission_id)
            payload = question["request"]["payload"]
            assert payload["blocking"] is True
            assert [option["key"] for option in payload["options"]] == ["pass", "fail"]
            assert len(case.events("AssuranceReviewSecondOpinionRequested")) == 1
            case.world.control.answer_planning_question({
                "decision_id": question["decision_id"], "answer": ruling,
                "expected_version": question["version"], "nonce": "final-ruling-1"})
            if ruling == "pass":
                mission = await case.settle()
                assert str(mission.status.value) == "COMPLETED", mission.final_report
                assert len(case.events("GoalResolutionCommitted")) == 1
            else:
                await case.run_until(provider.repair_asked.is_set)
                assert not case.events("GoalResolutionCommitted")
                assert case.events("RootGoalResolutionRefused")
                [entry] = provider.repair_packages[0]["repair_requests"]
                context = entry["request"]["context"]
                assert context["source"] == "root_review" and context["verdict"] == "INCONCLUSIVE"
                assert context["human_ruling"]["decision"] == "fail"
                assert entry["request"]["trigger_source"] == "VERIFIER_ACCEPTANCE_REJECT"
            assert len(case.events("AssuranceReviewAdjudicated")) == 1
            assert provider.review_calls["MISSION_FINAL"] == 2

    asyncio.run(run())
