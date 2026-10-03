# SPDX-License-Identifier: Apache-2.0
"""作用域纪元与计划提交的读集（HTN 补齐阶段 D）。

计划修订号是结构闸门；读集另核它管不到、在提案与提交之间会变的东西——现在包括作用域纪元
和被细化目标的义务。规划器作答期间纪元动了，它的回复按"请求过期"拒收、重问，不算它答错。
待答的用户题目不因纪元变化被收回。

**改坏检验**：过期拒绝照样计入答错 → 第一条里答错次数不为 0 → 变红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, planner_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_stale_reply_after_epoch_moved_is_not_charged(tmp_path):
    state: dict[str, Any] = {"world": None, "mission": None, "bumped": False}

    def planner(request: Any) -> Any:
        if not state["bumped"] and state["mission"] is not None:
            # 规划器作答期间世界变了（真实会发生：一个文件变了、一条观察的真值翻了）
            state["bumped"] = True
            HtnStore(state["world"].store).bump_epoch(state["mission"], "mission", bumped_by="test-world-moved")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            state["world"] = world
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "epoch-stale"})["mission_id"]
            state["mission"] = mission_id
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            events = list(world.store.list_events(mission_id))
            stale = [e for e in events if e.type == "PlanningRejected"
                     and any(p.get("code") == "REQUEST_BINDING_STALE"
                             for p in (e.payload.get("detail") or {}).get("problems", ()))]
            assert len(stale) == 1, [e.payload for e in events if e.type == "PlanningRejected"]
            # 那次拒收不算规划器答错：把它从事件流里单独数一遍
            from agent_orchestrator.contracts.error_table import refusal_charges_planner

            assert refusal_charges_planner(["REQUEST_BINDING_STALE"]) is False
            assert refusal_charges_planner(["METHOD_STRUCTURE_INVALID"]) is True
            assert refusal_charges_planner([]) is True
            # 提交的读集：有作用域纪元和被细化目标的义务，没有已删的三项
            rows = [json.loads(row[0]) for row in world.store.connection.execute(
                "SELECT item_json FROM plan_read_sets WHERE mission_id=? AND subject_type='read_set'"
                " ORDER BY created_at, rowid", (mission_id,))]
            read_set = rows[-1]
            assert {"scope_id": "mission", "validity_epoch": 1} in read_set["scope_epochs"]
            assert read_set["obligation_revisions"], read_set
            assert not {"manager_epoch", "budget_grant_revision", "support_sets"} & set(read_set)

    asyncio.run(case())


def test_pending_question_survives_epoch_change(tmp_path):
    """规划器问了用户一个问题；用户还没答，世界变了（纪元加一）→ 题目仍在等回答，不被收回；
    用户回答后规划照常继续、任务完成。纪元管的是依据凭证，不管该不该问人（阶段 D 偏差单 6）。

    **改坏检验**：把纪元放回题目的绑定里 → 纪元一变题目被判过期收回 → 变红。"""
    from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
    from agent_orchestrator.testing.fixtures import package_of
    from agent_orchestrator.testing.scripted_replies import decision

    state = {"asked": False}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if not state["asked"]:
            state["asked"] = True
            goal = next(item for item in package["views"]["goals"] if item["open"])
            return decision(goal["subject_key"], "REQUEST_HUMAN", {
                "question": "NOTES.md 用中文还是英文？", "options": [], "blocking": True}, "先问清楚语言。")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "question-epoch"})["mission_id"]
            humans = PlanningHumanStore(world.store)
            for _ in range(6):
                await world.drain(timeout=20)
                if humans.list(mission_id):
                    break
            [question] = humans.list(mission_id)
            assert question["state"] == "PENDING"
            HtnStore(world.store).bump_epoch(mission_id, "mission", bumped_by="test-world-moved")
            await world.drain(timeout=20)
            [question] = humans.list(mission_id)
            assert question["state"] == "PENDING"  # 没被收回
            world.control.answer_planning_question({
                "decision_id": question["decision_id"], "answer": "中文",
                "expected_version": int(question["version"]), "nonce": "answer-1"})
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report

    asyncio.run(case())
