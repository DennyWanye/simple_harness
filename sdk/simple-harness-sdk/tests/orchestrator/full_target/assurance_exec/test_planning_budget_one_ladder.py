# SPDX-License-Identifier: Apache-2.0
"""规划器答错几次就停，只有一个数（HTN 精简 片 A 第 8 项）。

此前被拒之后"还能不能再问"分六条阶梯各自计数（格式重试、回合重试、格式阶梯、规划阶梯、
修复阶梯、已有计划不判失败），外加"合成成功多给一次"。现在：自上一次提交成功起，被拒的
回答——读不懂、不被准入、提交被拒，不分种类——累计到 ``max_planning_attempts``（默认 3）
即停；同一请求的格式重试计入其中。被接受的服务类决定（提做法、问用户）不是答错。

HTN 补齐阶段 A′：跑在产品同形世界（:mod:`_assured_loop`），只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio

import pytest
from _assured_loop import (
    adopt,
    adopt_unknown,
    assured_loop,
    events_of,
    propose,
    provider,
    run_until,
)

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.runtime.assembly import OrchestratorConfig


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _status(world):
    return world.store.get_mission(world.mission.id).status


def test_the_default_is_three_wrong_answers() -> None:
    assert OrchestratorConfig.__dataclass_fields__["max_planning_attempts"].default == 3


def test_three_refused_answers_of_any_kind_end_the_first_plan(tmp_path):
    async def case():
        scripted = provider(planner=[
            "this is not a decision",                 # unreadable
            "still not a decision",                   # the same request's format retry
            adopt_unknown,                            # readable, refers to nothing it was shown
            adopt_unknown,                            # must never be asked for
        ])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: _status(w) is MissionStatus.FAILED)
            assert scripted.by_role == {"planner": 3}
            rejected = events_of(world, "PlanningRejected")
            assert [event.payload["reason"] for event in rejected] == [
                "proposal_unreadable", "proposal_unreadable", "proposal_not_grounded"]
            [failed] = events_of(world, "MissionFailed")
            assert failed.payload["stop_reason"] == "planning_failed"
            assert failed.payload["reason"] == "proposal_not_grounded"
    asyncio.run(case())


def test_an_accepted_proposal_is_not_a_wrong_answer_and_a_commit_starts_the_count_again(tmp_path):
    async def case():
        scripted = provider(planner=[
            adopt_unknown,                            # wrong: 1
            adopt_unknown,                            # wrong: 2
            propose(),                                # accepted: sent to review, not an answer refused
            adopt(1),                                 # commits
        ])
        async with assured_loop(tmp_path, scripted) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            assert scripted.by_role["planner"] == 4
            assert len(events_of(world, "PlanningRejected")) == 2
            assert _status(world) is MissionStatus.ACTIVE
            # the commit answered the question: the next one starts at zero
            assert world.loop._planning_attempts(world.mission.id) == 0
            assert not world.loop._planning_ladder_spent(world.mission.id)
    asyncio.run(case())
