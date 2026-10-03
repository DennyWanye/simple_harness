# SPDX-License-Identifier: Apache-2.0
"""内容审阅判不下来：换会话复审一次，仍判不下来就问人（用户 2026-09-30 决定；2026-10-03 迁到
产品同形世界）。

* 第一次就判得下：只调一次审阅，不复审；
* 第一次判不下：换一个会话复审一次，复审通过即验收；
* 两次都判不下：结果挂起、给人一张审阅卡；人判"通过"才准验收；人判"不通过"则不验收，这个事实
  （人的结论和备注）原样交给规划器。

只有模型回复是脚本（:mod:`_review_world`），人的裁决走门面命令。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


@pytest.mark.parametrize("verdicts,calls,second_opinions", (
    ([], 1, 0),
    (["INCONCLUSIVE"], 2, 1),
))
def test_one_inconclusive_review_gets_one_independent_second_opinion(tmp_path, verdicts, calls, second_opinions):
    provider = ReviewScript(verdicts={"TASK_CONTENT": verdicts})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert provider.review_calls["TASK_CONTENT"] == calls
            assert len(case.events("AssuranceReviewSecondOpinionRequested")) == second_opinions
            assert len(case.events("AcceptanceCommitted")) == 1
            assert not case.events("ApprovalRequested")

    asyncio.run(run())


def test_two_inconclusive_reviews_go_to_the_person_and_a_pass_licenses_acceptance(tmp_path):
    provider = ReviewScript(verdicts={"TASK_CONTENT": ["INCONCLUSIVE", "INCONCLUSIVE"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            await case.run_until(lambda: bool(case.pending_approvals()))
            [card] = case.pending_approvals()
            assert card["kind"] == "review" and card["reason"] == "needs_human"
            assert case.events("VerificationSuspended") and not case.events("AcceptanceCommitted")
            decided = case.world.control.decide(card["request_id"], "review_pass", note="人看过了，合格")
            assert decided["request_state"] == "GRANTED"
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert provider.review_calls["TASK_CONTENT"] == 2  # 人裁决之后不再调第三次
            assert len(case.events("AssuranceReviewAdjudicated")) == 1
            assert len(case.events("AcceptanceCommitted")) == 1

    asyncio.run(run())


def test_a_human_fail_after_two_inconclusive_reviews_does_not_license_acceptance(tmp_path):
    provider = ReviewScript(verdicts={"TASK_CONTENT": ["INCONCLUSIVE", "INCONCLUSIVE"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            await case.run_until(lambda: bool(case.pending_approvals()))
            [card] = case.pending_approvals()
            decided = case.world.control.decide(card["request_id"], "review_fail", note="要点不全")
            assert decided["request_state"] == "REJECTED"
            await case.run_until(provider.repair_asked.is_set)
            assert HtnStore(case.store).list_acceptances(case.mission_id) == ()
            assert case.events("VerificationFailed") and not case.events("AcceptanceCommitted")
            [entry] = [e for e in provider.repair_packages[0]["repair_requests"]
                       if e["request"]["context"].get("event_type") == "VerificationFailed"]
            [failure] = entry["request"]["context"]["detail"]["failures"]
            assert failure["layer"] == "human_review" and failure["status"] == "FAIL"
            assert failure["detail"]["note"] == "要点不全"
            assert provider.review_calls["TASK_CONTENT"] == 2

    asyncio.run(run())
