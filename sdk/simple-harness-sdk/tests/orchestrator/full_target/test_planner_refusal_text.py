"""规划器被拒时看到的理由不被截短（2026-10-07 opt.167 发版评估建议第 4 条）。

原先计划改动提交被拒时，理由在入库那一刻就截在 300 字，规划器下一轮的 ``previous_feedback``
只看得到前半句。按"判断交给 LLM"，长理由的后半句（往往是"可以怎么改"）也该如实交给它。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from publishing_round import publishing_round, run_rounds

from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected
from agent_orchestrator.planning.decision_feedback import (
    REFUSAL_TEXT_LIMIT,
    feedback_from_decision,
    refusal_text,
)
from agent_orchestrator.storage.store import StoreConflict

BUDGETS = {
    "same_request_format_retries_remaining": 0,
    "planning_rounds_remaining": 3,
    "root_review_repairs_remaining": 1,
    "repeated_failure_before_escalation_remaining": None,
}
TAIL = "【可以这样改：换一个不碰这次发布的步骤】"
WHY = "计划改动读到的来源在提交前变了。" * 40 + TAIL
LONG = "TASKGRAPH_PLAN_SOURCE_CHANGED: " + WHY


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_the_limit_keeps_a_long_reason_whole_and_still_bounds_it() -> None:
    assert len(LONG) > 600
    assert refusal_text(StoreConflict(LONG)) == LONG
    assert len(refusal_text("字" * (REFUSAL_TEXT_LIMIT * 2))) == REFUSAL_TEXT_LIMIT


def test_a_refused_plan_change_reaches_the_planner_with_its_whole_reason(tmp_path) -> None:
    async def case() -> dict:
        async with publishing_round(tmp_path, key="refusal-text") as round_:
            dispatch = round_.dispatch

            def refuse(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise PlanCommitRejected("TASKGRAPH_PLAN_SOURCE_CHANGED", WHY)

            # 提交那一步按名拒绝（与真实的"来源在提交前变了"同一出口），理由很长。
            dispatch.commit_preview_plan_proposal = refuse  # type: ignore[method-assign]
            return await run_rounds(round_)

    row = asyncio.run(case())
    assert row["status"] == "COMMIT_REJECTED", row
    assert TAIL in json.dumps(row["detail"], ensure_ascii=False), row["detail"]
    feedback = feedback_from_decision(row, budgets=BUDGETS)
    assert feedback is not None
    assert any(TAIL in problem.detail for problem in feedback.problems), feedback
