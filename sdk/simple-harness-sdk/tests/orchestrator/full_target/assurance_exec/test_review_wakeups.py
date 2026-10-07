# SPDX-License-Identifier: Apache-2.0
"""审阅这一侧的唤醒事件只唤醒该唤醒的那一项（推后第 1 批车道 P1a，A25 第 2、3 行）。

原计划事件消费表：

* ACTUAL_CHECK_AVAILABLE → REVIEW：工作键是"已有的待办审阅槽"——只唤醒对象、完成范围对得上、检查
  要求里有这项检查的、还没做完的导入；
* BUSINESS_OR_RUNTIME_SETTLED → REVIEW："新事实可解开原等待的工作"——调用与预算结清唤醒卡在预算上
  的导入，不等它按退避到点。

先用产品同形世界走完一个内容审阅必检的任务，留下真实的审阅、检查绑定与工作箱；再把工作箱里的行
摆成"还在等"的样子（外界时机：等的那一刻），看唤醒事件唤醒谁。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

import agent_orchestrator.testing.scripted_replies as scripted  # noqa: E402

FAR = 10**15


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)
    one_step = scripted.one_step_method

    def own_criterion_step(context):  # type: ignore[no-untyped-def]
        method = one_step(context)
        for link in method["composition"]["criterion_links"]:
            link["child_criterion_id"] = "c-notes-written"
        return method

    monkeypatch.setattr(scripted, "one_step_method", own_criterion_step)


def _imports(store, mission_id):  # type: ignore[no-untyped-def]
    """审阅导入工作：用途 → 工作键。"""
    rows = store.connection.execute(
        "SELECT w.work_key, json_extract(b.binding_json,'$.subject.purpose') FROM assurance_pending_work w "
        "JOIN assurance_review_bindings b ON w.work_key LIKE 'review-import:'||b.review_key||':%' "
        "WHERE w.mission_id=? AND w.consumer='REVIEW'", (mission_id,)).fetchall()
    return {purpose: key for key, purpose in rows}


def _hold(store, mission_id, key, reason):  # type: ignore[no-untyped-def]
    """按工作箱允许的转移把一项做完的导入摆回"在等"：重开（目标升到最新事件）→ 领取 → 等待。"""
    head = store.connection.execute(
        "SELECT event_id, seq FROM events WHERE mission_id=? ORDER BY seq DESC LIMIT 1", (mission_id,)).fetchone()
    where = " WHERE mission_id=? AND consumer='REVIEW' AND work_key=?"
    with store.transaction():
        for sql, args in (
            ("UPDATE assurance_pending_work SET state='PENDING', trigger_event_id=?, target_epoch=?, "
             "row_version=row_version+1", (head[0], head[1])),
            ("UPDATE assurance_pending_work SET state='RUNNING', owner='p1a', lease_until_ms=?, "
             "row_version=row_version+1", (FAR,)),
            ("UPDATE assurance_pending_work SET state='WAITING', owner=NULL, lease_until_ms=NULL, "
             "wait_reason=?, not_before_ms=?, row_version=row_version+1", (reason, FAR)),
        ):
            store.connection.execute(sql + where, (*args, mission_id, key))


def _emit_again(case, event):  # type: ignore[no-untyped-def]
    """同一个写方再写一条同类型、同内容的事件（新序号）：唤醒只看事件类型与它指向的事实。"""
    commit = case.world.loop.commit
    with case.store.transaction():
        return commit._emit(event.type, case.mission_id, key="p1a-again:" + event.id,
                            task_id=event.task_id, attempt_id=event.attempt_id, payload=event.payload)


def test_a_check_wakes_only_the_unfinished_import_it_can_complete(tmp_path):
    async def run() -> None:
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            consumer = case.world.loop._assurance_tick.consumers["REVIEW"]
            imports = _imports(case.store, case.mission_id)
            assert {"METHOD_PLAN", "TASK_CONTENT", "MISSION_FINAL"} <= set(imports)
            bound = case.events("AssuranceCheckBound")[-1]
            # 做完的导入不因晚到的检查重解
            assert consumer.classify(_emit_again(case, bound)) == ()
            for purpose in ("METHOD_PLAN", "TASK_CONTENT", "MISSION_FINAL"):
                _hold(case.store, case.mission_id, imports[purpose], "CHECK_PENDING")
            woken = consumer.classify(_emit_again(case, bound))
            # 只有对象、完成范围、检查要求都对得上的那一项（内容审阅）；方法审阅、终审不被这项检查唤醒
            assert [target.work_key for target in woken] == [imports["TASK_CONTENT"]]

    asyncio.run(run())


def test_a_settlement_wakes_an_import_waiting_on_budget_before_its_due_time(tmp_path):
    async def run() -> None:
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            store, tick = case.store, case.world.loop._assurance_tick
            consumer = tick.consumers["REVIEW"]
            imports = _imports(store, case.mission_id)
            released = case.events("BudgetReleased")[-1]
            _hold(store, case.mission_id, imports["TASK_CONTENT"], "BUDGET_WAIT")
            _hold(store, case.mission_id, imports["METHOD_PLAN"], "SOURCE_UNAVAILABLE")
            again = _emit_again(case, released)
            # 只唤醒卡在预算上的那一项；别的原因的等待不归结清事件管
            assert [target.work_key for target in consumer.classify(again)] == [imports["TASK_CONTENT"]]
            await tick.tick()
            rows = dict(store.connection.execute(
                "SELECT work_key, state FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW'",
                (case.mission_id,)).fetchall())
            # 到点还远，被结清事件唤醒后当轮就领走做完（导入早已正式入库：按原回执确认，不重算）
            assert rows[imports["TASK_CONTENT"]] == "DONE"
            assert rows[imports["METHOD_PLAN"]] == "WAITING"

    asyncio.run(run())
