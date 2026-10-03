# SPDX-License-Identifier: Apache-2.0
"""新做法审阅的检查策略：部署按"规划主体任务"无损投影并批准（HTN 精简 片 A 第 1 项；
2026-10-03 迁到产品同形世界）。

评估阻断 1（2026-10-01）：规划器一提做法，新做法审阅就因为查不到已批准的检查策略而开不起来。
原"规划主体的映射无损、批准后新做法审阅开得起来"由 ``product_world/test_sub_goal.py`` 覆盖
（子目标的做法策略已投影、子目标做法过了独立审阅；分诊表：删）。这里只留读侧：只有复合目标才是
规划主体，映射就是它覆盖的那几条要求；不认识的任务、原子步骤都没有映射。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_orchestrator.assurance.checks import CriterionPolicy
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.orchestrator.assurance_check_policy import lossless_planning_subject_mapping
from agent_orchestrator.storage.htn_store import HtnStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operation_completion"))

from publish_world import plan_committed  # noqa: E402


def test_a_task_that_is_not_a_planning_subject_has_no_mapping(tmp_path):
    async def case():
        async with plan_committed(tmp_path) as world:
            commit, mission_id = world.world.loop.commit, world.mission_id
            htn = HtnStore(world.store)
            [root] = htn.list_task_semantics(mission_id, form="compound")
            requirements_ref, subject_ref, mapping = lossless_planning_subject_mapping(
                commit, mission_id=mission_id, task_id=str(root.task_id))
            assert subject_ref.kind == "task" and subject_ref.pin.id == str(root.task_id)
            assert requirements_ref.kind == "requirements"
            # 根覆盖 c-user-1（file:）与 c-user-2（action:）；发布效果由系统按确认的效果去办，做法审阅
            # 只判内容那一条。
            assert root.requirement_refs == ("c-user-1", "c-user-2")
            assert mapping == (CriterionPolicy("c-user-1", "SEMANTIC", ()),)
            with pytest.raises(AssuranceError) as unknown:
                lossless_planning_subject_mapping(commit, mission_id=mission_id, task_id="task-nobody")
            assert unknown.value.code == "CHECK_POLICY_UNRESOLVED"
            [leaf] = htn.list_task_semantics(mission_id, form="primitive")
            with pytest.raises(AssuranceError) as primitive:
                lossless_planning_subject_mapping(commit, mission_id=mission_id, task_id=str(leaf.task_id))
            assert primitive.value.code == "CHECK_POLICY_UNRESOLVED"

    asyncio.run(case())
