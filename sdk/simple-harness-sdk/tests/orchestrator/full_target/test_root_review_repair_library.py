# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""修复轮里答错的替换决定：被按名拒绝、什么都不写，规划器下一轮仍能答对（HTN 补齐阶段 A′）。

P2.3j 的来历（C1 真机局：修复轮的规划包对已细化的目标什么材料都不给）已由
``product_world/test_repair_material.py`` 守住；"修复时换做法、在跑的兄弟步骤经执行图收敛"
由 ``product_world/test_repair_replace_method.py`` 守住（迁移裁决 A1 / 实施记录 A′-3）。
这里只剩原文件"替换必须明说"那一组里还没有覆盖的两条，在产品同形世界里重写：

* 对已细化的目标再发一次 REFINE（不退役旧做法就想换）→ 被拒，计划版本不动；
* REPLACE_METHOD 换成**同一个做法、同样的参数** → 按名拒绝 ``REPAIR_NOT_ALLOWED``（2026-09-27
  真机局：这种替换曾死在库表唯一约束上、报成内部错误），计划版本不动。

两种错答在同一局里先后发出（一步内容被退回 → 修复轮），之后规划器答"同一做法再试一次"，
任务照常完成：错答不变成系统事实，也不卡死任务。

原文件第三条"退役的实例必须是这一处正在用的那个"已由纯函数用例
``test_planning_decision_admission.py::test_an_instance_belonging_to_another_subject_is_refused`` /
``test_an_instance_that_is_not_active_on_the_subject_is_refused``（``METHOD_RETIRED``）与
``test_a_one_character_difference_in_any_quadruple_component_is_refused``（陌生引用
``REF_OUTSIDE_CONTEXT``）钉住，不另写（偏离：原用例按"删除"处理）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _wrong_answer(kind: str, package: dict[str, Any]) -> str:
    """规划器在修复轮里的一条错答：对被修复的已细化目标再细化一次，或"换成"同一个做法。"""

    [goal] = [item for item in package["views"]["goals"] if item.get("under_repair") and item.get("adopted_method")]
    subject = next(item["subject_key"] for item in package["planning_subjects"]
                   if item["occurrence_id"] == goal["occurrence_id"])
    current = dict(goal["adopted_method"]["method_ref"])
    if kind == "refine_again":
        return decision(subject, "REFINE", {"method_ref": current, "bindings": goal["params"]},
                        "对这个目标再细化一次。")
    instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                    and item["id"] == goal["adopted_method"]["method_instance_id"])
    return decision(subject, "REPAIR", {"repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                                        "replacement_method_ref": current, "bindings": goal["params"]},
                    "换成同一个做法、同样的参数。")


WRONG = ("refine_again", "readopt_same")


def test_a_wrong_replacement_in_the_repair_round_is_refused_and_writes_no_revision(tmp_path):
    asked: list[str] = []

    def planner(request: Any) -> Any:
        package = package_of(request)
        repairs = [entry for entry in package.get("repair_requests") or ()
                   if ((entry.get("request") or {}).get("context") or {}).get("event_type") != "GoalUnrefined"]
        if not repairs:
            return planner_reply(request)
        if len(asked) < len(WRONG):
            asked.append(WRONG[len(asked)])
            return _wrong_answer(asked[-1], package)
        asked.append("retry")
        return retry_same_method(request)

    rejected = {"done": False}

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT" and not rejected["done"]:
            rejected["done"] = True
            return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：内容不满足要求。")
        return review_reply(data)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider, max_planning_attempts=4) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "idempotency_key": "repair-wrong-replace",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            events = list(world.store.list_events(mission_id))
            assert str(mission.status.value) == "COMPLETED", [event.type for event in events][-20:]
            return events

    events = asyncio.run(case())
    assert asked == [*WRONG, "retry"]
    evaluated = [event for event in events if event.type == "PlanningDecisionEvaluated"]
    revisions = [event for event in events if event.type == "PlanRevisionCommitted"]
    # 两条错答各被拒一次，依次是"再细化"（预览就被拒）和"换成同一个做法"（提交时被拒）。
    refused = [event for event in evaluated if event.payload["status"] in {"REJECTED", "COMMIT_REJECTED"}]
    assert [(event.payload["decision_type"], event.payload["status"]) for event in refused] == [
        ("REFINE", "REJECTED"), ("REPAIR", "COMMIT_REJECTED")]
    refine, readopt = refused
    assert [event.payload["reason"] for event in events if event.type == "PlanningRejected"] == [
        "proposal_not_grounded"] * 2
    assert "exactly one open occurrence" in json.dumps(refine.payload["detail"])
    assert readopt.payload["rejection_codes"] == ["REPAIR_NOT_ALLOWED"]
    assert "a repair must choose a different method or different parameters" in json.dumps(readopt.payload["detail"])
    # 错答什么都没提交：从头到尾只有第一版计划（"再试一次"不是新计划版本）。
    assert [event.payload["base_plan_revision"] for event in revisions] == [0]
    assert all(event.seq < refine.seq for event in revisions)
    # 修复请求不是被错答了结的，而是被随后的"再试一次"了结。
    [addressed] = [event for event in events if event.type == "PlanningRepairAddressed"]
    assert addressed.seq > readopt.seq and addressed.payload["decision_type"] == "REPAIR"


# ======================================================================================
# 暂留给导入方的旧名字（``test_criteria_driven_write_step.py`` 用 ``C1_FINDING``；
# ``test_h1h_retired_method_unknown_action.py`` 用 ``_alt_method`` / ``_adopted_root`` /
# ``_replacement``）。它们改掉导入后随删。``_rejected_open`` 建在已退役的
# ``test_htn_end_to_end`` 世界与旧根审阅员上，无法保留。
# ======================================================================================

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

FIXTURE = _HTN_FIXTURES / "c1_repair_round"
ROOT_TASK = "task-root"
ROOT_DUTY = "obl-root"
C1_FINDING = json.loads((FIXTURE / "root_review_rejected.json").read_text(encoding="utf-8"))["findings"][0]


def _alt_method(method_id: str = "plan.alt"):
    from agent_orchestrator.contracts.htn import TaskForm
    from htn_world import method, out, param, step

    return method(
        method_id, "plan.goal", parameter_schema="plan.goal.params",
        steps=(
            step("leaf2", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                 capabilities=("plan.read",)),
            step("review2", "plan.review", TaskForm.PRIMITIVE,
                 {"subject": param("subject"), "result": out("leaf2", "result")}, capabilities=("plan.read",)),
        ),
        links=(("c-root", "review2", "c-reviewed"),),
        finalizer="review2",
    )


def _adopted_root(world: Any) -> str:
    network = world.network()
    draft = network.adopted_instance_for(network.root_occurrence_ids[0])
    assert draft is not None
    return str(draft.instance_id)


def _replacement(world: Any, contract: Any, *, instance_id: str, revision: int, proposal_id: str = "p-replace") -> str:
    from scripted_plans import plan_revision_proposal_step

    reference = contract.method_ref()
    method_ref = {"id": reference.method_id, "version": reference.version, "content_hash": reference.content_hash}
    return plan_revision_proposal_step(
        proposal_id=proposal_id, expected_plan_revision=revision,
        read_set=[{"kind": "method", "id": reference.method_id, "semantic_revision": reference.version,
                   "content_hash": reference.content_hash}],
        operations=[
            {"op": "retire_method", "method_instance_id": instance_id,
             "reason": "the root review rejected this instance's result"},
            {"op": "refine", "goal_id": ROOT_TASK, "obligation_id": ROOT_DUTY, "method_ref": method_ref, "bindings": {}},
        ],
        rationale="replace the rejected method with the alternative",
    )
