# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""最终审查打回 → 一条通用修复请求交规划器（HTN 精简 片 0 第 2 步，2026-10-01；2026-10-03 迁到
产品同形世界）。

Harness 只把事实如实记成一条修复请求（审阅员的全部意见、这是第几次、上限是多少），做法不动、
步骤不动，由规划器在重试 / 换做法 / 补步骤 / 问人里选。

偏离原用例（按产品实际走法写）：每个任务的终审打回上限为 0（或已用完）时，产品不再单独出一条
"终审打回"请求，也不是直接停任务；这个事实（终审结论、意见、已用几次、上限几次）放在"没有可派发
的工作"那条请求里交给规划器，由它决定。
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


def _plan_shape(case):
    htn = HtnStore(case.store)
    revision = htn.active_plan_revision(case.mission_id)
    return (revision.revision, sorted(str(item.instance_id) for item in htn.list_method_instances(case.mission_id)),
            sorted((task.id, str(task.status)) for task in case.store.list_tasks(case.mission_id)))


def test_a_rework_final_review_becomes_one_generic_request_and_touches_nothing_else(tmp_path):
    provider = ReviewScript(verdicts={"MISSION_FINAL": ["REWORK"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            await case.run_until(lambda: bool(case.events("RootGoalResolutionRefused")))
            before = _plan_shape(case)
            await case.run_until(provider.repair_asked.is_set)
            [entry] = provider.repair_packages[0]["repair_requests"]
            request = entry["request"]
            assert request["trigger_source"] == "VERIFIER_ACCEPTANCE_REJECT"
            context = request["context"]
            assert context["source"] == "root_review" and context["verdict"] == "REWORK"
            assert context["record_id"] and context["package_id"]
            # 审阅员的全部意见，而不只是 Harness 会叫"阻断"的那些
            [finding] = context["findings"]
            assert finding["criterion_id"] == "c-user-1" and finding["verdict"] == "FAIL"
            # 关于上限的事实，没有任何告诉规划器该怎么做的话
            assert context["repair_round"] == 1 and context["max_repairs"] == 1
            assert "must" not in str(context).lower() and "hint" not in context
            # Harness 没有替规划器动手：同一版计划、同一个做法、没有取消任何步骤
            assert _plan_shape(case) == before
            assert not [t for t in case.store.list_tasks(case.mission_id) if str(t.status) == "CANCELLED"]
            assert not case.events("PlanningRejected")
            assert len(case.events("PlanningRepairRequested")) == 1
            assert provider.review_calls["MISSION_FINAL"] == 1

    asyncio.run(run())


def test_a_mission_allowed_no_final_repair_hands_the_spent_bound_to_the_planner(tmp_path):
    provider = ReviewScript(verdicts={"MISSION_FINAL": ["REWORK"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider, max_root_review_repairs=0) as case:
            await case.run_until(provider.repair_asked.is_set)
            requests = [e["request"] for e in provider.repair_packages[0]["repair_requests"]]
            assert not [r for r in requests if r["context"].get("source") == "root_review"]
            [stalled] = [r for r in requests if r["context"].get("event_type") == "NoDispatchableWork"]
            detail = stalled["context"]["root_review"]
            assert detail["status"] == "REVIEW_REJECTED" and detail["reason"] == "root_review_rejected"
            assert detail["repairs_used"] == 0 and detail["max_root_review_repairs"] == 0
            assert detail["findings"] and detail["findings"][0]["criterion_id"] == "c-user-1"
            assert not case.events("GoalResolutionCommitted")

    asyncio.run(run())
