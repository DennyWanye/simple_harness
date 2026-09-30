"""2026-09-30 用户决定（结构修复真机第 6 局后）：反复失败后修方法的机会按步骤算。

第 6 局：旧第一步连错三次 → 换方法（用掉任务唯一一次机会）；资料换版后提出的后继步骤又
连错三次，因为机会是整个任务共用的，任务直接结束。现在每个步骤各有一次，新出现的步骤
（如后继步骤）重新获得机会；总数仍受规划总次数限制。失败报告只列这一步自己的说明
（原来把上一步的说明拼了进去，看起来像在说旧步骤）。
"""
from __future__ import annotations

import asyncio

from test_h1i_production_entry import _config, _seed_new_protocol

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import REPEATED_VERIFICATION_FAILURE_REASON
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def test_each_step_has_its_own_repeated_failure_repair(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, _dispatch = _seed_new_protocol(loop, tmp_path, key="repair-per-step")
            loop.commit.record_planning_rejected(
                mission.id, ordinal=2, reason=REPEATED_VERIFICATION_FAILURE_REASON,
                key=f"{mission.id}:repeated-verification:task-old:1",
                detail={"task_id": "task-old", "findings": [{"severity": "blocker", "detail": "old step"}]})
            assert loop._repeated_verification_repairs(mission.id, "task-old") == 1
            assert loop._repeated_verification_repairs(mission.id, "task-successor") == 0
            assert loop._repeated_verification_stop_detail(mission, None, "task-successor") == {}
            loop.commit.record_planning_rejected(
                mission.id, ordinal=3, reason=REPEATED_VERIFICATION_FAILURE_REASON,
                key=f"{mission.id}:repeated-verification:task-successor:3",
                detail={"task_id": "task-successor", "findings": [{"severity": "blocker", "detail": "new step"}]})
            detail = loop._repeated_verification_stop_detail(mission, None, "task-successor")
            assert detail["repeated_verification_failure"]["repairs_used"] == 1
            assert [f["detail"] for f in detail["repeated_verification_failure"]["findings"]] == ["new step"]
    asyncio.run(case())
