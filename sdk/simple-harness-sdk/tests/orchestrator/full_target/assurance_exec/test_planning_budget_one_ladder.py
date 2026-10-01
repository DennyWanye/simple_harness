# SPDX-License-Identifier: Apache-2.0
"""规划器答错几次就停，只有一个数（HTN 精简 片 A 第 8 项）。

此前被拒之后"还能不能再问"分六条阶梯各自计数（格式重试、回合重试、格式阶梯、规划阶梯、
修复阶梯、已有计划不判失败），外加"合成成功多给一次"。现在：自上一次提交成功起，被拒的
回答——读不懂、不被准入、提交被拒，不分种类——累计到 ``max_planning_attempts``（默认 3）
即停；同一请求的格式重试计入其中。被接受的服务类决定（提做法、问用户）不是答错。
"""
from __future__ import annotations

import asyncio

from _assured_loop import (
    REVIEWER,
    assured_loop,
    events_of,
    proposed_method,
    propose_step,
    refine_with_step,
    review_reply,
    run_until,
)

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def _status(world):
    return world.store.get_mission(world.mission.id).status


def test_the_default_is_three_wrong_answers() -> None:
    assert OrchestratorConfig.__dataclass_fields__["max_planning_attempts"].default == 3


def test_three_refused_answers_of_any_kind_end_the_first_plan(tmp_path):
    never_proposed = proposed_method("plan.never-proposed")

    async def case():
        provider = RoleScriptedProvider({"planner": [
            "this is not a decision",                 # unreadable
            "still not a decision",                   # the same request's format retry
            refine_with_step(never_proposed),         # readable, refers to nothing it was shown
            refine_with_step(never_proposed),         # must never be asked for
        ]})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: _status(w) is MissionStatus.FAILED)
            assert provider.by_role == {"planner": 3}
            rejected = events_of(world, "PlanningRejected")
            assert [event.payload["reason"] for event in rejected] == [
                "proposal_unreadable", "proposal_unreadable", "proposal_not_grounded"]
            [failed] = events_of(world, "MissionFailed")
            assert failed.payload["stop_reason"] == "planning_failed"
            assert failed.payload["reason"] == "proposal_not_grounded"
    asyncio.run(case())


def test_an_accepted_proposal_is_not_a_wrong_answer_and_a_commit_starts_the_count_again(tmp_path):
    contract = proposed_method()
    never_proposed = proposed_method("plan.never-proposed")

    async def case():
        provider = RoleScriptedProvider({
            "planner": [
                refine_with_step(never_proposed),     # wrong: 1
                refine_with_step(never_proposed),     # wrong: 2
                propose_step(contract),               # accepted: sent to review, not an answer refused
                refine_with_step(contract),           # commits
            ],
            REVIEWER: [review_reply("ACCEPT")]})
        async with assured_loop(tmp_path, provider) as world:
            assert await run_until(world, lambda w: events_of(w, "PlanRevisionCommitted"))
            assert provider.by_role["planner"] == 4
            assert len(events_of(world, "PlanningRejected")) == 2
            assert _status(world) is MissionStatus.ACTIVE
            # the commit answered the question: the next one starts at zero
            assert world.loop._planning_attempts(world.mission.id) == 0
            assert not world.loop._planning_ladder_spent(world.mission.id)
    asyncio.run(case())
